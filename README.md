# Contact App

A Flask web app for logging NationBuilder **contacts** in bulk. Field staff and volunteers can drop in a spreadsheet, a photo of a handwritten sign-in sheet, or a paragraph of typed notes — the app uses AI to pull out the people and interactions, matches each person to their NationBuilder ID, lets you review everything in an editable table, and then pushes each row to the NationBuilder API.

Everything happens on one page: **upload files**, **paste notes**, and **manual entry** all feed a single staged queue you review before importing.

---

## Table of contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Setup](#setup)
- [Running locally](#running-locally)
- [Deploying](#deploying)
- [Using the app](#using-the-app)
- [Supported file types](#supported-file-types)
- [Contact methods and statuses](#contact-methods-and-statuses)
- [How data is stored](#how-data-is-stored)
- [Troubleshooting](#troubleshooting)
- [Repo layout](#repo-layout)

---

## What it does

1. **Sign in with Google** — restricted to `@surusenterprises.com` accounts.
2. **Pick your nation** — search the nation directory; the app looks up your own NationBuilder ID automatically so it can stamp you as the contact's author.
3. **Feed it contacts** three ways, in any combination:
   - Drag in files (spreadsheets, PDFs, Word docs, photos of handwritten lists…)
   - Paste freeform notes ("Called John Smith on June 17th, great conversation…")
   - Fill in the manual entry form
4. **Review the staged queue** — every row is editable. The app auto-matches names to NationBuilder IDs (handling nicknames like Bill→William and suffixes like "Trip"→III), and shows a picker when there's more than one match.
5. **Import** — each row is POSTed to `https://<nation>.nationbuilder.com/api/v2/contacts`. You get a success/failure count with plain-English error messages for anything that didn't land.

Every login, setup, and import is logged to a Databricks table for auditing.

---

## Requirements

- **Python 3.10+** (the code uses `str | None` union syntax)
- **A Databricks workspace** with:
  - A SQL warehouse (its ID goes in `DATABRICKS_WAREHOUSE_ID`)
  - Read access to `universal.nb.source_nation_table` and `universal.prod.signups`
  - Write access to `universal.logging` (the app creates `contact_app_logs` on first boot)
  - A secret at scope `api`, key `surus_server_nb_secret` — used to mint NationBuilder tokens
- **Google OAuth credentials** (a Web application client in Google Cloud Console)
- **An OpenRouter API key** — powers file/photo/text extraction and field inference
- Network access to `server.surusenterprises.com` (the NationBuilder token broker)

---

## Setup

```bash
git clone <repo-url>
cd contact_app

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```bash
# Flask
FLASK_SECRET_KEY=some-long-random-string
APP_URL=http://localhost:5000        # must match your Google redirect URI host

# Google OAuth
GOOGLE_CLIENT_ID=xxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxxxx

# Databricks
DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
DATABRICKS_TOKEN=dapi...
DATABRICKS_WAREHOUSE_ID=abc123def456

# AI extraction
OPENROUTER_API_KEY=sk-or-...
```

In the Google Cloud Console, add this **Authorized redirect URI**:

```
http://localhost:5000/auth/callback
```

For production, use your real host: `https://your-app.example.com/auth/callback`, and set `APP_URL` to match. The callback path is always `APP_URL + /auth/callback`.

> **Note:** Every env var is optional at import time — the app boots without them. But with no `DATABRICKS_WAREHOUSE_ID` the nation directory and all person lookups come back empty, and with no Google credentials login fails. Set them all.

---

## Running locally

```bash
source .venv/bin/activate
python app.py
```

Then open <http://localhost:5000>. You'll be redirected to Google sign-in.

On boot you should see console output like:

```
Loaded 412 nations
Log table ready
```

If you see `Failed to load nations: …` instead, your Databricks credentials or warehouse ID are wrong.

Flask's debug reloader is on in `python app.py` mode, so code edits restart the server. Note that the nation directory is loaded **once at startup** — restart the app after new nations are added upstream.

---

## Deploying

The repo ships a `Procfile` for any Heroku-style platform:

```
web: gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120 --workers 2 --threads 4
```

To run it that way yourself:

```bash
PORT=8000 gunicorn app:app --bind 0.0.0.0:8000 --timeout 120 --workers 2 --threads 4
```

Deployment checklist:

- Set every variable from the `.env` above as a real environment variable
- Set `APP_URL` to the public HTTPS URL and add `<APP_URL>/auth/callback` to the Google client
- Use a strong, stable `FLASK_SECRET_KEY` — changing it signs everyone out
- Keep `--timeout` generous. Imports POST to NationBuilder one row at a time, so a few hundred rows can take a while.

---

## Using the app

### First sign-in

Sign in with your Surus Google account. Any other domain is rejected with "Access restricted to Surus Enterprises accounts only."

You'll land on **Setup**. Type a town, area, or nation name; pick the match. The app searches `universal.prod.signups` for your email in that nation and pre-fills **Your NationBuilder ID** — this is the ID recorded as the *author* of every contact you log. If it can't find you, type your ID in manually. Click **Continue →**.

Sessions last 90 days, so you normally sign in once. On later logins:

- If you've only ever set up **one** nation, you go straight to the import page.
- If you've set up several, Setup shows them as buttons — one click picks the nation for today.

You can switch nations any time via **Change Nation** in the nav bar.

### Logging on behalf of someone else

At the top of the import page there's a **Logging on behalf of** box. Search a volunteer by name and pick them, and every contact in that session is authored by *them* instead of you. The audit log still records that you did the importing (`"Your Name on behalf of Their Name"`).

Leave it blank to log under your own name. Click the **×** to clear it.

### Three ways to add contacts

All three feed the same **Staged Contacts** table, and you can mix them freely.

**Upload Files** — drag files onto the drop zone or click to browse. Multiple files at once are fine, and picking more files adds to the list rather than replacing it (deduplicated by name + size). Click **Upload & Map with AI**; files process in parallel and each shows its own status. The AI maps your column headers onto NationBuilder fields, so you don't need a specific layout. Up to 500 rows are read per file.

**Paste Notes** — type or paste plain prose and click **Process Text**. The AI splits it into one row per person, and pulls out dates, method, and status where it can:

```
Called John Smith on June 17th, had a great conversation about the event.
Knocked on Jane Doe's door, no answer.
```

**Manual Entry** — fill in name, method, status, date, and notes. **+ Add Another Contact** gives you another blank card. The **Find** button opens a name-search modal to attach the right NationBuilder ID.

### Reviewing the queue

The **Staged Contacts** table is fully editable — click any cell.

- **Signup ID** — the app looks up each name as it's staged. One match fills the ID in automatically (shown as a green ✓ with the matched name). Several matches show a "*N* matches found — pick one" link with names and addresses. No name at all gives you a "Type name…" box that searches as you type.
- **Contact Method / Contact Status** — dropdowns limited to NationBuilder's accepted values.
- **Date Contacted** — displayed spelled out ("June 17th, 2026"). This is **not** a NationBuilder field; it gets prepended to the note as `Date Contacted: June 17th, 2026`.
- **Notes** — free text; becomes the contact's content in NationBuilder.
- The trash icon drops a row; **+ Add Blank Row** adds an empty one.

Name matching handles the messy cases: nicknames (Bill/William, Peggy/Margaret), suffix names ("Trip" or "Trey" searches for last name + III), and titles like Jr./PhD. If nothing matches the first name, it falls back to showing everyone with that last name.

### Importing

- **Import All *N* Contacts** (top right) sends everything staged, including manual entry cards you haven't otherwise touched.
- Each section also has its own button — **Import File Contacts**, **Import Text Contacts**, **Import Manual Contacts** — for sending just that source's rows.

When it finishes you get an **Import Complete** panel with imported/failed counts and a readable reason for each failure. **Clear & Start Over** resets the page.

> Rows are sent one at a time, so a partial failure still means the successful rows landed. After **Import All**, use **Clear & Start Over** before staging new work — the queue is intentionally left intact so you can see what was sent, and re-clicking Import All would send those rows a second time.

---

## Supported file types

| Category | Extensions | How it's parsed |
| --- | --- | --- |
| Delimited text | `.csv` `.txt` | pandas, trying comma / tab / pipe / semicolon in turn |
| Spreadsheets | `.xlsx` `.xls` `.ods` `.numbers` | pandas / openpyxl / odfpy / numbers-parser |
| Documents | `.docx` `.doc` `.rtf` `.pdf` | first table if there is one, otherwise text lines |
| Slides | `.pptx` `.ppt` | tables and text frames |
| Data | `.json` | list of objects, or first list found in an object |
| Email | `.eml` `.msg` | sender, subject, and plain-text body lines |
| Archives | `.zip` | every parseable file inside, concatenated |
| Images | `.jpg` `.jpeg` `.png` `.gif` `.bmp` `.webp` `.tiff` `.heic` `.heif` `.avif` `.raw` `.cr2` `.cr3` `.nef` `.arw` | AI vision — reads handwriting, printed lists, screenshots |
| Vector | `.svg` | embedded text, falling back to AI vision |
| Anything else | — | tried as CSV, then as plain text lines |

Photos of handwritten sign-in sheets and canvassing tally sheets are a first-class input: images are downscaled to 1600px and sent to a vision model that extracts one row per person and infers method and status from the tone of the notes.

---

## Contact methods and statuses

NationBuilder only accepts fixed values. Both lists live in [app.py](app.py) as `CONTACT_METHODS` and `CONTACT_STATUSES`, and drive every dropdown in the UI.

**Methods:** `delivery` `door_knock` `email` `email_blast` `face_to_face` `facebook` `meeting` `phone_call` `robocall` `snail_mail` `text` `text_1to1` `text_blast` `tweet` `video_call` `webinar` `linkedin` `other`

**Statuses:** `answered` `bad_info` `left_message` `meaningful_interaction` `send_information` `not_interested` `no_answer` `refused` `inaccessible` `other`

You don't have to write them exactly. Common phrasings are normalized on the way in — "phone", "call", "canvass", "voicemail", "wrong number", "zoom", "in person", "sms" all map to the right value, and anything unrecognized becomes `other` rather than failing the row.

---

## How data is stored

The app has no database of its own. It reads and writes Databricks tables:

| Table | Used for |
| --- | --- |
| `universal.nb.source_nation_table` | The nation directory behind nation search (loaded once at startup) |
| `universal.prod.signups` | Person lookups — name search, volunteer autocomplete, finding your own NB ID |
| `universal.logging.contact_app_logs` | Audit log, created automatically on boot |

Contacts themselves are never stored locally — they go straight to the NationBuilder API.

The log table doubles as user preferences: your saved nations on the Setup page are reconstructed from past `nation_setup` log entries. Logged actions are `login`, `nation_setup`, `single_import`, and `bulk_import`; `bulk_import` records row counts, who it was imported on behalf of, and the names and IDs of everyone logged. Writes happen on a background thread and never block a request — if logging fails, the import still succeeds.

---

## Troubleshooting

**"Access restricted to Surus Enterprises accounts only."**
You signed in with a non-Surus Google account. Sign out of Google and retry with your work account.

**Nation search finds nothing**
`DATABRICKS_WAREHOUSE_ID` is missing or the warehouse is unreachable. Check the console for `Failed to load nations:`. Also remember the directory is read at startup — restart to pick up newly added nations.

**"Author ID is missing. Please sign out and sign back in."**
Your session has no NationBuilder ID. Go to **Change Nation** and re-run setup, entering your NB ID manually if the lookup can't find you.

**Setup can't find your account**
You aren't in that nation's `signups` under your work email. Enter your NationBuilder ID manually — that's what the field is there for.

**"Auth failed:" when importing**
The NationBuilder token broker rejected the request. Verify the Databricks secret `api/surus_server_nb_secret` is valid and that `server.surusenterprises.com` is reachable.

**Individual rows fail with "Missing or invalid person ID"**
That row has no Signup ID, or an ID that doesn't exist in this nation. Use **Find** to attach the right person and re-import just that row.

**File uploads fail or return junk**
The AI extraction needs `OPENROUTER_API_KEY`. Images and freeform text won't work at all without it. For spreadsheets, check that the first row is a real header row.

**Large imports time out**
Rows POST sequentially. Raise gunicorn's `--timeout`, or split the work into a few smaller imports.

**Everyone got signed out**
`FLASK_SECRET_KEY` changed (or defaulted to `dev-secret-change-me` on a fresh boot). Set it explicitly and keep it stable. Note also that a server restart clears the in-memory user cache — users are bounced through Google again, which is usually invisible if their Google session is live.

---

## Repo layout

```
contact_app/
├── app.py                       # The entire Flask application
├── nationbuilder_contacts.py    # Standalone script: fetch contacts from the NB API
├── requirements.txt
├── Procfile                     # gunicorn entry point
└── templates/
    ├── combined.html            # The main import page (all three input modes + queue)
    ├── setup.html               # Nation + author ID selection
    ├── login.html               # Google sign-in
    ├── index.html               # Legacy: single-contact form (no longer routed)
    └── bulk.html                # Legacy: original 3-step bulk flow (no longer routed)
```

`nationbuilder_contacts.py` is a scratch script, not part of the web app. Edit the `nation_slug` at the top and run `python nationbuilder_contacts.py` to dump the first 100 contacts from a nation — handy for checking that token brokering works.

For a function-by-function tour of the code, see [walkthrough.md](walkthrough.md).
