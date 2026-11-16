# Computer Connect: linking a computer to a MeasuredField account (MF-032)

Status: implemented and integration tested locally on PostgreSQL. Not deployed. No desktop
app talks to it yet; that client is the next story. No tunnel, no ChatGPT account link.

## What problem this solves

The local companion (`app/local_connect`, `app/paired_connect`, MF-027 to MF-031) lets an
owner share chosen folders with an assistant on one Windows user account. It has no idea
which MeasuredField business or person that computer belongs to, and nothing on the server
can see it or switch it off. MF-032 adds that server-side half:

| Owner sees | What happens |
|---|---|
| **Connect this computer** → name it, choose *Read only* or *Suggest changes* | A single-use code is created (10 minutes, stored as a hash). |
| Types the code on the computer | The computer receives its credential once and a six-digit check number. It is `pending` and can do nothing but ask its status. |
| Enters the check number in the workspace | The computer becomes `active`. |
| **Disconnect now** | The credential is revoked; the next request from that computer is refused. |
| *What happened on your computers* | A summary the computer reports: what kind of action, which named project, how many, result. |

Owner page: `/app/computers` (`app/templates/app/computers.html`). Service:
`app/services/devices.py`. Routes: `app/routers/devices.py`. Schema: migration `0006`.

## Device API

All under `/api/device/v1`, JSON, `Authorization: Bearer <credential>` except `pair`.

| Method and path | Who | Purpose |
|---|---|---|
| `POST /pair` `{code, device_name, platform}` | anyone holding a code | Returns `{device_id, credential, check_number, confirm_by, status: "pending"}` once. |
| `GET /status` | pending or active | `{status, access, access_label, business, name}` |
| `POST /heartbeat` | active | Marks the computer as seen. |
| `POST /rotate` | active | New credential; the old one stops working in the same transaction. |
| `POST /disconnect` | pending or active | The computer disconnects itself. |
| `POST /activity` `{events: [...]}` | active | Up to 50 summary events. |

Request bodies are typed Pydantic models with `extra="forbid"`: unknown fields are rejected.

## Security controls and where they are tested

`tests/test_pg_devices.py` (11 tests, real PostgreSQL).

| Control | Detail |
|---|---|
| Identity of the person | Codes are created only from a signed-in workspace session by an owner or admin (`devices.manage`), with CSRF. Staff and read-only members can view but not connect or disconnect. |
| Identity of the computer | A random credential `mfd1.<org>.<secret>` (256 bits), returned once, stored as SHA-256. The `<org>` part only selects the tenant to look in; the row is found by the hash of the whole value, so a credential replayed with another organization finds nothing. |
| A stolen or guessed code is not enough | The redeeming computer stays pending until the owner types the number shown **on that computer**. Five wrong numbers, or ten minutes, end the attempt permanently. |
| Code guessing | 30^8 codes, 10-minute life, single use, at most 5 open per business; 10 attempts per client and 600 in total per 10 minutes; every failure returns the same answer. |
| Revocation | Every device request re-reads the row under the tenant's row-level security. Disconnect replaces the stored hash, so a later copy of the database holds nothing matching the old credential. |
| Rotation | `/rotate` replaces the credential atomically. |
| Tenant isolation | `connected_devices` and `device_events` have forced row-level security. Business A's owner gets 404 for B's computer, code and pending request; A's export and activity contain nothing of B's; the database refuses a planted cross-tenant row. |
| People and computers stay separate | Device routes ignore cookies. A device credential is not a session and opens no page or workspace API. Device routes do not exist on customer-site hostnames. |
| Least privilege | *Read only* computers cannot report a change as made. The server grants no file access at all; folder choices and approvals stay on the computer. |
| No file data on the server | Activity accepts a fixed set of actions and results, a project name of up to 80 characters and a count. Anything containing `\`, `/`, `:` or control characters is refused, so file locations cannot be reported. There is no content field. |
| Secrets in logs | Request logs record the path only. Credentials travel in a header, never in a URL. Pair and rotate responses are `no-store`. Audit events carry no credential, code or check number. |
| Deletion | Deleting a business removes its computers, events and unused codes. |

## Standards and what was relied on

* The flow is modelled on the shape of the OAuth 2.0 Device Authorization Grant (RFC 8628):
  a short user code entered on a second device, with approval by a signed-in user. It is
  **not** an implementation of RFC 8628 and issues no OAuth tokens. It was chosen because
  the first-party session already authenticates the owner and no external identity provider
  is needed to link a computer to an account.
* The check number is a number-matching confirmation: the approving person must enter a
  value shown on the device being approved, which defeats approval of a device they cannot
  see.
* No OpenAI, MCP or Auth0 behaviour is relied on here, and none of their documentation was
  consulted for this story. Connecting ChatGPT or another assistant to a paired computer is
  MF-013 / MF-024 and must be designed against their current official documentation.

## What this does not do yet

1. **No desktop client.** Nothing in `app/local_connect_ui` calls this API. The companion
   still needs: a "Connect this computer" screen that takes the code and shows the check
   number, credential storage protected by Windows DPAPI (the current pairing secret is a
   plain file; `os.chmod` does not restrict access on Windows), status polling, activity
   reporting, and local shutdown of sharing when the server says `revoked`.
2. **Server-side access level is advisory to the computer.** The server records *Read only*
   or *Suggest changes* and refuses contradictory activity reports, but enforcement of what
   an assistant can do with files happens on the computer.
3. **Activity is self-reported.** A modified client could report anything within the schema.
   It is a convenience for the owner, not evidence.
4. **A credential is a bearer secret.** Anyone who copies it from the computer can act as
   that computer until it is rotated or disconnected. Binding it to a device-held key
   (proof of possession) is the stronger design and is not built.
5. **No step-up authentication.** Connecting a computer needs only a current session.
   Requiring a fresh password or a second factor first is recommended before release.
6. **Rate limits depend on the client address**, which is unreliable behind a proxy until
   `MF_TRUSTED_PROXY_HOPS` is measured on the real deployment (see `STAGING_RUNBOOK.md`).
7. **Not deployed, not load tested, and not reviewed by a person outside this team.**

## Next independently executable story

Desktop client for this API inside the existing Connected Workspace window: enter code,
show check number, store the credential with DPAPI, poll status, stop sharing on `revoked`,
report activity. It needs no external account and can be tested against a local server.
