"""Roles, usage tracking and the /admin page, from surus-auth v2 (surus_auth.platform).

Kept out of app.py so the routes stay readable. Everything is stored in the
shared universal.ops tables under app_id "contact_app". Variables: see
.env.example.

What is logged (kit.log; the app's tracking level on /admin decides what is
kept, and this app is meant to run at "full" with log_ai_calls on):
  login (auth), page.view (view), nation.select (update),
  bulk.upload / bulk.paste (query), sample_csv.download (export),
  contact.create / contact.bulk_import (create) + change_log rows,
  ai.<feature> (ai) + ai_calls rows.
Never contact details: only NationBuilder ids, row counts and the nation slug.
"""

import os
import time

from surus_auth.flask import get_current_user
from surus_auth.platform import from_env, memory_kit

APP_ID = "contact_app"
TITLE = "Contact App"
ENV = os.environ.get("APP_ENV", "dev").strip().lower()

# The admin page is shared by every Surus app; this makes it look like the
# Contact App's own pages (templates/combined.html, setup.html): white nav with
# a red rule, Oswald headings in #dc2626, Inter body text, rounded cards.
ADMIN_THEME = {
    "light": {
        "bg": "#f8fafc",
        "surface": "#ffffff",
        "sunk": "#f1f5f9",
        "ink": "#111827",
        # The templates' grey (#6b7280) is a little light for the admin
        # page's small mono labels; gray-600 keeps the same hue, readable.
        "muted": "#4b5563",
        "line": "#e5e7eb",
        "accent": "#dc2626",
        "accent-soft": "#fef2f2",
        "on-accent": "#ffffff",
        "header-bg": "#ffffff",
        "header-ink": "#111827",
        "radius": "12px",
        "radius-sm": "8px",
    },
    "dark": False,  # the app has no dark mode
    "font": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
    "stylesheets": [
        "https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&family=Inter:wght@400;500;600&display=swap",
    ],
    "css": (
        "header.top { border-bottom: 3px solid #dc2626; box-shadow: 0 1px 4px rgba(0,0,0,.06); }"
        " header.top h1 { font-family: 'Oswald', sans-serif; color: #dc2626; font-weight: 700;"
        " text-transform: uppercase; letter-spacing: .1em; font-size: 18px; }"
    ),
    "back_url": "/",
    "back_label": "Back to the Contact App",
}


def _http_path_from_warehouse_id():
    """surus-auth connects with databricks-sql-connector, which needs the SQL
    warehouse's HTTP path. This app has always configured the same warehouse
    as DATABRICKS_WAREHOUSE_ID (for databricks-sdk), so derive the path from
    it rather than asking for a second variable. An explicit
    DATABRICKS_HTTP_PATH / DATABRICKS_SQL_HTTP_PATH still wins."""
    if os.environ.get("DATABRICKS_HTTP_PATH") or os.environ.get("DATABRICKS_SQL_HTTP_PATH"):
        return
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
    if warehouse_id:
        os.environ["DATABRICKS_HTTP_PATH"] = f"/sql/1.0/warehouses/{warehouse_id}"


def make_kit():
    """The shared tables, or an in-memory copy for a local run with no warehouse.

    Production must have PLATFORM_SCHEMA: from_env() refuses to start without
    it, so a missing variable never silently drops admin changes.
    """
    if ENV != "prod" and not os.environ.get("PLATFORM_SCHEMA"):
        admins = [e.strip() for e in os.environ.get("ADMIN_BOOTSTRAP_EMAILS", "").split(",") if e.strip()]
        print("[contact_app] PLATFORM_SCHEMA is not set: roles and activity are kept in memory only.")
        return memory_kit(APP_ID, title=TITLE, admins=admins, tracking_level="full", log_ai_calls=True)
    _http_path_from_warehouse_id()
    # Non-prod environments create their own tables on first start; prod's
    # already exist in universal.ops (made once by an admin).
    return from_env(APP_ID, title=TITLE, create_tables=ENV != "prod")


kit = make_kit()


def track(action, event_type, **kwargs):
    """kit.log.log for the signed-in user. Returns the event_id (None when the
    event isn't kept or nobody is signed in). Never raises, never blocks:
    the write happens on a background thread."""
    try:
        user = get_current_user()
    except Exception:
        return None
    if not user:
        return None
    return kit.log.log(user["email"], action, event_type=event_type,
                       session_id=user.get("session_id"), **kwargs)


def track_change(event_id, *, target_type, target_id, field, new, old=None):
    """One change_log row (kept at the full level only) for the signed-in user."""
    try:
        user = get_current_user()
        if user:
            kit.log.change(user["email"], target_type=target_type, target_id=str(target_id),
                           field=field, old=old, new=new, event_id=event_id)
    except Exception as exc:  # tracking must never break an import
        print(f"[contact_app] change log skipped: {exc!r}")


def track_ai(feature, model, started, resp=None, error=None):
    """An `ai` activity event plus an ai_calls row for one OpenRouter call
    (both kept only when log_ai_calls is on). Tokens and cost come from the
    response's `usage` block when OpenRouter sends one. No prompt or output
    is stored."""
    try:
        latency_ms = int((time.monotonic() - started) * 1000)
        ok = error is None and resp is not None and resp.ok
        usage = {}
        if resp is not None and resp.ok:
            try:
                usage = (resp.json() or {}).get("usage") or {}
            except Exception:
                usage = {}
        cost = usage.get("cost")
        user = get_current_user() or {}
        email = user.get("email")
        if not email:
            return
        meta = {"model": model}
        if resp is not None:
            meta["status"] = resp.status_code
        if error is not None:
            meta["error"] = type(error).__name__
        event_id = kit.log.log(email, f"ai.{feature}", event_type="ai", session_id=user.get("session_id"),
                               outcome="success" if ok else "error", duration_ms=latency_ms, metadata=meta)
        kit.log.ai_call(email, feature=feature, provider="openrouter", model=model,
                        input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                        latency_ms=latency_ms, cost_usd=cost if isinstance(cost, (int, float)) else None,
                        outcome="success" if ok else "error", event_id=event_id)
    except Exception as exc:
        print(f"[contact_app] ai call log skipped: {exc!r}")
