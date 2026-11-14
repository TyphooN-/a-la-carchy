# Hermes accounting — explicit adapter and opt-in canonical feed

`hermes_accounting.py` projects canonical Hermes session/model counters into
`hermes-token-accounting/1`. The managed Agents clone reads its private record
from `XDG_STATE_HOME/omarchy/hermes-token-accounting/usage.json`, outside the
native syncable provider directory. It does not create another quota/account
card, enable authentication controls, or add native Codex CLI history.

## Explicit inputs only

The CLI requires `--profile`, `--output`, and exactly one input:

- `--json FILE`: a bounded export with `contract` equal to
  `hermes-session-model-usage/1`, `counterSemantics` equal to
  `hermes-canonical/1`, and `rows` containing canonical `session_model_usage`
  columns. Raw provider-response token fields are not this contract.
- `--sqlite-snapshot FILE`: an explicitly supplied, read-only, standalone
  rollback-mode SQLite snapshot without WAL/SHM/journal sidecars. The adapter
  reads bounded bytes and deserializes them into an in-memory, query-only
  connection. It does not open the input pathname as a database or discover
  the real Hermes database. The bytes come from a no-follow descriptor fenced
  by device/inode/size/time before and after the read; writable files, sidecar
  names (including dangling symlinks), generated/hidden columns and inputs over
  16 MiB are refused with exit 2, leaving the last good output unchanged. There
  is no oversized-database, URI-pathname or `VACUUM INTO` fallback; the in-memory
  connection has an authorizer limited to the fixed schema/usage columns.

The output parent must already exist as an absolute, owned, non-group/other-writable
controlled directory. Every publication mode, including cumulative-only JSON and
SQLite inputs, uses the same one-per-target deterministic private temporary, fsync
and atomic replacement. A nonblocking private target lock serializes cooperating
writers before stale recovery; a busy writer refuses with exit 2. Retried process
death cannot add random temporary names, and a healthy retry reclaims a valid
private stale temporary. Symlinked, hardlinked, nonregular, shared-mode or oversized
reserved obstacles are refused rather than adopted. Cleanup verifies its created
inode; observed target/directory/lock edits are refused. These are advisory locks
and observed preimage pins, not atomic CAS against a non-cooperating same-UID writer.
Legacy random temporary names are not swept or deleted; the new bound covers this
version's reserved per-target namespace, not unknown pre-existing artifacts.

Malformed inputs leave a previously published record unchanged. Error output
is generic, not raw database values. Before reading input or creating any
lock/output, the CLI rejects resolved-path and inode aliases against all relevant
state/export destinations, deterministic temporaries and publication locks (plus
the ledger lock in history mode). Relative names and hardlinks cannot turn an
input into a cleanup target. Use only disposable fixtures until independent review
is complete.

## Counter, provenance and coverage boundaries

Hermes canonical input excludes cache buckets; prompt tokens include canonical
input, cache-read and cache-write. Generated output already includes reasoning,
so reasoning is not added again. This follows the source `CanonicalUsage` contract
in `agent/usage_pricing.py`; it is not a rule for arbitrary provider payloads.
All published sums must be nonnegative safe integers. Duplicate canonical
session/model/provider/base-url/mode/task identities are rejected, not added
again. Public records omit raw session IDs, model names and endpoint URLs;
source IDs derive from the public profile/provider provenance.

The compatibility record publishes **cumulative aggregate counts only**. `today`
and `week` stay unavailable. Optional sampled history is a separate projection;
it must not be presented as complete daily usage. `first_seen` and `last_seen`
never date tokens: old migrations seeded aggregate start/end timestamps. A
canonical cumulative zero is distinct from an unknown dated interval. Counts
remain **unattributed to credentials**; a shared login establishes no event identity.

Local/loopback routes and unsupported or ambiguous provider/endpoint provenance
are excluded conservatively. Supported provider/host mappings are explicit in
`CLOUD_HOSTS`; this candidate does not claim complete coverage of every custom
remote route, profile or credential. IP literals must satisfy that same explicit
provider/host mapping: being globally routable is not Cloud provenance. No IP
literals are currently in the supported mapping. Admission does not resolve DNS
or rewrite retained journals. Local engine samples remain separate.

## Failure and freshness signals

The canonical feed emits a stable, non-data-bearing refusal code, with no raw
exception payload, pathname or usage row. Source-cap exhaustion is `E_SOURCE_CAP`;
main-database and sidecar byte limits are `E_DB_SIZE` and `E_SIDECAR_SIZE`;
selected-row/byte limits are `E_INPUT_LIMIT`; schema drift is `E_SCHEMA`; backward or
conflicting observation time is `E_CLOCK_BACKWARD`; lock contention is
`E_LOCK_BUSY`. Other SQLite, I/O and unclassified validation refusals remain
`E_SQLITE`, `E_IO` and `E_VALIDATION`. Diagnostics do not authorize a history reset.

