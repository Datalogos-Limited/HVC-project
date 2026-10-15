# Review of the deployed Dream Car Brokers frontend

Reviewed 15 October 2026 from the public Netlify deployment. This is a review of publicly served HTML and JavaScript, not the private repository, Netlify configuration, build pipeline or server-side functions. No server-side source was supplied. Absence of a mechanism in these files is not proof that no separate backend exists.

| Evidence | Observed behaviour | Required change |
|---|---|---|
| `assets/store.js`, `load/persist`, `loadDocs/persistDocs` | Vehicles and document metadata use `dcb_vehicles_v3` and `dcb_docs_v1` in localStorage, with embedded example records. Empty vehicle arrays fall back to seeds. | Replace authoritative browser storage with authenticated API reads/writes. Never silently replace an empty real portfolio with demonstration data. |
| `assets/vault.js`, `saveDoc` | Files up to 1 MiB become data URLs in localStorage; larger files are saved as metadata with no digital copy. | Upload actual bytes to the correct vault, validate length/checksum, quarantine, scan, and expose status. |
| `assets/vault.js`, `renderDocs/removeDoc` | Download is a data URL; removal deletes local metadata. | Server-authorised downloads; implement retention-aware archival, legal hold and deletion workflow before offering removal. |
| `login.html`, `handleLogin` | Calls Netlify Identity password login and redirects on success; also mutates `netlifyIdentity.store.user`. | Migrate to Identity Platform SDK with email verification, recovery and MFA; do not access private SDK state. Migration requires an agreed account transition/reset process. |
| `assets/chrome.js`, `renderChrome` | Displays Kiran and Principal—Administrator as fixed text. | Render `/v1/me` and approved role labels. Enforce permission at every API operation. |
| `assets/clients.js`, `PROFILES` | Client rows derive from vehicle client-name strings; selected profiles are hard-coded. Encumbered vehicles drive a label called compliance reviews. | Store first-class client IDs/entities. KYC/AML reviews must come from compliance cases and review dates; title encumbrance is a different signal. |
| `assets/register.js`, `saveAsset/nextId` | Browser increments DCB display IDs, uses floats for prices and accepts free-text client names. | Server UUIDs, separate display IDs, stable client references, integer minor units, recorded currencies and versioning. Preserve short historical chassis numbers. |
| `assets/concierge.js` | Requests persist in localStorage. | Add a case-management service/API in the next phase; no concierge persistence is implemented in this starter. |
| `assets/reports.js`, `assets/overview.js` | Computes metrics in browser; reports and market content use prototype/local data. | Authorised aggregate read models and provenance/as-of dates. Do not relabel illustrative market data as live. |
| `assets/register.js`, `exportCSV` | Escapes quotes but allows spreadsheet formula prefixes in user-entered fields. | Neutralise spreadsheet formulas on export and enforce export permissions/audit. |
| Several page scripts | Build HTML strings with `innerHTML`; many fields use `DCB.esc`. | Prefer `textContent`/DOM APIs and context-aware encoding; do not assume HTML escaping is safe for URLs or inline JavaScript. Add CSP and remove inline handlers. |

## Integration mapping

The included `frontend/api-client.js` is an ES module integration helper, not a patched copy of the live site. Existing page scripts must become asynchronous:

```js
import { VaultAPI } from './api-client.js';
// auth comes from the configured Firebase Auth SDK. Set in-memory persistence.
const api = new VaultAPI(hosts, () => auth.currentUser.getIdToken());
const assets = await api.assets();
// Map {id, client_id, data:{...}} into the page view model.
// Use an approved client UUID instead of a client name when registering an asset.
```

Use the Firebase Auth SDK for email verification, password reset, token renewal and MFA. The included backend sign-up/sign-in REST endpoints cover basic password auth only. A provider MFA challenge deliberately does not return a successful token from that basic endpoint; staff use the SDK's MFA resolver. Keep tokens in memory and call the SDK's `signOut` to end the local session; an authorised operator can revoke all sessions when needed. Signup grants no tenant or vault role. An authorised operator approves membership separately.

`DCB.load()` becomes `await api.assets()`; `DCB.persist()` becomes a specific create/update API, not a bulk overwrite. `loadDocs()` becomes a paginated list scoped to a parent record. Replace synchronous filters with a loaded view model; preserve formatting utilities only. Map pounds to pence precisely (decimal-string parser), never multiply arbitrary binary floats for settlement. Display purchase/valuation only in their recorded currency; cross-currency ROI needs a dated FX basis and is not implemented here.

A UI can call `upload(vault, parentId, file, category, stableOperationKey)`, then poll document metadata. Show `quarantined` until an authorised scanner job has marked it clean. A 423 response means no download yet. A lost upload response can be reconciled with the metadata endpoint; creating the same metadata with the same key is safe.

Read/list/create are implemented. Edit, deletion, reporting, market feeds, concierge, invitations and document sharing remain follow-on work; hide those controls until their backend workflows exist. No changes have been made to the live Netlify site.
