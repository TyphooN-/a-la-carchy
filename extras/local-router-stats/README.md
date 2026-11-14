# Local llama.cpp router statistics — staged, not activated

Adds **Local** to the installed Omarchy Agents widget through a supported user
clone, preserving its theme, typography, spacing, bar button, keyboard navigation
and scrollable panel. No subscription limits or cost estimates are invented for
local inference.

## Omarchy dev's section-based widget

The limits-first Agents panel in `omarchy-dev 4.0.0.r6694.g821ae58-1` and
`4.0.0.r7017.g84cfb93` is supported, including the latter's lazy content and
localized trouble label. Local appears as a provider section alongside Claude Code,
**native Grok**, and dynamically inventoried, read-only **Hermes credentials**, rather
than recreating the old tab layout. Its section shows collector
status, today's measured tokens, the measured seven-day total with recording
coverage, and every observed model's daily total. It uses the widget's native
light/dark provider-icon convention. Local's sampled counters are kept out of
the cross-subscription subtotal and never appear as a subscription quota
meter or an authentication error. An unrecorded today displays **—**, not zero.

This Omarchy dev release ships its own Grok collector, including subscription
limits. The Local clone keeps it unchanged; the optional observed-Grok fallback
is refused on releases with native Grok support, so it cannot shadow or double
count the first-party record. No Grok status-line configuration is needed here.

The older tab-based Agents panel is still supported. It retains Local's
seven-day chart and full per-model input/output rows. Layout detection is
fail-closed: unsupported or ambiguous QML is not patched.

## Persistent recorded totals (limits-first panel)

The bar keeps today's known cloud-plus-Local total visible. The popup pins the
combined total with Cloud and Local sampled breakdowns, then expands naturally.
Native Codex CLI history is not a separate summary or added again to the total.
It scrolls only when the current monitor cannot fit the content; provider order
and all cards are preserved. Both native eager and first-open lazy content shapes
are supported; mixed or partial shapes are refused. Hover the totals heading for accounting details.

- **Partial** means some counters are unavailable, not zero. Unknown counters
  remain **—**. Quota-only cards need no repeated token disclaimer.
- Select a provider aggregate or its account ledgers once. Local sampled traffic
  remains separate; cloud counters include native input/output/cache buckets.
- Codex CLI history is client-scoped, not necessarily another account. A shared
  Hermes login does not establish historical attribution or duplicate events.
- Claude's untyped recent-day message counts are withheld as token totals in both
  the calendar and generated bar. Dated CLI rows require the exact top-level
  `recentDaysUnit: "tokens"` declaration; absent units, message units and legacy
  activity markers are refused. Producers must establish units from their actual
  token source, not relabel an untyped publication. Existing native files without
  this declaration remain unadmitted as daily token history; explicitly recorded
  today/history and quota windows remain available. The optional CLI input uses
  the bounded no-follow JSON reader (duplicate keys and nonfinite numbers refused).
  A typed dated total establishes only `total_tokens`: the unified roll-up carries
  explicit `null` for its unavailable input/output/cache/reasoning/API-call/prompt
  counters, and per-source metric views show unknown, not a fabricated zero.
  Fully measured canonical Anthropic counters, including measured zeros, retain
  their strict integer identities. This total-only form belongs only to the
  unified roll-up; the canonical Hermes history contract is unchanged. Deliver
  the producer and generated consumer together; older strict consumers refuse
  the new total-only form rather than infer a breakdown. The optional merge checks
  the complete retained total before mutation, not only each date: period, source
  and combined totals must remain consumable safe integers. A refused optional
  merge leaves retained history and the usable Local projection intact.
- Model counts and actionable errors stay visible. Repeated coverage, cache,
  lifetime and attribution notes are removed from cards.

