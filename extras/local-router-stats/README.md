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
the rotating cross-subscription summary and never appear as a subscription quota
meter or an authentication error. An unrecorded today displays **—**, not zero.

This Omarchy dev release ships its own Grok collector, including subscription
limits. The Local clone keeps it unchanged; the optional observed-Grok fallback
is refused on releases with native Grok support, so it cannot shadow or double
count the first-party record. No Grok status-line configuration is needed here.

The older tab-based Agents panel is still supported. It retains Local's
seven-day chart and full per-model input/output rows. Layout detection is
fail-closed: unsupported or ambiguous QML is not patched.

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
