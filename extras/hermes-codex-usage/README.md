# Hermes credential visibility and read-only Codex quotas

**Source candidate, not an installation or live-acceptance claim.** The bridge
publishes sanitized credential identities for the managed Agents clone. Only
Codex has a supported quota reader; visibility is not token-history attribution.

## Inventory and identity contract

- Read the **persisted `credential_pool`** associated with the explicitly selected
  Hermes home. `--hermes-home` overrides `HERMES_HOME`; otherwise the CLI uses
  `$HOME/.hermes`. Supply the intended profile explicitly when invoking this
  standalone helper outside Hermes.
- Discover all provider slices in that pool, with the maintained per-provider
  profile/global fallback: a nonempty profile slice shadows the corresponding
  global slice; an empty or missing slice inherits it. Native-root suffix and
  external `profiles/<name>` layouts are recognized when deriving the global
  fallback from the selected home.
- This is not discovery of every environment-only key, legacy auth layout or
  external credential store. No auth library is imported: a loader that creates
  corrupt-store backups or migrates credentials is not a read-only inventory API.
- Each input auth snapshot is at most **4 MiB**. The merged input is bounded to
  **256 credential rows**, including at most **8 Codex rows**, before identity
  deduplication. Invalid/oversized inventories are refused, not silently truncated.
  Private display/publication reads are capped at **512 KiB**.
- Codex identities hash the JWT workspace and subject hints. The server still
  authenticates the fixed-host quota request; unverified claims grant no authority.
  Duplicate rows for a principal produce one card, independent of pool order.
- Other providers use a hash of provider plus persisted credential ID. Without
  that ID, a private material fingerprint is the fallback and can change when
  the material or metadata changes. Labels and row positions are not account IDs.

Scanner filenames are `codex-hermes-<64-hex-principal>.json` or
`hermes-credential-<64-hex-identity>.json`. Display names use a known provider label
and short opaque suffix, never an email, custom endpoint or credential label.
Unknown/plugin provider names are represented as **Other provider** rather than
copying a potentially private provider key into the UI.

## Quotas, token counters and controls

The existing bounded Codex quota GET is the only supported network path. Other
credential types remain visible with empty limits and no fabricated token counters.
The panel omits their default **Quota unavailable** placeholder; the raw unsupported
status remains in the published record for diagnostics. Their provider URLs are
not probed. Expired/auth/network/quota failures stay distinct from scanner errors
and meaningful actionable errors remain visible.
The limits-first panel also gates Hermes quotas on its independently ticking
clock: samples flagged stale, older than two validated refresh intervals, or
past any window reset are withheld until another measurement. A short **Quota
stale** note can fill a silent status for a previously fetched sample; never-measured
quota states add no default note, and existing collector errors are not duplicated.
Authenticated sample bytes remain unchanged, and a fresh measured zero remains
valid. The guard works with Qt sequence delegates and does not depend on native
Grok support.

Every bridge card is read-only. The generated consumer reconstructs safe fields,
removes native account switching/renaming/sign-in actions, and keeps native
providers' normal controls unchanged. Neither this bridge nor its UI refreshes
credentials, chooses a login or registers a native account.

Quota windows do not establish token usage. Native provider/account counters,
explicit-input Hermes accounting, Local sampled counters and observed history
have separate provenance and deduplication rules. No per-credential token total
is inferred from a shared login, session aggregate or quota percentage. See
[`../local-router-stats/HERMES_ACCOUNTING.md`](../local-router-stats/HERMES_ACCOUNTING.md).

## Explicit publication and failure handling

Without `--usage-dir`, the bridge writes only its private display state.
`--initialize` creates an unknown private placeholder without credential discovery
or HTTP; it does **not** populate scanner records. A collecting invocation with
an explicit `--usage-dir` is required for publication and may perform the Codex
quota GET. It is not an offline verification command or authorization to use real
credentials.

The controlled scanner destination contains:

- Per-identity JSON files.
- `.hermes-credential-inventory`: a sanitized identity set and its SHA-256 generation.
- `.hermes-codex-publication`: ownership hashes for the complete published set.
- `.hermes-codex-publication.lock`: the cooperating-writer lock.

The consumer validates inventory shape, bounded membership, canonical digest,
record identity/filename and generation across discovery, local/sync admission
and snapshot export. The generation changes with identity membership, not each
quota sample. Missing, unknown, malformed or mismatched identities fail closed.
These are consistency/admission checks, **not cryptographic authentication of an
arbitrary local writer or remote device**. The QML `FileView` reads JSON before
admission checks: the entry cap is not a bounded file-read guarantee against an
arbitrary local writer.

Files are prepared first; the identity inventory is replaced after member files
and the ownership manifest last. Individual replacements are atomic, not the
entire multi-file batch. Ordinary partial failures use checked rollback. Process
death, conflicting newer edits or incomplete recovery require reconciliation;
there is no force-clobber option. Cleanup removes only records whose ownership
hashes still match. Owned legacy numeric slots can migrate; manual records and
unowned name collisions are preserved, not adopted or broadly deleted.

Reads reject duplicate JSON fields, symlink parents/files, hardlinks, nonregular
files and oversized snapshots. Public records are rebuilt from allowlisted fields;
raw tokens, private IDs, emails, arbitrary provider keys and source errors are not
published. Tokens remain in process memory/private worker input, never request
argv or generated files. The bridge never writes its credential source.

## Offline proof and remaining gates

Use a disposable **parent-process** HOME/HERMES_HOME/XDG before any inventory
lookup, plus per-read fixture confinement. Isolating only a child process is
insufficient: a synthetic profile beneath the real Hermes root can legitimately
fall back to the real global pool. Do not investigate a fixture escape by reading
real stores again. Keep a violating run separate from synthetic proof.

`tests/agents-credentials.py` exercises the actual inventory, producer, explicit
publisher and generated JavaScript consumers with synthetic credentials/mocked
quota transport. It covers reorder/add/remove, mixed unsupported types, profile
shadowing, explicit bounds, ownership, rollback, malformed/no-follow inputs,
filename/generation mismatches, read-only action guards, and fixture escape
refusal before snapshot open. Publication and legacy bridge suites remain
separate checks. `tests/agents-qml.py` supplies instantiated native UI/geometry
and refresh proof under isolated offscreen software rendering.

These checks neither qualify deployed credentials/runtime nor prove a rendered
live popup. Independent review, runtime qualification, guarded installation and
live verification remain separate gates. No model/router/service restart,
billing change, commit or publication follows from a passing fixture.