The Local unified roll-up distinguishes the canonical feed's liveness from its
retained dated facts. An admitted projection older than 600 seconds is reported
as `cloudFeed.state: "stale"`, including when its inode and bytes are unchanged.
The liveness transition publishes on the next collector tick rather than waiting
for a file change. Previously admitted dated totals remain available; staleness
neither erases them nor fabricates fresh zero usage. A fresh admitted projection
restores `admitted` without adding the retained totals again. The maintained
consumer accepts this status and identifies it in its history help text.

The unified dated view is rebuilt only when publication is due, the calendar
changes or feed liveness changes; heartbeat and input-ingest checks still run on
every collector tick. Serialized publication JSON must fit the 4 MiB consumer
transport ceiling. An oversized payload is refused before creating a temporary
file, without truncating history or replacing the last-good destination. The
continuous collector reports a `PublicationLimitError` and retries on a later
tick; a single-shot invocation still fails. This byte gate does not imply that
all possible lifetime-source/calendar combinations fit: the source, cell and
journal capacity limits below remain separate, finite constraints.

## Opt-in observed history (explicit-input backend and private consumer)

Supply **all four** additional arguments to either existing snapshot CLI path:

```text
python3 hermes_accounting.py --json /owned/snapshot.json --profile default \
  --output /owned/accounting.json --history-state /owned/history-state.json \
  --history-output /owned/history.json --observed-at 2024-02-29T15:00:00Z \
  --timezone America/New_York
```

Paths are examples, not defaults. Nothing discovers or reads a live ledger.
The first sample of each admitted canonical source creates an **undated baseline**,
not a dated event. Subsequent comparable counters produce observed deltas between
operator-supplied observation instants. Per-row history IDs preserve the existing
SHA-256 of the compact JSON list of incoming `KEY_FIELDS` in their declared order
(`sort_keys=True`, compact separators, `allow_nan=False`); profile/domain prefixes
are **not** added. The canonical feed first pseudonymizes the original canonical
key into `session_id` and supplies its fixed normalized route fields to this
adapter. These two existing steps must not be replaced by a new ID scheme during
a managed refresh. Profile remains validated immutable provenance, not an ID
prefix. Raw identity strings are discarded; provider/profile and `unattributed`
are the exposed labels. IDs differ from the provider-aggregate IDs. Optional
lifetime bounds are validated metadata only: they never date a first baseline.
Snapshot digests canonicalize source order, so reordering identical facts is
idempotent without changing any source ID.

Instants require exactly `YYYY-MM-DDTHH:MM:SSZ` or an explicit `+/-HH:MM` offset;
offset hours/minutes must be valid. Fractional seconds, leap seconds, naive dates,
unknown `-00:00` offsets, and UTC years outside 1970–9998 are refused. Persisted
instants normalize to UTC `Z`. A supplied installed IANA key is required;
`localtime`, `posixrules`, `posix/`, `right/`, pathname traversal and unavailable
zones are refused. Profile and timezone are immutable for a ledger, including
alias changes. Changing either requires a separately owned new ledger/baseline;
there is no reinterpretation of old buckets. Changed timezone rules can make an
old journal incompatible; do not silently rewrite it under different rules.

Calendar keys are local `day: YYYY-MM-DD`, Monday-start `week: YYYY-MM-DD`,
`month: YYYY-MM`, and `year: YYYY`. Both interval endpoints must fit within one
bucket's UTC bounds for that grain, with the ending instant strictly before its
next boundary. Ending exactly at midnight is therefore unallocated for day.
Crossing a day/week/month/year boundary is never split or assigned to the ending
bucket. Independently containing grains can still receive the whole delta,
including a Monday week spanning New Year. Earliest local midnight boundaries
also conservatively exclude rare midnight rollback excursions even when endpoint
date labels match. Such exceptional discontiguous day/month segments can remain
unallocated. Ordinary spring/fall DST intervals use real UTC elapsed seconds,
not fabricated 24-hour days.

Coverage distinctions are stored without per-sample warning output:

| Event status | Meaning |
| --- | --- |
| `baseline` | New source; cumulative counters retained undated; no delta |
| `delta` | Comparable positive token delta; only contained grains allocated |
| `measured_zero` | Comparable zero **token** delta; call count may still increase |
| `reset` | Any counter decrease, or reasoning growth exceeding output growth; new baseline, unknown interval tokens |
| `missing` | Previously present source disappeared; no invented zero |
| `reappeared` | Source returned after absence; rebaseline without bridging absence |
| `gap` | Comparable interval **greater than 21,600 seconds**; delta measured, all calendar grains unallocated |

