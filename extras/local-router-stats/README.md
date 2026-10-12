# Local llama.cpp router statistics — staged, not activated

Adds **Local** to the installed Omarchy Agents widget through a supported user
clone, preserving its theme, typography, spacing, bar button, keyboard navigation
and scrollable panel. No subscription limits or cost estimates are invented for
local inference.

## Omarchy dev's section-based widget

The limits-first Agents panel in `omarchy-dev 4.0.0.r6694.g821ae58-1` is supported:
Local appears as a provider section alongside Claude Code, the read-only
**Codex (Hermes 1/2)** slots and **native Grok**, rather than recreating the old tab layout. Its section shows collector
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

The horizontal bar continuously shows **Cloud today … · partial/recorded**.
Vertical bars show a compact count; **\*** marks partial coverage, explained in
the tooltip. The popup pins
**Today**, **Last 7 recorded days**, and **Recorded history** above its scrollable
body; only the secondary model/prompt/session summary rotates. These are scoped
recorded subtotals, **not complete all-account use or subscription lifetime**.
The actual quota windows remain first inside each native provider/account card.

- Raw counter presence is preserved before native normalization can turn a
  missing field into zero. Quota-only Hermes/Grok rows say **token counters
  unavailable (quota-only)**. Missing counters show **—**; a valid recorded zero
  shows **0**. Coverage is shown separately for each window as known/selected
  ledger sources. Provider-level history does not establish account attribution.
- Each displayed provider aggregate is selected once, never added to its own
  accounts. Only when no provider window has counters are its account counters
  used instead. All displayed account rows receive coverage labels dynamically;
  no account count is baked into the totals algorithm. Unknown account counters
  are not imputed from the provider aggregate or subscription quota.
- History sums the native **input**, **output**, **cache read**, and **cache
  creation** buckets once. Missing optional cache buckets in older/Local records
  mean no recorded cache bucket; invalid present values are unavailable. No
  prompt/session count, quota percent, or provider/account roll-up is added to
  these token fields.
- The recent window selects at most seven explicitly recorded dates supplied
  by `recentDays`, chronologically. The legacy `messageCount` field normally
  holds tokens in native transcript scans. Claude's `dailyActivity` fallback
  carries message/session/tool counts instead, and native merging can erase
  those unit markers. Claude's recent-day sum therefore stays **unavailable**
  until explicit token-unit provenance is supplied; its measured today/history
  counters remain visible. Other identifiable fallback rows are rejected, not
  relabelled as tokens. Recordings beyond the published recent
  window are not rescanned to fill gaps. Local only publishes seven calendar
  dates, so its available recorded-day count may be less than seven.
- Local evaluated/generated **sampled** counters are pinned separately and
  never enter the cloud subtotal: the same Hermes request can already be counted
  by the router. Cache input remains excluded. History is bounded by retained
  recording data (Local currently retains up to 365 days), not lifetime traffic.
- Native Codex's existing record is read by a separate history-only reader and
  pinned as **Unattributed Codex CLI … · excluded**. It is neither a duplicate
  native provider nor a Hermes credential's usage. The current Hermes publisher
  supplies quotas, not token history, so no exact all-account total is possible.
- Aggregation is reactive to provider/FileView/sync revisions, never an animation
  or per-frame scan. Oversized arrays/maps and unsafe numeric sums are unavailable
  rather than silently truncated. All new text sinks are plain text.

This change preserves the existing credential publisher, admission guards,
read-only account actions and provider order. **The assigned base still admits
only its two published Hermes Codex slots**; this totals change does not claim
complete active-profile credential discovery, and does not widen admission to
unregistered filenames. The totals loop itself handles arbitrarily many displayed
rows within explicit defensive work bounds. Claude fallback data merged with
other histories can lose its daily-activity marker upstream; that producer
ambiguity remains, so these values must remain labelled recorded/partial rather
than exact all-traffic counters. Source/candidate proof is offline only; live
placement and bar-space acceptance require a separate guarded application.

Focused offline checks (isolated HOME/XDG/TMPDIR, no live collectors):

```sh
ALACARCHY_AGENTS_SOURCE=/home/typhoon/omarchy/shell/plugins/agents python3 -B tests/agents-visible-totals.py
ALACARCHY_AGENTS_SOURCE=/home/typhoon/omarchy/shell/plugins/agents ALACARCHY_SHELL_SOURCE=/home/typhoon/omarchy/shell python3 -B tests/agents-qml.py
```

The QML harness also accepts `ALACARCHY_CLOUD_CASE=zero|missing` and the existing
Local cases. It loads real offscreen Qt with command stubs and ends with
`Qt.quit()`; its child must exit zero, not merely print an assertion marker.

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
must exist. `--publish` writes the patched clone's separate record under
`XDG_STATE_HOME` (default `~/.local/state/omarchy/local-router/agents/local.json`).
Publishing into the stock native Agents usage directory is refused: it could
expose Local records to an unpatched widget's cross-device sync.
The collector's default ledger is `XDG_STATE_HOME/omarchy/local-router/tokens.sqlite3`.
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

Cloud collectors remain unchanged. Their updater scans packaged collectors,
so this collector runs independently and publishes `local.json` for the native
file watcher. Local is excluded from native cross-device snapshots and synced
aggregation in the staged clone, so its scope remains this machine and unknown
days are not converted into zero-filled remote aggregates.
