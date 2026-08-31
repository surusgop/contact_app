# Implementation: Tagging in the Contact App

**Status: shipped and verified.** See [goal.md](goal.md) for the why/what without implementation detail, and [changes.md](changes.md) for the chronological build/test log. This document is the technical "how" — written as a reference for the final, as-built implementation, not a forward-looking proposal.

---

## Table of contents

- [1. Scope and design decisions](#1-scope-and-design-decisions)
- [2. How this fits the existing app](#2-how-this-fits-the-existing-app)
- [3. Backend implementation](#3-backend-implementation)
  - [3.1 Helper functions](#31-helper-functions)
  - [3.2 Multi-tag splitting](#32-multi-tag-splitting)
  - [3.3 Column mapping: detecting a tags column](#33-column-mapping-detecting-a-tags-column)
  - [3.4 Route changes](#34-route-changes)
  - [3.5 Tag resolution & the batch cache](#35-tag-resolution--the-batch-cache)
  - [3.6 The `signup_taggings` request body](#36-the-signup_taggings-request-body)
  - [3.7 Idempotency and duplicate handling](#37-idempotency-and-duplicate-handling)
  - [3.8 Audit logging](#38-audit-logging)
- [4. Frontend implementation](#4-frontend-implementation)
- [5. Confirmed end-to-end behavior](#5-confirmed-end-to-end-behavior)
- [6. Edge cases & failure modes](#6-edge-cases--failure-modes)
- [7. How this was tested](#7-how-this-was-tested)
- [8. Remaining / deferred work](#8-remaining--deferred-work)

---

## 1. Scope and design decisions

Decisions made while building this, kept here so the reasoning isn't lost:

1. **Tagging only applies to rows that already resolve to a `signup_id`** via the existing name-matching/Find flow. No new-signup creation was added — see [goal.md](goal.md)'s scope boundary.
2. **A tag field can hold more than one tag** (comma/semicolon-separated) — see [§3.2](#32-multi-tag-splitting). This was a mid-course correction: the original plan started as "one tag per field, v1" with splitting deferred to a later phase, but it was pulled forward and built immediately once it came up, since the single-literal-tag behavior was very likely wrong for real spreadsheet data and the fix was small.
3. **When both a whole-list tag and a per-row spreadsheet tags-column value exist, both are applied** — a row can end up with two tags from one import (one shared across the batch, one specific to that row). Confirmed live: a 3-person test file with per-row tags plus a shared list-wide tag produced exactly 6 tags applied (3 × 2).
4. **The "apply to entire list" tag field exists on both the file-upload section and the paste section**, not just file upload — extending the original ask (which only mentioned "the spreadsheet part") to paste too, since paste already produces a batch of rows the same way file upload does.
5. **Tagging happens as part of the existing import action**, not a separate button/mode — it's an additional side effect for rows that have a tag and a resolved signup, and doesn't require `contact_method`/`contact_status` to be filled in.
6. **Reuses the existing NationBuilder auth/token flow** (`get_nb_token`) and the existing per-nation setup already in place — no new auth surface.
7. **Unmatched (non-signup) entries simply don't get tagged**, with no signal beyond a lower "tags applied" count in the results panel — accepted as-is, not revisited.

## 2. How this fits the existing app

The import pipeline, for reference (see [walkthrough.md](walkthrough.md) for full detail):

```
File / paste / manual entry
        │
        ▼
  row dict { signup_id, author_id, contact_method, contact_status, contact_date, content, tag, _first_name, ... }
        │
        ▼
   POST /bulk/import  →  per row: POST /api/v2/contacts  (logs an interaction against the signup)
```

Tagging is an **independent, additional per-row action** that only needs the same `signup_id` the pipeline already resolves — it never touches the `/api/v2/contacts` call. NationBuilder tags attach to **signups**, not to contacts, so this is a second, unrelated API call sequence per row:

```
row dict { ..., tag: "Canvass Kickoff 6/17, VIP" }
        │
        ▼
  split into individual tag names (§3.2)
        │
        ▼
  (once per unique tag name across the whole batch)
  find-or-create signup_tag  →  tag_id
        │
        ▼
  (once per row that has a signup_id, once per tag name that row has)
  apply tag_id to signup_id  via signup_taggings
```

Because tag resolution and tag application are independent of the contact-log POST, a row succeeds at tagging even if its contact-log entry fails validation, and vice versa. This was confirmed for real, not just by design: a production test hit an unrelated bug (a bad `author_nb_id` saved during Setup) that made the contact-log POST fail outright, and the tag still applied correctly in the very same request.

## 3. Backend implementation

All additions live in [app.py](app.py), following the existing style (plain functions, `requests` calls, `get_nb_token`, no new dependencies).

### 3.1 Helper functions

Three functions, added right after `get_nb_token`:

```python
def find_or_create_tag(nation_slug: str, token: str, tag_name: str) -> str:
    """Return the signup_tag id for tag_name in this nation, creating it if it doesn't exist.
    NationBuilder tag names are unique and case-insensitive, matching the API's default
    (case-insensitive) filter behavior — so a lookup by name is a correct existence check."""
    # GET /api/v2/signup_tags?filter[name]=<tag_name> ; if a match comes back, return its id;
    # otherwise POST /api/v2/signup_tags {"data": {"type": "signup_tags", "attributes": {"name": tag_name}}}
    # and return the new id.

def resolve_tags_for_batch(nation_slug: str, token: str, tag_names) -> dict:
    """Resolve every distinct tag name in a batch ONCE, up front. Returns {tag_name_lower: tag_id}.
    Prevents redundant API calls and a duplicate-tag race condition — see §3.5."""

def apply_tag_to_signup(nation_slug: str, token: str, signup_id: str, tag_id: str) -> bool:
    """Create a signup_tagging linking signup_id to tag_id. Returns True if newly applied,
    False if the signup already had the tag (a confirmed no-op, not an error — see §3.7).
    Raises requests.HTTPError for any other failure."""
    # POST /api/v2/signup_taggings — see §3.6 for the confirmed body shape.
```

These are plain, reusable functions — not Flask routes — called from the route handlers in §3.4.

**Verified live** against `suruszoo` (an internal Surus test/sandbox nation): `find_or_create_tag` creates a new tag on first call and returns the same id on a second call with different capitalization (no duplicate created); `apply_tag_to_signup` returns `True` on a fresh apply and `False` (no exception) on a repeat apply of the same signup+tag, using the real 422 shape from §3.7. All test tags/taggings were deleted afterward, restoring each test signup's original tags exactly.

### 3.2 Multi-tag splitting

A tag field or spreadsheet cell can legitimately hold more than one tag (e.g. `"Volunteer, Event Attendee"`). A dedicated helper handles this, next to the other tagging helpers:

```python
def split_tag_names(raw: str) -> list:
    """Split a tag field/cell that may hold multiple tags (comma and/or
    semicolon separated) into a clean list - trimmed, empties dropped,
    deduped case-insensitively while keeping the first-seen casing and order."""
```

Applied everywhere `/import` and `/bulk/import` read a `tag`/`list_tag` value (§3.4), so `"Volunteer, Event Attendee"` correctly becomes two separate tags — both created (if needed) and applied — instead of one literal tag named `"Volunteer, Event Attendee"`.

One API shape consequence: `/import`'s response field is `tag_results` (a **list**), not a single `tag_result`, since one contact can now carry several tags from one field.

**Verified live:** a batch row with a 2-tag column value *and* a separate whole-list tag correctly applied all 3 as distinct tags; a single `/import` call with `"tag-x, tag-y"` correctly returned two entries in `tag_results`, both applied.

### 3.3 Column mapping: detecting a tags column

`ai_map_and_clean`'s mapping prompt (listing `signup_id`, `author_id`, `contact_method`, `contact_status`, `contact_date`, `content`, plus the `_full_name`/`_first_name`/`_last_name` name-handling instructions) has one more target field, `_tag`, with an explicit "do NOT mark a tag column as null" instruction — the same treatment name columns get — plus an explicit case-insensitivity call-out: *"tag", "TAG", "Tag", "tags", "Tags", "TAGS", "Label", "Labels", "Group" all mean the same thing here.*

`_apply_mapping_locally` has a matching branch:

```python
elif nb == "_tag":
    row["tag"] = val
```

Downstream, a row carries a `tag` key sourced from the spreadsheet itself, independent of whatever the user types in the whole-list tag field.

**Verified live** with real (non-mocked) `gpt-4o-mini` calls: column headers `tag`, `Tag`, `Tags`, `TAG`, and `Label` were all correctly mapped to `_tag`, with the row's tag value carried through correctly in every case.

> **v1 scope note still in effect:** if a spreadsheet's tags column contains multiple tags per cell, they're split per §3.2 — this note originally said "no comma-splitting," which is no longer accurate; kept here only so the history is clear.

### 3.4 Route changes

| Route | What it does |
| --- | --- |
| `POST /import` (single contact) | Reads a `tag` form field, splits it (§3.2), and applies each resulting tag after the contact POST — independently of whether that POST succeeded (§2). Response includes `tag_results`, a list of `{success, tag, already_tagged}` (or an error shape). |
| `POST /bulk/upload` | No route-level change — the tags-column detection happens inside `ai_map_and_clean`/`_apply_mapping_locally` (§3.3), which this route already calls. Returned rows may carry a `tag` key. |
| `POST /bulk/paste` | Unchanged — tags from paste come from the whole-list field client-side, not from the extracted prose. |
| `POST /bulk/import` | The main route. Rows may carry a `tag` (from an AI-detected spreadsheet column) and/or a `list_tag` (the whole-list field) — both get applied per row when both are present. Before the per-row loop, every distinct tag name across the whole batch (after splitting) is collected and resolved exactly once via `resolve_tags_for_batch` (§3.5). Tag application happens inside the existing per-row loop, but outside the contact try/except, so it runs regardless of that row's contact-log outcome. Results: `results.tags = {"applied": N, "already_tagged": M, "failed": F, "errors": [...]}`; the audit log gains `tags_applied`. |

**Verified live** via the actual Flask routes (not just the underlying helper functions, using `app.test_client()` with a faked logged-in session) against `suruszoo`, and separately in a real browser against a production nation.

> **Unrelated bug found while testing this, still unfixed by choice:** `bulk_import`'s existing `attributes` dict can send `contact_method`/`contact_status` to NationBuilder as an **empty string** rather than omitting the key, which NationBuilder's API rejects (`contact_status value must be one of [...] or null`; on `/import`, `Contact method can't be blank`). This happens for any row where the key is present but blank — the normal shape for paste-derived rows (`bulk_paste` always sets both via `setdefault(..., "")`) and manually-added blank rows. Pre-existing, not caused by this feature; flagged for a separate decision (see [§8](#8-remaining--deferred-work)).

### 3.5 Tag resolution & the batch cache

The whole-list tag field means **the same tag name can appear on every row of an import**. Resolving it once — not once per row — matters for two reasons:

1. **Performance:** a 200-row import with one shared tag makes one `find_or_create_tag` call, not 200.
2. **Correctness:** resolving a brand-new tag name independently per row (naive per-row `GET`-then-`POST`) risks two rows processed close together both failing to see the tag yet and both trying to create it.

The fix: before applying any tags, build the **set** of distinct tag names in the batch (whole-list tag + every row's spreadsheet-column tag, each split per §3.2), resolve each name to an ID exactly once via `resolve_tags_for_batch`, and have every row look up its tag ID(s) from that resolved map. Mirrors how `get_nb_token` is fetched once per import rather than once per row.

### 3.6 The `signup_taggings` request body

Confirmed directly from NationBuilder's docs (this was an open gap early on, since resolved). Creating a tagging uses plain `attributes` — **not** a `relationships` block, unusually for this API:

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

This is the one resource in the whole NationBuilder API surface this app uses where creation happens via plain attributes rather than relationships — worth remembering if a similar resource comes up later.

### 3.7 Idempotency and duplicate handling

A signup may already carry the tag being applied (a prior event, or a duplicate name on the same sign-in sheet). **Confirmed against a real nation:** applying a tag a signup already has returns a `422` with this exact shape:

```json
{"errors": [{"code": "unprocessable_entity", "status": "422", "title": "Validation Error",
  "detail": "Tag has already been taken",
  "source": {"pointer": "/data/attributes/tag_id"},
  "meta": {"attribute": "tag_id", "message": "has already been taken", "code": "taken"}}]}
```

`apply_tag_to_signup` checks for `meta.code == "taken"` on a 422 and returns `False` (already-tagged, not a failure) instead of raising — confirmed working for both the fresh-apply (`True`) and duplicate-apply (`False`, no exception) cases, including in real browser use against production data (re-tagging someone who already had a tag correctly showed as "Already Tagged," not a failure). Any other 422/4xx still raises normally, treated as a real failure — mirroring how `bulk_import` already treats per-row NationBuilder errors as data to report, not exceptions to propagate.

If a batch contains the same person twice with the same tag, the second application resolves to `False` the same way — no special-casing needed.

### 3.8 Audit logging

Extended the existing `log_action` calls (`single_import`, `bulk_import`) rather than adding a new action type:

```python
log_action("bulk_import", ..., details={
    ...,
    "tags_applied": [{"tag": tag_name, "signup_id": ..., "name": ...}, ...],
})
```

(`single_import`'s log gained the analogous `"tags": tag_results` list.) A separate `tags_created` list (which names were brand-new vs. already existing) was considered and dropped — it would need a changed return contract on `find_or_create_tag`, not worth the churn for an audit-log nicety. `tags_applied` still gives a full record of which signups got which tags, which is what actually matters for an audit trail. This reuses infrastructure that already existed — `get_user_nations` already reads this same log table back for the Setup page's saved-nations list.

> **Related, but a separate feature:** contacts submitted through `/bulk/import` are now *also* logged to a dedicated Databricks table (`universal.contacts.contact_app_logs`), not just this audit-log JSON blob. That work came after tagging was finished and isn't part of this document's scope — see [changes.md §6](changes.md) for the full detail.

## 4. Frontend implementation

All in [templates/combined.html](templates/combined.html), following its existing patterns (imperative DOM construction for manual forms, shared `NB_FIELDS`-style row contract, per-section import bars). One simplification from the original design: instead of keeping a separate `list_tag` key alongside each row's own `tag`, the client folds the whole-list tag directly into `row.tag` (comma-joined) at the moment rows are staged — the backend's `split_tag_names` already splits and dedupes a joined string correctly, so there was no need to carry two parallel keys through the UI.

- **Manual entry** — a Tag text input on each manual-entry card (`addManualForm()`), wired the same way every other manual-form field is wired: `tagInp.addEventListener('input', () => { formData.tag = tagInp.value.trim(); })`.
- **File upload section** — a "Tag this entire list" text input (`#file-list-tag`). A `mergeListTag(rows, listTagValue)` helper folds it into every new row's `tag` field the moment rows come back from `/bulk/upload` — comma-joined with whatever tag that row already carries from its own spreadsheet column, skipping the join if it's already an exact (case-insensitive) match.
- **Paste section** — the same `#paste-list-tag` input and `mergeListTag` call, applied to `pasteRows`.
- **Staged preview table** — a Tag column added to `NB_FIELDS` (`['signup_id', 'contact_method', 'contact_status', 'contact_date', 'content', 'tag']`). No special-case rendering needed — `renderPreviewRow`'s existing generic branch (a plain text input bound on `change`) already handles any field name it doesn't specifically recognize. A row with both a spreadsheet-detected tag and a list-wide tag shows them as one comma-joined, directly editable string.
- **Results panel** — `showResults()` renders a second, parallel "Tagging" block when `results.tags` is present and non-empty — Tags Applied / Already Tagged (only shown if non-zero) / Tag Failures stat tiles, plus a "Failed Tags" error list. Kept visually distinct from the contact-log stats above it.
- **Not built:** tag autocomplete against existing nation tags — see [§8](#8-remaining--deferred-work).

## 5. Confirmed end-to-end behavior

What was actually run and observed, replacing the original hypothetical walkthrough:

1. A 3-row test spreadsheet (`tag_test_import.csv`) with columns `Name`, `Tag`, `Notes` — each of 3 real people given a different per-row tag.
2. Uploaded via **Upload Files**; the AI correctly mapped the `Tag` column to `_tag` and `Name` to `_full_name`.
3. A shared "tag this entire list" value was also filled in.
4. All 3 people auto-matched to real signups via the existing name-matching pipeline.
5. **Import All** clicked. Result: **3 imported, 0 failed, 6 tags applied, 0 tag failures** — exactly 3 people × 2 tags each (their own + the shared one).
6. Separately, re-importing one of those same people with the same tag correctly showed **1 already tagged, 0 tag failures** — not a failure.
7. Separately again, a manual-entry test with a tag succeeded at tagging even while the contact-log side failed outright (due to an unrelated bad `author_nb_id` bug, since fixed) — confirming the independence design under a real, unplanned failure.

## 6. Edge cases & failure modes

| Case | Handling |
| --- | --- |
| Tag field left blank | No tag-related API calls at all for that row/batch — fully backward compatible. |
| Row has no `signup_id` | Tag is simply not applied (can't tag a person you haven't identified) — a skipped outcome, not a hard error for the whole batch. |
| Same tag name, different capitalization, across two rows in one batch | Resolved to the **same** tag ID via the batch-level cache (§3.5) — no duplicate tag created. |
| Tag already applied to that signup | A no-op success, not a failure (§3.7) — confirmed live. |
| A field/cell holds multiple tags (comma/semicolon-separated) | Split into separate tags, each created (if needed) and applied (§3.2) — confirmed live. |
| `POST /api/v2/signup_tags` fails (network, auth, validation) | That batch's tagging step fails gracefully — contact-log imports proceed independently; tag failures reported separately. |
| Spreadsheet's own tags column *and* the whole-list field both filled in | Both applied — confirmed live, 3×2=6 tags in one real test. |
| Extremely large batch, one shared tag | Still one `find_or_create_tag` call regardless of row count (§3.5). |

## 7. How this was tested

No formal test suite exists in this repo — everything below was run by hand, in order of increasing realism:

1. **Direct function calls** against `suruszoo` (an internal Surus sandbox nation) to verify `find_or_create_tag`, `resolve_tags_for_batch`, and `apply_tag_to_signup` in isolation, including reproducing NationBuilder's actual duplicate-tagging error to confirm the exact shape to handle.
2. **Route-level calls** via `app.test_client()` with a faked logged-in session, hitting `/import` and `/bulk/import` for real against `suruszoo` — confirming the routes themselves (not just the helpers) behave correctly, including the tag/contact independence guarantee.
3. **A real browser session** against a production nation (`affordablenaperville`, a nation being decommissioned the same day and used freely as a de facto sandbox) — every input mode (manual entry, file upload with a real generated test spreadsheet, paste notes) exercised through the actual deployed UI on Railway.
4. **Cleanup discipline throughout:** every test tag created during scripted testing was deleted afterward, with the affected test signup's tag list verified back to its exact original state each time.

Every test run has passed on the first try since the initial round of fixes.

## 8. Remaining / deferred work

| Item | Status |
| --- | --- |
| Tag autocomplete against existing nation tags | Not built. Recommended: NationBuilder has no fuzzy matching on tag names, so a typo (`"Voluteer"` vs `"Volunteer"`) silently creates a permanent near-duplicate tag rather than erroring. Mitigation sketch: a small autocomplete dropdown backed by a new `GET /search-tag?q=&nation_slug=` endpoint, following the existing `/search-volunteer` pattern. |
| Editing a tag directly in the staged preview table before import | Not explicitly tested (the UI supports it — plain editable text input — but the specific case of "edit then confirm the edited value is what's actually sent" hasn't been walked through). |
| The pre-existing `contact_method`/`contact_status` empty-string bug (§3.4) | Known, reproducible, actively affecting any row with no method/status chosen. Explicitly left unfixed per a deliberate decision to flag and move on rather than fix immediately. |
| Creating brand-new signups for unmatched sign-in-sheet entries | Out of scope by design — see [goal.md](goal.md). A separate, larger future initiative if ever prioritized. |