Exactly 21,600 seconds is eligible for allocation. Repeated identical snapshots
at the same absolute instant are idempotent (generation and bytes unchanged),
not new measured-zero intervals. A changed snapshot at a repeated instant or any
backward instant is refused. Duplicate canonical identities/source IDs are
rejected, not summed. Missing/unsupported/Local-route sources do not establish
cloud history or measured zero. This is sampled counter history, **not full
request history**; an unobserved reset that recovers between samples is inherently
undetectable. Completeness of the explicitly supplied snapshot is the producer's
responsibility, not guessed by this adapter.

## Public history schema and reusable APIs

`history.json` has contract `hermes-observed-history/1`, `schemaVersion: 1`,
`readOnly: true`, `timezone`, UTC `startedAt`/`observedAt`, and `generation`.

- `sources[]`: sanitized `id`, `provider`, `profile`, `attribution`, latest
  cumulative `counts`, `present`, `firstObservedAt`, `lastObservedAt`, `baselineAt`,
  and per-source `coverage`. These dates describe recording, **not token dates**.
- `buckets`: arrays for `day`, `week`, `month`, `year`. Each entry is
  `{key, sources: [{id, counts, coveredSeconds, intervals}]}`. Only recorded,
  retained cells exist. A missing bucket/cell is unknown, not implicit zero.
  `coveredSeconds` is sampled recording span per source, not active compute time,
  request coverage, or a complete-calendar guarantee; never sum it across sources
  to infer elapsed wall time.
- `counts`: `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_write_tokens`, `reasoning_tokens`, `api_call_count`, `prompt_tokens`,
  `total_tokens`. Prompt is input + both cache buckets; total is prompt + output;
  reasoning is an output subset and is never added twice.
- `events[]`: bounded `{sourceId, status, start, end, counts, allocated}` observed
  intervals/baseline markers. Unknown intervals have `counts: null`; `allocated`
  lists the containing grains. No request, raw session/model, endpoint, mode,
  task, credential, native CLI or per-credential attribution is exposed.
- `coverage`: `events` counts by status; lifetime `observed` counters;
  `unallocated` and `pruned` count vectors by grain; `prunedEvents`;
  `status` (`unknown`, `baseline-only`, `partial-observed`), `missingHistory: true`,
  and `interpretation: sampled-counter-deltas-not-requests`. Unknown/reset/absent
  token amounts are **not** quantified as zero. Unallocated counts represent
  measured portions only. For each source/grain, retained + pruned + unallocated
  equals observed. A partial-observed status can have no dated allocation.

Python seams (no installed Hermes imports):

```text
hermes_accounting.canonical_snapshot(rows, profile) -> {profile, record, sources}
usage_history.observe(state_or_none, snapshot, *, observed_at, timezone) -> new_state
usage_history.validate_state(state) -> validated_state
usage_history.project_history(state, *, providers=None, profiles=None,
                              source_ids=None, grain=None) -> detached_public_view
hermes_accounting.record_history(snapshot, *, state_path, history_output,
                                output, observed_at, timezone) -> committed_state
hermes_accounting.recover_history(*, state_path, history_output, output) -> state
```

The pure transform/projection never mutate supplied ledgers. Filters are bounded
unique lists/tuples, select sanitized public metadata only, and recompute coverage
for selected sources. `grain` selects just that bucket array; coverage still
describes selected sources across all grains. Empty selection is unknown. Durable
state uses `hermes-observed-state/1` and a private transaction envelope; do not
send that private envelope (explicit target paths/preimage hashes) to UI consumers.

## Limits, pruning, and storage recovery contract

- Canonical input: at most 16 MiB, 65,536 rows, 2,048 characters per identity
  field. SQLite queries have a two-second progress deadline. Existing route
  admission is unchanged. The history inventory admits at most **512 retained
  source IDs**, not an ever-growing lifetime union. Under capacity pressure an
  absent identity retires only when **no retained cell or event references it**.
  Its lifetime coverage folds into the private `retired` aggregate; its canonical
  ID is never rewritten or merged with a provider/profile. A later return of
  that ID starts a fresh undated baseline, never a guessed catch-up delta.
  Identities holding any retained fact, including a measured-zero cell, cannot
  retire. If more than 512 are still needed, the new observation is refused
  before publication rather than discarding facts or resetting the last-good state.
- At most **31 recorded day, 13 recorded week, 12 recorded month, 5 recorded year
  buckets**, **31,232 source/bucket cells** (512 identities in all 61 buckets),
  and **512 source observation events**. These are counts of recorded buckets,
  not promised rolling calendar coverage. Pruning removes oldest keys within a
  grain and oldest journal events. Cell pressure never removes another grain's
  still-retained bucket; measured zero occupies a real cell. Pruned counters and
  event markers remain in coverage summaries. This finite contract does not
  promise admission of arbitrary new identities while all slots retain facts.