Hermes credential admission is dynamic and inventory-bound; it no longer depends
on two numeric display slots. The separate bridge reads the selected profile's
persisted pool with per-provider global fallback, publishes opaque identities, and
marks unsupported quota types unavailable. Credential visibility is not complete
usage accounting or per-credential token attribution. See
[`../hermes-codex-usage/README.md`](../hermes-codex-usage/README.md) for scope,
bounds, read-only controls, migration and checked publication/recovery.
New labels are plain text; oversized or invalid counters stay unavailable.

Offline proof uses `tests/agents-visible-totals.py` and `tests/agents-qml.py` with
isolated HOME/XDG/TMPDIR and the selected native Agents source. The QML fixture
supports `ALACARCHY_SCREEN_CASE=tall|short`, `ALACARCHY_BAR_CASE=horizontal|vertical`,
`ALACARCHY_CLOUD_CASE=recorded|zero|missing`, and Local availability cases. It
asserts full provider-card geometry when content fits and scrolling otherwise.
Live acceptance still requires the guarded clone update and a fresh popup capture.

## Interactive observed history (limits-first panel)

The **Observed history** section offers native, focusable themed buttons for:

- Total, input, output, prompt, cache read, cache write, reasoning and API calls.
  Total = input + caches + output; reasoning is already an output subset.
- Calendar day, Monday-start week, month or year in the combined calendar.
  The finite calendar keeps seven date columns (five for years), with a bounded
  vertical viewport rather than a sideways-scrolling chart.

The generated panel uses all admitted sources. It deliberately has no source
checkboxes, **All sources**, or **None** controls. Source-filter options remain
part of the shared consumer API for explicit callers and isolated tests, not
additional controls in this popup. Combined/Per-source mode buttons are also
deliberately absent. Metric and grain choices are transient panel state; they
never rewrite the retained ledger.

Combined views are anchored to the shared calendar clock, not silently shifted
to the newest retained measurement or reconstructed lifetime usage. Missing
cells display `?` without a bar; measured zero displays `0` with a minimal bar.
Combined counts with missing contributions carry `+` (for example `0+`): a known
lower bound, never a complete zero. Short status stays in the section; full
coverage detail stays in its tooltip. Backend per-source views and renderer-only
fixtures retain source-ID/value captions and PlainText hover/focus details; they
do not add a mode toggle to this popup. The section-heading tooltip carries
recording/coverage limitations. History never contributes again to pinned
totals, Cloud/Local accounting, quotas or sync. The view itself does not export
a live ledger, schedule ingestion, or change provider/account state.

`tests/agents-history-controls.py` exercises the shared selector through Node and
actual canonical projections. `ALACARCHY_HISTORY_CASE=controls` with
`tests/agents-qml.py` drives real native metric/grain button signals, zero/unknown,
dynamic membership and private FileView refreshes under offscreen software rendering.
Separate renderer-only scenes exercise combined and per-source geometry without
restoring the removed controls. Add `ALACARCHY_SCREEN_CASE=short`
for fitting/scroll proof. `ALACARCHY_HISTORY_DISCONNECT=metric` is a discriminating
negative control: it disconnects only the disposable generated metric handler and
must fail. These are synthetic offline checks, not live popup/keyboard acceptance.

## Measurement contract

- Sample the existing loopback router at `127.0.0.1:8080` every five seconds.
- List `/models` without reloading its configuration. Read `/metrics` directly
  from an identified loaded worker's loopback port, not through the router;
  these reads cannot trigger router autoloading or eviction.
- Collect **evaluated prompt tokens** and **generated tokens**. Cached input,
  full request input, request counts and session counts are not available from
  these counters and are not fabricated. Hermes counts are not added on top:
  that would double-count requests already passing through this router.
- Find children across all router threads, match their alias/internal port, and identify them using boot ID,
  PID and process start time. Check identity again after reading its counters.
- First observations, new workers and counter regressions establish a baseline;
  their historical lifetime counters are not assigned to today's date.
- Persist snapshots and local-calendar-day/model aggregates transactionally in
  SQLite with WAL and FULL synchronous writes. Reject concurrent collectors
  sharing one state directory. Keep at most 365 calendar days and 256 models.
