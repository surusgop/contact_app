# Azure Databricks Authentication Guide

An organized reference for authorizing access to Azure Databricks resources via the CLI and REST APIs. Reorganized from Databricks' own documentation pages for clarity — no information dropped, just restructured. Source pages covered: *Authorize access to Azure Databricks resources*, *Authenticate access to Azure Databricks using OAuth token federation*, *Configure a federation policy*, *Authenticate with Azure Databricks personal access tokens (legacy)*, and *Environment variables and fields for unified authentication*.

> 📌 **If you just want to connect this repo (`contact_app`) to Databricks**, skip straight to [§5. Personal Access Tokens](#5-personal-access-tokens-legacy) — that's the method `app.py` already uses (`WorkspaceClient(host=..., token=...)`), and it's exactly what your `token.txt` is.

---

## Table of contents

- [1. The big picture](#1-the-big-picture)
- [2. Account & API types](#2-account--api-types)
- [3. Authorization methods overview](#3-authorization-methods-overview)
- [4. OAuth token federation](#4-oauth-token-federation)
  - [4.1 What it is, and why](#41-what-it-is-and-why)
  - [4.2 Two flavors: account-wide vs. workload identity federation](#42-two-flavors-account-wide-vs-workload-identity-federation)
  - [4.3 Configuring a workload/service-principal federation policy](#43-configuring-a-workloadservice-principal-federation-policy)
  - [4.4 Example federation policies by tool](#44-example-federation-policies-by-tool)
  - [4.5 Best practices for federation policies](#45-best-practices-for-federation-policies)
  - [4.6 Configuring an account-wide federation policy](#46-configuring-an-account-wide-federation-policy)
  - [4.7 After configuring a federation policy](#47-after-configuring-a-federation-policy)
- [5. Personal access tokens (legacy)](#5-personal-access-tokens-legacy)
  - [5.1 Overview and limits](#51-overview-and-limits)
  - [5.2 Creating a PAT as a workspace user](#52-creating-a-pat-as-a-workspace-user)
  - [5.3 Scoped tokens and auto-scoping](#53-scoped-tokens-and-auto-scoping)
  - [5.4 Creating a PAT for a service principal](#54-creating-a-pat-for-a-service-principal)
  - [5.5 Using a PAT to authenticate](#55-using-a-pat-to-authenticate)
  - [5.6 Managing PATs via the REST API](#56-managing-pats-via-the-rest-api)
- [6. Unified authentication](#6-unified-authentication)
  - [6.1 What it is](#61-what-it-is)
  - [6.2 Full environment variable / field reference](#62-full-environment-variable--field-reference)
  - [6.3 Supported authentication types (`auth_type`)](#63-supported-authentication-types-auth_type)
  - [6.4 Configuration profiles](#64-configuration-profiles)
- [7. Third-party integrations](#7-third-party-integrations)
- [8. Quick-reference cheat sheet](#8-quick-reference-cheat-sheet)
- [Source note](#source-note)

---

## 1. The big picture

To access Azure Databricks resources with the CLI or REST APIs, you must authenticate as an **Azure Databricks account** with the right permissions. That account is set up by your Azure Databricks administrator (or a user with admin privileges) — you don't self-provision it.

Everything else in this guide answers one question: **given your account type, what you're trying to call, and your operating environment (human at a keyboard vs. automated pipeline), which authentication method should you configure?**

---

## 2. Account & API types

**Two account types:**

| Account type | Used for |
| --- | --- |
| **User account** | Interactive CLI commands and API calls — a human at a keyboard, prompted in real time. |
| **Service principal** | Automated CLI commands and API calls with **no** human interaction — scripts, CI/CD, scheduled jobs. |

You *can* use a Microsoft Entra service principal to authorize account/workspace access, but **Databricks recommends a Databricks service principal with OAuth instead** — its access tokens are described as more robust for Databricks-only authorization.

**Two API tiers:**

| API tier | Who can use it | Hosted at |
| --- | --- | --- |
| **Account-level APIs** | Account owners and admins | The account console URL |
| **Workspace-level APIs** | Workspace users and admins | Workspace-specific URLs |

Which tier you're calling determines your `DATABRICKS_HOST` value (§6.2). **Note:** personal access tokens (§5) only ever work at the *workspace* level — they cannot drive account-level operations.

---

## 3. Authorization methods overview

| Method | What it is | Best for |
| --- | --- | --- |
| **Databricks OAuth token federation** ⭐ *(Recommended)* | OAuth tokens from your own identity provider, for users or service principals | Authenticating without managing Databricks-issued secrets (§4) |
| **Databricks OAuth for service principals (OAuth M2M)** | Short-lived OAuth tokens for service principals | Unattended auth — automated pipelines, CI/CD |
| **OAuth for users (OAuth U2M)** | Short-lived OAuth tokens for users | Attended auth — you authenticate live via a browser when prompted |
| **Personal access tokens (legacy)** | Long-lived static bearer tokens tied to one workspace | Simple scripts and local dev; superseded by OAuth where possible (§5) |
| **Azure managed service identity authorization** | Microsoft Entra ID tokens for Azure managed identities | Only for Azure resources that support managed identities (e.g. Azure VMs) |
| **Microsoft Entra ID service principal authorization** | Microsoft Entra ID tokens for Entra ID service principals | Azure resources with Entra ID support but *no* managed identities (e.g. Azure DevOps) |
| **Azure CLI authorization** | Microsoft Entra ID tokens for users or Entra ID service principals | Authorizing Azure resources *and* Azure Databricks via the Azure CLI |
| **Microsoft Entra ID user authorization** | Microsoft Entra ID tokens for users | Only for Azure resources that support *exclusively* Entra ID tokens (Databricks doesn't recommend manual Entra tokens for Databricks users) |

**Decision path:**

1. **No human present (script/service)?** → A Databricks service principal with **OAuth M2M**, or **OAuth token federation** if you'd rather not manage any Databricks-issued secret at all.
2. **Human, interactive, browser available?** → **OAuth U2M**.
3. **Just need something simple for local dev / a one-off script, and you already have a token?** → **Personal access token** (§5) — this is what `contact_app` uses today.
4. **On Azure infra with managed identities** (e.g. an Azure VM)? → **Azure managed service identity authorization**.
5. **Integrating an Azure tool with Entra ID but no managed identities** (e.g. Azure DevOps)? → **Microsoft Entra ID service principal authorization**.
6. **Scripting against the Azure CLI itself?** → **Azure CLI authorization**.
7. **General default / greenfield automation** → **OAuth token federation**.

---

## 4. OAuth token federation

### 4.1 What it is, and why

**Databricks OAuth token federation** lets you securely access Databricks APIs using tokens from your **trusted identity provider (IdP)** instead of a Databricks-issued secret. Users and service principals exchange a JWT from their IdP for a Databricks OAuth token, which is then used against the Databricks APIs.

Why it's recommended, especially for automated workloads: your workload authenticates as a Databricks service principal using identity tokens issued by its own automation environment (e.g. GitHub Actions, Kubernetes). The Databricks SDKs and CLI **automatically fetch and exchange these tokens** — eliminating the need to manage or rotate Databricks secrets like PATs or OAuth client secrets.

### 4.2 Two flavors: account-wide vs. workload identity federation

| Flavor | What it enables | Typical use |
| --- | --- | --- |
| **Account-wide token federation** | *Every* user and service principal in your Databricks account can authenticate using tokens from your IdP. Centralizes token-issuance policy management in the IdP. | Usually paired with SCIM, so IdP users sync into your Databricks account. |
| **Workload identity federation** | Automated workloads running *outside* Databricks (CI/CD, cloud runtimes) authenticate as a Databricks service principal using tokens from their own runtime — no Databricks secret needed. | GitHub Actions, Kubernetes, Azure DevOps, GitLab, CircleCI, AWS IAM workloads. |

> Azure users can alternatively use MS Entra tokens directly with the Databricks CLI/APIs.

**Configuring OAuth token federation, at a glance:**

1. Decide: account-wide, or workload identity federation?
2. Create a **federation policy** — you'll need your account ID (account-wide) or the target service principal's ID (workload identity), plus issuer info from whichever tool/IdP will issue the tokens.
3. Configure that tool/IdP to actually authenticate to Databricks using the federated token.

### 4.3 Configuring a workload/service-principal federation policy

A **service principal federation policy** attaches to one service principal in your Databricks account and specifies:

- **Issuer** (or IdP) the service principal is allowed to authenticate from
- **Subject** — the specific workload identity permitted to act as that service principal

**Fields you must specify:**

| Field | Description |
| --- | --- |
| **Issuer URL** | HTTPS URL identifying the workload identity provider; matched against the token's `iss` claim. |
| **Subject** | The workload's unique identifier in its runtime environment. Defaults to the `sub` claim if unspecified. |
| **Audiences** | Intended token recipient(s), matched against `aud`. Matches if *any* configured audience matches. Defaults to your Databricks account ID if unspecified. |
| **Subject claim** *(optional)* | Which token claim holds the subject, if not `sub`. Databricks recommends keeping the default `sub` — only override when `sub` isn't stable/suitable (rare; see CircleCI example in §4.4). |
| **Token signature validation** *(optional)* | Public keys (JWKS JSON, up to 5 keys) or a JWKS URI to validate signatures. If unset, Databricks fetches keys from the issuer's `/.well-known/openid-configuration` endpoint (the recommended default) — your IdP must publish `jwks_uri` there. |

**Limit:** up to 20 service principal federation policies per service principal.

**How to configure it — three equivalent paths:**

<details>
<summary><strong>Databricks UI</strong></summary>

1. Sign in to the account console (`https://accounts.azuredatabricks.net`) as an account admin.
2. **User management** → **Service principals** tab → select the service principal.
3. **Credentials & secrets** tab → **Federation policies** → **Create policy**.
4. Select a federated credential provider, fill in the fields, **Create policy**.
</details>

<details>
<summary><strong>Databricks CLI</strong></summary>

> Not usable from the workspace web terminal — needs a real CLI install.

```bash
# 1. Authenticate as an account admin
databricks auth login --host ${ACCOUNT_CONSOLE_URL} --account-id ${ACCOUNT_ID}

# 2. Look up the service principal's numeric ID from its application ID (GUID)
databricks account service-principals list --filter 'applicationId eq "<service-principal-application-id>"'

# 3. Create the federation policy (example: GitHub Actions)
databricks account service-principal-federation-policy create ${SERVICE_PRINCIPAL_NUMERIC_ID} --json \
'{
  "oidc_policy": {
    "issuer": "https://token.actions.githubusercontent.com",
    "audiences": ["https://github.com/my-github-org"],
    "subject": "repo:my-github-org/my-repo:environment:prod"
  }
}'
```
</details>

<details>
<summary><strong>Databricks Account REST API</strong></summary>

```bash
curl --request POST \
  --header "Authorization: Bearer $TOKEN" \
  "${ACCOUNT_CONSOLE_URL}/api/2.0/accounts/${ACCOUNT_ID}/servicePrincipals/${SERVICE_PRINCIPAL_NUMERIC_ID}/federationPolicies" \
  --data '{
    "oidc_policy": {
      "issuer": "https://token.actions.githubusercontent.com",
      "audiences": ["https://github.com/my-github-org"],
      "subject": "repo:my-github-org/my-repo:environment:prod"
    }
  }'
```

Get the service principal's numeric ID from the console or the Service Principals API first. Full reference: the **Account Federation Policy API**.
</details>

### 4.4 Example federation policies by tool

| Tool | Federation policy | Example matching token |
| --- | --- | --- |
| **GitHub Actions** | Issuer: `https://token.actions.githubusercontent.com` · Audience: `https://github.com/<org>` · Subject: `repo:<org>/<repo>:environment:prod` | `{"iss": "...githubusercontent.com", "aud": "https://github.com/<org>", "sub": "repo:<org>/<repo>:environment:prod"}` |
| **Kubernetes** | Issuer: `https://kubernetes.default.svc` · Audience: `https://kubernetes.default.svc` · Subject: `system:serviceaccount:namespace:serviceaccountname` · JWKS JSON provided directly | `{"iss": "https://kubernetes.default.svc", "aud": ["https://kubernetes.default.svc"], "sub": "system:serviceaccount:namespace:serviceaccountname"}` |
| **Azure DevOps** | Issuer: `https://vstoken.dev.azure.com/<org_id>` · Audience: `api://AzureADTokenExchange` · Subject: `sc://my-org/my-project/my-connection` | `{"iss": "https://vstoken.dev.azure.com/<org_id>", "aud": "api://AzureADTokenExchange", "sub": "sc://my-org/my-project/my-connection"}` |
| **GitLab** | Issuer: `https://gitlab.example.com` · Audience: `https://gitlab.example.com` · Subject: `project_path:my-group/my-project:...` | `{"iss": "https://gitlab.example.com", "aud": "https://gitlab.example.com", "sub": "project_path:my-group/my-project:..."}` |
| **CircleCI** | Issuer: `https://oidc.circleci.com/org/<org_id>` · Audience: `<org_id>` · Subject: `7cc1d11b-...` · **Subject claim:** `oidc.circleci.com/project-id` *(non-default — a real example of overriding the subject claim)* | `{"iss": "https://oidc.circleci.com/org/<org_id>", "aud": "<org_id>", "oidc.circleci.com/project-id": "7cc1d11b-..."}` |
| **AWS IAM Outbound Identity Federation** | Issuer: `https://<uuid>.tokens.sts.global.api.aws` (account-specific) · Audience: `databricks` (or agreed value) · Subject: `arn:aws:iam::<account>:role/<role-name>` | `{"iss": "https://<uuid>.tokens.sts.global.api.aws", "aud": ["databricks"], "sub": "arn:aws:iam::123456789012:role/my-workload-role"}` |

> For AWS IAM federation, the `sub` claim is the calling workload's IAM role ARN (Lambda execution role, ECS task role, EC2 instance role, etc.).

### 4.5 Best practices for federation policies

Each service principal caps out at **20 federation policies** — these practices keep you well under that:

- **Map one external identity per service principal.** Create a dedicated service principal per distinct external workload identity. Multiple policies on one service principal are appropriate *only* when the same logical identity authenticates through different providers (e.g. one workload running in both GitHub Actions and Azure DevOps → two policies, one service principal). Don't map *different* workloads (e.g. separate Kubernetes pods per region) onto a single service principal — give each its own, so audit-log attribution stays clean and you can revoke one without affecting others.
- **Streamline permissions with groups.** When several service principals need the same permissions, add them to a Databricks group and assign permissions there instead of per-principal.
- **Use `subject_claim` only when you must.** Default is the `sub` claim. Override only if your IdP doesn't use `sub` as a stable workload identifier (the CircleCI row in §4.4 is the textbook case).

### 4.6 Configuring an account-wide federation policy

An **account federation policy** lets *every* user/service principal in the account authenticate with IdP tokens. It specifies the issuer and how to map a token to the corresponding Databricks identity.

Example — a policy with issuer `https://idp.mycompany.com/oidc`, audience `databricks`, subject claim `sub` accepts:
```json
{ "iss": "https://idp.mycompany.com/oidc", "aud": "databricks", "sub": "username@mycompany.com" }
```
...authenticating as `username@mycompany.com`.

**Fields:**

| Field | Description |
| --- | --- |
| **Issuer URL** | HTTPS URL identifying your IdP, matched against `iss`. |
| **Audiences** | Matched against `aud`; any match counts. Defaults to your account ID if unset. |
| **Subject claim** | Which claim holds the Databricks username the token was issued for. Defaults to `sub`. |
| **Token signature validation** *(optional)* | Same JWKS JSON/URI mechanism as §4.3. |

> ⚠️ **Important:** for account-wide federation, only register IdPs **fully managed and trusted by your organization** (e.g. your own company IdP). Never configure it against external IdPs you don't control (customers, partners, etc.).

**Configuring it** — same three paths as workload policies:

- **UI:** Account console → **Security** → **Authentication** tab → **Federation policies** → **Create policy** → enter issuer, audiences, subject claim, optional key validation.
- **CLI:**
  ```bash
  databricks auth login --host ${ACCOUNT_CONSOLE_URL} --account-id ${ACCOUNT_ID}
  databricks account federation-policy create --json \
  '{
    "oidc_policy": {
      "issuer": "https://idp.mycompany.com/oidc",
      "audiences": ["databricks"],
      "subject_claim": "sub"
    }
  }'
  ```
- **REST API:**
  ```bash
  curl --request POST \
    --header "Authorization: Bearer $TOKEN" \
    "${ACCOUNT_CONSOLE_URL}/api/2.0/accounts/${ACCOUNT_ID}/federationPolicies" \
    --data '{"oidc_policy": {"issuer": "https://idp.mycompany.com/oidc", "audiences": ["databricks"], "subject_claim": "sub"}}'
  ```
  (Full reference: the Account Federation Policy API.)

**Example account federation policies:**

| Federation policy | Example matching token |
| --- | --- |
| Issuer + audience only | `{"iss": "...", "aud": "2ff814a6-...", "sub": "username@mycompany.com"}` |
| + custom subject claim `preferred_username` | `{"iss": "...", "aud": ["2ff814a6-...", "other-audience"], "preferred_username": "username@mycompany.com", "sub": "ignored-value"}` |
| + inline JWKS JSON | Same token, signature verified against the embedded public key |
| + JWKS URI | Same token, signature verified against keys fetched from the URI |

### 4.7 After configuring a federation policy

1. Configure your IdP to actually issue tokens your users/workloads can exchange (consult the IdP's own docs; for common CI/CD providers see "Enable workload identity federation in CI/CD").
2. In your code: exchange the IdP JWT for a Databricks OAuth token, then send that OAuth token in the `Authorization: Bearer` header. The JWT must be valid and signed with **RS256** or **ES256**.

---

## 5. Personal access tokens (legacy)

### 5.1 Overview and limits

Personal access tokens (**PATs**) authenticate to Databricks resources and APIs **at the workspace level only** (never account-level). Key facts:

- Store them in environment variables or a `.databrickscfg` configuration profile.
- **One PAT works for exactly one workspace.**
- A user can hold **up to 600 PATs per workspace**.
- Databricks **auto-revokes any PAT unused for 90 days**.
- ⚠️ **Databricks recommends OAuth over PATs where possible** — OAuth provides stronger security. (See §4 for OAuth token federation, or "Authorize user access to Azure Databricks with OAuth" for interactive OAuth.)
- **PATs cannot drive account-level functionality.** For account-level automation, use the Entra ID tokens of Databricks account admins (who can be users or service principals) instead.

### 5.2 Creating a PAT as a workspace user

1. In the workspace, click your username (top bar) → **Settings**.
2. **Developer** tab.
3. Next to **Access tokens**, click **Manage**.
4. **Generate new token.**
5. Name it (for your own future reference).
6. Set a **lifetime in days**.
7. Pick a **scope type**: **BI Tools** (for Tableau, Power BI, etc. connecting to SQL warehouses) or **Other APIs** (choose scopes manually).
8. Optionally toggle **Auto-scope tokens** (see §5.3).
9. **Generate**, then **copy the token immediately and store it securely** — it's shown once. Losing it means creating a new one.

If token creation/use is blocked, your workspace admin may have disabled tokens or not granted permission — that's an admin-side setting (`Enable or disable personal access token authentication for the workspace`, `Personal access token permissions`).

### 5.3 Scoped tokens and auto-scoping

**Scoped tokens** restrict a PAT to specific API operations instead of granting full workspace access — e.g. `sql`, `unity-catalog`, `scim` scopes. Set the scope type and add scopes at creation time in the UI.

> ⚠️ **Warning:** a token with the `authentication` scope can create *new* tokens with *any* scope. Grant it only to tokens that genuinely need to manage other tokens.

**Auto-scoping** automatically narrows a token's permissions down to only the APIs it's actually observed using:

- Databricks watches API usage over a **30-day period**, then applies the inferred scopes.
- Applies to new long-lived tokens (30+ days) and to existing all-APIs tokens.
- You get a reminder email **7 days before enforcement**, and another when scopes are actually applied.
- **To opt out:** manually set scopes yourself (UI: *Settings → Developer → Access Tokens → Manage → Update token*, or `PATCH /api/2.0/token/{token_id_sha256}`). Once you set scopes manually, auto-scoping is **permanently disabled** for that token.

### 5.4 Creating a PAT for a service principal

A service principal can mint a PAT for itself via the CLI:

```bash
databricks tokens create \
  --lifetime-seconds <lifetime-seconds> \
  -p <profile-name>
```

| Placeholder | Meaning |
| --- | --- |
| `<lifetime-seconds>` | Token lifetime, e.g. `86400` for 1 day. Defaults to the workspace maximum (typically 730 days). |
| `<profile-name>` | Which configuration profile's auth info to use. Defaults to `DEFAULT`. |

Copy `token_value` from the response — that's the access token. Save it securely; it's unrecoverable if lost.

### 5.5 Using a PAT to authenticate

To configure PAT authentication you always need exactly two things — a workspace host and the token itself — expressed differently per tool:

| What you need | Value |
| --- | --- |
| **Host** | Your per-workspace URL, e.g. `https://adb-1234567890123456.7.azuredatabricks.net` |
| **Token** | The PAT string for your Databricks user account |

**Environment variables:**
```bash
DATABRICKS_HOST=https://adb-1234567890123456.7.azuredatabricks.net
DATABRICKS_TOKEN=<token>
```

**`.databrickscfg` profile:**
```ini
[<some-unique-configuration-profile-name>]
host  = <workspace-url>
token = <token>
```
Or generate that profile interactively instead of hand-editing it:
```bash
databricks configure --profile DEFAULT
# Prompts: Databricks Host → your workspace URL
#          Personal Access Token → your PAT
```
> Note: this **overwrites** an existing `DEFAULT` profile. Check first with `databricks auth env --profile DEFAULT`, and pass a different `--profile <name>` if you need to preserve an existing one.

**Databricks CLI directly:** run `databricks configure` and answer the same two prompts (host, token).

**Databricks Connect:** PAT auth is supported for Python/Scala on Databricks Connect for Runtime 13.3 LTS+. Set it up the same way as the profile above, but with cluster selection added:
```bash
databricks configure \
  --configure-cluster \
  --profile DEFAULT
```
(host prompt → token prompt → pick your target cluster from the list, filterable by name.)

### 5.6 Managing PATs via the REST API

**Issue a new PAT** (`POST /api/2.0/token/create`) using an *existing* valid PAT that has permission to create tokens:

```bash
curl -X POST https://<databricks-instance>/api/2.0/token/create \
  -H "Authorization: Bearer <your-existing-access-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "lifetime_seconds": <lifetime-seconds>,
    "scopes": ["sql", "authentication"],
    "autoscope_enabled": true
  }'
```

Response:
```json
{
  "token_value": "<your-newly-issued-pat>",
  "token_info": {
    "token_id": "<token-id>",
    "creation_time": "<creation-timestamp>",
    "expiry_time": "<expiry-timestamp>",
    "comment": "<comment>",
    "scopes": ["authentication", "sql"],
    "last_accessed_time": 0
  }
}
```

**Use the new token** on subsequent calls:
```bash
curl -X GET "https://<databricks-instance>/api/2.0/<path-to-endpoint>" \
     -H "Authorization: Bearer <your-new-pat>"
```
```python
import requests
headers = {'Authorization': 'Bearer <your-new-pat>'}
response = requests.get('https://<databricks-instance>/api/2.0/<path-to-endpoint>', headers=headers)
```
*(This is essentially the exact pattern `contact_app`'s `nationbuilder_contacts.py` and `app.py` already follow — just against Databricks instead of NationBuilder.)*

**Update a token's scopes** (`PATCH /api/2.0/token/<token_id>`) — requires the *calling* token to have the `authentication` scope:
```bash
curl -X PATCH https://<databricks-instance>/api/2.0/token/<token_id> \
  -H "Authorization: Bearer <your-existing-access-token>" \
  -H "Content-Type: application/json" \
  -d '{"token": {"scopes": ["sql", "unity-catalog"]}, "update_mask": "scopes"}'
```
Scope changes can take **up to 10 minutes** to propagate. See all available scopes via `GET /api/2.0/token-scopes`.

---

## 6. Unified authentication

### 6.1 What it is

**Unified authentication** is Databricks' umbrella term for one consistent configuration approach across all supported tools and SDKs (CLI, Terraform provider, Python/Java/Go SDKs). Set standard environment variables and/or a configuration profile once, and every compliant tool picks them up automatically — no per-tool reconfiguration.

By account type:

| Account type | How OAuth is handled |
| --- | --- |
| **User** | OAuth automatically creates and manages access tokens for any unified-auth-aware tool. |
| **Service principal** | OAuth requires client credentials (client ID + secret), issued once the service principal is assigned to workspaces. |

**Prerequisite either way:** an admin must grant your user/service principal the **`CAN USE`** permission on the account/workspace features your code touches — a valid token alone isn't sufficient.

### 6.2 Full environment variable / field reference

Each row below has four possible representations: an **environment variable** (shell), a **`.databrickscfg` field** (config profile), a **Terraform field**, and a **Config field** (programmatic SDK config) — same setting, different surface depending on which tool reads it.

**General configuration**

| Common name | Description | Env var | `.databrickscfg` / Terraform field | Config field |
| --- | --- | --- | --- | --- |
| Databricks host | The workspace or accounts endpoint URL | `DATABRICKS_HOST` | `host` | `host` (Python) / `setHost` (Java) / `Host` (Go) |
| Databricks token | A PAT or Microsoft Entra ID token | `DATABRICKS_TOKEN` | `token` | `token` (Python) / `setToken` (Java) / `Token` (Go) |
| Databricks account ID | Account ID; only takes effect when host = `https://accounts.azuredatabricks.net` | `DATABRICKS_ACCOUNT_ID` | `account_id` | `account_id` (Python) / `setAccountID` (Java) / `AccountID` (Go) |
| Cluster ID | Target cluster | `DATABRICKS_CLUSTER_ID` | `cluster_id` | `cluster_id` |
| Serverless compute | Auto-enablement (`auto`) | `DATABRICKS_SERVERLESS_COMPUTE_ID` | `serverless_compute_id` | `serverless_compute_id` |

**Azure-specific fields**

| Common name | Description | Env var | Terraform field | Config field |
| --- | --- | --- | --- | --- |
| Azure client ID | Entra ID service principal's application ID (managed identity / Entra SP auth) | `ARM_CLIENT_ID` | `azure_client_id` | `azure_client_id` (Python) / `setAzureClientID` (Java) / `AzureClientID` (Go) |
| Azure client secret | Entra ID service principal's client secret | `ARM_CLIENT_SECRET` | `azure_client_secret` | `azure_client_secret` (Python) / `setAzureClientSecret` (Java) / `AzureClientSecret` (Go) |
| Client ID | Databricks/Entra managed service principal's client ID (OAuth M2M) | `DATABRICKS_CLIENT_ID` | `client_id` | `client_id` (Python) / `setClientId` (Java) / `ClientId` (Go) |
| Client secret | Same principal's client secret (OAuth M2M) | `DATABRICKS_CLIENT_SECRET` | `client_secret` | `client_secret` (Python) / `setClientSecret` (Java) / `ClientSecret` (Go) |
| Azure environment | Azure cloud environment; defaults to `PUBLIC` | `ARM_ENVIRONMENT` | `azure_environment` | `azure_environment` (Python) / `setAzureEnvironment` (Java) / `AzureEnvironment` (Go) |
| Azure tenant ID | Entra ID service principal's tenant ID | `ARM_TENANT_ID` | `azure_tenant_id` | `azure_tenant_id` (Python) / `setAzureTenantID` (Java) / `AzureTenantID` (Go) |
| Azure use MSI | `true` to use Azure Managed Service Identity passwordless auth (requires resource ID too) | `ARM_USE_MSI` | `azure_use_msi` | `AzureUseMSI` (Go) |
| Azure resource ID | Azure Resource Manager ID for the workspace | `DATABRICKS_AZURE_RESOURCE_ID` | `azure_workspace_resource_id` | `azure_workspace_resource_id` (Python) / `setAzureResourceID` (Java) / `AzureResourceID` (Go) |

**`.databrickscfg`-specific fields**

| Common name | Description | Env var | Terraform field | Config field |
| --- | --- | --- | --- | --- |
| Config file path | Non-default path to `.databrickscfg` | `DATABRICKS_CONFIG_FILE` | `config_file` | `config_file` (Python) / `setConfigFile` (Java) / `ConfigFile` (Go) |
| Default profile | Named profile to use, other than `DEFAULT` | `DATABRICKS_CONFIG_PROFILE` | `profile` | `profile` (Python) / `setProfile` (Java) / `Profile` (Go) |

**Authentication-forcing fields**

| Common name | Description | Env var | Terraform field | Config field |
| --- | --- | --- | --- | --- |
| Auth type | Force a specific auth type when multiple are available in the environment (see §6.3) | `DATABRICKS_AUTH_TYPE` | `auth_type` | `auth_type` (Python) / `setAuthType` (Java) / `AuthType` (Go) |
| OIDC token env var name | Name of the env var holding your IdP-issued OIDC token; used with `env-oidc`. Defaults to `DATABRICKS_OIDC_TOKEN` | `DATABRICKS_OIDC_TOKEN_ENV` | `oidc_token_env` | `oidc_token_env` (Python) / `setOIDCTokenEnv` (Java) / `OIDCTokenEnv` (Go) |
| OIDC token file path | Path to a local file holding your IdP-issued OIDC token; used with `file-oidc` | `DATABRICKS_OIDC_TOKEN_FILEPATH` | `oidc_token_filepath` | `oidc_token_filepath` (Python) / `setOIDCTokenFilepath` (Java) / `OIDCTokenFilepath` (Go) |

### 6.3 Supported authentication types (`auth_type`)

Use `DATABRICKS_AUTH_TYPE` (or the equivalent field) to force a specific method when the environment has more than one set of credentials available:

| Value | Meaning |
| --- | --- |
| `oauth-m2m` | Machine-to-machine OAuth 2.0 with a Databricks service principal |
| `pat` | Personal access token (§5) |
| `databricks-cli` | Interactive CLI sign-in via OAuth 2.0 |
| `oidc-token` | Token federation — Databricks exchanges an IdP OIDC token for a Databricks OAuth token (§4) |
| `env-oidc` | Federation where the IdP token lives in an env var (`DATABRICKS_OIDC_TOKEN`) |
| `file-oidc` | Federation where the IdP token lives in a local file (`DATABRICKS_OIDC_TOKEN_FILEPATH`) |
| `github-oidc` | GitHub Actions federated auth via OIDC |
| `azure-devops-oidc` | Azure DevOps federated auth via OIDC |
| `azure-msi` | Azure Managed Service Identity |
| `azure-client-secret` | Azure service principal with a client secret |

### 6.4 Configuration profiles

A **configuration profile** bundles settings + credentials for Databricks tools/SDKs to read, stored in a local file — conventionally **`.databrickscfg`** — so CLI, SDKs, scripts, and apps all reuse the same stored config instead of re-entering it per tool. See §5.5 for the PAT-specific version of this file.

---

## 7. Third-party integrations

| Tool | Auth mechanism |
| --- | --- |
| **Databricks Terraform Provider** | Your Databricks **user account** |
| **Git providers** (GitHub, GitLab, Bitbucket) | A **Databricks service principal** |
| **Jenkins** | A **Databricks service principal** |
| **Azure DevOps** | An **MS Entra ID–based service principal** |

---

## 8. Quick-reference cheat sheet

- **Two account types:** user (interactive) vs. service principal (automated).
- **Two API tiers:** account-level (admin, account console URL) vs. workspace-level (workspace users/admins, workspace URL) — **PATs only ever reach workspace-level**.
- **Default recommendation for anything new:** OAuth token federation — no Databricks secret to manage or rotate.
- **Automated/CI pipeline, no existing token?** → OAuth M2M (or token federation) with a service principal.
- **Interactive human login?** → OAuth U2M.
- **Already have / just need a simple long-lived token for local dev or a script?** → Personal access token — workspace-scoped, up to 600 per user, auto-revoked after 90 days idle, capped at 20 minutes for scope-change propagation.
- **Four core unified-auth env vars:** `DATABRICKS_HOST`, `DATABRICKS_TOKEN` (or `DATABRICKS_ACCOUNT_ID` for account ops), `DATABRICKS_CLIENT_ID` + `DATABRICKS_CLIENT_SECRET` (service principals only).
- **Store credentials in:** a `.databrickscfg` configuration profile (one setup, reused everywhere) or plain environment variables / `.env`.
- **Permission gate:** even with a valid token, you also need `CAN USE` granted on the specific account/workspace features you're calling.
- **Federation policy limit:** 20 per service principal, and 20 account-wide federation policies per account.

---

## Source note

This guide reorganizes five Azure Databricks documentation pages (as saved in [databricks.md](databricks.md) in this repo): the authorization overview, the OAuth token federation overview, the federation policy configuration guide, the legacy personal access tokens guide, and the unified authentication environment-variable/field reference. All information from those pages is retained here — only the structure changed (grouped by topic, tabled where the source used prose, cross-referenced between sections).

For `contact_app` specifically: your `app.py` authenticates via `WorkspaceClient(host=os.getenv("DATABRICKS_HOST"), token=os.getenv("DATABRICKS_TOKEN"))` — that's straight PAT auth (§5.5, environment-variable form). Nothing else in this guide is required to get the app working; §4 (OAuth token federation) is background for if/when you want to move off a static token later.