- Private state/target readers, accounting/history exports and the public history
  parser share a **16 MiB encoded-byte** ceiling. The separate unified daily
  roll-up publication/`parseRollup` ceiling remains **4 MiB**. All counters,
  derived values, coverage seconds/intervals, generations and cumulative sums
  must be integers in **0…9,007,199,254,740,991** (booleans are not integers).
  Overflow, duplicate JSON keys (including escaped aliases), nesting over 32,
  wrong contracts/shapes, unexpected persisted fields, invalid dates/provenance,
  overlapping events and contradictory coverage fail closed before a new commit.
- Work/memory are bounded by input bytes/rows plus the retained inventory, cells
  and events. An observation temporarily holds the old and incoming inventories
  before admission and can add one event/four cells per incoming source before
  pruning. There is no expansion across unobserved dates or per-token iteration.
  Validation, copying and serialization retain bounded scans and allocations;
  there is no O(1) claim.
  The duplicate-key lexer scans characters in place rather than materializing
  regular-expression matches; the old token stream exceeded the enforced native
  memory budget at dense capacity even though Node accepted that same input.
- The public history schema and canonical identities are unchanged. Older valid
  128- and 512-source journals without `retired` remain admissible without a
  migration. The optional private aggregate preserves retired lifetime coverage;
  public filtering/coverage remains derived from the selected retained sources.
  Retirement does not backfill tokens or silently reclaim still-retained facts.
- A conservative compact-JSON envelope charges all numeric fields 16 digits,
  longest supported provider/64-character profile, a 128-character timezone,
  all 31,232 cells, 512 events and 61 bucket envelopes. It also includes the
  optional retired summary and two maximally escaped 4,096-character transaction
  paths. Including a final newline, the independently constructed upper bounds
  are **15,220,626 journal bytes** and **15,294,634 public-history bytes**, below
  16 MiB. Independent field maxima need not coexist: this envelope is a size
  upper bound, not a fabricated valid recording. Sanitized feed output remains
  small and the canonical input/cardinality limits have not been raised.
- A separately validated synthetic dense journal contains all 512 identities
  and 31,232 measured-zero cells. Its 9,921,902-byte public projection loads
  through the actual generated history `FileView` and shared JavaScript consumer
  under an enforced 1 GiB RAM/no-swap envelope. Padding that valid JSON to exactly
  16 MiB is accepted; one further byte is rejected. This is offscreen transport
  proof, not whole-panel compatibility, live desktop acceptance or a portable
  RSS/latency guarantee. Keep independent final-source acceptance separate.
- State/accounting/history must be distinct explicit absolute destinations in
  existing owned, no-symlink directories not writable by group/other. Existing
  files must be private singly-linked owned bounded regular files; hardlinks,
  symlinks, input aliases, lock/temp aliases and malformed state are refused.
  New files are mode 0600. A persistent zero-byte `<state-name>.lock` uses
  nonblocking `flock`; concurrent cooperative writers fail rather than race.
  Keep exclusive ownership of all destinations; this is not an atomic CAS against
  a hostile same-UID writer or another ledger sharing an export path.
- Preflight validates the next input/time/counts, state, paths and bounded exports.
  The adapter recovers the preceding committed exports, then atomically commits
  fsynced state **before** publishing either export. State stores generation,
  each bound export pathname, prior content hash (or absence), and intended payload
  hash. Exports use private temporary files, final preimage/inode/directory checks,
  fsync, atomic replacement and readback. Each history target has one deterministic
  owned temporary name; a valid private stale temporary is replaced on retry,
  never followed/truncated if foreign, symlinked or hardlinked. Across crashes the
  three targets plus their at-most-one temporaries are at most six 16-MiB files,
  plus the zero-byte ledger lock and one zero-byte publication lock per target,
  excluding supplied input, legacy unknown files and filesystem overhead.
- **Before a state commit**, failure leaves counters unadvanced and exports
  unchanged (except completion of an already committed prior export transaction).
  Retain and retry the *same supplied snapshot and observation instant*; no
  process can durably preserve a new delta when its first state write fails.
  **After a state commit**, publication/fsync/readback failure never rolls back or
  discards the committed delta. A late directory-fsync failure has an uncertain
  commit outcome: inspect/recover the retained journal, do not assume rollback.
  Retry/recovery accepts only each saved preimage or intended new payload;
  unrelated changes are refused without overwriting them. Recovery re-fsyncs even
  already-matching exports. There is no claim of a multi-file atomic export;
  individual readers may briefly see different generations. State is authoritative.

Recovery uses the same three explicit destinations, without input or a new sample:

```text
python3 hermes_accounting.py --recover-history --output /owned/accounting.json \
  --history-state /owned/history-state.json --history-output /owned/history.json
```

Recovery/retry is idempotent. Never remove the state to "fix" a pending export.
An unrelated target edit or changed destination requires operator reconciliation;
there is no force-clobber option. Successful CLI execution is quiet and returns 0.
Source, timestamp, count, bound, storage, lock and recovery refusal returns 2 with
one generic message; API callers receive `ValueError`/`OSError` (snapshot reads
can raise SQLite errors). No raw row values appear in error output.

