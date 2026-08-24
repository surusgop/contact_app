# Goal: Tagging in the Contact App

## The problem

Field Directors (FDs) currently tag event attendees by hand after every event — going through a paper sign-in sheet and manually applying a NationBuilder tag to each person, one at a time. It's a pure time sink that eats into time the FD could spend on higher-value work with their clients.

Training volunteers to do this instead hasn't worked: they either do it wrong, or doing it well pulls them out of socializing with attendees — which is the actual point of having them at the event. Having the FD do it live via an NB event page has the same problem, plus it makes every event depend on the FD being physically present, which doesn't scale. And since the org is moving away from NationBuilder longer-term, the fix needs to be a capability of *our own tool*, not a NationBuilder-specific workaround — so it survives a backend platform change.

## The fix

The Contact App already lets an FD import a spreadsheet or manually enter people to log NationBuilder contacts, with an existing pipeline that matches each row to a real NationBuilder signup. We're extending that same pipeline so tagging comes along for free wherever a match already happens:

- **Manual entry:** a short-answer "Tag" field per person, applied to just that individual.
- **Spreadsheet import:** a short-answer "Tag" field that applies to everyone in that list — *and*, at the same time, the AI column-mapping step looks for a tags column already present in the spreadsheet and applies whatever's in it per row.
- **Behind the scenes:** for any tag name that comes in, the app checks whether that tag already exists in the nation. If not, it creates it. Either way, it then applies the tag to the matched person(s) — a "check tag exists" + "create tag" (Signup Tags) followed by "apply tag to person" (Signup Taggings), reusing the same NationBuilder API surface already documented in this repo.

The FD (or eventually, a volunteer at check-in) uploads or types the sign-in sheet once. Everyone on it who's already a known signup gets tagged automatically, no manual per-person tagging required.

## What success looks like

- Importing a 50-person sign-in sheet with a "Canvass Kickoff 6/17" tag takes the same one import click it takes today — tags land as a side effect, with no extra manual step.
- A brand-new tag name typed into the field creates that tag in the nation automatically; an existing tag name (even with different capitalization) reuses the existing tag rather than creating a near-duplicate.
- A spreadsheet that already has its own tags column gets those tags applied per-row, without the FD having to retype anything.
- Nothing about today's contact-logging workflow gets slower or more confusing for the case where no tag is provided at all — tagging is additive and fully optional per row/batch.

## Explicitly out of scope for this pass

- **Creating brand-new NationBuilder signups** for sign-in sheet entries that don't match anyone already in the system. Today's tool (and this feature) only tags people it can already match to an existing signup. Handling first-time attendees who aren't in NationBuilder yet is a real gap for the sign-in-sheet use case, but it's a separate, larger feature (a "create person" path doesn't exist anywhere in the app today) and isn't part of this plan.
- Tag management UI (renaming/merging/deleting tags from within the Contact App) — creation and application only.
- Anything NationBuilder-specific that wouldn't carry over to a future platform migration, beyond the unavoidable fact that today's implementation calls the NationBuilder API.

See [plans.md](plans.md) for the concrete, step-by-step implementation plan.
