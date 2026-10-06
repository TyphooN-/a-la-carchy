# Grok observed-token collector (built, not activated)

A credential-free bridge from Grok's **documented status-line JSON** to a
native Agents panel. It makes no network requests, starts no Grok processes,
reads no session transcripts, and does not access authentication storage.

## Omarchy dev: use native Grok instead

`omarchy-dev 4.0.0.r6694.g821ae58-1` ships Grok support in its Agents widget and
`omarchy-agent-usage-grok` collector, including the X Premium quota shown by the
native panel. That support is **not** this status-line bridge. The Local llama.cpp
clone preserves it unchanged, and this fallback's stager/enable action refuses to
shadow it. Do not add a Grok hook for that dev widget.

The bridge below remains an opt-in fallback for supported older tab-based Agents
releases without native Grok. Its limits are the fallback's limits, not a claim
that the newer Omarchy widget cannot obtain Grok quotas.

## What is supported

- Persistent, sampled daily input/output/cache counters with model breakdown.
- Full input = uncached input + cache creation + cache reads, as documented by
  Grok. No subscription prices or paid API usage are inferred.
- Missing quotas display **—**, not 0% or the previously observed screenshot.
- Only observed dates appear in the chart. Missing dates are not zero usage.
- Observations stay on this machine, outside native cross-device snapshots.

**Subscription quota remains unavailable to this fallback collector.** The Grok documentation,
`~/.grok/docs/user-guide/25-status-line.md`, explicitly says rate-limit summaries
are not exported. Lines 97–105 define the schema and cumulative counters.
`grok usage <session-id>` is a supported local ledger reader, but existing local
sessions had no `usage.json` ledgers during discovery; no history was backfilled.
This collector does not claim to retrieve the X Premium weekly allowance.

## Accounting boundaries

The first counters for a session establish a baseline, not historical spend.
If the preceding payload proves full session input/output are both zero, the
first subsequent call can be counted. Repeated payloads never double count.
Counter regression rebaselines without erasing already observed spend.

A counter change spanning midnight is assigned to the later day only when
observations are at most 25 seconds apart; longer cross-day gaps rebaseline.
This is **observation-time attribution**, not exact request timestamps. Same-day
changes after a gap can still be counted. Changes spanning a model switch go
into an explicit unattributed bucket, not the newly selected model by assumption.
Fork/resume history is never charged as a first observation.

SQLite transactions serialize concurrent status-line invocations. Retention is
365 calendar dates, at most 4,096 session baselines and 256 model identifiers.
Raw payloads, prompts, workspace names and account identifiers are not retained.
Model and session identifiers plus numeric counters are retained locally.

## Offline verification

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/grok-usage.py
python3 extras/grok-usage/stage_plugin.py /path/to/new/staged-agents
```

The staging helper depends on the adjacent `extras/local-router-stats` helper
and copies the package-owned plugin into a **new directory**. It never modifies
`/usr/share/omarchy`, installs a plugin, changes shell settings, enables router
metrics, or restarts a process. Existing targets are refused. The clone supports
Local and Grok observed counters; providers are independently visible only when
the corresponding record exists.

## Activation — not performed

The hook is opt-in. Before activating it, inspect the existing
`~/.grok/config.toml` status-line section and preserve any custom row. Do not
blindly replace an existing command or execute it from this collector.

The documented configuration for a new row is:

```toml
[ui.status_line]
type = "command"
command = "python3 /absolute/path/to/collector.py --hook"
refresh_interval = 5
```

Grok reads this setting at startup. Apply it on the next user-controlled launch;
do not interrupt an active session. The hook prints a short observed-token row.
It intentionally does not attempt to chain an existing custom script.

The default record is
`$XDG_STATE_HOME/omarchy/grok-usage/agents/grok.json`, falling back to
`~/.local/state/omarchy/grok-usage/agents/grok.json`. Initialize a waiting record
with `python3 collector.py --initialize` only when activating the feature.

Enable a staged user-owned Agents clone through the supported Omarchy plugin
workflow after preserving current settings. The separate Grok `Agent` is kept
outside the native provider map and sync aggregation. Do not put the record in
`omarchy/agents/usage/` on the stock widget: that would lose this local-only
boundary. Do not override a future first-party Grok quota collector without
reviewing compatibility first.

No live configuration, plugin installation, Grok restart, router restart, login
change, paid model call, commit or push is part of this artifact's verification.
The native first-party widget and collector are neither modified nor replaced.