## Managed adapter delivery

From a reviewed complete checkout, the managed Agents `--install-tools` and
`--install-bridge` paths deliver both `hermes_accounting.py` and its history
sibling `usage_history.py` under `$HOME/.local/share/a-la-carchy/local-router-stats/`.
The installed adapter resolves only that sibling; it does not import Hermes or
fall back to the repository. Invoke that installed adapter with the same explicit
input/output/history arguments documented above. Installation does not create
accounting data or schedule this CLI. To refresh the pair, install the bundle
from the newly reviewed checkout; clone `--upgrade` alone updates neither helper.
The existing hash registry, manual-edit refusal, backup and rollback protections
apply to both files. See [managed installation](../agents-clone/README.md).

## Explicit canonical feed (opt-in, dormant delivery)

`canonical_feed.py` is the separate producer for **explicitly selected** canonical
SQLite sources. It imports only stdlib and the owned adapter/history siblings; it
never launches Hermes, reads credentials/config, bootstraps a profile, runs
migrations, queries HTTP, or discovers a database. The older collector `--input`
option remains detached input; it is not reinterpreted as a live database.

The supported maintained contracts are **schema versions 31, 32 and 33**, with the ordinary
18-column `session_model_usage` table, its declared types/nullability, and the
six-column session/model/provider/base-url/mode/task primary key. This follows
`hermes_state_common.py`'s ledger DDL, `hermes_state_usage.py`'s incremental writer
and auxiliary-task writer, and `agent/usage_pricing.py`'s `CanonicalUsage` and
`normalize_usage`. Schema drift, views/virtual tables and unsupported versions
are refused, not migrated. Version 32 adds unrelated runtime/worker declarations;
version 33 only re-tags delegate metadata in `sessions.model_config`. Neither
changes the canonical usage ledger or counter semantics. The exact ledger shape
is still mandatory, and future versions are not automatically admitted.
The runtime/source qualification records the exact
launcher, PM interpreter/dependency selection, source/import precedence and
semantic-source hashes; source shape alone cannot prove unchanged semantics in
a future modified runtime. Requalify after runtime updates.

**One ledger, once:** the feed reads only canonical per-route usage columns from
`session_model_usage`, plus narrowly required schema metadata. It never sums the
`sessions` cumulative summary alongside that ledger, reads message/prompt/tool
columns, reconciles residual absolute-only session counters, or reconstructs
request dates. Auxiliary task rows in the ledger are included once. Committed
ledger coverage is narrower than all possible runtime activity: pending token
writer queues, failed accounting writes and summary-only residuals are not
invented. Cache/input/output/reasoning units and conservative Local/unknown-route
exclusion remain the existing adapter's rules.

A single `mode=ro` SQLite connection and explicit read transaction establish one
committed schema/ledger view, including committed WAL. Normal concurrent WAL
writers can advance after that snapshot without mixing rows from two commits.
There is no piecemeal DB/WAL copy, backup/transcript dump, immutable-live opening,
checkpoint, writer flush, migration or runtime constructor. **Read-only SQLite
can take locks and create/use WAL/SHM sidecars; it is not zero side effects.**
Only synthetic databases have been exercised for this candidate.

Example invocation after separate source/read authorization (all paths are
examples, not defaults; existing destination directories must be private/owned):

```text
python3 -I -B /installed/local-router-stats/canonical_feed.py \
  --source-db /explicit/profile/state.db --profile default --timezone UTC \
  --canonical-output /owned/canonical.json --history-state /owned/state.json \
  --history-output /owned/history.json --output /owned/usage.json
```

The private canonical export retains the existing `hermes-session-model-usage/1`
and `hermes-canonical/1` contracts. It contains only allowlisted normalized usage
fields: a SHA-256 pseudonym of each canonical identity, a fixed model label, a
supported provider, counters and blank route/task/mode. The canonical source's
validated first/last bounds are retained as metadata (or null when absent), never
as dated events or authority to allocate a cumulative baseline.
Its metadata retains the UTC `observedAt` instant for explicit pre-commit replay.
No raw session ID, model name, endpoint, task, message or credential is exported.
The adapter reads that real JSON export and produces the **existing** `usage.json`
and `history.json`; the feed does not implement a second history accumulator.
Public identity remains provider/profile aggregate and **unattributed**, never a
per-credential claim. The private journal still contains explicit destination
paths and must not be published to the UI or native sync directories.

Without `--observed-at`, the CLI samples the UTC clock at acquisition completion,
with second precision. An explicit instant is accepted for controlled replay or
fixtures. Observation time is not an exact SQL commit/request timestamp. The
first observation is undated baseline; subsequent deltas keep the existing
calendar/coverage rules above. First/last session bounds never date-backfill old
tokens; a missed poll or unobserved reset is not reconstructed.

### Source, privacy, bounds and recovery

