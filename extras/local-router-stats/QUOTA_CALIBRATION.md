# Passive Codex quota/token calibration (opt-in candidate)

`quota_calibration.py` correlates **future observed** canonical Hermes token
counter deltas with **used** weekly Codex quota movement. It never calls a model,
quota endpoint, credential loader or database. It does not start or repair either
producer. An absent canonical feed is a deployment prerequisite, not zero usage.

This is not a published token entitlement. A token/quota-point rate depends on
model, effort, cache mix, task shape and other uses of the subscription. Hermes
has no credential ID in its canonical ledger: even exclusive observed quota
movement establishes **correlation only**, not credential attribution. External
Codex/ChatGPT activity can bias this estimate; it cannot be ruled out offline.

## Inputs and units

Both inputs must already exist in owned, non-symlink directories. Individual
files must be private (0600), bounded, singly linked, owned regular files.

* `--canonical`: the sanitized `canonical_feed.py` export, including
  `feed: hermes-canonical-feed/1`, source/configuration bindings, `observedAt`,
  `contract: hermes-session-model-usage/1` and
  `counterSemantics: hermes-canonical/1`. It is **not** the aggregate `usage.json`,
  an auth file, a DB or an arbitrary provider response. The maintained
  `hermes_accounting.canonical_snapshot` performs the token projection:
  input + output + cache-read + cache-write, once each; reasoning is an output
  subset. No new raw-token normalizer is introduced.
* `--quota-dir`: the existing sanitized scanner publication made by
  `hermes-codex-usage/collector.py --usage-dir`, including its inventory,
  ownership hash manifest and all current inventoried Codex account records.
  No scanning outside that explicit directory. The collector's
  `normalize_window` maps `used_percent / 100` into `limits[].percent`.
  Thus a change 0.10 -> 0.12 is **2 quota percentage points used**, not 2%
  remaining. Only the 604800-second (weekly) window is used for calibration.
* `--profile`: the public canonical feed profile label, not a credential name.
  The operator must qualify that the feed and quota inventory cover the same
  active profile/provider and relevant workload. A file hash/binding is not
  independent proof of that association. Multiple profile aggregates, parallel
  producers or incomparable scopes must not be substituted.

Every currently inventoried Codex principal must have fresh usable weekly quota
metadata. Missing/stale/partially published quotas refuse the sample rather than
silently dropping an account. Other provider entries remain in the inventory
hash but contribute no Codex token or quota data. Dynamic principal hashes, not
slot ordinals or a two-account assumption, key the state.

## Conservative admission

* First observation establishes a baseline only. No historic daily tokens,
  account lifetime counters or weekly token allowance are manufactured.
* Canonical observation and every quota observation must be no more than 120 s
  old, not future, and paired within 120 s. Every admitted subsequent pair must
  have strictly increasing canonical **and all quota** observation clocks.
  Exact repeated observations are byte-idempotent. A new canonical snapshot
  with a repeated quota clock is refused; schedule near existing quota refreshes
  for useful pairs. Refusal never refreshes an old estimate's expiry.
* Completed segments need 300–7200 s, 1–25 **used quota points**, a positive
  canonical token delta, and positive movement in exactly one principal. Both
  the canonical and every quota clock must span at least 300 s. Fresh monotonic
  short pairs coalesce against a separate learning start; they update the latest
  publication clocks without pretending that each minute is a new sample.
  Every admitted pair still passes the identity, counter and window guards.
  Even tiny movement in a second principal anywhere in the learning window is
  ambiguous and discards unfinished progress. Long gaps and source/inventory/
  window transitions also discard it. Too little resolution or high ratios earn
  no samples. Within an otherwise valid window, tokens preceding a rounded quota
  tick remain **unassigned**, bounded by the same 7200-second lifetime. Old
  samples stay retained but **no account estimate is eligible** on unresolved
  polls. A later tick must include all accumulated tokens, never only that
  tick's minute. A sample can finish only on an actual new quota tick, including
  a delayed tick with no new tokens; this avoids closing on a flat percentage
  while more tokens accumulate. Unresolved usage across a long observation gap
  or source/inventory/window rebaseline still clears retained samples. Newly
  seen positive source counters never earn sample credit.
* New canonical source identities establish new baselines; their accumulated
  historical counters are never assigned to the new interval. Disappearing
  sources, any counter component decrease or changed feed association refuse
  the sample. A real source reset needs explicit operator-qualified fresh state.
* Inventory membership changes skip attribution across the transition. Removed
  principals are dropped, new principals start empty; no rate is borrowed.
  A changed reset timestamp, quota decrease or plan tier clears the affected
  principal's samples and skips the transition. Window rollover cannot retain
  an old-window rate.
* A numeric estimate requires at least **3 account-specific usable segments**
  covering at least **1800 observed seconds**. The latest pair must complete a
  usable segment, consistently continue an unfinished segment for that same
  previously usable principal, or be wholly idle after an eligible observation.
  A fresh idle pair does not erase a rate or add sample coverage. A partial segment adds no sample or observed
  coverage and borrows no other principal's rate. Keep at most 24 segments, no
  older than 24 h, in the current quota window. A max/min observed rate ratio
  above 2 withholds the estimate. This range is empirical dispersion, **not a
  confidence interval**.
* Rate = sum(observed token deltas) / sum(observed used quota points).
  Estimated already-used weekly tokens = rate × current used weekly points.
  This extrapolates the sample workload to earlier unobserved weekly usage;
  it is explicitly estimated/workload-dependent, not measured history.

Private journal version 3 permits unassigned rounded-quota windows and requires
their statuses to withhold estimates. Versions 1 and 2 retain their exact recovery
and repeated-observation behavior; a new admitted pair migrates without wiping
earned samples. Version 2 is still validated against its original stricter
segment shape. The public consumer contract remains version 1, so no panel
replacement or shell restart is required. No historical quota observations are
invented or backfilled during migration.

