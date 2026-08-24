# NationBuilder API Guide

An organized reference for the NationBuilder v2 API, reorganized from seven NationBuilder documentation articles pasted into [nationbuilder.md](nationbuilder.md) in this repo. No information dropped — restructured, deduplicated where three articles said the same thing three different ways, and (per request) **cleanly split into two parts**: the general API (Part 1) and the two specific resources this project uses a lot — **signup tags** and **signup taggings** (Part 2). In the source file these were interleaved with general API docs; here they get their own section.

> 📌 **Looking for the tags/taggings functions specifically?** Skip straight to [Part 2](#part-2-signup-tags--signup-taggings-the-functions-this-project-uses).

---

## Table of contents

**Part 1 — The NationBuilder v2 API (general reference)**
- [1.1 API design basics](#11-api-design-basics)
- [1.2 Authentication](#12-authentication)
- [1.3 Query parameters reference](#13-query-parameters-reference)
- [1.4 Relationships, sideloading & sideposting](#14-relationships-sideloading--sideposting)
- [1.5 Full resource relationship map](#15-full-resource-relationship-map)
- [1.6 Error codes reference](#16-error-codes-reference)
- [1.7 Troubleshooting FAQs](#17-troubleshooting-faqs)
- [1.8 Appendix: full Signup resource example](#18-appendix-full-signup-resource-example)
- [1.9 Further reading](#19-further-reading)

**Part 2 — Signup Tags & Signup Taggings (the functions this project uses)**
- [2.1 The concept: tags vs. taggings](#21-the-concept-tags-vs-taggings)
- [2.2 Signup Tags endpoints](#22-signup-tags-endpoints)
- [2.3 Signup Taggings endpoints](#23-signup-taggings-endpoints)
- [2.4 Practical patterns (cookbook)](#24-practical-patterns-cookbook)
- [2.5 Gotchas specific to tags/taggings](#25-gotchas-specific-to-tagstaggings)

- [Quick-reference cheat sheet](#quick-reference-cheat-sheet)
- [Source note](#source-note)

---

# Part 1: The NationBuilder v2 API (general reference)

## 1.1 API design basics

NationBuilder's v2 web APIs follow the **JSON:API** specification (via the Graphiti framework). You don't need to know that spec in depth, but it explains the shape you'll see everywhere: requests/responses travel over HTTP as **resources**, organized by resource type, with the HTTP verb carrying the meaning (standard CRUD):

| Action | HTTP verb | Example |
| --- | --- | --- |
| Create | `POST` | `POST /api/v2/voters` |
| Read | `GET` | `GET /api/v2/voters/{id}` |
| Update | `PATCH` | `PATCH /api/v2/voters/{id}` |
| Delete | `DELETE` | `DELETE /api/v2/voters/{id}` |

Note that `POST /api/v2/voters` doesn't take any data in the URL or verb — the actual payload always goes in the request **body**.

**Where to find the interactive docs:** in your nation's control panel, under **Settings → Developer → API testing** ("Interactive API Documentation"). Developer API access is gated by plan type; if you don't have API access yet, there's also a non-interactive reference resource available.

**Content type:** the API speaks JSON. If you get a **406** response, it means you're missing the `Content-Type` and `Accept` headers — both need to be set to `application/json` (or `application/vnd.api+json` for JSON:API-typed bodies).

## 1.2 Authentication

### Access tokens

Every API request must prove who it's acting on behalf of, via an **access token** — either:
- as a URL parameter: `?access_token=xxx`, or
- as a header or body attribute: `access_token=xxx`.

Tokens are **scoped to the user who generated them**. If that user's permission set only exposes a limited segment of the nation's database, the API will only read/write that same limited segment — the API never grants more than the underlying Control Panel User (CPU) already has. If a CPU can't access the API at all, check that their assigned permission set has API access enabled in Nation Settings.

**Quick test tokens:** generate one from **Settings → Developer → API token** to explore the API immediately. These expire in **24 hours** and cannot be refreshed — testing/dev only, never production.

For production, use the full **OAuth 2.0 authorization code flow** below.

### Registering an application

Before any sustained API usage (single-user, multi-user, multi-nation, or pure machine-to-machine), **register an application**: **Settings → Developer → Register New App**, giving it a name and an OAuth callback URL. This gets you:

- an **OAuth Client ID**
- an **OAuth Client Secret**
- your chosen **Callback URL**

These credentials alone don't grant data access — they let an authorized user's login be exchanged for a working API token. You can share an app with another specific nation (enter its slug in the app settings) or make it public and request listing in the NationBuilder Apps & Integrations Directory (`partners@nationbuilder.com`).

### OAuth 2.0 authorization code flow

**Step 1 — Request authorization.** Send the nation admin to:
```
https://{slug}.nationbuilder.com/oauth/authorize?response_type=code&client_id=...&redirect_uri=...
```
`{slug}` is the target nation. If they aren't logged in, they'll be prompted to. If your app hasn't been approved for that nation before, they'll see an authorization screen first — this happens once per admin whose token your app will use.

**Step 2 — Receive a code.** NationBuilder redirects to your callback with a short-lived code:
```
GET http://www.yourapp.com/oauth_callback?code=...
```
This `code` expires in **10 minutes** and isn't meant to be stored — use it immediately. If the admin denies access, you get `error=access_denied` instead.

**Step 3 — Exchange the code for an access token:**
```bash
curl -X POST --header "Content-Type: application/json" --header "Accept: application/json" \
  --data '{"grant_type":"authorization_code", "code":"{code}", "client_id":"{client_id}", "client_secret":"{client_secret}", "redirect_uri":"{redirect_uri}"}' \
  https://{slug}.nationbuilder.com/oauth/token
```
Response:
```json
{ "access_token": "<access_token_here>", "token_type": "bearer", "scope": "default", "created_at": 1632773996 }
```

**Step 4 — Use it:**
```
GET https://{slug}.nationbuilder.com/api/v1/people?access_token=...
```

> ⚠️ If you've migrated to v2 access tokens, they **expire after 24 hours** and you must implement the refresh flow below.

### Refresh token flow

V2 access tokens expire in 24 hours and come with a **refresh token** in the same response:
```json
{ "access_token": "...", "refresh_token": "...", "token_type": "Bearer", "expires_in": 86400, "scope": "default", "created_at": 1632773996 }
```

Store the refresh token — it's tied to that specific access token. To refresh:
```
POST https://{slug}.nationbuilder.com/oauth/token
     grant_type=refresh_token
     refresh_token=...
     client_id=...
     client_secret=...
```
You get back a **new** access token *and* a new refresh token in the same shape. Two ways to trigger this:

1. **Reactively** — you'll get a `401 token_expired` error (`{"statusCode": 401, "data": {"code": "token_expired", "message": "..."}}`) on an expired token; refresh, then retry the original request.
2. **Proactively** — the token response's `expires_in` field tells you how many seconds until expiry (24h by default); schedule a refresh before that.

Rules to know:
- The refresh token you just used gets **revoked on use** — always store the *new* one.
- **Refresh tokens themselves don't expire.**
- You can test the refresh flow any time, even on a still-valid token — it'll still return a fresh pair.

### PKCE

Optional extra security layer for the OAuth flow, preventing an intercepted `code` from being redeemed by an attacker. Include PKCE params on both `/oauth/authorize` and `/oauth/token` and NationBuilder will store the code challenge and validate the code verifier.

---

## 1.3 Query parameters reference

### Filtering

Use `filter[attribute][clause]=value` on any attribute. Full clause set (merged from all source articles — some clauses only appeared in one of them):

| Clause | Example | Behavior |
| --- | --- | --- |
| *(default, no clause)* | `filter[note]=my note` | Case-**insensitive** exact match |
| `eql` | `filter[note][eql]=My note` | Case-**sensitive** exact match |
| `prefix` | `filter[note][prefix]=my` | Starts with |
| `suffix` | `filter[note][suffix]=my` | Ends with |
| `match` | `filter[note][match]=ot` | Contains |
| `gt` | `filter[donations_amount_in_cents][gt]=500` | Greater than |
| `gte` | `filter[donations_amount_in_cents][gte]=500` | Greater than or equal |
| `lt` | `filter[donations_amount_in_cents][lt]=1000` | Less than |
| `lte` | `filter[donations_amount_in_cents][lte]=1000` | Less than or equal |
| `not_eq` with `null` | `filter[middle_name][not_eq]=null` | Exists (is not null) |
| *(default)* with `null` | `filter[middle_name]=null` | Does not exist (is null) |

**Comma-delimited values** work as an OR list in most clauses:
```
GET /api/v2/signups?fields[signups]=first_name,last_name,support_level&filter[support_level][eq]=1,2
```

**Escaping special characters** (e.g. a comma inside a note) — wrap the value in `{{ }}`:
```
/api/v2/signups?filter[note][contains]={{fundraiser, volunteer}}
/api/v2/signups?filter[last_name][contains]={{, jr}},{{, esq.}}
```

**Filtering on hash attributes** (e.g. `custom_values`) — use `eq` with well-formed JSON:
```
GET /api/v2/signups?filter[custom_values][eq]={ "programming_language": "COBOL" }
```
Comma-delimit multiple JSON values to OR across them:
```
GET /api/v2/signups?filter[custom_values][eq]={ "programming_language": "COBOL" },{ "programming_language": "Ruby" }
```

**Filtering sideloaded/nested resources** — nest the filter under the *sideloaded resource's type* (not the relationship name):
```
/api/v2/signups?include=primary_address,work_address,registered_address&filter[addresses][state]=MT
```

**Filtering sideloads by their own attributes**, combined with `include`:
```
GET /api/v2/signups/18?include=petition_signatures&filter[petition_signatures][created_at][gte]=2024-01-01
```

> 📌 **Filtering never removes unassociated objects from the response.** In the address example above, signups *without* a matching address are still returned — the filter narrows the sideloaded data, not the primary result set.

### Sparse fields

Return only specific attributes with `fields[resource_type]=comma,delimited,list` — keyed by **resource type**, not relationship name:
```
/api/v2/signups?fields[signups]=first_name,last_name,email
```
To limit fields on a sideloaded relationship (e.g. a recruiter, which is itself a signup): `include=recruiter&fields[signups]=first_name,email`.

### Extra fields

Some attributes are **not returned by default** even when requested normally — these are "extra fields," and must be explicitly opted into via `extra_fields[resource_type]=comma,delimited,list`. Example: `home_address` on the signup resource is an extra field.
```
/api/v2/signups/18?fields[signups]=first_name,last_name,custom_values&extra_fields[signups]=home_address
```
Which extra fields exist for a given resource is documented per-resource in the OpenAPI reference (under that resource's query-string parameters).

### Sorting

`sort=attribute` for ascending, `sort=-attribute` for descending:
```
/api/v2/signups?sort=first_name    # ascending
/api/v2/signups?sort=-first_name   # descending
```

### Pagination

Index endpoints return **20 results per page by default** (max 100, min 1). Control it with:
```
/api/v2/signups?page[size]=10
/api/v2/signups?page[number]=3
```
The response carries `links` (a sibling of `data`) with `self`, `prev`, `next` — and sometimes `first`/`last` — plus a `meta` block:
```json
{
  "data": [ /* ... */ ],
  "meta": { "total_pages": 5, "total_count": 50, "current_page": 2 },
  "links": {
    "self": "/api/v2/signups?page[number]=2&page[size]=10",
    "prev": "/api/v2/signups?page[number]=1&page[size]=10",
    "next": "/api/v2/signups?page[number]=3&page[size]=10"
  }
}
```
- `next` is **always present** while there are more results, even if the next page turns out empty.
- **An empty `data` array means you've reached the last page.**
- ⚠️ If records are added/deleted *while* you're paginating through sorted results, some records may be skipped or duplicated across pages.

### Count

Get just a count (no records) with `stats[total]=count`:
```
GET /api/v2/donations?stats[total]=count
```
Returned in `meta` (sibling of `data`):
```json
{ "meta": { "stats": { "total": { "count": 402 } } } }
```

---

## 1.4 Relationships, sideloading & sideposting

### Concepts

Two objects reachable via the API with an association between them are **connected resources**. Relationships come in two shapes:

| Shape | Sub-type | Meaning | Example |
| --- | --- | --- | --- |
| **To-one** | One-to-one | Each instance ties to exactly one instance of the other, and vice versa | Signup ↔ Signup Profile |
| **To-one** | Many-to-one | Many instances of A each point to one instance of B | Many Memberships → one Signup |
| **To-many** | To-many | One instance of A relates to many instances of B | One Recruiter → many Signups |
| **To-many** | Many-to-many | Many instances of A relate to many instances of B | Signups ↔ Signup Tags |

**Sideloading** = reading a connected resource in the same request. **Sideposting** = creating/updating/deleting a connected resource in the same request. Either can be used regardless of which side is "primary" — e.g. a Membership can be updated via a POST to the Signup resource, and vice versa.

### Sideloading

Request connected resources via `include=relationship,another_relationship`:
```
GET /api/v2/signups/18?include=recruiter,petition_signatures
```
This changes the response in two ways: the primary resource's `relationships` block gains a `data` array pointing at the sideloaded records (by `type`+`id`), and those full records appear in a top-level `included` array. Example:
```json
{
  "data": {
    "id": "18", "type": "signups",
    "attributes": { "first_name": "Grace", "last_name": "Hopper", "email1": "grace.hopper@example.com" },
    "relationships": {
      "petition_signatures": {
        "links": { "related": "/api/v2/petition_signatures?filter[signup_id]=18" },
        "data": [ { "type": "petition_signatures", "id": "2" } ]
      }
    }
  },
  "included": [
    {
      "id": "2", "type": "petition_signatures",
      "attributes": {
        "page_id": "11", "petition_page_id": "1", "recruiter_id": null, "signup_id": "18",
        "is_private": false, "comment": "I, Grace Hopper, support you!",
        "created_at": "2024-01-01T13:54:36-04:00", "updated_at": "2024-01-01T13:54:36-04:00"
      },
      "relationships": {
        "petition": { "links": { "related": "/api/v2/petitions/1" } },
        "signup": { "links": { "related": "/api/v2/signups/18" } }
      }
    }
  ],
  "meta": {}
}
```

⚠️ **Sideloading a to-many relationship can produce large, timeout-prone payloads** — especially on index endpoints. `GET /api/v2/signups?include=taggings` fetches taggings for *every* signup in the page. If you hit timeouts: reduce the primary resource's page size, and/or filter the sideloaded data down to what you need — e.g. `?page[size]=10&include=taggings&filter[taggings][tag_id]=123`.

**Paginating sideloads** — on **Show** endpoints only, use `page[resource_type][size]` / `page[resource_type][number]` and increment until you get an empty response:
```
GET /api/v2/petitions/1?include=petition_signatures&page[petition_signatures][size]=10&page[petition_signatures][number]=3
```

### Sideposting

You can create, update, and delete a connected resource in the *same* `POST`/`PATCH`/`PUT` as the primary resource. Example — a `PATCH` on Grace's signup that touches five different relationships at once:
```json
{
  "data": {
    "id": "6", "type": "signups",
    "attributes": {
      "support_level": 2,
      "home_address_attributes": { "delete": true },
      "registered_address_attributes": { "address1": "123 Sesame St.", "city": "New York", "state": "NY" }
    },
    "relationships": {
      "author":   { "data": { "type": "signups", "id": "42", "method": "update" } },
      "recruiter": { "data": { "type": "signups", "id": "555", "method": "disassociate" } },
      "petition_signatures": { "data": { "type": "petition_signatures", "id": "888", "method": "destroy" } },
      "signup_tags": {
        "data": [
          { "type": "signup_tags", "temp-id": "temp-signup-tag-id", "method": "create" },
          { "type": "signup_tags", "id": "123", "method": "update" }
        ]
      },
      "voter": { "data": { "type": "voters", "temp-id": "temp_voter_id", "method": "create" } }
    }
  },
  "included": [
    { "type": "voters", "temp-id": "temp_voter_id", "attributes": { "is_absentee_voter": true, "is_active_voter": true, "is_early_voter": true, "is_permanent_absentee_voter": false } },
    { "type": "signup_tags", "temp-id": "temp-signup-tag-id", "attributes": { "name": "updated_via_api" } },
    { "type": "signups", "id": "42", "attributes": { "support_level": 1 } }
  ]
}
```

**Sideposting methods:**

| Method | Behavior |
| --- | --- |
| `create` | Creates the sideposted resource. Requires a `temp-id` unique across the entire request. |
| `update` | Updates the relationship, and — if attribute data is provided in `included` — updates the sideposted resource itself. If only relationship data is given, it just associates the existing resource. Requires an `id`. |
| `destroy` | **Deletes** the sideposted resource entirely (identified by `id`), not just the link. |
| `disassociate` | Removes the connection between the two resources **without deleting either one**. |

How `create` actually works: add a `temp-id` + `method: "create"` entry under `relationships`, then supply that same `temp-id` with the full attribute payload in the top-level `included` array. The `temp-id` (or `id`, for existing resources) is what maps a `relationships` entry to its `included` payload.

Note: an *already-existing* related resource (e.g. signup tag `id: "123"` in the example above) doesn't need a corresponding `included` entry at all — referencing it in `relationships` is sufficient to associate it.

⚠️ **Caution:** `destroy` and `disassociate` are destructive — test sidepost payloads using these methods in a sandbox before running them against a live nation.

---

## 1.5 Full resource relationship map

Every documented resource and what it connects to. Where a related object has no dedicated resource of its own (e.g. "Recruiter"), it's accessed *through* another resource — noted in parentheses.

<details>
<summary><strong>Automation Enrollment, Automation, Ballot</strong></summary>

- **Automation Enrollment** → Automation *(To-One)* · Signup *(To-One)*
- **Automation** → Enrollments *(AutomationEnrollmentResource, To-Many)*
- **Ballot** → Election *(To-One)* · Voter *(To-One)*
</details>

<details>
<summary><strong>Broadcaster, Contact</strong></summary>

- **Broadcaster** → Point Person *(SignupResource, To-One)* · Voicemail Point Person *(SignupResource, To-One)* · Text Point Person *(SignupResource, To-One)* · Mailings *(To-Many)* · Signups *(To-Many)*
- **Contact** → Signup *(To-One)* · Author *(SignupResource, To-One)*
</details>

<details>
<summary><strong>Donation, Donation Tracking Code</strong></summary>

- **Donation** → Author *(SignupResource, To-One)* · Donation Tracking Code *(To-One, as `tracking_code`)* · Import *(To-One)* · Membership *(To-One)* · Page *(To-One)* · Payment Type *(To-One)* · Pledge *(To-One)* · Mailing *(To-One)* · Recruiter *(SignupResource, To-One)* · Signup *(To-One)*
- **Donation Tracking Code** → Donations *(To-Many)*
</details>

<details>
<summary><strong>Election, Event, Event RSVP, Event Ticket Level</strong></summary>

- **Election** → Ballots *(To-Many)*
- **Event** → Auto Response Broadcaster *(BroadcasterResource, To-One)* · Page *(PageResource, To-One)* · Point Person *(SignupResource, To-One)* · Tracking Code *(DonationTrackingCodeResource, To-One)* · RSVPs *(EventRsvpResource, To-Many, as `rsvps`)* · Ticket Levels *(EventTicketLevelResource, To-Many, as `ticket_levels`)*
- **Event RSVP** → Author *(SignupResource, To-One)* · Event Page *(EventResource, To-One)* · Ticket Level *(EventTicketLevelResource, To-One)* · Recruiter *(SignupResource, To-One)* · Signup *(To-One)*
- **Event Ticket Level** → Event Page *(EventResource, To-One)* · RSVPs *(EventRsvpResource, To-Many, as `rsvps`)*
</details>

<details>
<summary><strong>Import, Mailing, Membership, Membership Type</strong></summary>

- **Import** → Point Person *(SignupResource, To-One)* · Author *(SignupResource, To-One)* · Terminator *(SignupResource, To-One)* · Signups *(SignupResource, To-Many)*
- **Mailing** → Author *(SignupResource, To-One)*
- **Membership** → Donations *(To-Many)* · Membership Type *(To-One)* · Signup *(To-One)*
- **Membership Type** → Page *(To-One)* · Memberships *(To-Many)*
</details>

<details>
<summary><strong>Page, Path History, Path Journey, Path Journey Status Change, Path, Path Step</strong></summary>

- **Page** → Site *(To-One)* · Membership Types *(To-Many)*
- **Path History** → Current Step Point Person *(SignupResource, To-One)* · Path Journey *(To-One)* · Path Journey Status Change *(To-One)* · Point Person *(SignupResource, To-One)* · Recruiter *(SignupResource, To-One)*
- **Path Journey** → Signup *(To-One)* · Path *(PathResource, To-One)* · Point Person *(SignupResource, To-One)* · Current Step Point Person *(SignupResource, To-One)* · Path Journey Status Change *(To-One)* · Current Step *(PathStepResource, To-One)* · Path Histories *(PathHistoryResource, To-Many)*
- **Path Journey Status Change** → Path *(To-One)* · Path Journeys *(To-Many)* · Path Histories *(To-Many)*
- **Path** → Path Journeys *(PathJourneyResource, To-Many)* · Path Steps *(To-Many)* · Path Journey Status Changes *(To-Many)* · Default Point Person *(SignupResource, To-One)*
- **Path Step** → Path *(To-One)* · Default Point Person *(SignupResource, To-One)*
</details>

<details>
<summary><strong>Petition, Petition Signature, Pledge, Precinct, Relationship</strong></summary>

- **Petition** → Petition Signatures *(PetitionSignatureResource, To-Many, as `signatures`)* · Page *(To-One)*
- **Petition Signature** → Page *(To-One)* · Petition *(To-One, as `petition_page`)* · Recruiter *(SignupResource, To-One, as `signup`)* · Signup *(To-One)*
- **Pledge** → Author *(SignupResource, To-One)* · Donation Tracking Code *(To-One, as `tracking_code`)* · Page *(To-One)* · Recruiter *(SignupResource, To-One)* · Signup *(To-One)*
- **Precinct** → Point Person *(SignupResource, To-One)* · Signups *(To-Many)*
- **Relationship** → First Signup *(SignupResource, To-One)* · Second Signup *(SignupResource, To-One)*
</details>

<details>
<summary><strong>Signup Nation, Signup Profile, Signups, Signup Source, Signup Tag, Signup Tagging</strong> ⭐</summary>

- **Signup Nation** → Signup *(To-One)*
- **Signup Profile** → Signup *(One-To-One)*
- **Signups** → Author *(SignupResource, To-One)* · Last Contacted By *(SignupResource, To-One)* · Page *(To-One)* · Parent *(SignupResource, To-One)* · Precinct *(To-One)* · Recruiter *(SignupResource, To-One)* · Signup Profile *(One-To-One)* · Voter *(One-To-One)* · **Signup Tags** *(SignupTagResource, Many-To-Many, as `tags`)* · Identity Mappings *(To-Many)* · Memberships *(To-Many)* · Path Journeys *(PathJourneyResource, To-Many)* · **Taggings** *(SignupTaggingResource, To-Many)* · Petition Signatures *(To-Many)* · Signup Nations *(To-Many)* · Signup Sources *(To-Many)*
- **Signup Source** → Signup *(To-One)*
- **Signup Tag** → Signups *(Many-To-Many)* — *this is Part 2's first resource*
- **Signup Tagging** → Tag *(SignupTagResource, To-One)* · Signup *(To-One)* — *this is Part 2's second resource*

> This is the row group that matters for Part 2: a **Signup** relates to many **Signup Tags** (`tags`, many-to-many) and many **Taggings** (`taggings`, to-many) — the **Signup Tagging** is the join record connecting one Signup to one Signup Tag. See [§2.1](#21-the-concept-tags-vs-taggings).
</details>

<details>
<summary><strong>Site, Survey Question, Survey Question Possible Response, Survey Question Response, Survey, Voter</strong></summary>

- **Site** → Pages *(To-Many)*
- **Survey Question Possible Response** → Survey Question *(To-One)* · Survey Question Responses *(To-Many, as `responses`)*
- **Survey Question** → Survey *(To-One)* · Author *(SignupResource, To-One)* · Survey Question Responses *(To-Many, as `responses`)* · Survey Question Possible Responses *(To-Many, as `possible_responses`)*
- **Survey Question Response** → Survey Question *(To-One)* · Survey Question Possible Response *(To-One, as `response`)* · Signup *(To-One)* · Author *(SignupResource, To-One)* · Page *(To-One)*
- **Survey** → Survey Questions *(To-Many)*
- **Voter** → Signup *(One-To-One)* · Ballots *(To-Many)*
</details>

---

## 1.6 Error codes reference

**Validation errors** (creating/updating a resource) come back as an `errors` array:
```json
{
  "errors": [
    {
      "code": "unprocessable_entity", "status": "422", "title": "Validation Error",
      "detail": "Address is required",
      "source": { "pointer": "/data/relationships/address" },
      "meta": { "attribute": "address", "message": "is required", "code": "is required" }
    }
  ]
}
```

**Non-validation errors** use a simpler body:
```json
{ "code": "not_found", "message": "Record not found" }
```

| Status | Code | Meaning |
| --- | --- | --- |
| 400 | `bad_request` | Invalid `include`, `filter`, attribute, `sort`, operator, sideload, or other malformed request. |
| 400 | `invalid_custom_field` | Unknown custom field in the request. |
| 401 | `unauthorized` | Missing access token, or the resource owner lacks sufficient permissions. |
| 401 | `token_expired` | Access token has expired. |
| 401 | `token_upgrade_required` | A JWT is required — v1 tokens don't work on v2. |
| 404 | `not_found` | Record not found — check the ID. |
| 422 | `unprocessable_entity` | Validation error (see the shape above). |
| 500 | `server_error` | Server error — retry, and double-check the request. |
| 502 | `bad_gateway` | Backend service timed out. |
| 503 | `service_unavailable` | Temporarily unavailable — retry later. Can happen from sideloading too much data. |

> Databricks — er, NationBuilder — considers changing/removing an error **status or code** a breaking change, but the **message text** can change any time. Don't build logic that depends on exact message strings.

## 1.7 Troubleshooting FAQs

**Data access constraints.** What a request can see is bounded by the *resource owner's* (the token-holder's) permission set. Missing data you expect to see? Check whether that user's permission level recently changed.

**403 on a token that hasn't expired.** The most common cause: the resource owner **lost access to the nation's control panel**. Once that happens, their access token stops working for API calls even though it hasn't technically expired.

## 1.8 Appendix: full Signup resource example

<details>
<summary><strong>Create + read a Signup — every attribute in the response (from the QuickStart Guide)</strong></summary>

**Create:**
```
POST https://slug.nationbuilder.com/api/v2/signups?access_token=XXX
```
```json
{
  "data": {
    "type": "signups",
    "attributes": {
      "born_at": "1990-08-06", "email": "test@example.com", "email_opt_in": true,
      "employer": "NationBuilder", "external_id": "12345", "first_name": "Lucy",
      "is_volunteer": true, "last_name": "Butler", "mobile_number": "1234567890",
      "mobile_opt_in": true, "middle_name": "Octavia", "note": "Good talk",
      "occupation": "Software engineer", "party": "P", "phone_number": "1234567890",
      "recruiter_id": "1", "sex": "F", "signup_type": 1
    }
  }
}
```
Returns **201** with the full record — every attribute the Signup resource can carry (abridged view of the ones most likely to matter beyond what's above): `city_district`, `county_district`, `datatrust_id`, `do_not_call`, `do_not_contact`, `federal_district`, `judicial_district`, `ngp_id`, `precinct_id`, `rnc_id`, `salesforce_id`, `school_district`, `state_file_id`, `van_id`, `ward`, `work_phone_number`, `active_customer_started_at` / `_expires_at`, `author_id`, `auto_import_id`, `availability`, `banned_at`, `capital_amount_in_cents`, `church`, `closed_invoices_amount_in_cents` / `_count`, `contact_status`, `could_vote_status`, `donations_amount_in_cents`, `donations_count`, `donations_pledged_amount_in_cents`, `email1`–`email4` (+ `_is_bad` / `_is_bouncing` flags each), `ethnicity`, `fax_number`, `federal_donotcall`, `first_donated_at`, `first_fundraised_at`, `first_invoice_at`, `first_prospect_at`, `first_recruited_at`, `first_supporter_at`, `first_volunteer_at`, `full_name`, `import_id`, `inferred_party`, `inferred_support_level`, `invoice_payments_amount_in_cents`, `invoices_amount_in_cents`, `is_cpu`, `is_deceased`, `is_donor`, `is_fundraiser`, `is_leaderboardable`, `is_mobile_bad`, `is_possible_duplicate`, `is_profile_private` / `_searchable`, `is_prospect`, `is_supporter`, `is_survey_question_private`, `language`, `last_call_id`, `last_contacted_at` / `_by_id`, `last_donated_at`, `last_fundraised_at`, `last_invoice_at`, `last_rule_violation_at`, `legal_name`, `locale`, `marital_status`, `mobile_number_normalized`, `note_updated_at`, `outstanding_invoices_amount_in_cents` / `_count`, `overdue_invoices_count`, `parent_id`, `party_member`, `phone_number_normalized`, `phone_time`, `prefix`, `previous_party`, `primary_email_id`, `priority_level` / `_changed_at`, `profile_content` / `_html`, `profile_headline`, `received_capital_amount_in_cents`, `registered_at`, `religion`, `rule_violations_count`, `spent_capital_amount_in_cents`, `submitted_address`, `suffix`, `support_level_changed_at`, `support_probability_score`, `township`, `turnout_probability_score`, `unsubscribed_at`, `username`, `warnings_count`, `custom_values` (your own custom field hash), `updated_at`, `created_at`.

Plus a `relationships` block with links (and, where sideloaded, `data`) for: `author`, `last_contacted_by`, `page`, `parent`, `precinct`, `recruiter`, `signup_profile`, `voter`, `signup_tags`, `memberships`, `path_journeys`, `taggings`, `petition_signatures`, `signup_nations`.

> 📌 In this particular example response, `"signup_tags": {"meta": {"included": false}}` appears instead of a `links`/`data` block — a reminder that the `signup_tags` relationship isn't sideloaded by default on the Signup resource; request it explicitly with `include=signup_tags` if you need it there (or query `/api/v2/signup_tags?filter[signup_id]=...` directly — see [Part 2](#22-signup-tags-endpoints)).

**Read:**
```
GET https://slug.nationbuilder.com/api/v2/signups/1
```
Same shape, **200** instead of 201.
</details>

## 1.9 Further reading

Referenced by the source articles (titles as given, no URLs supplied in the pasted docs):

- NationBuilder API QuickStart Guide
- API v2 walkthrough
- Relationships between Resources in v2 API
- Using parameters to interact with the NationBuilder API
- Connecting Zapier to NationBuilder
- "Unleashing New Possibilities with the NationBuilder API v2"
- Generating API tokens (support article)
- API authentication guide (support article)
- Access token migration guide (for legacy non-expiring tokens)

---

# Part 2: Signup Tags & Signup Taggings (the functions this project uses)

## 2.1 The concept: tags vs. taggings

These are **two distinct resources** that work together, and the split is exactly why NationBuilder splits them into two endpoints:

| Resource | What it is | Key attributes |
| --- | --- | --- |
| **Signup Tag** (`signup_tags`) | The **tag itself** — a named label that can exist independent of any signup. | `name` (unique, case-insensitive), `from_sharing_nation`, `imported`, `shared_with_nations`, `taggings_count` |
| **Signup Tagging** (`signup_taggings`) | The **join record** — one specific signup wearing one specific tag. | `signup_id`, `tag_id`, `created_at` |

Per the relationship map ([§1.5](#15-full-resource-relationship-map)): a **Signup** connects to many **Signup Tags** (many-to-many, exposed as `tags`) and to many **Signup Taggings** (to-many, exposed as `taggings`) — and each **Signup Tagging** points to exactly one **Tag** and one **Signup** (both to-one). So:

- Deleting a **Signup Tag** removes it (and its taggings) from *every* signup it was on.
- Deleting/disassociating a **Signup Tagging** removes the tag from *just that one* signup — the tag itself still exists for everyone else.

That distinction drives which endpoint you want for a given job — see the cookbook in [§2.4](#24-practical-patterns-cookbook).

## 2.2 Signup Tags endpoints

Base path: `/api/v2/signup_tags`

| Attribute | Type | Notes |
| --- | --- | --- |
| `name` | string or null | Must be unique, case-insensitive. |
| `from_sharing_nation` | boolean or null | This tag was shared in from another nation. |
| `imported` | boolean or null | Tag originated from an import. |
| `shared_with_nations` | boolean or null | This tag is shared out to other nations. |
| `taggings_count` | integer or null | How many signups currently carry this tag. Default `0`. |

All read-only per the schema (i.e. these attributes describe the tag, they aren't separately settable beyond `name`).

### List all signup tags

`GET /api/v2/signup_tags`

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `filter[signup_id]` | string | ∅ | Restrict to tags applied to a given signup. |
| `page[number]` | string | `1` | Page number (starting at 1). |
| `page[size]` | string | `20` | Max 100, min 1. |
| `include` | string | ∅ | **Supported sideloads: none** on this endpoint. |
| `fields[signup_tags]` | array of string | — | Allowed: `from_sharing_nation`, `imported`, `name`, `shared_with_nations`, `taggings_count`. |

Response: `200` / `401` / `429`.
```json
{
  "data": [
    { "id": "1", "type": "signup_tags", "attributes": { "name": "tag_name", "from_sharing_nation": false, "imported": false, "shared_with_nations": false, "taggings_count": 0 } }
  ],
  "links": { "self": "...", "first": "...", "last": "...", "prev": "...", "next": "..." },
  "meta": {}
}
```

### Create a signup tag

`POST /api/v2/signup_tags`

| Query param | Notes |
| --- | --- |
| `include` | Supported sideloads: none. |
| `fields[signup_tags]` | Same allowed list as above. |

Request body: `application/vnd.api+json`, the tag's attributes (i.e. `name`). Response: `201` / `400` / `401` / `422` / `429` — same resource shape as the list item above.

### Show a signup tag

`GET /api/v2/signup_tags/{id}`

| Query param | Notes |
| --- | --- |
| `include` | **Allowed: `signups` ┃ `taggings`** — note this differs from List/Create, which support no sideloads at all. |
| `fields[signup_tags]` | Same allowed list. |

Response: `200` / `401` / `404` / `429`.

### Update a signup tag

`PATCH /api/v2/signup_tags/{id}`

Same path/query params as Show (`include`: `signups` ┃ `taggings`). Request body: the attributes to change. Response: `200` / `400` / `401` / `404` / `422` / `429` — updated resource returned.

### Delete a signup tag

`DELETE /api/v2/signup_tags/{id}`

Same path/query params as Show. **Deletes the tag and removes it from every signup it's applied to** — this is the destructive, "everywhere" removal (contrast with disassociating a single tagging, [§2.4](#24-practical-patterns-cookbook)). Response: `200` / `401` / `404` / `429` — a meta-only body (`{"meta": {}}`), since NationBuilder returns `200` rather than JSON:API's more common `204 No Content` for destroy operations.

## 2.3 Signup Taggings endpoints

Base path: `/api/v2/signup_taggings`

| Attribute | Type | Notes |
| --- | --- | --- |
| `signup_id` | string | The signup that was tagged. |
| `tag_id` | string | The tag assigned to the signup. |
| `created_at` | date-time or null | When the tagging was created. |

The table above shows the **response** shape, where all three attributes are read-only. The **create request** schema is narrower and *is* writable: `signup_id` and `tag_id` only (no `created_at` — that's server-set). Unusually for this API, the tagging is created directly via plain `attributes`, **not** via `relationships` — see the confirmed example under Create, below.

### List all signup taggings

`GET /api/v2/signup_taggings`

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `page[number]` | string | `1` | — |
| `page[size]` | string | `20` | Max 100, min 1. |
| `include` | array of string | — | Allowed: `signup` ┃ `tag`. |
| `fields[signup_taggings]` | array of string | — | Allowed: `created_at`, `signup_id`, `tag_id`. |

Response: `200` / `401` / `429`.
```json
{
  "data": [
    { "id": "1", "type": "signup_taggings", "attributes": { "signup_id": "1", "tag_id": "1", "created_at": "2019-10-26T10:00:00-04:00" } }
  ],
  "links": { "self": "...", "first": "...", "last": "...", "prev": "...", "next": "..." },
  "meta": {}
}
```

### Create a signup tagging

`POST /api/v2/signup_taggings`

| Query param | Notes |
| --- | --- |
| `include` | Allowed: `signup` ┃ `tag`. |
| `fields[signup_taggings]` | Same allowed list. |

Request body — plain attributes, **not** a `relationships` block (contrast with how `signups`, `contacts`, etc. link to other resources elsewhere in this API):
```json
{
  "data": {
    "type": "signup_taggings",
    "attributes": {
      "signup_id": "1",
      "tag_id": "1"
    }
  }
}
```

Response: `201` / `400` / `401` / `422` / `429`.
```json
{
  "data": { "id": "1", "type": "signup_taggings", "attributes": { "signup_id": "1", "tag_id": "1", "created_at": "2019-10-26T10:00:00-04:00" } },
  "meta": {}
}
```

### Show a signup tagging

`GET /api/v2/signup_taggings/{id}`

Same query params as List (`include`: `signup` ┃ `tag`; `fields[signup_taggings]`). Response: `200` / `401` / `404` / `429`.

### Delete a signup tagging

`DELETE /api/v2/signup_taggings/{id}`

**"Deletes the tagging, removing the tag from the signup"** — the tag itself is untouched; only this one signup↔tag link is removed. Same query params as Show. Response: `200` / `401` / `404` / `429` — meta-only body, same as tag delete.

> Note there is **no `PATCH`/Update endpoint for signup taggings** in the source docs — a tagging is either created or destroyed, never edited (which makes sense: there's nothing to change about "signup X has tag Y" other than whether it's true).

## 2.4 Practical patterns (cookbook)

Synthesized from the worked sidepost examples in Part 1 §1.4, applied specifically to tags/taggings:

**Tag a signup at the moment you create it** (sidepost, mixing a brand-new tag and an already-existing one):
```json
{
  "data": {
    "type": "signups",
    "attributes": { "first_name": "Kim", "last_name": "Possible", "email": "test@example.com" },
    "relationships": {
      "signup_tags": {
        "data": [
          { "type": "signup_tags", "temp-id": "new-signup-tag", "method": "create" },
          { "type": "signup_tags", "temp-id": "another-new-signup-tag", "method": "create" },
          { "type": "signup_tags", "id": "123", "method": "update" }
        ]
      }
    }
  },
  "included": [
    { "type": "signup_tags", "temp-id": "new-signup-tag", "attributes": { "name": "create-from-api-call" } },
    { "type": "signup_tags", "temp-id": "another-new-signup-tag", "attributes": { "name": "api-recruited" } }
  ]
}
```
The already-existing tag (`id: "123"`) needed **no** `included` entry — referencing its `id` in `relationships` was enough to apply it.

**Add a tag to an existing signup** — either a `PATCH` to `/api/v2/signups/{id}` with just the `signup_tags` relationship changed (as above), or more directly, once you already know both IDs:
```json
{ "data": { "type": "signup_taggings", "attributes": { "signup_id": "18", "tag_id": "123" } } }
```
via `POST /api/v2/signup_taggings`.

**Remove one tag from one signup, without deleting the tag** (untag, not delete) — sidepost `disassociate` on the `signup_tags` relationship:
```json
{
  "data": {
    "id": "48", "type": "signups",
    "relationships": {
      "signup_tags": { "data": [ { "type": "signup_tags", "id": "456", "method": "disassociate" } ] }
    }
  }
}
```
Equivalent, more directly: `DELETE /api/v2/signup_taggings/{tagging_id}`.

**Delete a tag everywhere** (removes it from every signup that has it): `DELETE /api/v2/signup_tags/{id}`.

**Find every tag on a signup:** `GET /api/v2/signup_tags?filter[signup_id]={id}`, or sideload with `GET /api/v2/signups/{id}?include=signup_tags` (remember §1.8's note — this relationship isn't included by default).

**Find every signup carrying a tag:** sideload taggings filtered to that tag, keeping the payload small — `GET /api/v2/signups?page[size]=10&include=taggings&filter[taggings][tag_id]=123` (per the sideload-timeout guidance in §1.4, don't drop the page-size limit on a large nation).

## 2.5 Gotchas specific to tags/taggings

1. **Deleting a Signup Tag (`DELETE /api/v2/signup_tags/{id}`) cascades** — it removes the tag from every signup that had it, not just one. If you want to untag a single person, use a tagging-level operation (`DELETE /api/v2/signup_taggings/{id}` or a `disassociate` sidepost) instead.
2. **No Update endpoint for taggings.** You can update a *tag's* name (`PATCH /api/v2/signup_tags/{id}`), but a *tagging* only ever exists or doesn't — create or destroy, no in-between.
3. **`signup_tags` isn't sideloaded on a Signup by default** — the QuickStart example response literally shows `"signup_tags": {"meta": {"included": false}}` in place of the usual `links`/`data`. Ask for it explicitly with `include=signup_tags` (on the Signup resource) or query `/api/v2/signup_tags` directly.
4. **Both List and Create on `signup_tags` support zero sideloads** (`Supported sideloads: (none)`), but **Show, Update, and Delete support `signups` and `taggings`.** Don't assume all five endpoints on the same resource accept the same `include` values — check each one.
5. **Creating a tagging uses plain `attributes` (`signup_id` + `tag_id`), not `relationships`** — the one resource in this whole reference where that's true. Every other create/sidepost example in Part 1 links resources via a `relationships` block; `signup_taggings` breaks that pattern, so don't copy the relationships shape here by habit.
6. **Tag names must be unique and are case-insensitive** — creating `"Volunteer"` when `"volunteer"` already exists will conflict, not create a second tag.

---

## Quick-reference cheat sheet

- **CRUD = HTTP verb:** POST create, GET read, PATCH update, DELETE delete.
- **Auth:** OAuth 2.0 authorization-code flow for production; test tokens (24h, no refresh) for dev only; v2 tokens need a refresh-token flow.
- **Filtering:** `filter[attr][clause]=value` — clauses: *(none)*/`eql`/`prefix`/`suffix`/`match`/`gt`/`gte`/`lt`/`lte`, plus `null` checks. Escape special chars with `{{ }}`. Filters don't strip unassociated sideloaded records.
- **Shaping responses:** `fields[type]=...` (sparse), `extra_fields[type]=...` (opt-in extras), `sort=attr`/`-attr`, `page[size]`/`page[number]`, `stats[total]=count`.
- **Relationships:** `include=...` to sideload (read), `relationships` + `included` with `create`/`update`/`destroy`/`disassociate` to sidepost (write). `destroy` deletes the resource; `disassociate` only removes the link.
- **Tags vs. taggings:** a **Signup Tag** is the label (`name`, unique); a **Signup Tagging** is one signup wearing one tag (`signup_id` + `tag_id`). Delete a tag → gone everywhere. Delete/disassociate a tagging → gone from just that one signup.
- **Signup Tags** support List/Create/Show/Update/Delete. **Signup Taggings** support List/Create/Show/Delete only — no update.

---

## Source note

This guide reorganizes seven NationBuilder documentation articles pasted into [nationbuilder.md](nationbuilder.md): *NationBuilder v2 API Core Concepts*, *API v2 walkthrough*, *NationBuilder API QuickStart Guide*, *API Authentication Guide*, *Relationships between Resources in v2 API*, *Using parameters to interact with the NationBuilder API*, and the **Signup Tags** / **Signup Taggings** endpoint reference pages. All information from those pages is retained here; overlapping content (three articles each described filtering, sideloading, etc., slightly differently) was merged into single authoritative sections rather than repeated three times. A handful of obvious JSON bracket/comma typos in the source's copy-pasted examples were corrected for validity — no data or keys were changed. Per your request, the **Signup Tags** and **Signup Taggings** endpoint documentation — which was interleaved between three unrelated general-API articles in the source — is now entirely contained in **Part 2**, separate from the general API reference in **Part 1**.