- Source and sidecars require explicit absolute no-symlink parent paths, owned
  directories not writable by group/other, and bounded private owned singly-linked
  regular files. A held no-follow source descriptor fences observed identity
  before/after SQLite acquisition. The full alias set includes the database and
  WAL/SHM/journal names, every output, deterministic temporary, history lock and
  feed lock, **before database open or cleanup**. Symlinks/hardlinks are refused.
- The canonical export binds a digest of source pathname/device/inode/ownership/
  mode and an immutable digest of profile/timezone/destinations. Changed source
  identity or missing association beside existing history is refused before SQL.
  A replaced store requires separately reviewed new destinations/baseline, not a
  force-clobber or silent rebind. There is no raw source path in the export.
- A zero-byte `<canonical-name>.lock` serializes cooperative feeds with nonblocking
  `flock`; the existing history lock protects journal/export commits. There are
  **no retries**. Missing, busy, malformed, oversized, incompatible or observed
  replacement/racing sources fail without publishing new counters or zeros.
- Acquisition is limited to 3 seconds (SQLite progress plus row/deadline checks),
  100 ms SQLite lock waits, 65,536 scanned rows, 16 MiB encoded selected-row bytes,
  and 512 admitted canonical cloud identities (also the history lifetime
  bound). Scanned rows include excluded routes; history identities retain the
  complete session/model/route key for independent reset/baseline comparisons.
  The provider/profile aggregate in `usage.json` does not collapse those history
  identities: 129 distinct cloud identities are supported, 513 refuse, while
  excluded rows consume only the separate scanned-row/byte bounds.
  The default is **1 GiB main DB** and 128 MiB per sidecar, checked before/after.
  An explicit `--max-source-bytes` may raise only the main-file ceiling, up to
  **4 GiB**, for unrelated transcript storage/free pages. It does not relax
  sidecar, selected-row, memory or acquisition bounds. Dormant managed units can
  pass the same opt-in with `--accounting-max-source-bytes`; omitting it preserves
  the default unit bytes and does not silently increase any limit.
  This feed reads selected ledger rows through SQLite, never the whole main file
  into memory; the standalone snapshot adapter's 16 MiB input bound is unchanged.
  SQLite values/SQL/columns are limited to 64 KiB/8 KiB/128, page cache to 2 MiB,
  and memory mapping is disabled. The Linux CLI sets a 256 MiB address-space
  ceiling and a 10-second wall timer; SIGTERM/SIGINT/timeout/memory refusal exits
  nonzero quietly. These are limits, not a measured peak-RSS or an unconditional
  deadline for uninterruptible kernel/filesystem I/O.
- Canonical, journal and exports are bounded private 0600 atomic replacements with
  preimage checks/readback; directories must already exist. Generic failure text
  never echoes source paths, SQL errors or row contents. Source failure leaves the
  last good canonical/journal/exports unchanged. After **valid** source acquisition,
  canonical publication can precede journal/export failure; it is not a four-file
  transaction. A committed journal delta survives partial export failure and is
  recovered exactly once by the installed adapter's `--recover-history` command
  above, without opening the source. If a first state commit fails, retain the
  canonical export and its saved `observedAt` for explicit adapter `--json` replay;
  a later automatic poll cannot promise to recover that missed first sample.
- Ownership/no-follow checks and inode/preimage rechecks detect observed changes,
  not an atomic CAS against arbitrary same-UID writers. Python's SQLite binding
  opens its own pathname/sidecars rather than consuming the held guard descriptor;
  exclusive directory ownership is a prerequisite, not a proof against an
  adversarial same-UID swap-and-restore race. No full database is copied into
  evidence. Unrelated output edits are preserved and require reconciliation;
  never delete the journal to bypass recovery.

### Dormant managed user units

The hash-owned `--install-tools`/`--install-bridge` bundle now includes the feed
CLI and `canonical-feed.service.in` / `canonical-feed.timer` templates. These
ordinary installs do **not** render, enable or start accounting units or collect
anything. The separate explicit mode renders `hermes-canonical-feed.service`
and `.timer` only when **every** source/profile/timezone/destination is supplied:

```text
python3 -I -B /reviewed/extras/agents-clone/manage.py --shell /explicit/shell.json \
  --install-accounting-feed --accounting-source /explicit/profile/state.db \
  --accounting-profile default --accounting-timezone UTC \
  --accounting-canonical-output /owned/canonical.json \
  --accounting-state /owned/state.json --accounting-history-output /owned/history.json \
  --accounting-output /owned/usage.json local
```

Unit arguments are systemd double-quoted/escaped, including literal `%` and `$`,
not shell-quoted. The service executes the real installed isolated Python CLI,
with umask 0077, no new privileges, AF_UNIX-only address families, a 20-second
start timeout and 5-second stop timeout. The timer is a non-persistent minute
observation schedule, not a catch-up/backfill mechanism. Nothing calls systemctl,
reloads the manager, creates an enablement symlink, or starts collection. Local
collectors and the separate Codex quota timer are preserved.

