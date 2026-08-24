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
  - [The name-matching engine](#the-name-matching-engine-lines-85657)
  - [NationBuilder auth](#nationbuilder-auth-lines-660667)
  - [Databricks helpers](#databricks-helpers-lines-670817)
  - [Date helpers](#date-helpers-lines-717739)
  - [Search routes](#search-routes)
  - [Setup and auth routes](#setup-and-auth-routes)
  - [File parsing](#file-parsing-lines-10601278)
  - [AI mapping and normalization](#ai-mapping-and-normalization-lines-12811461)
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
  ├── OpenRouter ──────────► extract rows from files, images, prose; classify method/status
  ├── Databricks SQL ──────► nation directory, person lookups, audit log
  ├── server.surusenterprises.com ──► exchange Databricks secret for a NationBuilder token
  └── <nation>.nationbuilder.com/api/v2/contacts ──► create contacts (one POST per row)
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
| OpenRouter | `gpt-4o` (vision), `gpt-4o-mini` (text) | `parse_image_with_ai`, `ai_map_and_clean`, `bulk_paste`, `infer_contact_type` |

Databricks tables:

- `universal.nb.source_nation_table` — columns `group` (display name), `slug`, `state`
- `universal.prod.signups` — `nb_id`, `first_name`, `last_name`, `full_name`, `suffix`, `email`, `nation`, plus backticked address columns (`mailing_address.address1`, `registered_address.city`, `home_address.zip`, …). Queries `COALESCE` mailing → registered → home.
- `universal.logging.contact_app_logs` — created by `ensure_log_table()` at import time

---

## The row dict — the app's core data structure

Every input path (file, paste, manual, blank row) produces the same flat dict, which flows browser → `/bulk/import` → NationBuilder. Understanding this shape explains most of the code.

```python
{
  # → sent to NationBuilder
  "signup_id":      "12345",           # relationship: the person contacted
  "author_id":      "678",             # relationship: who logged it
  "contact_method": "phone_call",      # attribute, must be in CONTACT_METHODS
  "contact_status": "answered",        # attribute, must be in CONTACT_STATUSES
  "content":        "Great chat…",     # attribute (notes)

  # → folded into `content`, NOT a NationBuilder field
  "contact_date":   "2026-06-17",

  # → client/logging only, never sent as attributes
  "_first_name":    "John",
  "_last_name":     "Smith",
  "_full_name":     "John Smith",
}
```

Two rules worth internalizing:

1. **Underscore-prefixed keys never reach NationBuilder.** They exist so the browser can look up an `nb_id` and so the audit log can record a human-readable name. `bulk_import` filters attributes down to `contact_method`, `contact_status`, `content`.
2. **`contact_date` is synthetic.** NationBuilder's contact object has no date-of-contact field, so the app prepends `Date Contacted: June 17th, 2026` to the note text. This is why dates get spelled out (`_spell_date`) rather than kept as ISO.

---

## app.py

1,838 lines, ~600 of which are the nickname table. Below, "lines" refer to the current file.

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

### The name-matching engine (lines 85–657)

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

### Databricks helpers (lines 670–817)

- **`load_all_nations()` (670–687)** — `SELECT group, slug, state FROM universal.nb.source_nation_table`, returned as a list of dicts. Swallows all errors and returns `[]`.
- **`ALL_NATIONS` (689)** — module-level, so the query runs **once at import time**. Nation search is then a pure in-memory filter. Requires a restart to see new nations.
- **`ensure_log_table()` (692–714)** — `CREATE TABLE IF NOT EXISTS universal.logging.contact_app_logs (...)`. Called at line 774, i.e. at import time. Non-fatal on failure.
- **`log_action(action, user_email, user_name, nation_slug, details, success, error_message)` (742–771)** — fire-and-forget audit write. The inner `_write()` runs on a `daemon=True` thread so the request never waits on it; `details` is JSON-serialized. Never raises into the caller. Actions in use: `login`, `nation_setup`, `single_import`, `bulk_import`.
- **`get_user_nations(email) -> [{slug, name, author_nb_id}]` (777–817)** — reads the log table back for this user's `nation_setup` events, newest first, deduplicating by slug and unpacking `nation_name` / `author_nb_id` out of the JSON `details`. **The audit log is also the preferences store** — there is no separate settings table. This function is what makes the Setup page show your previously-used nations.

### Date helpers (lines 717–739)

- **`_ordinal_date(dt)`** — formats a `datetime` as `June 17th, 2026`, with correct `st/nd/rd/th` and the 11–13 exception.
- **`_DATE_FMTS`** — nine `strptime` patterns covering ISO, US, European, and spelled-out forms.
- **`_spell_date(date_str)`** — zero-pads loose ISO input (`2026-6-17` → `2026-06-17`), tries each format, and returns the ordinal form. **Returns the input unchanged if nothing parses**, so a garbled date degrades to literal text in the note rather than failing the row.

### Search routes

**`GET /search-nation?term=` → `search_nation` (820–830)**
Case-insensitive substring match over `ALL_NATIONS` on `group` or `slug`, capped at 20. No Databricks round-trip — this is why nation search feels instant.

**`GET /get-author-id?nation_slug=` → `get_author_id` (833–864)**
Looks up `current_user.email` in `universal.prod.signups` for that nation and returns `{success, nb_id, name}`. Powers the Setup page's automatic ID fill. Returns `nb_id: None` rather than an error when there's no match.

**`GET /search-by-name?first=&last=&nation_slug=` → `search_by_name` (889–968)**
The main person-matching endpoint, and the most interesting logic in the file. A nested `run_query` helper handles execute + status check + row-dict conversion; a shared `select` string carries the `COALESCE`d address columns.

Three strategies, tried in order, returning on the first hit:

1. **Positional nickname** — if the first name is in `POSITIONAL_NICKNAMES`, search by last name + `suffix LIKE '%iii%'`. ("Trip Harrison" → the Harrison whose suffix is III.)
2. **Nickname variants** — expand the first name via `get_name_variants` into an `OR` of `LOWER(first_name) = LOWER(:vN)` conditions, AND'd with the last name.
3. **Last-name-only fallback** — returns everyone with that last name and sets `"fallback": true` in the response, which the UI surfaces as "No exact match for 'X' — showing all Ys".

All parameters are bound via `StatementParameterListItem`, never string-interpolated. Limit 20.

**`GET /search-volunteer?q=&nation_slug=` → `search_volunteer` (971–1007)**
Prefix autocomplete (`LIKE 'q%'` on first or last name), limit 12. Backs the "log on behalf of" box, which is why it fires from the very first character typed.

**`GET /search-signup?name=&nation_slug=` → `search_signup` (1010–1054)**
`full_name ILIKE 'name%'` prefix search, limit 20. Used by the Find modal when only one word has been typed (no last name to split on).

### Setup and auth routes

**`GET /login` (1695–1699)** — redirects to Google, with `redirect_uri = APP_URL + "/auth/callback"`.

**`GET /auth/callback` (1701–1727)** — exchanges the code, pulls `userinfo`, and **rejects any email not ending in `@surusenterprises.com`** by re-rendering `login.html` with an error. On success: builds a `User`, stores it in `_users`, `login_user(remember=True)`, `session.permanent = True`, logs the login, then calls `get_user_nations`. Exactly one known nation → seed the session and go to `/`; zero or several → go to `/setup`.

**`GET /logout` (1729–1733)** — `logout_user()` then redirect to `/login`.

**`GET|POST /setup` (867–886)** — `POST` writes `default_nation_slug`, `default_nation_name`, and `author_nb_id` into the session and emits a `nation_setup` log entry (which is what makes the nation remembered next time). `GET` renders `setup.html` with `get_user_nations()` plus current session values.

**`GET /` → `index` (1736–1746)** — redirects to `/setup` when the session has no nation; otherwise renders `combined.html` with the vocabularies and session values.

**`GET /bulk` (1506–1509)** — a permanent redirect to `/`, kept so old bookmarks still work.

### File parsing (lines 1060–1278)

**`parse_image_with_ai(raw, _filename) -> DataFrame` (1060–1115)**
Opens the bytes with Pillow, converts to RGB, downscales so the longest side is ≤1600px, re-encodes as JPEG q85 (keeping the payload near 1.5 MB), then base64-encodes it into an OpenRouter `gpt-4o` vision call. The prompt asks for a JSON array with keys `name, first_name, last_name, date, phone, email, address, notes, id, contact_method, contact_status`, embeds the valid enum lists, tells the model to infer method/status from tone, and insists dates stay verbatim. Strips markdown fences before `json.loads`. Falls back to a one-row "No structured data found in image" frame. If Pillow fails, the original bytes are sent as-is.

**`parse_upload(file) -> DataFrame` (1118–1278)**
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

### AI mapping and normalization (lines 1281–1461)

**`_STATUS_ALIASES` (1281–1304) and `_METHOD_ALIASES` (1305–1328)**
Hand-built maps from how humans actually write things to NationBuilder's enums: `"voicemail"` → `left_message`, `"wrong number"` → `bad_info`, `"canvass"` → `door_knock`, `"zoom"` → `video_call`, `"sms"` → `text`. Extend these rather than loosening validation.

**`_apply_mapping_locally(column_mapping, all_rows) -> list` (1330–1379)**
Deterministic, offline application of a column mapping. Per target field:

- `signup_id` / `author_id` — strip to digits only; drop if nothing remains
- `contact_method` / `contact_status` — normalize case, hyphens, and spaces; look up in the alias map; **anything unresolved becomes `"other"`** so a row never fails validation on a typo
- `contact_date` — zero-pad loose ISO, try nine formats, emit `YYYY-MM-DD`, keep the raw string if unparseable
- `_full_name` — split on the first whitespace into `_first_name` / `_last_name`
- everything else — pass through as a stripped string

Empty values are skipped entirely, so rows stay sparse. **This function, not the AI, decides the final values** — the model only chooses which source column maps to which field.

**`ai_map_and_clean(columns, all_rows) -> dict` (1382–1461)**
Two AI calls plus local normalization:

1. `gpt-4o-mini` receives the column names and 6 sample rows and returns `{"column_mapping": {...}, "notes": "..."}`. The prompt explicitly warns against nulling out name columns and names the `_full_name` / `_first_name` / `_last_name` targets.
2. `_apply_mapping_locally` turns the mapping into cleaned rows.
3. Rows that have `content` but are missing method or status are batched into **one** follow-up call that returns a JSON array in the same order. Results are only applied when the value is in the valid list and the field is still empty. The whole step is wrapped in `try/except: pass` — inference failure is non-critical.

Returns the mapping dict with `cleaned_rows` attached.

**`POST /infer-contact-type` → `infer_contact_type` (1464–1503)**
Classifies a single free-text note into `{contact_method, contact_status}` via `gpt-4o-mini`, blanking anything not in the valid lists. Only the legacy `index.html` calls this (as you type notes, it fills the dropdowns and shows an "AI" badge) — it's live and reusable if you want the same behavior in `combined.html`.

### Import routes

**`POST /bulk/upload` → `bulk_upload` (1512–1533)**
Multipart, one file per request (the browser fires several in parallel). `parse_upload` → `dropna(how="all")` → **`head(500)`** → stringify → `ai_map_and_clean`. Responds with `{success, columns, mapping, preview (first 10), total_rows, all_rows}`. The 500-row cap is the hard per-file limit.

**`POST /bulk/paste` → `bulk_paste` (1536–1597)**
Sends the pasted prose to `gpt-4o-mini` at `temperature: 0.1` asking for `{"rows": [{_full_name, contact_method, contact_status, contact_date, content}]}`. Then, server-side: splits `_full_name` into first/last (keeping the full name too), and `setdefault`s every expected key so the browser always gets a uniform row shape. Returns `{success, all_rows}`.

**`POST /bulk/import` → `bulk_import` (1600–1692)**
The one route that writes to NationBuilder. Body: `{nation_slug, rows, imported_by}`.

1. `get_nb_token(nation_slug)` — a failure here returns `Auth failed: …` and aborts the whole batch.
2. Builds an import stamp: `--- Bulk import by: <imported_by> | <Month D, YYYY at H:MM:SS AM UTC> ---`.
3. Per row:
   - Attributes are filtered down to `contact_method`, `contact_status`, `content`.
   - Method and status are **re-normalized** through the alias maps, because the user may have hand-edited the preview table. Unresolvable → `"other"`.
   - `content` is assembled as `Date Contacted: <spelled date>` ∥ user notes ∥ import stamp, joined by blank lines.
   - `signup_id` and `author_id` become JSON:API `relationships` (`signup` and `author`, both `type: "signups"`).
   - `POST` to `https://<slug>.nationbuilder.com/api/v2/contacts`.
4. Successes increment a counter and append `{signup_id, name}` to `contacts_logged`. `HTTPError`s capture the parsed JSON detail and the attributes that were sent (minus `content`) under `errors[]`, keyed by 1-based row number.
5. One `bulk_import` log entry records totals, `imported_by`, and every contact logged.
6. Always returns HTTP 200 with `{success: true, results: {success, failed, errors}}` — **`success: true` means "the batch ran", not "every row landed"**. Check `results.failed`.

Rows are POSTed **sequentially**. That's the reason for gunicorn's `--timeout 120` and why very large imports are better split up.

**`POST /import` → `import_contact` (1749–1834)**
The single-contact endpoint, form-encoded rather than JSON. Richer than the bulk path: it supports `broadcaster_id`, `path_id`, and `path_step_id` relationships (`path_step` uses `type: "path_steps"`) and a `pc_in_cents` integer attribute (political capital), rejecting non-integers with a 400. Same content assembly (`Date Contacted:` prefix), same token flow, logs `single_import` on both success and failure. Currently only reachable from the legacy `index.html`, but fully functional — the natural base if you ever want a one-off entry page again.

---

## templates/combined.html

1,339 lines: the entire main UI. Styles in one `<style>` block, markup, then ~940 lines of vanilla JS. No dependencies beyond Google Fonts (Oswald for headings, Inter for body).

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
| `.two-col` | Upload Files card (`#drop-zone`, `#file-list`, `#upload-status`) and Paste Notes card (`#paste-text`) side by side |
| Manual Entry card | `#manual-forms-container` plus "+ Add Another Contact" |
| `#preview-section` | The staged-contacts `<table>` (`#preview-body`) |
| `#results-section` | Import counts and failed-row list |
| `#lookup-overlay` | Full-screen Find Person modal |

### Client state (lines 398–414)

```js
const NATION_SLUG, CONTACT_METHODS, CONTACT_STATUSES   // injected from Jinja
const NB_FIELDS = ['signup_id','contact_method','contact_status','contact_date','content']

let allRows   = []    // everything staged in the preview table
let fileRows  = []    // subset that came from files (for per-section import)
let pasteRows = []    // subset that came from pasted text
let manualForms = []  // [{id, data}] — manual cards NOT yet in allRows
let onBehalfOf = null // {name, nb_id}
let selectedFiles = []
let activeLookupDropdown, _lookupCallback, _lookupTimer, _behalfTimer, _lastErrors
```

The important subtlety: `fileRows` and `pasteRows` hold **the same object references** as `allRows`, which is how `importSection` can remove a section's rows with `allRows.filter(r => !fileRows.includes(r))`. Manual forms are different — they live outside `allRows` until import, and are counted separately.

### JS functions

**Files**
- `fileIcon(name)` — emoji per extension.
- `setFiles(files)` — merges into `selectedFiles` deduplicating on `name + size`, so repeated picks accumulate instead of replacing; re-renders the list and updates the button label.
- `setFileStatus(i, cls, text)` — per-file badge (Waiting / Processing / ✓ N rows / ✗ error).
- `startUpload()` — `Promise.all` over `uploadOne`, so all files process concurrently; flattens `all_rows`, appends to `fileRows`, calls `addToQueue`, and reports full or partial success.
- `uploadOne(file, i)` — one `POST /bulk/upload`; returns the JSON or `null` on failure (which is how `startUpload` detects partial failure).

**Paste**
- `processPaste()` — `POST /bulk/paste`, appends to `pasteRows`, queues the rows, clears the textarea, shows the per-section import bar.

**Queue**
- `addToQueue(rows)` — push into `allRows`, render a table row for each, reveal the preview section, update counts.
- `showPreviewSection()`, `updateQueueCount()` — the count is `allRows.length + countUnflushedManualRows()`, written into the badge, the footer, and the Import All label, and used to enable/disable the button.
- `countUnflushedManualRows()` — a manual card counts as real if it has any of `signup_id`, `_first_name`, `_last_name`, `_full_name`, or `content`.

**Manual entry**
- `addManualForm()` — builds a card imperatively via `document.createElement` (no template strings), wiring each control to mutate its own `formData` object: name input splits into first/last/full, NB ID input, **Find** button (opens the overlay and fills the ID, plus the name if it was blank), method/status `<select>`s built from the injected vocabularies, a date input, a notes textarea, and a Remove button that splices the form out of `manualForms`. Called once on `DOMContentLoaded`, so there's always one blank card.

**Preview table**
- `spellDate(dateStr)` — the browser-side twin of `_spell_date`, handling ISO and `M/D/YYYY`. Display only.
- `autoLookup(rowData, inputEl, statusEl, firstName, lastName, findBtn)` — hits `/search-by-name`; no-ops when the row already has an ID. One match → fill the ID, show a green ✓, **hide the Find button**. Several → `showDisambiguator`. Zero → clear the status quietly.
- `showDisambiguator(...)` — renders an "N matches found — pick one" link with a panel of names + addresses; a one-shot document click handler closes it.
- `renderPreviewRow(body, rowData, fields)` — the heart of the table. Per column:
  - `signup_id` — ID input, a **Find** button, and (when the row has no name at all) a "Type name…" input that re-runs `autoLookup` on an 800 ms debounce. Auto-lookup fires via `setTimeout(…, 0)` when a name is present and the ID is empty.
  - `content` — auto-growing textarea that highlights on focus and commits on blur.
  - `contact_method` / `contact_status` — `<select>` from the injected lists, pre-selecting the current value.
  - `contact_date` — text input showing `spellDate(...)`; whatever the user types is stored raw and re-parsed server-side.
  - everything else — plain text input.
  - Plus a trash button that splices the row out of `allRows` and hides the section when it empties.
- `addBlankRow()` — pushes an empty row and scrolls to it.

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
- `showResults(results, total)` — imported/failed stat tiles plus a scrollable failed-row list; scrolls itself into view.
- `resetAll()` — clears all state, all DOM, re-adds one blank manual card, scrolls to top.

---

## templates/setup.html

378 lines. Nation + author-ID selection, and the only template with two genuinely different modes, branched in Jinja on `user_nations`:

**Returning user (`{% if user_nations %}`)** — renders one `.nation-btn` per known nation with `data-slug` / `data-name` / `data-nbid`. `selectNation(btn)` marks it selected, `POST`s all three values to `/setup`, and redirects to `/`. One click, no lookup.

**New user (`{% else %}`)** — the full flow:
- `nationSearch` input, 300 ms debounced → `runNationSearch(term)` → `/search-nation`, rendering `group` with `state — slug` underneath.
- Picking a nation calls `lookupAuthorId(slug)` → `/get-author-id`. Found → prefill the ID, green confirmation, enable Continue. Not found → reveal the field with a warning and focus it.
- `nbIdInput` listener keeps Continue disabled until both slug and ID are present.
- `saveAndContinue()` → `POST /setup` → `/`.
- Session values (`default_nation_slug`, `default_nation_name`, `author_nb_id`) are read into JS consts at the top, so a returning-but-not-yet-logged user sees their previous choice pre-filled.

Note that both branches' scripts are inside one `<script>` with the Jinja conditional around them, so only one branch's JS is ever emitted — don't add shared helpers outside the conditional expecting both to see them.

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
| POST | `/bulk/import` | `bulk_import` | ✓ | Create N contacts in NationBuilder |
| POST | `/import` | `import_contact` | ✓ | Create one contact (form-encoded, richer fields) |

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

---

## Where to add things

**A new contact method or status** — append to `CONTACT_METHODS` / `CONTACT_STATUSES` in [app.py](app.py). Dropdowns, AI prompts, and validation all read from there. Add colloquial spellings to the alias maps too.

**Support for another file type** — add a branch to `parse_upload` returning a DataFrame; everything downstream is format-agnostic. Add the dependency to `requirements.txt` and the extension to the drop-zone hint in `combined.html`.

**More nickname coverage** — append a tuple to `_NICKNAME_GROUPS`. Overlapping groups merge automatically.

**A new NationBuilder field on bulk import** — extend the attribute filter in `bulk_import` (line ~1629) and add the column to `NB_FIELDS` plus a `renderPreviewRow` branch. Look at `import_contact` first: it already handles `broadcaster_id`, `path_id`, `path_step_id`, and `pc_in_cents`.

**A new audit event** — call `log_action("your_event", current_user.email, current_user.name, nation_slug, {...})`. It's non-blocking and can't raise into your handler.

**A new Databricks-backed lookup** — copy the shape of `search_volunteer`: `get_db().statement_execution.execute_statement(...)` with `StatementParameterListItem` bindings, check `StatementState.SUCCEEDED`, zip `manifest.schema.columns` against `result.data_array`. Never interpolate user input into SQL — every existing query binds.

**UI changes** — `combined.html` only. `index.html` and `bulk.html` are unrouted; editing them changes nothing that runs.