These deliberately strict defaults can leave the display calibrating for a
long time. Do not generate synthetic tasks, spend tokens, relax guards or borrow
another principal's samples just to populate it.

## CLI and dormant scheduling

For an already qualified deployment (illustrative paths only):

```sh
TOOLS="$HOME/.local/share/a-la-carchy/local-router-stats"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/hermes-token-accounting"
# STATE must already be an owned controlled directory; this CLI creates no roots.
python3 -I -B "$TOOLS/quota_calibration.py" \
  --canonical "$STATE/canonical.json" \
  --quota-dir "$HOME/.local/state/omarchy/agents/usage" \
  --profile default \
  --state "$STATE/quota-calibration-state.json" \
  --output "$STATE/quota-calibration.json"
```

Use the **actual already-qualified quota publication directory**, not the example
if different. Exit 0 means an admitted observation or exact replay; exit 2 means
refusal. The error is generic and prints no private input. `--now` is an explicit
RFC3339 clock for offline replay only; never put it in a scheduled unit.

`manage.py --install-tools` includes the sidecar and its two templates through
the existing guarded managed-file transaction. This alone does not create or
activate a calibration service, alter a layout, collect data or enable the
currently incomplete canonical feed. No new install mode is required.

The same explicit options plus `--render-service` print a quoted dormant unit
using the executing installed helper path. Rendering requires no live input and
writes nothing. Save the result for independent review as
`hermes-quota-calibration.service`; pair it with `quota-calibration.timer` renamed
to `hermes-quota-calibration.timer`. Paths, literal `%` and `$`, and spaces are
encoded for systemd, not a shell. The template uses a 15 s timeout, 0077 umask
and no automatic producer dependencies. The timer polls once a minute; it only
reads publications and cannot refresh quotas. Most polls may refuse while
existing producers are stale or asynchronous. It neither catches up missed
history nor spends tokens. Installing/enabling it is a separate parent-owned
guarded operation; no service activation is implied by source or fixture proof.

## Persistence, limits and recovery

All source, state and export paths, deterministic temporaries and the lock are
alias-checked before any input read or cleanup. Outputs cannot be in the scanner
directory. Inputs are never modified. The sidecar uses the maintained private
I/O, nonblocking advisory lock, atomic replacement and fsync helpers.

Private state `/2` separates the latest observation from its compact unfinished
learning segment; it does not duplicate the canonical ledger. Supported `/1`
journals retain exact recovery/repeated-observation bytes and migrate only on
an admitted new observation, starting unfinished learning at their saved anchor
without inventing older samples. Both versions must pass the current projection
and transaction checks; an incompatible payload is refused, not silently
rewritten or discarded.

The journal commits before the export and binds the output path, prior payload
hash and expected new payload. Interrupted export can be replayed exactly once
by rerunning the same fresh observation, or with the same paths and `--recover`
(no new observation). Recovery **does not update freshness**. A foreign/manual
output edit, malformed state, configuration drift or unexpected preimage is
refused, not silently overwritten. Preserve both files for review. For a genuine
producer/source replacement, separately archive the state **and** output before
starting a qualified new baseline. Never delete live state automatically.

Bounds: 4 MiB canonical input; 128 KiB inventory and manifest each; 64 KiB per
quota record; 256 inventory/principal entries; 512 canonical source identities;
24 samples/principal; 1 MiB journal; 1 MiB export/parser budget; safe integer
counters <= 2^53-1; token/point rate <= 1e9. CLI address space and wall clock are
bounded by the maintained feed's process limits. The generated reader transports
at most 1 MiB + 1 byte and refuses the extra byte. The capacity regression
constructs the full account/source/sample cardinalities and checks serialized
size; it is a synthetic capacity proof, not evidence of 256 live Codex accounts.

## Presentation and acceptance boundary

The maintained limits-first stager adds a **separate** per-Hermes-account weekly
estimate/calibrating line. No provider CLI aggregate is copied onto account
cards, no Codex CLI row is added, and existing quota meters, measured counters,
Cloud/Local totals and token history/chart inputs are unchanged. The calibration
file is not an Agent, is not scanned as a provider and is never included in sync
snapshots. The legacy tabs layout is otherwise unchanged (CLI remains usable).

The generated consumer joins the exact inventory generation/principal, used
fraction, observation clock and reset to the cleaned quota record. Reset joins
retain the producer's microsecond ISO precision instead of truncating to
JavaScript date milliseconds. The envelope's expiry is bounded by both the
canonical and quota observation deadlines and the reset; the ticking text also
checks the quota clock independently, including future and unobserved clocks.
It reads only the private local export through a bounded `head` process every
30 s, and requires
both complete stream and successful process exit before parsing. Its existing
panel clock expires the estimate independently of publication; stale, reset or
malformed values display calibrating. Numeric display says approximate used
weekly tokens, estimated/workload-dependent, sample count, observed duration,
observed rate range and correlation-only/no confidence-bound caveats.

Offline regression coverage includes real CLI baseline/learning/refusals,
producer percentage direction, distinct principals, inventory rotation, resets,
mixed activity, malformed types, monotonicity, alias refusal, journal replay,
managed packaging, generated JS admission/text and expiry without a file write.
It does **not** establish live input association, installed helper provenance,
current native stager compatibility, instantiated QML behavior/geometry or visual
acceptance. The parent must independently accept this source delta, qualify
sanitized real input contracts/cadence/profile association, stage against the
current native plugin, review/reversibly install the units, and obtain separately
authorized activation/rendered acceptance. Never read auth or spend inference
tokens to qualify this feature.