Rendering and all helper/unit replacements use the existing hash registry,
manual-edit refusal, backups and rollback transaction. Repeat install is a no-op.
Refresh owned helpers/templates or rendered units through the reviewed installer
with the explicit options; clone `--upgrade` alone preserves these units and
updates neither feed helpers nor parameters. A changed profile/timezone/destination
requires a separately reviewed new history rather than reinterpretation. Runtime
read authorization, independent acceptance, actual installation and eventual
unit activation remain separate operator gates. This source-only task performed
none of them.

`tests/hermes-canonical-feed.py` covers the real SQLite/WAL -> canonical JSON ->
existing adapter -> private usage/history path, a concurrent writer's single view,
privacy, aliases/no-follow/ownership/bounds, source/schema replacement, busy/missing/
malformed failures, locks, journal/export fault injection and recovery. Its fixture
SQLite escape guard is exercised before connections. `tests/hermes-canonical-delivery.py`
installs the actual managed bundle into a disposable HOME, removes its checkout,
and denies original-repository reads via an exercised Python audit hook before
running the installed CLI, including its clocked service arguments. It verifies
real baseline -> 19 synthetic observed tokens -> failure/recovery, registry update/
refusal/rollback, systemd argument round trips, unchanged quota/Local assets, no
systemctl calls and no enablement links. This is Python-boundary fixture proof,
not an OS sandbox, live unit execution or independent acceptance.

## Verification scope

`tests/hermes-accounting.py` exercises synthetic rows, a real-shaped offline
SQLite table, malformed/duplicate/overflow/local-route cases, privacy, bounded
publication and both actual CLI paths through the generated summary. Header,
Local/stager and existing totals regressions remain separate. These checks are
not independent acceptance, live ingestion, or installation. Native fixture proof
and current-runtime qualification remain distinct; the former is described below.

`tests/agents-upgrade.py` also installs the real tool bundle into a disposable
HOME, removes the installer checkout, and denies original-repository reads while
running the installed accounting CLI. Synthetic canonical JSON establishes an
undated baseline; a standalone read-only SQLite snapshot produces the later
observed calendar delta, and recovery preserves the actual published bytes.
Both helpers are checked against registry hashes and exercised through registered
updates, unregistered/manual-edit refusal and registry-write rollback. This is
installed-copy fixture proof, not a host install, automatic ingestion or live data.

`tests/agents-history.py` adds an exercised baseline→later CLI snapshot→public
calendar tracer bullet and focused offline calendar/DST, lifecycle, bounds,
privacy, immutable SQLite history input, lock, overflow/corrupt persistence,
preimage, late-fsync and recovery checks. Injected I/O failures are fault-injection
fixtures around real file/CLI behavior, not live failure telemetry. All fixture
counts are synthetic, not actual user tokens. Run with an isolated HOME/XDG and
short unique TMPDIR under the authorized scratch directory, without display/GPU
or installed runtime activation. Parent owns independent acceptance and any live ingestion/activation.


## First history-consumer tranche — historical source/proof notes

**Historical tranche, not the current UI or admission contract.** The initial
4 MiB/2,048-cell limits, source-selection controls and latest-retained-date
presentation described below are preserved as implementation/proof history.
The current shared history contract above admits 16 MiB and 31,232 cells;
combined views use the shared calendar clock. The generated popup has no source
picker and uses a bounded vertical per-source viewport, not horizontal chart
scrolling. See the current Observed history section in `README.md`. The separate
unified daily roll-up limit remains 4 MiB. These notes do not override current
constants, generated controls, or retained source-bound test receipts.

The generated `Main.qml` watches **only**
`$XDG_STATE_HOME/omarchy/hermes-token-accounting/history.json` (fallback:
`$HOME/.local/state/omarchy/hermes-token-accounting/history.json`) with a separate
private `FileView`. It is not an Agent/provider, quota card, sync input or snapshot
member. Publish the public `--history-output` there explicitly; keep the private
`--history-state` separate. The compatibility `--output` can remain `usage.json`
in that same private directory. These are source contracts, not installed paths
or permission to export a live ledger. The consumer itself schedules no work;
the feed above requires separate source/read/install/activation authorization.

`stage_plugin.py` copies the maintained `history_consumer.js` into the new clone
as `History.js`, and imports that exact consumer from generated QML. Rendering
captures the JS bytes before writing; a native `History.js` name collision is
refused. Supported native lazy/eager limits layouts retain their own fitting
and actions. The existing pinned Total/Cloud/Local header stays outside scrolling
and rotation. History is a separate sampled view **within** the scroll body and
is not added a second time to the pinned totals; no extra CLI/provider card.

