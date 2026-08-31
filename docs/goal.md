# Goal: Tagging in the Contact App

**Status: shipped.** Everything described below is built, live in production, and verified — see [changes.md](changes.md) for the full build/verification log and [plans.md](plans.md) for the technical detail.

## The problem

Field Directors (FDs) used to tag event attendees by hand after every event — going through a paper sign-in sheet and manually applying a NationBuilder tag to each person, one at a time. It was a pure time sink that ate into time the FD could spend on higher-value work with their clients.

Training volunteers to do this instead hadn't worked: they either did it wrong, or doing it well pulled them out of socializing with attendees — which is the actual point of having them at the event. Having the FD do it live via an NB event page has the same problem, plus it makes every event depend on the FD being physically present, which doesn't scale. And since the org is moving away from NationBuilder longer-term, the fix needed to be a capability of *our own tool*, not a NationBuilder-specific workaround — so it survives a backend platform change.

## The fix

The Contact App already let an FD import a spreadsheet or manually enter people to log NationBuilder contacts, with an existing pipeline that matches each row to a real NationBuilder signup. Tagging was built as an extension of that same pipeline, so tagging comes along for free wherever a match already happens:

- **Manual entry:** a short-answer "Tag" field per person, applied to just that individual.
- **Spreadsheet import:** a short-answer "Tag this entire list" field that applies to everyone in the batch — *and*, at the same time, the AI column-mapping step recognizes a tags column already present in the spreadsheet (any casing — `Tag`, `TAG`, `Tags`, `Label`, …) and applies whatever's in it per row. Both apply together when both are present.
- **Paste Notes:** the same "tag this entire list" field, for consistency with file upload.
- **Multi-tag support:** a field or cell holding more than one tag (comma- or semicolon-separated, e.g. `"Volunteer, VIP"`) is split into separate tags rather than becoming one garbled tag name.
- **Behind the scenes:** for any tag name that comes in, the app checks whether that tag already exists in the nation (case-insensitively — NationBuilder's own uniqueness rule). If not, it creates it. Either way, it then applies the tag to the matched person(s) — a "check tag exists" + "create tag" (Signup Tags) followed by "apply tag to person" (Signup Taggings), using the same NationBuilder API surface documented in [nationbuilder-guide.md](nationbuilder-guide.md).

The FD (or eventually, a volunteer at check-in) uploads or types the sign-in sheet once. Everyone on it who's already a known signup gets tagged automatically, no manual per-person tagging required.

## What success looks like — confirmed

- **Importing a sign-in sheet with a shared tag takes the same one import click it always did** — tags land as a side effect, no extra manual step. Confirmed live: a 3-person test spreadsheet with its own per-row tags column plus a shared list-wide tag produced exactly 3 imported contacts and 6 tags applied (3 people × 2 tags each), 0 failures.
- **A brand-new tag name creates that tag automatically; an existing tag name (even different capitalization) reuses it rather than creating a near-duplicate.** Confirmed via repeated live tests against real NationBuilder nations.
- **Re-tagging someone who already has a tag is a harmless no-op, not a failure.** Confirmed live — shows as "Already Tagged" in the results panel, using NationBuilder's real duplicate-tagging response (`422`, `meta.code: "taken"`).
- **A spreadsheet with its own tags column gets those tags applied per-row automatically**, with no retyping. Confirmed via both scripted tests and real browser use.
- **Tagging never blocks or slows down the case where no tag is provided** — it's additive and fully optional per row/batch, verified by the fact that ordinary (untagged) contact imports behave exactly as before.
- **Tagging succeeds independently of the contact-log entry.** This was confirmed by accident in the best possible way: a real production test hit an unrelated bug that made the contact-log side fail, and the tag still applied correctly in the same request — proving the independence design holds up under a genuine, unplanned failure, not just the deliberate test cases.

## Explicitly out of scope

- **Creating brand-new NationBuilder signups** for sign-in sheet entries that don't match anyone already in the system. This feature only tags people it can already match to an existing signup. Handling first-time attendees who aren't in NationBuilder yet is a real gap for the sign-in-sheet use case, but it's a separate, larger feature (a "create person" path doesn't exist anywhere in the app) and was never part of this work.
- Tag management UI (renaming/merging/deleting tags from within the Contact App) — creation and application only.
- Tag autocomplete against existing nation tags — a recommended follow-on (mitigates typo'd tags silently creating near-duplicates, since NationBuilder has no fuzzy matching on tag names), deliberately deferred and not yet built.
- Anything NationBuilder-specific that wouldn't carry over to a future platform migration, beyond the unavoidable fact that today's implementation calls the NationBuilder API.

See [plans.md](plans.md) for the full technical detail and [changes.md](changes.md) for the chronological build/test log.