- Unrecorded dates remain unknown, not zero: the older layout's chart uses **—**;
  the dev layout reports recorded-day coverage and uses **—** for an unrecorded
  today. Recorded days are partial coverage; the ledger retains their first/last
  successful samples.
- Sampling is **best effort**, not exact request accounting. Unloads between
  samples can lose tokens; short interval deltas go to the sample's day,
  so a sample crossing midnight can move a few seconds of usage between days.
  Long gaps crossing calendar days are rebaselined rather than backfilled.
  Exact all-traffic accounting would require a
  separate completion-event ledger in the router; this feature does not add
  an inference proxy or alter request execution.
- Exact model aliases are preserved. The tab layout shows all recorded models'
  evaluated input/output; the dev layout lists every nonzero daily model total,
  with no top-four truncation. Ledger cardinality is bounded to 256 models.
- Store no prompts, responses, credentials, model launch arguments or raw errors.
  There are no cloud requests, secret discovery, redirects or proxy usage.

## Current-task live rates

`LiveSampler` projects only slot IDs, task IDs, counters and worker-generation
metadata. Prompt/generation rates are positive counter deltas divided by the
elapsed wall-clock interval between two observations of the same active task
set and worker generation. They are **not** engine/lifetime average throughput
or exact request accounting. Prompts, responses, params and credentials are
removed before sampling and are never published.

The producer keeps one previous observation per model (at most 16 models and
256 slots each), with a 30-second maximum interval and 0.1-second minimum rate
window. Task/worker changes, counter rollback, idle/completion, missing counters,
clock reversal and long gaps rebaseline rather than reuse another task's rates.
Only fresh progress renews its own phase: generation progress cannot keep an
old prompt rate alive. Unknown or absent phase measurements stay **—**, not zero.

The `local-task-rates/1` record is separately bound to observation time, worker
and task identity, and a bounded expiry. The limits-first panel's visible
PlainText rate label checks those bindings and ticks its own clock every second,
so rates expire even if the publisher stops. A malformed numeric phase invalidates
the record; a legitimately absent or expired phase preserves the other valid
phase. Compatibility status text never contains immortal `tok/s` values.

Live slot admission is deliberately **fail-closed**. Without proven deployed
engine/parser/sleep/identity semantics, `/slots` is not requested and the UI
reports rates unavailable. Offline tests mock that admission and transport;
they do not qualify a live worker, wake models, or authorize activation.

Focused proof: `tests/local-live.py`, `tests/local-live-consumer.py` and the
maintained `tests/agents-qml.py` fixture with
`ALACARCHY_LIVE_CASE=rates|expired|malformed|cancelled|identity`. The `rates` case
exercises the actual synthetic producer/export through instantiated native
Main/Panel/Text, then waits for the consumer's real Timer to expire prompt and
later the whole observation without another publication. Geometry and PlainText
are checked; Qt must quit and the child must exit zero. Fixtures remain isolated,
CPU-only and do not establish live popup acceptance.

## Files

- `collector.py`: independently runnable collector and native JSON publisher.
- `stage_plugin.py`: creates a new Agents clone from the installed stock source.
  It refuses existing destinations and unsupported/ambiguous QML. It changes
  Local discovery, unknown-day display, model labels/tooltips and Local's
  four-model truncation. The Local record stays outside the stock syncable
  directory. Package-owned files are never modified.
- `local-router-stats.service`: inactive user-service template. It does not have
  a dependency that starts or restarts `llama-router.service`.
- `../../tests/local-router-stats.py`: offline numeric, restart, calendar,
  persistence, identity, locking, retention and native-source contract fixtures.

## Safe staging / verification

