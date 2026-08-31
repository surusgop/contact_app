# Code Walkthrough

A developer's map of this repo: what every file does, what every function does, how data flows, and where the sharp edges are. For install and usage instructions see [README.md](README.md).

---

## Table of contents

- [Architecture at a glance](#architecture-at-a-glance)
- [External dependencies](#external-dependencies)
- [The row dict — the app's core data structure](#the-row-dict--the-apps-core-data-structure)
- [app.py](#apppy)
  - [Module setup](#module-setup-lines-169)
  - [Vocabularies](#vocabularies-lines-7383)
  - [The name-matching engine](#the-name-matching-engine-lines-85639)
  - [NationBuilder auth](#nationbuilder-auth-lines-660667)
  - [The tagging engine](#the-tagging-engine-lines-670756)
  - [Databricks helpers](#databricks-helpers-lines-759947)
  - [Date helpers](#date-helpers-lines-806830)
  - [Search routes](#search-routes)
  - [Setup and auth routes](#setup-and-auth-routes)
  - [File parsing](#file-parsing-lines-11891457)
  - [AI mapping and normalization](#ai-mapping-and-normalization-lines-14591598)
  - [Import routes](#import-routes)
- [templates/combined.html](#templatescombinedhtml)
- [templates/setup.html](#templatessetuphtml)
- [templates/login.html](#templatesloginhtml)
- [templates/index.html (legacy)](#templatesindexhtml-legacy)
- [templates/bulk.html (legacy)](#templatesbulkhtml-legacy)
- [nationbuilder_contacts.py](#nationbuilder_contactspy)
- [Route reference](#route-reference)
- [Gotchas and known quirks](#gotchas-and-known-quirks)
- [Where to add things](#where-to-add-things)

---

## Architecture at a glance

A single-file Flask app with server-rendered Jinja templates and vanilla JavaScript. No build step, no database of its own, no JS framework, no CSS files (styles are inline `<style>` blocks per template).

```
Browser (combined.html)
  │
  │  files / pasted text / manual form
  ▼
Flask (app.py)
  ├── OpenRouter ──────────► extract rows from files, images, prose; classify method/status; detect a tags column
  ├── Databricks SQL ──────► nation directory, person lookups, audit log, contact log (universal.contacts.contact_app_logs)
  ├── server.surusenterprises.com ──► exchange Databricks secret for a NationBuilder token
  ├── <nation>.nationbuilder.com/api/v2/contacts ──► create contacts (one POST per row)
  └── <nation>.nationbuilder.com/api/v2/signup_tags, /signup_taggings ──► find-or-create + apply tags (independent of the contacts POST)
```

The browser holds all staged state in memory (`allRows` and friends). Nothing is persisted between page loads — a refresh loses the queue. The server is stateless apart from the Flask session cookie.

---

## External dependencies

| Service | Used for | Where |
| --- | --- | --- |
| Google OAuth (authlib) | Sign-in, domain restriction | `login`, `auth_callback` |
| Databricks SQL warehouse | Nation directory, `signups` lookups, audit log | `get_db()`, every `execute_statement` call |
| Databricks secrets | `api / surus_server_nb_secret` | `get_nb_token` |
| `server.surusenterprises.com` | Mints per-nation NationBuilder access tokens | `get_nb_token` |
| NationBuilder API v2 | Creating contacts | `bulk_import`, `import_contact` |
| NationBuilder API v2 | Creating/applying tags | `find_or_create_tag`, `apply_tag_to_signup` |
| OpenRouter | `gpt-4o` (vision), `gpt-4o-mini` (text) | `parse_image_with_ai`, `ai_map_and_clean`, `bulk_paste`, `infer_contact_type` |

Databricks tables:

- `universal.nb.source_nation_table` — columns `group` (display name), `slug`, `state`
- `universal.prod.signups` — `nb_id`, `first_name`, `last_name`, `full_name`, `suffix`, `email`, `nation`, plus backticked address columns (`mailing_address.address1`, `registered_address.city`, `home_address.zip`, …). Queries `COALESCE` mailing → registered → home.
- `universal.logging.contact_app_logs` — the audit log, created by `ensure_log_table()` at import time. `action` values in use: `login`, `nation_setup`, `single_import`, `bulk_import`.
- `universal.contacts.contact_app_logs` — a **separate**, append-only structured record of every successfully-created NationBuilder contact (not just an audit-log JSON blob). Created manually (schema added via `ALTER TABLE`), written by `log_contact_to_databricks()`. Columns: `event_time` (`TIMESTAMP`), `nation`, `logged_in_as`, `signup_id`, `author_id`, `contact_method`, `contact_status`, `contact_date`, `content`, `tag` (all `STRING`). Only written for rows where the NationBuilder contact POST actually succeeded, and only from `/bulk/import` — the legacy `/import` route isn't wired to it.

---

## The row dict — the app's core data structure

Every input path (file, paste, manual, blank row) produces the same flat dict, which flows browser → `/bulk/import` → NationBuilder. Understanding this shape explains most of the code.

```python
{
  # → sent to NationBuilder as a "contact" (POST /api/v2/contacts)
  "signup_id":      "12345",           # relationship: the person contacted
  "author_id":      "678",             # relationship: who logged it
  "contact_method": "phone_call",      # attribute, must be in CONTACT_METHODS
  "contact_status": "answered",        # attribute, must be in CONTACT_STATUSES
  "content":        "Great chat…",     # attribute (notes)

  # → folded into `content`, NOT a NationBuilder "contact" field
  "contact_date":   "2026-06-17",

  # → sent to NationBuilder as tag(s) (signup_tags / signup_taggings) - a
  # completely separate API call sequence from the contact above, applied
  # to the signup independently of whether the contact POST succeeds
  "tag":            "Canvass Kickoff 6/17, VIP",   # comma/semicolon-splittable, see split_tag_names

  # → client/logging only, never sent as attributes
  "_first_name":    "John",
  "_last_name":     "Smith",
  "_full_name":     "John Smith",
}
```

Three rules worth internalizing:

1. **Underscore-prefixed keys never reach NationBuilder.** They exist so the browser can look up an `nb_id` and so the audit log can record a human-readable name. `bulk_import` filters attributes down to `contact_method`, `contact_status`, `content`.
2. **`contact_date` is synthetic.** NationBuilder's contact object has no date-of-contact field, so the app prepends `Date Contacted: June 17th, 2026` to the note text. This is why dates get spelled out (`_spell_date`) rather than kept as ISO.
3. **`tag` doesn't go through the `attributes` dict at all.** It's read directly off the row and routed to a completely different NationBuilder resource (`signup_tags`/`signup_taggings`, not `contacts`) — see [The tagging engine](#the-tagging-engine-lines-670756). A row can succeed at tagging and fail its contact log, or vice versa; they're independent outcomes, not one all-or-nothing unit.

---

## app.py

2,051 lines, ~600 of which are the nickname table. Below, "lines" refer to the current file.

### Module setup (lines 1–69)

- **HEIC support** — `register_heif_opener()` inside a `try/except ImportError`, so Pillow can open iPhone photos when `pillow-heif` is installed and degrade quietly when it isn't.
- **`_send_no_verify` (24–28)** — monkeypatches `requests.Session.send` to force `verify=False` on *every* outbound request, plus `urllib3.disable_warnings`. This exists because a corporate proxy intercepts HTTPS. It's global and unconditional; be aware that nothing in this process verifies TLS.
- **Flask app (32–34)** — `secret_key` from `FLASK_SECRET_KEY` (dev fallback `"dev-secret-change-me"`), and a 90-day `PERMANENT_SESSION_LIFETIME`.
- **`login_manager` (36–37)** — `login_view = "login"`, so `@login_required` redirects to `/login`.
- **`oauth` / `google` (39–46)** — authlib client using Google's OIDC discovery document, scope `openid email profile`.
- **`class User(UserMixin)` (48–53)** — plain object holding `id` (the email), `email`, `name`, `picture`.
- **`_users` (55)** — an in-memory `dict` acting as the user store. **Lost on restart** (see [Gotchas](#gotchas-and-known-quirks)).
- **`load_user(user_id)` (57–59)** — flask-login's loader; a `_users.get`.
- **`get_db()` (61–69)** — lazily constructs and caches one `WorkspaceClient` from `DATABRICKS_HOST` / `DATABRICKS_TOKEN`. Every Databricks call goes through this.

### Vocabularies (lines 73–83)

`CONTACT_METHODS` (18 values) and `CONTACT_STATUSES` (10 values) are NationBuilder's accepted enum values. They're the single source of truth: passed into Jinja templates for dropdowns, embedded in every AI prompt, and used as the final validation gate before a row is sent. **Add a value here and it propagates everywhere.**

### The name-matching engine (lines 85–639)

The hardest problem in the app: a canvasser writes "Bill Harrison" but NationBuilder has "William Harrison".

- **`_NICKNAME_GROUPS` (85–602)** — ~500 tuples of interchangeable names, loosely grouped by section comments (Male / Female / Gender-neutral / additional batches). Groups may overlap, and overlaps are intentionally merged rather than deduplicated.
- **`NICKNAME_MAP` (604–612)** — flattens the tuples into `{name: [all variants]}` at import time. When a name appears in several groups, the lists are concatenated and deduplicated, so `alex` resolves to the union of every `alex` group.
- **`POSITIONAL_NICKNAMES` (615–627)** — names that encode a generational suffix instead of a given name: `trey`/`tres`/`trip`/`tripp` → `iii`, `junior` → `jr`, `deuce` → `ii`, `quad` → `iv`. Used by `search_by_name`'s first strategy.
- **`NAME_SUFFIXES` (629–637)** — suffixes and post-nominals to strip before matching: `jr`, `iii`, `2nd`, `esq`, `phd`, `md`, `the third`, …
- **`strip_suffix(name) -> (clean_name, suffix_or_None)` (640–647)** — trims a trailing one-word suffix, or a trailing two-word suffix like "the third".
- **`get_name_variants(name) -> list` (650–657)** — returns the variant list for a name, falling back to `[name]` when unknown. The second loop (scanning every value list) is redundant now that `NICKNAME_MAP` is flat, but harmless.

Adding nicknames means appending a tuple to `_NICKNAME_GROUPS`; the map rebuilds itself on next boot.

### NationBuilder auth (lines 660–667)

**`get_nb_token(nation_slug) -> str`** — reads the Databricks secret `api / surus_server_nb_secret`, then `GET`s `server.surusenterprises.com/auth/api_token/<slug>` with an `x-api-key` header and returns `access_token`.

Called once per import request, never cached. Two network round-trips (Databricks + broker) sit in front of every import. A short-lived cache is the obvious optimization if imports feel slow to start.

### The tagging engine (lines 670–756)

Four functions, all plain (not routes), all reused by both `/import` and `/bulk/import`:

- **`split_tag_names(raw) -> list` (672–692)** — splits a tag string on comma **and** semicolon, trims whitespace, drops empties, dedupes case-insensitively while keeping first-seen casing/order. Handles the case where a spreadsheet cell or tag field holds more than one tag (e.g. `"Volunteer, VIP"`). Called centrally wherever a route reads a `tag`/`list_tag` value — the individual tag-application helpers below only ever see one tag name at a time.
- **`find_or_create_tag(nation_slug, token, tag_name) -> str` (694–716)** — `GET /api/v2/signup_tags?filter[name]=<name>`; if a match comes back, returns its `id`. Otherwise `POST /api/v2/signup_tags` and returns the new `id`. Relies on NationBuilder's default (no-clause) filter being a case-insensitive exact match, matching NationBuilder's own case-insensitive tag-name uniqueness rule — so this is a correct existence check, not an approximation.
- **`resolve_tags_for_batch(nation_slug, token, tag_names) -> dict` (718–730)** — calls `find_or_create_tag` once per **distinct** name in the input (case-insensitive key), returning `{name.lower(): tag_id}`. This is what keeps a 200-row import sharing one tag down to a single `find_or_create_tag` call instead of 200, and avoids a race where two rows both try to create the same brand-new tag at once.
- **`apply_tag_to_signup(nation_slug, token, signup_id, tag_id) -> bool` (732–756)** — `POST /api/v2/signup_taggings` with plain `attributes` (`signup_id`, `tag_id`) — notably **not** a `relationships` block, unlike every other create/sidepost call in this codebase. Returns `True` on a fresh apply. If NationBuilder returns a `422` with `meta.code == "taken"` (the signup already has this tag), returns `False` instead of raising — a confirmed no-op, not a failure. Any other error still raises `requests.HTTPError` normally.

Tags attach to **signups**, not to **contacts** — so tag application is a completely separate API call sequence from the `/api/v2/contacts` POST that logs an interaction, and the two succeed or fail independently. See [the row dict](#the-row-dict--the-apps-core-data-structure) and the Import routes section below for how the two get wired together per-row.

### Databricks helpers (lines 759–947)

- **`load_all_nations()` (759–777)** — `SELECT group, slug, state FROM universal.nb.source_nation_table`, returned as a list of dicts. Swallows all errors and returns `[]`.
- **`ALL_NATIONS` (778)** — module-level, so the query runs **once at import time**. Nation search is then a pure in-memory filter. Requires a restart to see new nations.
- **`ensure_log_table()` (781–805)** — `CREATE TABLE IF NOT EXISTS universal.logging.contact_app_logs (...)`. Called at line 903, i.e. at import time. Non-fatal on failure.
- **`log_action(action, user_email, user_name, nation_slug, details, success, error_message)` (831–860)** — fire-and-forget audit write. The inner `_write()` runs on a `daemon=True` thread so the request never waits on it; `details` is JSON-serialized. Never raises into the caller. Actions in use: `login`, `nation_setup`, `single_import`, `bulk_import`.
- **`log_contact_to_databricks(nation_slug, user_email, row, attributes, relationships)` (863–901)** — same fire-and-forget daemon-thread pattern as `log_action`, but writes to the separate, structured `universal.contacts.contact_app_logs` table instead of the JSON-blob audit log. Called once per row from `bulk_import`, immediately after a successful contact creation — never for failed rows. Pulls `signup_id`/`author_id` out of the `relationships` dict it's passed (not the raw row), and `contact_method`/`content` out of `attributes` — i.e. it logs the *final*, post-normalization values actually sent to NationBuilder, not the pre-normalization row values. ⚠️ **Gotcha:** because the actual write happens on a daemon thread, calling this (or `log_action`) from a short-lived one-off script that exits immediately can silently drop the write — the process dies before the thread's network call finishes. Fine in the real Flask/gunicorn process, which stays alive; a footgun only for ad-hoc debugging scripts (see [Gotchas](#gotchas-and-known-quirks) #21).
- **`get_user_nations(email) -> [{slug, name, author_nb_id}]` (906–947)** — reads the log table back for this user's `nation_setup` events, newest first, deduplicating by slug and unpacking `nation_name` / `author_nb_id` out of the JSON `details`. **The audit log is also the preferences store** — there is no separate settings table. This function is what makes the Setup page show your previously-used nations.

### Date helpers (lines 806–830)

- **`_ordinal_date(dt)`** — formats a `datetime` as `June 17th, 2026`, with correct `st/nd/rd/th` and the 11–13 exception.
- **`_DATE_FMTS`** — nine `strptime` patterns covering ISO, US, European, and spelled-out forms.
- **`_spell_date(date_str)`** — zero-pads loose ISO input (`2026-6-17` → `2026-06-17`), tries each format, and returns the ordinal form. **Returns the input unchanged if nothing parses**, so a garbled date degrades to literal text in the note rather than failing the row.

### Search routes

**`GET /search-nation?term=` → `search_nation` (949–960)**
Case-insensitive substring match over `ALL_NATIONS` on `group` or `slug`, capped at 20. No Databricks round-trip — this is why nation search feels instant.

**`GET /get-author-id?nation_slug=` → `get_author_id` (962–994)**
Looks up `current_user.email` in `universal.prod.signups` for that nation and returns `{success, nb_id, name}`. Powers the Setup page's automatic ID fill. Returns `nb_id: None` rather than an error when there's no match.

**`GET /search-by-name?first=&last=&nation_slug=` → `search_by_name` (1018–1098)**
The main person-matching endpoint, and the most interesting logic in the file. A nested `run_query` helper handles execute + status check + row-dict conversion; a shared `select` string carries the `COALESCE`d address columns.

Three strategies, tried in order, returning on the first hit:

1. **Positional nickname** — if the first name is in `POSITIONAL_NICKNAMES`, search by last name + `suffix LIKE '%iii%'`. ("Trip Harrison" → the Harrison whose suffix is III.)
2. **Nickname variants** — expand the first name via `get_name_variants` into an `OR` of `LOWER(first_name) = LOWER(:vN)` conditions, AND'd with the last name.
3. **Last-name-only fallback** — returns everyone with that last name and sets `"fallback": true` in the response, which the UI surfaces as "No exact match for 'X' — showing all Ys".

All parameters are bound via `StatementParameterListItem`, never string-interpolated. Limit 20.

**`GET /search-volunteer?q=&nation_slug=` → `search_volunteer` (1100–1137)**
Prefix autocomplete (`LIKE 'q%'` on first or last name), limit 12. Backs the "log on behalf of" box, which is why it fires from the very first character typed.

**`GET /search-signup?name=&nation_slug=` → `search_signup` (1139–1187)**
`full_name ILIKE 'name%'` prefix search, limit 20. Used by the Find modal when only one word has been typed (no last name to split on).

### Setup and auth routes

**`GET /login` (1878–1883)** — redirects to Google, with `redirect_uri = APP_URL + "/auth/callback"`.

**`GET /auth/callback` (1884–1911)** — exchanges the code, pulls `userinfo`, and **rejects any email not ending in `@surusenterprises.com`** by re-rendering `login.html` with an error. On success: builds a `User`, stores it in `_users`, `login_user(remember=True)`, `session.permanent = True`, logs the login, then calls `get_user_nations`. Exactly one known nation → seed the session and go to `/`; zero or several → go to `/setup`.

**`GET /logout` (1912–1917)** — `logout_user()` then redirect to `/login`.

**`GET|POST /setup` (996–1017)** — `POST` writes `default_nation_slug`, `default_nation_name`, and `author_nb_id` into the session and emits a `nation_setup` log entry (which is what makes the nation remembered next time). `GET` renders `setup.html` with `get_user_nations()` plus current session values. Reachable not just at login but at any time via the "+ Use a different nation" / "Edit ID" links now built into `setup.html` — see [templates/setup.html](#templatessetuphtml).

**`GET /` → `index` (1919–1930)** — redirects to `/setup` when the session has no nation; otherwise renders `combined.html` with the vocabularies and session values.

**`GET /bulk` (1642–1646)** — a permanent redirect to `/`, kept so old bookmarks still work.

### File parsing (lines 1189–1457)

**`parse_image_with_ai(raw, _filename) -> DataFrame` (1189–1245)**
Opens the bytes with Pillow, converts to RGB, downscales so the longest side is ≤1600px, re-encodes as JPEG q85 (keeping the payload near 1.5 MB), then base64-encodes it into an OpenRouter `gpt-4o` vision call. The prompt asks for a JSON array with keys `name, first_name, last_name, date, phone, email, address, notes, id, contact_method, contact_status`, embeds the valid enum lists, tells the model to infer method/status from tone, and insists dates stay verbatim. Strips markdown fences before `json.loads`. Falls back to a one-row "No structured data found in image" frame. If Pillow fails, the original bytes are sent as-is. **Note:** this prompt doesn't ask for a tag — a photo of a sign-in sheet won't produce a per-row `tag`, only whatever the "tag this entire list" field supplies client-side.

**`parse_upload(file) -> DataFrame` (1247–1457)**
One long extension dispatch. Every branch returns a DataFrame, so callers never special-case a format:

| Branch | Behavior |
| --- | --- |
| `.csv` `.txt` | Six pandas configs in order (comma/python, comma/skip-bad, tab, pipe, semicolon, comma/skip). Accepts the first that yields >1 column; last resort `on_bad_lines="skip"`. |
| `.xlsx` `.xls` | `pd.read_excel` |
| `.docx` | First table (row 0 as headers); falls back to non-empty paragraphs as a `text` column |
| `.json` | List → DataFrame; dict → first list-valued key; else single-row |
| `.pdf` | pdfplumber: all `extract_tables()` across pages, else `extract_text()` lines |
| `.ods` | `pd.read_excel(engine="odf")` |
| `.pptx` | Tables from every slide, plus text-frame contents as `text` rows |
| `.rtf` | `striprtf`, latin-1 decode |
| `.eml` | stdlib `email`; sender, subject, and plain-text body lines |
| `.msg` | `extract_msg` via a `NamedTemporaryFile` (the library needs a real path), cleaned up in a `finally` |
| `.numbers` | `numbers-parser`, first sheet / first table, also via a temp file |
| `.zip` | Recurses into each member with a tiny `_F` shim class exposing `.filename`/`.read()`; skips `__MACOSX`, dotfiles, and directories; `pd.concat`s the results and swallows per-entry failures |
| `.svg` | ElementTree text nodes; falls back to `parse_image_with_ai` |
| `.doc` `.ppt` | Best-effort: latin-1 decode, regex out printable runs of ≥5 chars, first 300 |
| Images | `parse_image_with_ai` — includes `heic`, `heif`, `avif`, and raw formats |
| anything else | `read_csv`, then plain text lines |

The `_F` shim in the ZIP branch is the trick that makes recursion work: `parse_upload` only needs `.filename` and `.read()`, so it doesn't care that the "file" isn't a Werkzeug upload.

### AI mapping and normalization (lines 1459–1598)

**`_STATUS_ALIASES` and `_METHOD_ALIASES`** (module level, near `CONTACT_METHODS`/`CONTACT_STATUSES`)
Hand-built maps from how humans actually write things to NationBuilder's enums: `"voicemail"` → `left_message`, `"wrong number"` → `bad_info`, `"canvass"` → `door_knock`, `"zoom"` → `video_call`, `"sms"` → `text`. Extend these rather than loosening validation.

**`_apply_mapping_locally(column_mapping, all_rows) -> list` (1459–1511)**
Deterministic, offline application of a column mapping. Per target field:

- `signup_id` / `author_id` — strip to digits only; drop if nothing remains
- `contact_method` / `contact_status` — normalize case, hyphens, and spaces; look up in the alias map; **anything unresolved becomes `"other"`** so a row never fails validation on a typo
- `contact_date` — zero-pad loose ISO, try nine formats, emit `YYYY-MM-DD`, keep the raw string if unparseable
- `_full_name` — split on the first whitespace into `_first_name` / `_last_name`
- **`_tag`** — pass through as-is to `row["tag"]` (no splitting here — that happens later, centrally, via `split_tag_names` when a route actually reads the value)
- everything else — pass through as a stripped string

Empty values are skipped entirely, so rows stay sparse. **This function, not the AI, decides the final values** — the model only chooses which source column maps to which field.

**`ai_map_and_clean(columns, all_rows) -> dict` (1513–1598)**
Two AI calls plus local normalization:

1. `gpt-4o-mini` receives the column names and 6 sample rows and returns `{"column_mapping": {...}, "notes": "..."}`. The prompt explicitly warns against nulling out name columns and names the `_full_name` / `_first_name` / `_last_name` targets — and, the same way, warns against nulling out an obvious tags column, naming `_tag` as the target and calling out that it should match regardless of capitalization or exact wording (`tag`, `TAG`, `Tags`, `Label`, `Group`, …). Verified live: `tag`, `Tag`, `Tags`, `TAG`, and `Label` headers all correctly map to `_tag`.
2. `_apply_mapping_locally` turns the mapping into cleaned rows.
3. Rows that have `content` but are missing method or status are batched into **one** follow-up call that returns a JSON array in the same order. Results are only applied when the value is in the valid list and the field is still empty. The whole step is wrapped in `try/except: pass` — inference failure is non-critical.

Returns the mapping dict with `cleaned_rows` attached.

**`POST /infer-contact-type` → `infer_contact_type` (1600–1640)**
Classifies a single free-text note into `{contact_method, contact_status}` via `gpt-4o-mini`, blanking anything not in the valid lists. Only the legacy `index.html` calls this (as you type notes, it fills the dropdowns and shows an "AI" badge) — it's live and reusable if you want the same behavior in `combined.html`.

### Import routes

**`POST /bulk/upload` → `bulk_upload` (1648–1670)**
Multipart, one file per request (the browser fires several in parallel). `parse_upload` → `dropna(how="all")` → **`head(500)`** → stringify → `ai_map_and_clean`. Responds with `{success, columns, mapping, preview (first 10), total_rows, all_rows}`. The 500-row cap is the hard per-file limit. Rows may now carry a `tag` key, sourced from a detected spreadsheet column.

**`POST /bulk/paste` → `bulk_paste` (1672–1734)**
Sends the pasted prose to `gpt-4o-mini` at `temperature: 0.1` asking for `{"rows": [{_full_name, contact_method, contact_status, contact_date, content}]}`. Then, server-side: splits `_full_name` into first/last (keeping the full name too), and `setdefault`s every expected key so the browser always gets a uniform row shape. Returns `{success, all_rows}`. No tag extraction from the prose itself — paste-derived tags come from the client-side "tag this entire list" field instead.

**`POST /bulk/import` → `bulk_import` (1736–1877)**
The one route that writes to NationBuilder. Body: `{nation_slug, rows, imported_by}`.

1. `get_nb_token(nation_slug)` — a failure here returns `Auth failed: …` and aborts the whole batch.
2. Builds an import stamp: `--- Bulk import by: <imported_by> | <Month D, YYYY at H:MM:SS AM UTC> ---`.
3. **Before the per-row loop:** collects every distinct tag name across the whole batch (from each row's `tag` and `list_tag` keys, each split via `split_tag_names`) and resolves them all to tag IDs **once** via `resolve_tags_for_batch` — see [The tagging engine](#the-tagging-engine-lines-670756).
4. Per row:
   - **Contact log** (unchanged by tagging): attributes filtered down to `contact_method`, `contact_status`, `content`; method/status **re-normalized** through the alias maps (the user may have hand-edited the preview table), unresolvable → `"other"`; `content` assembled as `Date Contacted: <spelled date>` ∥ user notes ∥ import stamp; `signup_id`/`author_id` become JSON:API `relationships`; `POST` to `.../api/v2/contacts`.
   - On success: increments a counter, appends `{signup_id, name}` to `contacts_logged`, and calls `log_contact_to_databricks(...)` to append a row to `universal.contacts.contact_app_logs` (fire-and-forget, only for this success path — see [Databricks helpers](#databricks-helpers-lines-759947)).
   - On failure: captures the parsed JSON detail and the attributes sent (minus `content`) under `errors[]`, keyed by 1-based row number.
   - **Tag application** (independent of the contact-log outcome above, runs regardless of whether it succeeded or failed): for each tag name that row has (deduped, looked up in the batch-resolved map), calls `apply_tag_to_signup`. `True` → `tags_results["applied"]++`; `False` (already tagged) → `tags_results["already_tagged"]++`; exception → `tags_results["failed"]++` plus an entry in `tags_results["errors"]`.
5. One `bulk_import` log entry records totals, `imported_by`, every contact logged, and now `tags_applied` (a list of `{tag, signup_id, name}`).
6. Always returns HTTP 200 with `{success: true, results: {success, failed, errors, tags: {applied, already_tagged, failed, errors}}}` — **`success: true` means "the batch ran", not "every row landed"**. Check `results.failed` and `results.tags.failed` separately — they're independent outcomes.

Rows are POSTed **sequentially**. That's the reason for gunicorn's `--timeout 120` and why very large imports are better split up. Tag application adds one more sequential HTTP call per tag per row on top of the existing per-row contact POST.

**`POST /import` → `import_contact` (1932–2051)**
The single-contact endpoint, form-encoded rather than JSON. Richer than the bulk path: it supports `broadcaster_id`, `path_id`, and `path_step_id` relationships (`path_step` uses `type: "path_steps"`) and a `pc_in_cents` integer attribute (political capital), rejecting non-integers with a 400. Same content assembly (`Date Contacted:` prefix), same token flow, logs `single_import` on both success and failure. Currently only reachable from the legacy `index.html`, but fully functional — the natural base if you ever want a one-off entry page again.

Also handles tagging, same independence principle as `bulk_import`: reads a `tag` form field, splits it via `split_tag_names`, and applies each resulting tag after attempting the contact POST — regardless of whether that POST succeeded. Response includes `tag_results` (a **list**, since one `tag` field can hold several tags): `[{"success": bool, "tag": str, "already_tagged": bool}, ...]` or an error shape per entry. The `single_import` audit log entry gained a `"tags": tag_results` field. **Not wired to `log_contact_to_databricks`** — that's `bulk_import`-only, since this route isn't reachable from the live UI anyway.

---

## templates/combined.html

1,406 lines: the entire main UI. Styles in one `<style>` block, markup, then ~1,000 lines of vanilla JS. No dependencies beyond Google Fonts (Oswald for headings, Inter for body).

### Styling

CSS custom properties on `:root` (lines 12–24) define a light theme: `--primary: #dc2626` (red), `--charcoal`, `--text`, `--muted`, `--border`, `--surface`. Reusable classes: `.s-card`, `.s-card-gold`, `.s-label`, `.form-control`, `.btn-red`, `.btn-red-lg`, `.btn-gold`, `.btn-ghost`, `.btn-outline`, `.drop-zone`, `.lookup-dropdown`, `.find-btn`, `.del-btn`, `.two-col`, `.form-grid`.

Two breakpoints: `640px` (stack `.two-col`, hide user name and brand suffix, shrink padding) and `420px` (single-column `.form-grid`, hide "Change Nation").

### Markup structure

| Section | Purpose |
| --- | --- |
| `.app-nav` | Brand, avatar, name, nation badge, Change Nation, Sign Out |
| Hidden inputs (259–261) | `default_author_id`, `imported_by`, `default_contact_date` — how Jinja values reach the JS |
| Header row | Title plus the **Import All** button and staged count |
| On Behalf Of card | `#behalf-input` + `#behalf-dropdown`, or `#behalf-chosen` once picked |
| `.two-col` | Upload Files card (`#drop-zone`, `#file-list`, `#upload-status`, `#file-list-tag`) and Paste Notes card (`#paste-text`, `#paste-list-tag`) side by side |
| Manual Entry card | `#manual-forms-container` plus "+ Add Another Contact" — each card includes a Tag input |
| `#preview-section` | The staged-contacts `<table>` (`#preview-body`), now with a Tag column |
| `#results-section` | Import counts and failed-row list, plus a parallel Tagging stats block |
| `#lookup-overlay` | Full-screen Find Person modal |

### Client state

```js
const NATION_SLUG, CONTACT_METHODS, CONTACT_STATUSES   // injected from Jinja
const NB_FIELDS = ['signup_id','contact_method','contact_status','contact_date','content','tag']

let allRows   = []    // everything staged in the preview table
let fileRows  = []    // subset that came from files (for per-section import)
let pasteRows = []    // subset that came from pasted text
let manualForms = []  // [{id, data}] — manual cards NOT yet in allRows
let onBehalfOf = null // {name, nb_id}
let selectedFiles = []
let activeLookupDropdown, _lookupCallback, _lookupTimer, _behalfTimer, _lastErrors
```

Each row's `tag` is a single string — possibly comma-joined if it represents more than one tag (e.g. a spreadsheet's own per-row tag plus the shared "tag this entire list" value). The server's `split_tag_names` does the actual splitting on import; the client only ever needs to join strings together, never parse them apart.

The important subtlety: `fileRows` and `pasteRows` hold **the same object references** as `allRows`, which is how `importSection` can remove a section's rows with `allRows.filter(r => !fileRows.includes(r))`. Manual forms are different — they live outside `allRows` until import, and are counted separately.

### JS functions

**Files**
- `fileIcon(name)` — emoji per extension.
- `setFiles(files)` — merges into `selectedFiles` deduplicating on `name + size`, so repeated picks accumulate instead of replacing; re-renders the list and updates the button label.
- `setFileStatus(i, cls, text)` — per-file badge (Waiting / Processing / ✓ N rows / ✗ error).
- `startUpload()` — `Promise.all` over `uploadOne`, so all files process concurrently; flattens `all_rows`, calls `mergeListTag(newRows, ...)` to fold in the `#file-list-tag` value, appends to `fileRows`, calls `addToQueue`, and reports full or partial success.
- `uploadOne(file, i)` — one `POST /bulk/upload`; returns the JSON or `null` on failure (which is how `startUpload` detects partial failure).

**Paste**
- `processPaste()` — `POST /bulk/paste`, calls `mergeListTag(json.all_rows, ...)` to fold in the `#paste-list-tag` value, appends to `pasteRows`, queues the rows, clears the textarea, shows the per-section import bar.

**Tagging**
- `mergeListTag(rows, listTagValue)` — folds a "tag this entire list" value into every row's own `tag` field, comma-joined with whatever tag that row already carries (e.g. from an AI-detected spreadsheet column), skipping the join if it's already an exact case-insensitive match. Called from `startUpload()` and `processPaste()` right before the rows are queued. This is the client-side half of "apply both" — the server-side half is `split_tag_names` treating a comma-joined string as multiple tags.

**Queue**
- `addToQueue(rows)` — push into `allRows`, render a table row for each, reveal the preview section, update counts.
- `showPreviewSection()`, `updateQueueCount()` — the count is `allRows.length + countUnflushedManualRows()`, written into the badge, the footer, and the Import All label, and used to enable/disable the button.
- `countUnflushedManualRows()` — a manual card counts as real if it has any of `signup_id`, `_first_name`, `_last_name`, `_full_name`, or `content`.

**Manual entry**
- `addManualForm()` — builds a card imperatively via `document.createElement` (no template strings), wiring each control to mutate its own `formData` object: name input splits into first/last/full, NB ID input, **Find** button (opens the overlay and fills the ID, plus the name if it was blank), method/status `<select>`s built from the injected vocabularies, a date input, a notes textarea, a **Tag** input (`formData.tag`), and a Remove button that splices the form out of `manualForms`. Called once on `DOMContentLoaded`, so there's always one blank card.

**Preview table**
- `spellDate(dateStr)` — the browser-side twin of `_spell_date`, handling ISO and `M/D/YYYY`. Display only.
- `autoLookup(rowData, inputEl, statusEl, firstName, lastName, findBtn)` — hits `/search-by-name`; no-ops when the row already has an ID. One match → fill the ID, show a green ✓, **hide the Find button**. Several → `showDisambiguator`. Zero → clear the status quietly.
- `showDisambiguator(...)` — renders an "N matches found — pick one" link with a panel of names + addresses; a one-shot document click handler closes it.
- `renderPreviewRow(body, rowData, fields)` — the heart of the table. Per column:
  - `signup_id` — ID input, a **Find** button, and (when the row has no name at all) a "Type name…" input that re-runs `autoLookup` on an 800 ms debounce. Auto-lookup fires via `setTimeout(…, 0)` when a name is present and the ID is empty.
  - `content` — auto-growing textarea that highlights on focus and commits on blur.
  - `contact_method` / `contact_status` — `<select>` from the injected lists, pre-selecting the current value.
  - `contact_date` — text input showing `spellDate(...)`; whatever the user types is stored raw and re-parsed server-side.
  - `tag` and everything else not specifically handled — plain text input, committed on `change`. No special-case code was needed to add the Tag column — it falls straight into this generic branch.
  - Plus a trash button that splices the row out of `allRows` and hides the section when it empties.
- `addBlankRow()` — pushes an empty row (now including `tag: ''`) and scrolls to it.

**Lookup UI**
- `closeActiveLookup()` / `openLookup(btn, idInput, rowData, first, last)` — the in-cell dropdown. Renders name, address, and ID per match, and surfaces the `fallback` flag as a yellow "showing all Ys" note. Selecting sets the ID and turns the button into a ✓.
- `openLookupOverlay(first, last, callback)` / `closeLookupOverlay(e)` / `runLookupOverlaySearch(nameStr)` — the modal used by manual cards. It picks its endpoint by shape: two-plus words → `/search-by-name` (nickname-aware), one word → `/search-signup` (prefix match). Selection invokes the stored `_lookupCallback` once.

**On Behalf Of**
- `onBehalfInput(val)` — 200 ms debounce, then `searchBehalf`.
- `searchBehalf(query)` — `/search-volunteer` prefix search, rendered with name, ID, and address.
- `selectBehalf(r)` / `clearBehalf()` — toggle between the search box and the chosen-person chip.
- `getEffectiveAuthor()` — the one place authorship is decided: with someone selected it returns `{authorId: <their nb_id>, importedBy: "<You> on behalf of <Them>"}`; otherwise your own ID and name. So the NationBuilder author becomes the volunteer while the audit trail still names you.

**Import**
- `importSection(section)` — imports just `'file'`, `'paste'`, or `'manual'`. Fills in `author_id` and a fallback `contact_date` per row, POSTs to `/bulk/import`, then clears that section: file/paste rows are filtered out of `allRows` by identity; manual forms are wiped and a fresh blank card added.
- `runImport()` — imports `allRows` plus every non-empty manual form. **Does not clear `allRows` afterwards** (see [Gotchas](#gotchas-and-known-quirks)).
- `friendlyError(e)` — turns NationBuilder's JSON:API errors and bare HTTP codes into readable sentences ("Missing or invalid person ID…", "Person not found — the NationBuilder ID may be wrong or not in this nation"), appending the attributes that were sent.
- `showResults(results, total)` — imported/failed stat tiles plus a scrollable failed-row list; then, if `results.tags` is present and non-empty, a second "Tagging" block (Tags Applied / Already Tagged if non-zero / Tag Failures, plus a Failed Tags list) — kept visually separate since tag and contact outcomes are independent. Scrolls itself into view.
- `resetAll()` — clears all state, all DOM (including `#file-list-tag` / `#paste-list-tag`), re-adds one blank manual card, scrolls to top.

---

## templates/setup.html

461 lines. Nation + author-ID selection. **This used to be two mutually-exclusive Jinja-branched modes with no way to move between them at runtime — that was a real bug, since fixed.** Both blocks are now always rendered in the page; which one is visible on load is still decided server-side (`{% set has_nations = user_nations|length > 0 %}` controls each block's initial `display:` style), but the user can toggle between them client-side at any time afterward.

**`#returning-block`** — one `.nation-row` per known nation, each holding two buttons:
- The main `.nation-btn` (`data-slug`/`data-name`/`data-nbid`) — `selectNation(btn)` marks it selected, `POST`s all three values to `/setup`, and redirects to `/`. One click, no lookup — unchanged from before.
- A smaller **"Edit ID"** button (`.nation-edit-btn`, same three `data-*` attributes) — calls `editNation(event, btn)`, which switches to the search/edit flow (`showNewNationFlow()`) with the nation already selected and the ID field pre-filled with the *current* (possibly wrong) value, ready to correct — skipping the search step entirely.

Below the list, **"+ Use a different nation"** calls `addNewNation()` — `resetNewNationFields()` then `showNewNationFlow()` — for adding a nation from scratch.

**`#new-nation-flow`** — the search + NB-ID flow, functionally unchanged from before:
- `nationSearch` input, 300 ms debounced → `runNationSearch(term)` → `/search-nation`, rendering `group` with `state — slug` underneath.
- Picking a nation calls `lookupAuthorId(slug)` → `/get-author-id`. Found → prefill the ID, green confirmation, enable Continue. Not found → reveal the field with a warning and focus it.
- `nbIdInput` listener keeps Continue disabled until both slug and ID are present.
- `saveAndContinue()` → `POST /setup` → `/`.
- The section label/copy above the fields (`#new-nation-label` / `#new-nation-copy`) is rewritten in place by `editNation()` ("Edit NationBuilder ID" / "Update your NationBuilder ID for X (slug)") vs. its defaults, so the same markup serves both "add a nation" and "fix this nation's ID" without a second copy of the form.
- If the user has saved nations, a **"← Back to my nations"** link (`showReturningBlock()`) appears below Continue — hidden entirely for a brand-new user with nothing to go back to (`{% if has_nations %}`).
- The old "pre-fill from an in-progress session" logic (`savedSlug`/`savedName`/`savedNbId`) still exists but is now wrapped in `{% if not has_nations %}` — it's only relevant for a genuinely new user landing on this flow by default; `addNewNation()`/`editNation()` populate the fields explicitly for a returning user, so without that guard a returning user would see stale data flash in before their own action overwrites it.

**Why this needed fixing:** `get_user_nations()` (see [Databricks helpers](#databricks-helpers-lines-759947)) rebuilds the saved-nations list purely from past `nation_setup` audit-log entries — never from live session state. Combined with the old hard Jinja either/or, a user with even one saved nation had **no way to add a different one, and no way to fix a bad `author_nb_id`** short of someone hand-editing the log table directly. That's exactly what happened in practice: a sign-in key typed into the ID field by mistake during Setup left a nation permanently stuck with a non-numeric "ID," and neither signing out nor back in helped, since both `auth_callback` and this page rebuild from the same log data every time.

Verified via `app.test_client()`: a returning user's render shows the picker visible / search flow hidden / Back link present; a brand-new user's render shows the opposite, with no Back link.

---

## templates/login.html

135 lines, dark-themed (`#0d1823` with gold/red accents — the app's original palette). Static apart from `{% if error %}`, which renders the domain-restriction or userinfo-failure message from `auth_callback`. The Sign In button is a plain link to `/login` with an inline Google SVG. No JS.

There's a vestigial empty `{% if error %}{% endif %}` inside the `<style>` block (lines 67–68) — harmless leftover.

---

## templates/index.html (legacy)

522 lines. The **original single-contact form**, no longer rendered by any route — `/` serves `combined.html`. It still works if you re-point a route at it, and the `/import` endpoint it posts to is live.

What it does that `combined.html` doesn't:

- One `<form>` submitted as `FormData` to `/import`, with client-side validation for nation, signup ID, and author ID
- Nation search and person search (`/search-signup`) as separate dropdowns, with the Signup ID field `readonly` and filled only by selection
- **`inferFromNote(text)`** — as you type notes (900 ms debounce, min 8 chars), calls `/infer-contact-type` and fills empty method/status dropdowns, showing a small gold **AI** badge that disappears if you override the value manually

That last feature is the interesting one: it exists only here, and `/infer-contact-type` is otherwise unused. Porting it into `combined.html`'s manual cards would be straightforward.

---

## templates/bulk.html (legacy)

937 lines. The **original three-step bulk flow** (Upload → Preview → Results) with `showStep(id)` navigation, superseded by the single-page `combined.html`. `/bulk` now just redirects to `/`.

Most of its JS was carried over to `combined.html` in near-identical form (`fileIcon`, `setFiles`, `startUpload`, `uploadOne`, `processPaste`, `renderPreviewRow`, `openLookup`, `friendlyError`, `spellDate`). Two functions did **not** make the jump:

- **`mergeUploadResults(results)`** — combined multiple files' rows and column sets into one preview
- **`fixFailedRows()`** — after a partial import, re-staged only the failed rows so you could correct and retry them

`fixFailedRows` is worth knowing about: today, a partial failure in `combined.html` leaves you re-entering the failed rows by hand. If someone asks for retry-failed-rows, this is the prior art (and `_lastErrors` already exists in `combined.html`, populated but unused).

Keep this file as reference or delete it, but don't assume edits here affect the running app.

---

## nationbuilder_contacts.py

34 lines, standalone — not imported by `app.py`. Applies the same `verify=False` monkeypatch, builds a `WorkspaceClient` from ambient config, reads the `api / surus_server_nb_secret` secret, exchanges it for a token against a hardcoded `nation_slug = "barringtongop"`, then `GET`s the first 100 contacts and prints the raw response.

It's the minimal reproduction of the token-brokering flow — useful for verifying Databricks and NationBuilder connectivity outside the web app. Edit the slug in place; there's no CLI argument.

---

## Route reference

| Method | Path | Handler | Auth | Purpose |
| --- | --- | --- | --- | --- |
| GET | `/login` | `login` | — | Redirect to Google |
| GET | `/auth/callback` | `auth_callback` | — | OAuth callback, domain gate, session seeding |
| GET | `/logout` | `logout` | ✓ | Sign out |
| GET | `/` | `index` | ✓ | Main page (or redirect to `/setup`) |
| GET/POST | `/setup` | `setup` | ✓ | Choose nation + author ID |
| GET | `/search-nation?term=` | `search_nation` | ✓ | In-memory nation directory search |
| GET | `/get-author-id?nation_slug=` | `get_author_id` | ✓ | Find the signed-in user's NB ID |
| GET | `/search-by-name?first=&last=&nation_slug=` | `search_by_name` | ✓ | Nickname/suffix-aware person match |
| GET | `/search-volunteer?q=&nation_slug=` | `search_volunteer` | ✓ | Prefix autocomplete (on-behalf-of) |
| GET | `/search-signup?name=&nation_slug=` | `search_signup` | ✓ | `full_name` prefix search |
| POST | `/infer-contact-type` | `infer_contact_type` | ✓ | Classify one note → method/status |
| GET | `/bulk` | `bulk` | ✓ | Legacy redirect to `/` |
| POST | `/bulk/upload` | `bulk_upload` | ✓ | Parse one uploaded file → cleaned rows |
| POST | `/bulk/paste` | `bulk_paste` | ✓ | Extract rows from prose |
| POST | `/bulk/import` | `bulk_import` | ✓ | Create N contacts in NationBuilder, apply tags, log successes to Databricks |
| POST | `/import` | `import_contact` | ✓ | Create one contact (form-encoded, richer fields), apply tags |

---

## Gotchas and known quirks

Real behaviors that will bite you, roughly in order of how likely they are to matter.

1. **`runImport()` doesn't clear the queue.** `importSection` removes its rows from `allRows`; `runImport` doesn't. Clicking **Import All** twice sends everything twice. The UI relies on the user pressing **Clear & Start Over**. If you touch import code, this is the first thing to fix or deliberately preserve.

2. **`/bulk/import` returns HTTP 200 with `success: true` even when every row failed.** The flag means "the batch executed". Always read `results.failed` and `results.errors`.

3. **`_users` is an in-memory dict.** After a restart, `load_user` returns `None` for a valid remember-me cookie, so `@login_required` bounces the user to `/login`. It's usually invisible (Google re-auths silently), but it also means **multiple gunicorn workers don't share the store** — each worker re-authenticates independently. Any real session backing (Redis, signed user data in the cookie) would fix both.

4. **TLS verification is globally disabled** by the `requests.Session.send` monkeypatch at the top of both Python files. Intentional (corporate proxy), but it applies to every outbound call including OAuth and the NationBuilder API.

5. **`ALL_NATIONS` loads once at import time.** New nations upstream require a restart. Same for `ensure_log_table()`.

6. **`contact_date` is not a NationBuilder field.** It's prepended to `content` as `Date Contacted: June 17th, 2026`. Don't add it to `attributes` expecting it to stick — and note the date therefore isn't queryable in NationBuilder.

7. **Unrecognized method/status silently becomes `"other"`**, in both `_apply_mapping_locally` and `bulk_import`'s re-normalization pass. Rows never fail validation on a typo, but a mis-typed method also won't announce itself. Add aliases to `_METHOD_ALIASES` / `_STATUS_ALIASES` when you see a pattern.

8. **Imports are a sequential loop.** N rows = N sequential HTTPS POSTs, all inside one request. Hence `--timeout 120`. Batching or threading is the fix if this becomes a problem.

9. **`importSection` reads the implicit global `event`** (`const btn = event.currentTarget`) rather than taking an event parameter. Works in Chrome; would break under strict module semantics or a non-Chromium engine. `runImport` does it correctly via `getElementById`.

10. **`--gold` is referenced but never defined** in `combined.html`'s `:root` (leftover from the dark theme) — used at line 354 and line 872. Those elements inherit their color instead. `.btn-gold` is a real class and works fine.

11. **500-row cap per uploaded file** (`df.head(500)` in `bulk_upload`), silently applied — a 900-row spreadsheet imports 500 rows with no warning.

12. **AI calls have no retry.** `parse_image_with_ai` (90 s timeout), `ai_map_and_clean` and `bulk_paste` (30 s), `infer_contact_type` (15 s). A transient OpenRouter failure fails the request; the batch method/status inference is the one exception, wrapped in a bare `except: pass`.

13. **`get_nb_token` is uncached.** Two round-trips (Databricks secret + broker) before every import.

14. **The audit log is also the preferences store.** `get_user_nations` reconstructs saved nations from `nation_setup` log rows. Changing the log schema or the `details` JSON keys (`nation_name`, `author_nb_id`) breaks the Setup page.

15. **`get_name_variants` has a redundant fallback loop** over every value in `NICKNAME_MAP`. Dead code since the map is flat, but it runs on misses.

16. **`_lastErrors` in `combined.html` is populated and never read** — a leftover hook for the retry-failed-rows feature that lives in `bulk.html`'s `fixFailedRows`.

17. **Underscore-prefixed row keys are client-side only.** `_first_name` / `_last_name` / `_full_name` are used for lookup and log labels; only `contact_method`, `contact_status`, and `content` become NationBuilder attributes.

18. **Python 3.10+ required** — `strip_suffix` is annotated `tuple[str, str | None]`.

19. **`bulk_import` can send `contact_method`/`contact_status` to NationBuilder as an empty string instead of omitting the key**, which NationBuilder rejects outright rather than treating as "no value." Confirmed with two different real error messages depending on route: `contact_status value must be one of [...] or null` on `/bulk/import`, and `Contact method can't be blank` on `/import`. Triggers on any row where the key is present but blank — the normal shape for paste-derived rows (`bulk_paste` always sets both via `setdefault(..., "")`) and manually-added blank rows. Known, reproducible, and deliberately left unfixed as of this writing — a real bug, not a design choice.

20. **`log_action` and `log_contact_to_databricks` write on a `daemon=True` thread and return immediately** — correct and necessary in the real Flask/gunicorn process (never blocks a request on a Databricks round-trip), but a footgun for ad-hoc debugging: a short one-off script that calls either function and then exits immediately can silently drop the write, since the process dies before the thread's network call finishes. If you're testing either function outside the running app, keep the process alive (e.g. `time.sleep(8)`) long enough for the write to actually land before checking the result.

21. **`suruszoo` (and presumably other shared internal sandbox nations) can have its data reset or changed by other people at any time.** A signup that worked as a test subject in one session may 404 in the next — don't assume a previously-used test signup or tag still exists; re-verify before reusing one across sessions.

22. **NationBuilder's `signup_taggings` create endpoint is the one resource in this whole API that uses plain `attributes` instead of `relationships`** to link two resources (`{"attributes": {"signup_id": ..., "tag_id": ...}}`, not a `relationships` block). Every other create/sidepost call in this codebase uses `relationships` — don't copy that pattern here by habit.

---

## Where to add things

**A new contact method or status** — append to `CONTACT_METHODS` / `CONTACT_STATUSES` in [app.py](app.py). Dropdowns, AI prompts, and validation all read from there. Add colloquial spellings to the alias maps too.

**Support for another file type** — add a branch to `parse_upload` returning a DataFrame; everything downstream is format-agnostic. Add the dependency to `requirements.txt` and the extension to the drop-zone hint in `combined.html`.

**More nickname coverage** — append a tuple to `_NICKNAME_GROUPS`. Overlapping groups merge automatically.

**A new NationBuilder field on bulk import** — extend the attribute filter in `bulk_import` and add the column to `NB_FIELDS` plus a `renderPreviewRow` branch. Look at `import_contact` first: it already handles `broadcaster_id`, `path_id`, `path_step_id`, and `pc_in_cents`.

**A new audit event** — call `log_action("your_event", current_user.email, current_user.name, nation_slug, {...})`. It's non-blocking and can't raise into your handler.

**A new Databricks-backed lookup** — copy the shape of `search_volunteer`: `get_db().statement_execution.execute_statement(...)` with `StatementParameterListItem` bindings, check `StatementState.SUCCEEDED`, zip `manifest.schema.columns` against `result.data_array`. Never interpolate user input into SQL — every existing query binds.

**A new Databricks *write*** — copy the shape of `log_contact_to_databricks` (fire-and-forget on a daemon thread, wrapped in `try/except` so a Databricks hiccup can never break the request) rather than `log_action`'s JSON-blob-in-one-table pattern, if what you're adding deserves its own structured, queryable table.

**Another tag-bearing NationBuilder resource, or a new tag-related route** — reuse `find_or_create_tag` / `resolve_tags_for_batch` / `apply_tag_to_signup` / `split_tag_names` as-is; they're generic over nation/token and don't assume they're only called from `bulk_import`. Remember tags are independent of the contact-log outcome — don't nest tag application inside the contact try/except.

**Tag autocomplete** (a known gap — see [goal.md](goal.md)) — copy the shape of `/search-volunteer`: a new `GET /search-tag?q=&nation_slug=` doing a prefix search over `GET /api/v2/signup_tags`.

**UI changes** — `combined.html` and `setup.html` are both live and routed. `index.html` and `bulk.html` are unrouted; editing them changes nothing that runs.
