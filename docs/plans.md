# Implementation Plan: Tagging in the Contact App

See [goal.md](goal.md) for the why/what without implementation detail. This document is the how.

---

## Table of contents

- [1. Assumptions & scope](#1-assumptions--scope)
- [2. How this fits the existing app](#2-how-this-fits-the-existing-app)
- [3. Backend design](#3-backend-design)
  - [3.1 New helper functions](#31-new-helper-functions)
  - [3.2 Column mapping: detecting a tags column](#32-column-mapping-detecting-a-tags-column)
  - [3.3 Route changes](#33-route-changes)
  - [3.4 Tag resolution & the batch cache](#34-tag-resolution--the-batch-cache)
  - [3.5 The signup_taggings request body (confirmed)](#35-the-signup_taggings-request-body-confirmed)
  - [3.6 Idempotency and duplicate handling](#36-idempotency-and-duplicate-handling)
  - [3.7 Audit logging](#37-audit-logging)
- [4. Frontend design](#4-frontend-design)
  - [4.1 Manual entry](#41-manual-entry)
  - [4.2 File upload section](#42-file-upload-section)
  - [4.3 Paste section](#43-paste-section)
  - [4.4 Staged preview table](#44-staged-preview-table)
  - [4.5 Results panel](#45-results-panel)
  - [4.6 Tag autocomplete (recommended, Phase 2)](#46-tag-autocomplete-recommended-phase-2)
- [5. End-to-end walkthrough](#5-end-to-end-walkthrough)
- [6. Edge cases & failure modes](#6-edge-cases--failure-modes)
- [7. Testing plan](#7-testing-plan)
- [8. Phased rollout](#8-phased-rollout)
- [9. Open questions](#9-open-questions)

---

## 1. Assumptions & scope

Stated up front so a wrong assumption is easy to catch and correct:

1. **Tagging only applies to rows that already resolve to a `signup_id`** via the existing name-matching/Find flow. No new-signup creation is added by this plan (see [goal.md](goal.md)).
2. **One tag per field, v1.** The manual-entry and whole-list tag fields are single short-answer text inputs — one tag name per field, not a comma-separated list. Multi-tag input is a natural extension, not in v1.
3. **When both a whole-list tag and a per-row spreadsheet tags-column value exist, both are applied** — a row can end up with two tags from one import (one shared across the batch, one specific to that row). This seems like the intended reading of "at the same time" in the original ask, but is worth a quick confirmation.
4. **The "apply to entire list" tag field is added to both the file-upload section and the paste section**, not just file upload. The original ask only mentioned "the spreadsheet part" explicitly; extending it to paste is a small, consistent addition (paste already produces a batch of rows the same way file upload does) — flagged here as an assumption, easy to drop if unwanted.
5. **Tagging happens as part of the existing import action**, not a separate button/mode. It's an additional side effect for rows that have a tag and a resolved signup — it doesn't require the row to also have `contact_method`/`contact_status` filled in (the app already permits blank contact fields today).
6. **This plan reuses the existing NationBuilder auth/token flow** (`get_nb_token`) and the existing per-nation setup already in place — no new auth surface needed.

## 2. How this fits the existing app

Today's import pipeline, for reference (see [walkthrough.md](../walkthrough.md) for full detail):

```
File / paste / manual entry
        │
        ▼
  row dict { signup_id, author_id, contact_method, contact_status, contact_date, content, _first_name, ... }
        │
        ▼
   POST /bulk/import  →  per row: POST /api/v2/contacts  (logs an interaction against the signup)
```

Tagging slots in as an **independent, additional per-row action** that only needs the same `signup_id` the pipeline already resolves — it doesn't touch the `/api/v2/contacts` call at all. NationBuilder tags attach to **signups**, not to contacts, so this is a second, unrelated API call sequence per row:

```
row dict { ..., tag: "Canvass Kickoff 6/17" }
        │
        ▼
  (once per unique tag name in the batch)
  find-or-create signup_tag  →  tag_id
        │
        ▼
  (once per row that has a tag + a signup_id)
  apply tag_id to signup_id  via signup_taggings
```

Because tag resolution and tag application are independent of the contact-log POST, a row can succeed at tagging even if its contact-log entry fails validation (or vice versa) — the plan treats these as two separate outcomes per row, not one all-or-nothing unit (see [§6](#6-edge-cases--failure-modes)).

## 3. Backend design

All additions live in [app.py](../app.py), following the existing style (plain functions, `requests` calls, `get_nb_token`, no new dependencies needed).

### 3.1 New helper functions

**Status: implemented in `app.py` (right after `get_nb_token`) and verified end-to-end against the `suruszoo` test nation** — see the implementation log at the end of this section.

```python
def find_or_create_tag(nation_slug: str, token: str, tag_name: str) -> str:
    """Return the signup_tag id for tag_name in this nation, creating it if it doesn't exist.
    NationBuilder tag names are unique and case-insensitive, matching the API's default
    (case-insensitive) filter behavior — so a lookup by name is a correct existence check."""
    # GET /api/v2/signup_tags?filter[name]=<tag_name> ; if a match comes back, return its id;
    # otherwise POST /api/v2/signup_tags {"data": {"type": "signup_tags", "attributes": {"name": tag_name}}}
    # and return the new id.

def apply_tag_to_signup(nation_slug: str, token: str, signup_id: str, tag_id: str) -> bool:
    """Create a signup_tagging linking signup_id to tag_id. Returns True if newly applied,
    False if the signup already had the tag (confirmed no-op, not an error — see §3.6).
    Raises requests.HTTPError for any other failure."""
    # POST /api/v2/signup_taggings — see §3.5 for the confirmed body shape.

def resolve_tags_for_batch(nation_slug: str, token: str, tag_names) -> dict:
    """Resolve every distinct tag name in a batch ONCE, up front. Returns {tag_name_lower: tag_id}.
    This is the piece that prevents redundant API calls and duplicate-tag race conditions —
    see §3.4."""
```

**Implementation log:** tested live against `suruszoo` (an internal Surus test/sandbox nation, not a real client nation) using a real non-Surus-team signup already present there (found via the existing `import_20260704` tag). Confirmed: `find_or_create_tag` creates a new tag on first call and returns the same id on a second call with different capitalization (no duplicate created); `apply_tag_to_signup` returns `True` on a fresh apply and `False` (no exception) on a repeat apply of the same signup+tag, using the real 422 shape documented in §3.6. All test tags/taggings were deleted afterward, restoring the test signup's original three tags exactly.

These are plain, reusable functions — not Flask routes — called from the route handlers below.

### 3.2 Column mapping: detecting a tags column

**Status: implemented in `app.py` and verified live via real AI calls.**

`ai_map_and_clean`'s mapping prompt (listing `signup_id`, `author_id`, `contact_method`, `contact_status`, `contact_date`, `content`, plus the `_full_name`/`_first_name`/`_last_name` name-handling instructions) gained one more target field, `_tag`, with an explicit "do NOT mark a tag column as null" instruction — the same treatment name columns already get — plus an explicit case-insensitivity call-out, since that was specifically requested: *"tag", "TAG", "Tag", "tags", "Tags", "TAGS", "Label", "Labels", "Group" all mean the same thing here.*

`_apply_mapping_locally` got a matching branch:

```python
elif nb == "_tag":
    row["tag"] = val
```

Downstream, a row can now carry a `tag` key sourced from the spreadsheet itself, independent of whatever the user types in the whole-list tag field (the `list_tag` field `/bulk/import` already handles from §3.3).

**Verified live** (real `gpt-4o-mini` calls via `ai_map_and_clean`, no mocking): column headers `tag`, `Tag`, `Tags`, `TAG`, and `Label` were all correctly mapped to `_tag`, and the resulting rows carried the right `tag` value through to `_apply_mapping_locally`'s output.

> **Update — no longer v1-limited:** the "no comma-splitting" simplification below was reconsidered and implemented ahead of Phase 2, since it was a small, low-risk change and the single-literal-tag behavior was very likely wrong for real spreadsheet data. A new `split_tag_names(raw)` helper (next to the other tagging helpers in `app.py`) splits on comma **and** semicolon, trims whitespace, drops empties, and dedupes case-insensitively. It's applied centrally wherever `/import` and `/bulk/import` already read a `tag`/`list_tag` value — so a cell/field like `"Volunteer, Event Attendee"` now correctly becomes two separate tags, both created (if needed) and applied. Verified live: a batch row with a 2-tag column value *and* a whole-list tag correctly applied all 3 tags; a single `/import` call with `"tag-x, tag-y"` correctly applied both. One API shape change fell out of this: `/import`'s response field is now `tag_results` (a **list**, since one contact can carry several tags), replacing the earlier singular `tag_result` — safe to change since no UI was built against it yet.

### 3.3 Route changes

| Route | Change | Status |
| --- | --- | --- |
| `POST /import` (single contact) | Accept a new form field `tag` (comma/semicolon-splittable into several tags — see §3.2's update). If present and a `signup_id` was resolved, apply each tag after the contact POST (independently — see §2). | **Done, verified live** — `tag_results` (a list) comes back `[{"success": true, "already_tagged": false, "tag": "..."}, ...]` even when the contact POST itself fails, confirming the independence this section calls for. |
| `POST /bulk/upload` | No change to the endpoint itself — the tags-column detection happens inside `ai_map_and_clean`/`_apply_mapping_locally`, which this route already calls. Each returned row may now carry a `tag` key. | Not started — this is §3.2's AI column-mapping work, next up. |
| `POST /bulk/paste` | The AI extraction prompt gains an optional `tag` key in its per-row JSON schema, same as `contact_method`/`contact_status`, in case the pasted notes name a tag explicitly (e.g. "Tag everyone here as Fundraiser Attendee"). Low priority — most paste-derived tags will come from the whole-list field, not the prose itself. | Not started. |
| `POST /bulk/import` | Accept rows that may carry a `tag` field (from an AI-detected spreadsheet column) and/or a `list_tag` field (the whole-list tag) — both get applied per row if both are present, per the "apply both" decision. Collects the set of distinct non-empty tag names across the whole batch, calls `resolve_tags_for_batch` once, then per row with a `signup_id` calls `apply_tag_to_signup` once per tag name that row has. Tag results tracked separately from contact-import results: `results.tags = {"applied": N, "already_tagged": M, "failed": F, "errors": [...]}`, and `bulk_import`'s audit log now also records `tags_applied`. | **Done, verified live** — tested via the actual Flask route (not just the helper functions) against `suruszoo`: `{"tags": {"applied": 1, "already_tagged": 0, "failed": 0, "errors": []}}`. Tag application succeeded independently of a (pre-existing, unrelated) contact-log validation failure in the same test row — see the note at the end of this section. |

> **Unrelated bug found while testing this:** `bulk_import`'s existing `attributes` dict can end up sending `contact_method`/`contact_status` as an **empty string** to NationBuilder (rather than omitting the key), which NationBuilder's API rejects — `contact_status value must be one of [...] or null`, and separately `Contact method can't be blank` on the single-contact route. This happens whenever a row has the key present but blank, which is the normal shape for paste-derived rows (`bulk_paste` always sets `contact_status`/`contact_method` via `setdefault(..., "")`) and manually-added blank rows in the UI — i.e., this bug already existed before this feature and is not caused by anything here. Flagged for a decision on whether to fix it as part of this work or separately.

### 3.4 Tag resolution & the batch cache

The whole-list tag field means **the same tag name can appear on every single row of an import**. Resolving it once — not once per row — matters for two reasons:

1. **Performance:** a 200-row import with one shared tag should make one `find_or_create_tag` call, not 200.
2. **Correctness:** if a brand-new tag name is resolved independently per row (e.g. via naive per-row `GET`-then-`POST`), two rows processed close together could both fail to see the tag yet and both try to create it — resulting in either a duplicate-name error on the second `POST`, or (if NationBuilder doesn't enforce this atomically) two tags that differ only by ID.

The fix is straightforward: before applying any tags, build the **set** of distinct tag names in the batch (whole-list tag + every row's spreadsheet-column tag, if any), resolve each name to an ID exactly once via `resolve_tags_for_batch`, and have every row look up its tag ID(s) from that resolved map. This mirrors how `get_nb_token` is already fetched once per import rather than once per row.

### 3.5 The `signup_taggings` request body (confirmed)

Previously flagged as a documentation gap; now confirmed from the NationBuilder docs. Creating a tagging uses plain `attributes` — **not** a `relationships` block, unusually for this API:

```json
{
  "data": {
    "type": "signup_taggings",
    "attributes": {
      "signup_id": "<signup_id>",
      "tag_id": "<tag_id>"
    }
  }
}
```

```python
def apply_tag_to_signup(nation_slug: str, token: str, signup_id: str, tag_id: str) -> None:
    resp = requests.post(
        f"https://{nation_slug}.nationbuilder.com/api/v2/signup_taggings",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        json={"data": {"type": "signup_taggings", "attributes": {"signup_id": str(signup_id), "tag_id": str(tag_id)}}},
    )
    resp.raise_for_status()
```

No sandbox round-trip needed before implementing this one — go straight to writing it as above. (The fallback sidepost-onto-the-signup approach from [nationbuilder-guide.md §1.4](nationbuilder-guide.md#14-relationships-sideloading--sideposting) is no longer needed, but is still worth knowing about if a future resource turns out to have the same kind of documentation gap this one had.)

### 3.6 Idempotency and duplicate handling (confirmed)

A signup may already carry the tag being applied (from a prior event, or a duplicate name on the same sign-in sheet). **Verified against a real nation** (`suruszoo`, using an internal test signup, tag created and deleted afterward — see the implementation log below): applying a tag a signup already has returns a **422** with this exact shape:

```json
{"errors": [{"code": "unprocessable_entity", "status": "422", "title": "Validation Error",
  "detail": "Tag has already been taken",
  "source": {"pointer": "/data/attributes/tag_id"},
  "meta": {"attribute": "tag_id", "message": "has already been taken", "code": "taken"}}]}
```

`apply_tag_to_signup` (§3.5's helper, already implemented in `app.py`) checks for `meta.code == "taken"` on a 422 and returns `False` (already-tagged, not a failure) instead of raising — confirmed working for both the fresh-apply (`True`) and duplicate-apply (`False`, no exception) cases. Any other 422/4xx still raises normally, which callers should treat as a real failure — mirroring how `bulk_import` already treats per-row NationBuilder errors as data to report, not exceptions to propagate.

If the batch contains the same person twice (two rows resolving to the same `signup_id` with the same tag), the second application resolves to `False` the same way — no special-casing needed beyond what's already implemented.

### 3.7 Audit logging

**Status: implemented, in a slightly simplified form.** Extended the existing `log_action` calls (`single_import`, `bulk_import`) rather than adding a new action type, keeping with the existing pattern where `bulk_import`'s `details` already carries `contacts_logged`:

```python
log_action("bulk_import", ..., details={
    ...,
    "tags_applied": [{"tag": tag_name, "signup_id": ..., "name": ...}, ...],
})
```

(`single_import`'s log gained the analogous `"tags": tag_results` list.) **Simplification from the original proposal:** a separate `tags_created` list (which names were brand-new vs. already existing) was dropped — `find_or_create_tag` would need a changed return contract to expose that distinction, and it wasn't worth the churn against an already-tested function for an audit-log nicety. `tags_applied` still gives a full record of which signups got which tags, which is the part that actually matters for an audit trail.

This gives a natural audit trail of which tags got applied and to whom, for free, using infrastructure that already exists ([get_user_nations](../app.py) already reads this same log table back for other purposes).

## 4. Frontend design

**Status: implemented in [templates/combined.html](../templates/combined.html)**, following its existing patterns (imperative DOM construction for manual forms, shared `NB_FIELDS`-style row contract, per-section import bars). One simplification from the original design, noted in §4.2 below: instead of keeping a separate `list_tag` key alongside each row's own `tag`, the client folds the whole-list tag directly into `row.tag` (comma-joined) at the moment rows are staged — the backend's `split_tag_names` (added when the "what about a list in the tags column?" question came up) already splits and dedupes a comma-joined string correctly, so there was no need to carry two parallel keys through the UI.

### 4.1 Manual entry

Added a **Tag** text input to each manual-entry card (`addManualForm()`), below the Notes field. Wired the same way every other manual-form field is wired:
```js
tagInp.addEventListener('input', () => { formData.tag = tagInp.value.trim(); });
```
`formData.tag` flows through unchanged, exactly like `formData.content` does.

### 4.2 File upload section

Added a **"Tag this entire list"** text input (`#file-list-tag`) right below the drop zone. A new `mergeListTag(rows, listTagValue)` helper folds it into every new row's `tag` field at the moment rows come back from `/bulk/upload` — comma-joined with whatever tag that row already carries from its own spreadsheet column, skipping the join if it's already an exact (case-insensitive) match. This is the "both apply" behavior from the resolved open question, achieved without needing a second `list_tag` key client-side (see the note at the top of §4).

### 4.3 Paste section

Same `#paste-list-tag` input and the same `mergeListTag` call, applied to `pasteRows` the same way `fileRows` gets it (the "both sections" answer from the resolved open questions).

### 4.4 Staged preview table

Added a **Tag** column to `NB_FIELDS` (`['signup_id', 'contact_method', 'contact_status', 'contact_date', 'content', 'tag']`) and the table header. No special-case rendering needed — `renderPreviewRow`'s existing generic branch (a plain text input bound on `change`) already handles any field name it doesn't specifically recognize, and `tag` isn't one of the specially-handled ones. A row with both a spreadsheet-detected tag and a list-wide tag shows them as one comma-joined, directly editable string, exactly as originally proposed.

### 4.5 Results panel

`showResults()` still reports `success`/`failed` and a failed-rows list for contact imports; it now also renders a second, parallel "Tagging" block when `results.tags` is present and non-empty — Tags Applied / Already Tagged (only shown if non-zero) / Tag Failures stat tiles, plus its own "Failed Tags" error list (row number + tag name + raw error). Kept visually distinct from the contact-log stats above it, per §2's independence principle.

### 4.6 Tag autocomplete (recommended, Phase 2)

Not required for v1, but worth doing soon after: NationBuilder tag names are case-insensitive-unique with **no fuzzy matching** — a typo (`"Voluteer"` vs `"Volunteer"`) silently creates a permanent duplicate tag rather than erroring. Given the FD is typing a tag name into a bare text field, this is a real, likely failure mode.

Mitigation: add a small autocomplete dropdown to the tag input(s), backed by a new lightweight endpoint (e.g. `GET /search-tag?q=&nation_slug=`, following the exact pattern already used by `/search-volunteer`) that does a prefix search over `GET /api/v2/signup_tags`. This nudges the FD toward reusing an existing tag instead of typo-creating a new one, without blocking free-text entry for genuinely new tags.

## 5. End-to-end walkthrough

A concrete run-through, to sanity-check the design against the actual Basecamp use case:

1. FD comes back from a canvass kickoff event with a photo of the paper sign-in sheet.
2. Uploads the photo in the **Upload Files** section, types `"Canvass Kickoff 6/17"` into the new **Tag this entire list** field, clicks **Upload & Map with AI**.
3. The AI vision path (`parse_image_with_ai`, unchanged) extracts names from the handwriting. Say the sheet has no tags column of its own — every row's `tag` stays empty; only `list_tag = "Canvass Kickoff 6/17"` applies.
4. Rows land in the staged queue; the existing name-matching auto-resolves `signup_id` for most attendees, exactly as it does today. A couple of names don't match anyone (first-time attendees) — those rows show no signup_id, same as today (see [goal.md](goal.md)'s scope note — those two people won't get tagged, since they don't exist as signups yet).
5. FD reviews the queue, manually resolves one ambiguous match via the existing Find dropdown, and clicks **Import All**.
6. `/bulk/import` runs: resolves `"canvass kickoff 6/17"` to a tag ID once (creating it, since it's brand new), then for every row that has a `signup_id`, applies that tag ID via a `signup_taggings` call. Contact-log POSTs happen exactly as they do today, in parallel with (not blocked by) tagging.
7. Results panel shows, e.g., "23 imported, 0 failed" for contacts, and "21 tags applied, 0 failed" for tags (2 fewer, matching the 2 unmatched attendees from step 4).

## 6. Edge cases & failure modes

| Case | Handling |
| --- | --- |
| Tag field left blank | No tag-related API calls at all for that row/batch — fully backward compatible with today's behavior. |
| Row has no `signup_id` | Tag is simply not applied to that row (can't tag a person you haven't identified) — surfaced as a skipped/failed tag outcome, not a hard error for the whole batch. |
| Same tag name, different capitalization, across two rows in one batch | Resolved to the **same** tag ID via the batch-level cache (§3.4) — no duplicate tag created. |
| Tag already applied to that signup | Treated as a no-op success, not a failure (§3.6). |
| `POST /api/v2/signup_tags` fails (network, auth, validation) | That whole batch's tagging step fails gracefully — contact-log imports for the batch still proceed independently; tag failures are reported separately (§4.5). |
| Spreadsheet's own tags column *and* the whole-list field both filled in | Both applied to affected rows (assumption #3, §1) — two `signup_taggings` calls per such row. |
| Extremely large batch, one shared tag | Still one `find_or_create_tag` call regardless of row count (§3.4) — no per-row cost added by tagging beyond the one `signup_taggings` call per row. |

## 7. Testing plan

1. **Unit-level checks** (manual, given this repo has no test suite today) for `find_or_create_tag`: brand-new name creates a tag; re-running with the same name (different case) returns the same ID and creates nothing new.
2. **Manual QA pass** covering every row in the [edge cases table](#6-edge-cases--failure-modes) above, run against a real test/sandbox nation, not production.
3. **A full walkthrough** of [§5](#5-end-to-end-walkthrough) end to end with a real (small) test spreadsheet and a real test nation, confirming the results panel's tag counts match expectations.

## 8. Phased rollout

| Phase | Scope | Status |
| --- | --- | --- |
| **Phase 1 (MVP)** | Manual entry tag field + file-upload whole-list tag field + backend find-or-create/apply + preview table Tag column + results panel tag counts. | **Done.** |
| ~~Phase 2~~ (pulled forward into Phase 1) | AI column-mapping detects a spreadsheet's own tags column (§3.2); paste-section whole-list tag field. | **Done** — both were implemented and verified live rather than deferred, once the core mechanics were proven out. |
| ~~Phase 3~~ (pulled forward into Phase 1) | Multi-tag support (comma/semicolon-separated values in a single field/cell). | **Done** — `split_tag_names`, verified live with a 2-tag column value plus a separate whole-list tag applying as 3 distinct tags. |
| **Remaining** | Tag autocomplete against existing nation tags (§4.6) — genuinely deferred, not yet built. | Not started. |
| **Future / separate initiative** | Creating brand-new signups for unmatched sign-in-sheet entries — the scope boundary called out in [goal.md](goal.md). | Not started, intentionally out of scope for now. |

## 9. Open questions — resolved

Carried over from [§1](#1-assumptions--scope); all three were answered and built accordingly:

1. **Apply both** a whole-list tag and a spreadsheet-detected per-row tag when both are present — **confirmed**, and implemented (§4.2).
2. Add the whole-list tag field to the **paste** section too — **confirmed**, and implemented (§4.3).
3. Unmatched (non-signup) sign-in-sheet entries simply don't get tagged, with no signal beyond a lower "tags applied" count — left as-is for v1, not revisited.

No open questions remain blocking the current scope.