Run from the repository root, with a new scratch output directory:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/local-router-stats.py
python3 extras/local-router-stats/stage_plugin.py --id typhoon.agents /ABSOLUTE/NEW/SCRATCH/agents
python3 extras/local-router-stats/collector.py --render-only --state-dir /ABSOLUTE/SCRATCH/state
```

`--render-only` does not contact the router or inspect services. With an empty
ledger it reports **Activation pending · not collecting**, without historical
usage. `--output FILE` atomically publishes a JSON record; its parent directory
must exist. Each target has one fixed private sibling temporary
(`.local-<hash>.tmp`), which is synced, renamed and followed by a parent-directory
sync. Repeated crashes therefore leave at most that one file per target. The next
publication replaces it only if it is an owned, private, singly-linked regular
file. Anything else at that name is refused and left in place. Random
`.local-XXXXXXXX` leftovers from earlier collector versions are not removed
automatically. Before opening inputs, creating directories, or acquiring the
ledger/lock, the CLI checks both canonical names and existing inodes of outputs
and deterministic temporaries against the history input, ledger, reserved SQLite
sidecars and lock, and against each other. An alias is refused with all preimages
untouched. This admission check is not a compare-and-swap against concurrent
same-user filesystem changes. `--publish` writes the patched clone's separate record under
`XDG_STATE_HOME` (default `~/.local/state/omarchy/local-router/agents/local.json`).
Publishing into the stock native Agents usage directory is refused: it could
expose Local records to an unpatched widget's cross-device sync.
The collector's default ledger is `XDG_STATE_HOME/omarchy/local-router/tokens.sqlite3`.
Direct CLI execution restricts future creation to owner-only access while preserving
any stricter caller umask; the staged service also declares `UMask=0077`. A new
state tree is created with private directories and database, sidecars and lock.
Existing directories and file permissions are not changed. In particular, this
is not remediation of an older permissive ledger or its inherited sidecar modes;
that would require a separately authorized live operation.
`--interval` accepts 2–3600 seconds. Unsupported counter formats fail closed.

## Activation is intentionally pending

The raw staging commands above do not install into the live bar or install/enable
a service. The configurator's **Local LLM stats → Enable** action creates the
managed clone, publishes an activation-pending record and installs an **inactive**
collector unit. It preserves native Grok and other provider settings; it does not
start collection or restart/reload the router or models. Full collection activation
needs an agreed safe boundary and confirmation that each child exposes metrics
(e.g. a supported `metrics` preset option or `--metrics` at a safe restart).
Do not edit the live preset or use `/models?reload=1` during active model work.

At activation, preserve any existing custom Agents clone and shell layout;
use Omarchy's supported user-plugin clone/enable workflow rather than overwriting
custom plugins. Install the collector under
`~/.local/share/a-la-carchy/local-router-stats/collector.py` before using the
service template. Verify actual model counters and the rendered Local tab after
activation. Native QML data-model smoke tests and formatter parsing are not a
claim that the live popup has been visually accepted.

Native cloud collectors remain unchanged. The independent Hermes credential bridge
is documented above; it does not replace native Grok or infer credential token
history. The native updater scans packaged collectors, so the router sampler runs
independently and publishes its separate record for the clone's dedicated watcher.
That private router observer (`local`) stays outside native cross-device snapshots
and synced aggregation. Unknown days are not converted into zero-filled remote
aggregates.

Native `usage/local.json` is a different source: session transcripts collected by
Omarchy itself. A genuine current-native record is retained as **Local transcripts**
(display identity `local-transcripts`), with its model-usage/history fields intact.
Its stored and sync identity remains `local`; snapshots add the source-kind marker
`native-local-transcripts`. Existing native snapshots remain compatible. The two
sources have independent enable settings and are never added together: the native
transcript row is excluded from the additive Cloud + router token header. Its own
history remains available in its separate read-only section. The alias filename
`local-transcripts.json` is reserved, not another collector input.

Filename alone does not admit a native transcript: the current record contract
requires schema version 1, prompt statistics and device scope, with no router-live
or private-read-only marker. Misplaced router records and private sync payloads
remain refused. This is source-contract separation, not a same-user authenticity
or cryptographic provenance guarantee. One dedicated private router observer is
retained; native transcript refresh does not start another sampler.
