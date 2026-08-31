# Change Log

A record of everything changed in `contact_app` during this conversation, in roughly chronological order. Nothing listed here has been committed or pushed — see [Current git state](#current-git-state) at the bottom.

---

## 1. Repo documentation

### 1.1 [README.md](../README.md) — rewritten
Replaced the original two-line placeholder ("Import contacts into Nationbuilder") with a full usage guide: what the app does, requirements, `.env` setup, running locally, deploying (gunicorn/Procfile), a full walkthrough of the actual UI workflow (sign-in → nation setup → the three import modes → reviewing the staged queue → importing), supported file types, the contact method/status vocabularies, how data is stored (Databricks tables), and a troubleshooting section tied to real error strings from the code.

### 1.2 [docs/walkthrough.md](walkthrough.md) — created
A function-by-function, file-by-file tour of the codebase for future coding work: architecture overview, the "row dict" data contract every import path produces, a section-by-section breakdown of every function in `app.py` with line numbers, a breakdown of `combined.html`'s client-side state and JS functions, a full route reference table, an 18-item **Gotchas** list (including the `runImport()` double-send bug, `/bulk/import` always returning `success: true`, and the in-memory `_users` dict not surviving a restart), and a "where to add things" section.

*(Originally written to the repo root as `walkthrough.md`; you later moved it into `docs/` yourself along with the files below, as part of reorganizing the repo — that reorganization wasn't something I did.)*

### 1.3 [docs/databricks-auth-guide.md](databricks-auth-guide.md) — created, then expanded
First pass reorganized the initial (52-line) Azure Databricks authorization overview you pasted into `docs/databricks.md` into a structured guide (account/API types, authorization methods comparison, unified authentication, config profiles, third-party integrations, cheat sheet).

When you pasted in four more source articles (OAuth token federation, federation policy configuration, the legacy Personal Access Tokens guide, and the full environment-variable/field reference — `docs/databricks.md` grew to 533 lines), the guide was rewritten to cover all five articles: added full OAuth token federation mechanics (account-wide vs. workload identity federation, policy configuration via UI/CLI/API, per-tool example table for GitHub Actions/Kubernetes/Azure DevOps/GitLab/CircleCI/AWS IAM), the complete PAT lifecycle (creation, scoped tokens, auto-scoping, service-principal tokens, all four ways to authenticate with one, the REST API to issue/update tokens), and the full env-var/`.databrickscfg`/Terraform/Config field reference tables. Called out throughout that `app.py`'s `WorkspaceClient(host=..., token=...)` call is plain PAT auth (§5.5 of the guide), since that's what actually matters for this repo.

### 1.4 [docs/nationbuilder-guide.md](nationbuilder-guide.md) — created, then corrected
Reorganized seven NationBuilder documentation articles pasted into `docs/nationbuilder.md` (2,709 lines) into one guide, split into two parts as requested:
- **Part 1** — the general v2 API: design basics, full OAuth 2.0 auth flow, a merged query-parameters reference (three source articles each documented filtering slightly differently — combined into one authoritative table), sideloading/sideposting mechanics, the full 121-row resource relationship map (regrouped by resource under collapsible sections instead of a flat table), error codes, troubleshooting FAQs, and a collapsible full-Signup-resource appendix.
- **Part 2** — **Signup Tags** and **Signup Taggings**, which were interleaved between three unrelated general-API articles in the source and are now fully self-contained: the tag-vs-tagging concept, full endpoint tables for both resources, a practical cookbook (tag at creation, add/remove a tag, delete a tag everywhere vs. untag one person), and a gotchas list.

**Correction:** you later supplied the previously-missing "Create a signup tagging" request-body example from NationBuilder's docs. §2.3 and the cookbook were updated to show the confirmed shape (plain `attributes` — `signup_id` + `tag_id` — notably *not* a `relationships` block, unlike every other create/sidepost example in the guide), and gotcha #5 was rewritten from "the example is missing" to "this is the one resource that breaks the relationships pattern."

### 1.5 [docs/goal.md](goal.md) — written
The why/what for the tagging feature, no implementation steps: the Field Director time-sink problem from the Basecamp note, the fix (tagging rides along on the existing import/matching pipeline), what success looks like, and an explicit scope boundary — this feature tags people already matched to an existing NationBuilder signup; it does not add a way to create brand-new signups for unmatched sign-in-sheet attendees.

### 1.6 [docs/plans.md](plans.md) — written, then updated twice
The full implementation plan: assumptions/scope, how tagging fits the existing import pipeline, backend design (three new helper functions, AI column-mapping changes to detect a tags column, route-by-route changes, the batch-level tag-resolution cache and why it matters, idempotency handling, audit logging), frontend design (manual entry, file upload, paste, preview table, results panel, a recommended Phase 2 tag-autocomplete addition), an end-to-end walkthrough, an edge-case table, a testing plan, and a phased rollout.

**Update 1:** once you supplied the missing `signup_taggings` request-body example, §3.5 was rewritten from "two candidate approaches, verify in sandbox" to the confirmed shape with working code, and the now-unnecessary "verify in sandbox first" testing step was removed.

**Update 2:** after live-testing against a real nation (see [§2](#2-environment--credentials-setup) and [§4](#4-live-verification-against-suruszoo) below), §3.1 and §3.6 were updated from planned/speculative to **confirmed** — including the exact 422 error shape NationBuilder returns for a duplicate tagging, which was previously an open question.

Three open questions from the original plan were also resolved during the conversation (not by editing the doc, but by your direct answers): the whole-list tag field applies to both the file-upload *and* paste sections (not file-upload only), and when a row has both a spreadsheet-column tag and the whole-list tag, **both are applied**.

---

## 2. Environment / credentials setup

### 2.1 `.env` created (gitignored, not committed)
Did not exist at the start of the conversation. Now contains:
- `FLASK_SECRET_KEY` and `APP_URL` (`http://localhost:5000` — deliberately kept as localhost rather than the production Railway URL you shared, so the OAuth callback matches a local redirect instead of bouncing to production)
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
- `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_WAREHOUSE_ID`
- `OPENROUTER_API_KEY`

The original Databricks token (the one that had been sitting in `token.txt`) is kept as a commented-out line above the active `DATABRICKS_TOKEN`, at your request, rather than being discarded.

### 2.2 `token.txt` deleted
Its value was moved into `.env` (`DATABRICKS_TOKEN`) first. `token.txt` itself was never gitignored and showed as untracked in `git status` — flagged as a real risk (a stray `git add .` would have committed it) before it was removed.

---

## 3. Code changes

### 3.1 [app.py](../app.py) — three new functions added
Inserted immediately after `get_nb_token` (around line 660), under a new `# ── Tagging (signup_tags / signup_taggings) — see docs/plans.md §3 ──` section header:

- **`find_or_create_tag(nation_slug, token, tag_name) -> str`** — looks up a signup tag by name (case-insensitive exact match, via NationBuilder's default filter behavior); creates it if it doesn't exist; returns the tag's id either way.
- **`resolve_tags_for_batch(nation_slug, token, tag_names) -> dict`** — resolves every distinct tag name in a batch exactly once, returning `{tag_name.lower(): tag_id}`. Exists to avoid redundant API calls and a duplicate-tag race condition when many rows in one import share the same tag name.
- **`apply_tag_to_signup(nation_slug, token, signup_id, tag_id) -> bool`** — creates a `signup_tagging` linking the two. Returns `True` on a fresh apply, `False` (no exception) if the signup already had the tag — confirmed against a real duplicate-tagging response (see below) rather than guessed at.

No routes or templates were changed yet — these are standalone helper functions, not yet wired into `/import`, `/bulk/import`, or the UI. That's the next step.

### 3.2 [app.py](../app.py) — `/import` and `/bulk/import` wired to the tagging helpers

**`/import`** (single contact): now reads a `tag` form field. After attempting the contact-log POST (unchanged logic), it separately resolves and applies the tag if both `tag` and `signup_id` are present — independent of whether the contact POST succeeded, per `plans.md §2`. The JSON response gained a `tag_result` field (`{"success": bool, "tag": str, "already_tagged": bool}` or an error shape), and the `single_import` audit log entry now records the tag outcome too.

**`/bulk/import`**: rows may now carry a `tag` (AI-detected spreadsheet column) and/or `list_tag` (whole-list field) — both are applied per row when both are present. Before the per-row loop, every distinct tag name across the whole batch is collected and resolved exactly once via `resolve_tags_for_batch` (the batch cache from `plans.md §3.4`). Tag application happens inside the existing per-row loop but outside the contact try/except, so it runs regardless of that row's contact-log outcome. Results now include `results.tags = {"applied", "already_tagged", "failed", "errors"}`, and the `bulk_import` audit log entry gained `tags_applied`.

Both routes were verified against real running Flask routes (via `app.test_client()`, with a faked logged-in session) hitting `suruszoo` live — not just the underlying helper functions in isolation. Test tags were created, applied, verified, and deleted afterward in both cases.

**Unrelated bug surfaced by this testing, not caused by it:** `bulk_import`'s existing attribute-building logic can send `contact_method`/`contact_status` to NationBuilder as an **empty string** rather than omitting the key, which NationBuilder's API rejects outright (`contact_status value must be one of [...] or null`; on `/import`, `Contact method can't be blank`). This is the normal shape for paste-derived rows (`bulk_paste` always sets both keys via `setdefault(..., "")`) and for manually-added blank rows in the UI — meaning this bug already existed in production before this conversation touched the code, for any row imported with no method/status chosen. Flagged in `plans.md §3.3` for a decision on whether to fix it now or separately — decided to leave it flagged and unfixed for now, per your instruction.

### 3.3 [app.py](../app.py) — AI column mapping now detects a spreadsheet's own tags column

`ai_map_and_clean`'s prompt gained a `_tag` target field, with an explicit instruction to recognize it regardless of capitalization or exact wording (`"tag"`, `"TAG"`, `"Tag"`, `"tags"`, `"Tags"`, `"TAGS"`, `"Label"`, `"Labels"`, `"Group"`), matching what you asked for. `_apply_mapping_locally` gained a matching `elif nb == "_tag": row["tag"] = val` branch, so a detected column flows straight into the same `tag` key `/bulk/import` already consumes from Step 1.

Verified with real (non-mocked) `gpt-4o-mini` calls: column headers `tag`, `Tag`, `Tags`, `TAG`, and `Label` were each correctly mapped to `_tag`, with the row's tag value carried through correctly in every case.

### 3.4 [app.py](../app.py) — multi-tag values now split into separate tags

New `split_tag_names(raw)` helper (comma **and** semicolon separators, trimmed, empties dropped, deduped case-insensitively) is now applied everywhere `/import` and `/bulk/import` read a tag value, so a cell/field like `"Volunteer, Event Attendee"` becomes two real, separately-created-and-applied tags instead of one literal tag named `"Volunteer, Event Attendee"`. This was originally deferred to "Phase 2" in the plan but implemented now since it was small and the old behavior was very likely wrong for real data.

`/import`'s response shape changed as a result: `tag_result` (singular) → **`tag_results`** (a list), since one contact can now carry several tags from one field. Safe to change — no UI was built against the old shape yet.

Verified live against `suruszoo`: a `/bulk/import` row with a 2-tag column value (`"zz-test-multi-a, zz-test-multi-b"`) plus a separate whole-list tag (`"zz-test-multi-c"`) correctly applied all 3 as distinct tags; a single `/import` call with `"tag-x, tag-y"` correctly returned two entries in `tag_results`, both applied. All test tags deleted afterward; test signup confirmed back to its original three tags both times.

### 3.5 [templates/combined.html](../templates/combined.html) — Tag UI added

Phase 1's frontend work, all in one file:

- **Manual entry** — a Tag input on every manual-entry card, wired to `formData.tag`.
- **File upload section** — a new "Tag this entire list" input (`#file-list-tag`).
- **Paste section** — the same, `#paste-list-tag`.
- **New `mergeListTag(rows, listTagValue)` helper** — folds the whole-list tag into each row's `tag` field (comma-joined with whatever that row already has from its own spreadsheet column, skipping an exact case-insensitive duplicate) the moment rows come back from `/bulk/upload` or `/bulk/paste`. This is why the client never needed a separate `list_tag` key: the server's `split_tag_names` (§3.4) already knows how to split and dedupe a joined string, so joining client-side is enough.
- **Staged preview table** — a new Tag column (`NB_FIELDS` gained `'tag'`); no special rendering code needed since the table's existing generic branch (plain text input) already handles any field it doesn't specifically recognize.
- **Results panel** — `showResults()` now renders a second "Tagging" block (Tags Applied / Already Tagged / Tag Failures, plus a Failed Tags list) whenever `results.tags` is present, kept visually separate from the contact-import stats above it.
- **`resetAll()` and `importSection()`** updated to clear/reset the two new tag inputs at the right times.

Verified: the full page renders with no template errors and all new elements/functions present (checked via `app.test_client()` against a rendered `/`), and the embedded `<script>` block's braces/parens/brackets are balanced. No Node.js is available in this environment to run a true JS parse, so this was checked as thoroughly as the toolchain allows short of manual browser testing.

### 3.6 Real browser walkthrough (Playwright)

Not a code change — a verification pass, done at your request, using an actual rendered browser rather than `app.test_client()`/direct API calls. Installed Playwright + Chromium into the project's `.venv` (not otherwise a dependency of this repo — not added to `requirements.txt`, since it's a one-off verification tool, not something the app needs at runtime).

Ran the real Flask dev server, minted a valid session cookie for a throwaway test login, and drove Chromium through: uploading a CSV (with its own `Tags` column) with a "tag this entire list" value set, pasting text about the same person with a different "tag this entire list" value, checking the merged Tag column in the staged preview table, filling the manual-entry Tag field, and clicking **Import All** — screenshotting at each step, and checking the browser console for JS errors (none found).

**Result:** 2 contacts imported, 0 failed; 2 tags applied, 1 correctly recognized as already-tagged, 0 tag failures — confirmed both in the UI and by querying NationBuilder directly afterward. All test tags deleted afterward (test signup back to its original three tags, same pattern as every other round).

**A real, useful discovery from this pass — not a bug in this feature:** the app's person-search (`/search-by-name`, used for auto-matching a name to a signup) queries the **Databricks-replicated** `universal.prod.signups` table, not live NationBuilder. `suruszoo` isn't synced into that table (same reason it's absent from the nation directory — see the "Environment / credentials setup" work earlier), so auto-lookup found no match for a person who is very much real and reachable via the live NationBuilder API. Worked around it in the walkthrough by filling the Signup ID field manually — exactly what an FD would do when auto-match fails — which is itself part of the existing, already-shipped UI. Worth knowing if further testing against `suruszoo` needs name-based matching to work: it won't, for architectural reasons unrelated to anything built in this conversation.

---

## 4. Live verification against `suruszoo`

Not a repo file change, but directly informs the code above and is worth recording for anyone reviewing it later. Using the already-working NationBuilder token broker and a real (internal, non-client) test nation:

- Confirmed the NationBuilder token broker recognizes `suruszoo` even though that nation isn't in the app's own Databricks-backed nation directory.
- Identified a safe test subject — a real signup already tagged with an existing `import_20260704` tag, not a Surus team member — rather than testing against a colleague's own record.
- Created a disposable test tag (`zz-test-tag-delete-me`), confirmed a second `find_or_create_tag` call with different capitalization returned the same tag id (no duplicate created), applied the tag to the test signup, and verified it via `GET /api/v2/signup_taggings?filter[signup_id]=...`.
- Attempted to apply the same tag to the same signup a second time — this is what surfaced NationBuilder's real duplicate-tagging error shape (a `422` with `meta.code: "taken"` on the `tag_id` field), which `apply_tag_to_signup` now handles explicitly.
- **Cleaned up fully afterward** — deleted the test tag (which cascades the removal from every signup it was on, per the documented behavior) and confirmed the test signup was back to exactly its original three tags.

This same live-test-then-clean-up pattern was repeated for every subsequent round of changes (route wiring, AI column detection, multi-tag splitting) — each round's test tags were deleted and the test signup verified back to its original three tags (`1486`, `1519`, `729`) afterward, every time.

---

## 5. Production browser walkthrough — bug found and fixed (not tagging-related)

Once the tagging code was live on Railway, testing moved to a real browser session against `affordablenaperville` (a real nation, but one being deleted the same day — used freely as a de facto sandbox). This surfaced two separate issues, neither caused by the tagging feature:

**Bad `author_nb_id` in session data.** A manual-entry test failed with a confusing NationBuilder error (`included..attributes.id should be type integer_id`). Root-caused by reproducing the exact request body directly against the API: the nation's saved `author_nb_id` was literally the string `"Winin2030!"` — a sign-in key from a teammate that had been typed into the "Your NationBuilder ID" field by mistake during Setup, rather than an actual numeric ID. NationBuilder correctly rejected it, just via an unhelpfully generic message. **Tagging itself succeeded independently in the same test** (`1 tags applied, 0 tag failures`), confirming the independence design in `plans.md §2` held up under a real, unplanned failure — not just the deliberate ones tested against `suruszoo`.

Fixed the immediate data problem by writing a corrected `nation_setup` audit-log entry (the mechanism `get_user_nations` already reads from) with a real signup ID (`4`, Kartik Kulkarni's real record in that nation) in place of the bad value. One diagnostic note for future debugging sessions: `log_action`'s background write uses a daemon thread — calling it from a short-lived one-off script that exits immediately (rather than the long-running Flask process) can silently drop the write, since the process exits before the thread's network call completes. The fix landed only once the verifying script stayed alive long enough (`time.sleep`) for the write to finish.

**The actual bug: no escape hatch in Setup.** Separately, and more importantly: once a user has *any* saved nation, [templates/setup.html](../templates/setup.html) permanently locked them into a "pick from your saved nations" screen with no way to add a different nation or fix a wrong ID — signing out and back in doesn't help either, since `auth_callback` and Setup both rebuild the nation list from the same audit-log data, not session state. This is how the bad ID above got stuck in place. **Fixed:**

- Both the "returning user" nation-picker and the "search for a nation" flow are now always rendered in the page (previously it was a hard either/or via server-side `{% if %}`), toggled client-side instead of being a dead end.
- A new **"+ Use a different nation"** link on the nation-picker screen opens the search flow fresh.
- A new **"Edit ID"** button next to each saved nation skips straight to the ID field, pre-filled with the current (possibly wrong) value, ready to correct — no need to re-search the nation from scratch.
- A **"← Back to my nations"** link appears in the search/edit flow, but only for users who actually have saved nations to go back to.

Verified via `app.test_client()`: a returning user's render shows the picker visible / search flow hidden / Back link present; a brand-new user's render shows the opposite, with no Back link (nothing to go back to). Script braces/parens balanced, same as the `combined.html` check.

---

## Current git state

**Update:** everything through the Phase 1 tagging work (`README.md`, `app.py`, `templates/combined.html`) was committed and pushed to `main` directly by you via VS Code (`d152fcb "tags field"`), and confirmed live on Railway. The Setup escape-hatch fix in §5 above is newer than that commit and not yet pushed:

| Status | Paths |
| --- | --- |
| Modified, uncommitted | `templates/setup.html` (§5 fix), `docs/changes.md` (this file) |
| Committed & pushed (`d152fcb`) | `README.md`, `app.py`, `templates/combined.html` |
| Untracked (new) | `docs/` minus this file's already-committed state — `walkthrough.md`, `goal.md`, `plans.md`, `nationbuilder-guide.md`, `databricks-auth-guide.md`, and the raw pasted source docs `databricks.md`/`nationbuilder.md` |
| Not tracked, not in git (by design) | `.env` — contains live secrets, correctly gitignored |
| Deleted | `token.txt` |

Per the phased rollout in [plans.md §8](plans.md#8-phased-rollout), **Phase 1 (and what were originally Phase 2/3) are now done**: helper functions, route wiring, AI tags-column detection, multi-tag splitting, and the full UI are all implemented and verified. **Remaining, not yet done:** tag autocomplete against existing nation tags (a recommended but non-blocking addition, `plans.md §4.6`), an end-to-end test through the actual running app in a browser (everything so far has been verified via `app.test_client()` and direct API calls, not a real browser session), and a decision on the unrelated `contact_method`/`contact_status` empty-string bug found during testing (currently flagged and intentionally left unfixed).