The public consumer rejects unsupported/wrong shapes, duplicate JSON keys,
non-safe or inconsistent canonical count vectors, invalid real calendar dates
or non-Monday week keys, duplicate/unknown source IDs, unsanitized identities,
unsupported cloud providers, wrong attribution, invalid UTC observation bounds,
coverage conservation errors and inconsistent retained-event summaries. It
accepts only the complete four-grain public projection, not filtered single-grain
exports or the private journal. Limits mirror the public backend: 4 MiB UTF-8,
512 sources, 31/13/12/5 buckets, 2,048 cells, 512 events; JSON nesting is capped at
32. Calendar allocation and actual IANA-zone-rule admission remain the producer's
responsibility: QML does not reconstruct timezone transitions or validate raw
source authenticity. The source IDs establish sanitized references, not trust,
credential attribution or a cryptographic signature. `FileView` itself reads the
file before the validator checks size; these are admission/model bounds, not a
claim of bounded file-read memory against an arbitrary local writer.

Day/Monday-week/month/year rows identify the **latest retained recorded key for
each grain**, not current-period usage or lifetime counts. Missing grains are
`—`. The chart offers seven calendar slots ending at the latest retained key for the
chosen day/week/month grain, or five for years. Intervening/missing cells are `?`
with no bar; a recorded zero has a minimal bar and explicit `0`. Date labels and
counts are native PlainText, with full-date/source/metric accessible names and
hover/focus details. Hover or focus on the section title shows recording bounds,
admitted-source coverage, unallocated/pruned measured amounts and undated/missing
history limitations in a PlainText tooltip. Native keyboard navigation is not
replaced; end-to-end keyboard dispatch remains a parent review obligation.

Native `Ui.Button` controls select sources, any of the eight canonical counters,
calendar grain, and combined or per-source display. Source membership is dynamic,
up to the public schema's 512-source bound, with no two-account assumption. Explicit
ID selection (including `[]`) survives `FileView` reloads, malformed/recovered files
and absent/removed sources without defaulting to all. Initial untouched selection
uses all available sources; **All sources** captures the current membership and
**None** keeps an unknown empty view. Selection is transient panel state, not
persisted user configuration or a mutation of the underlying history.

`selectHistory(parseHistory(text), options)` queries only the validated retained
projection. Options are `sources` (null/default all, or a unique bounded ID array),
`metric`, `grain`, and `mode` (`combined`/`multiple`); invalid types/values/extra
keys fail closed. It exposes selected sources, known/partial cells and per-source
series, never fabricating a zero for missing data. Combined values are known
subtotals when some selected cells are unknown, not complete calendar totals.
Such combined labels and summary rows carry `+`; the accessible/hover text names
the known/selected-source count. A `0+` is not complete zero activity. Visible
status is compact, without a permanently hardcoded partial-recording heading;
the section tooltip retains the sampling and coverage limits.
Per-source bars are separately rendered, use theme-derived colors, show short-ID
and value captions, and retain full sanitized source labels in PlainText details.
A horizontal Flickable/AsNeeded scrollbar preserves legible minimum widths. The
native panel still fits the work area without an arbitrary height cap; the pinned
header and lazy-loaded content remain independent of the scrollable history.

States remain separate: initial loading; missing/read-failed unavailable;
malformed/unsupported; no admitted sources (empty/unknown); undated baseline
(no dated delta); measured zero; and observed sampled deltas with partial
recording. A gap can have measured observed counters with no dated buckets.
No absent source/date/interval is turned into a measured zero. Summaries expose
retained lower bounds, not complete all-profile/credential/calendar coverage.
The work is bounded by encoded input plus sources/cells/events and seven display
slots, including a linear JSON structural scan and bounded object validation;
there is no per-token iteration or O(1) claim.

`tests/agents-history-consumer.py` drives real baseline→later synthetic CLI
publications through the same JavaScript and checks malformed/state/retention/
gap cases. `tests/agents-qml.py` exercises those actual publications through the
generated native data/UI, real Text and bar geometry, lazy open/reopen, tall/short
screen fitting, pinned header, native quota delegates and guarded actions. Its
isolated refresh handshake waits for the version-matched logging stub before
`Qt.quit()`; a deliberate stub delay reproduces the old premature-quit assertion.
A printed result is not sufficient: the offscreen child must exit 0 within the
unchanged deadline. All counts and input databases are synthetic; layer-shell
placement, real desktop focus/theming, live counters and installation are not
proved by these fixtures. `tests/agents-history-controls.py` adds source/metric/
calendar/multiple selection, refresh/removal, zero/partial and invalid-option
checks against actual canonical projections. The native `controls` case invokes
real button signals, checks Text/PlainText and separate bar values/geometry, and
handshakes actual private file replacements for selected/removed/new/empty/
malformed/recovered publications. A disconnected disposable metric handler must
fail this same case. Local live-rate fixtures remain separately accounted proof,
not history acceptance or current-engine qualification.
