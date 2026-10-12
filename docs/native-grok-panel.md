# Native Grok in the Agents + Local panel

When the installed Agents manifest advertises Grok support, the managed clone
retains the native Grok account record. It does not inject a second status-line
observer provider or replace the native subscription quota with token counts.
Native-shadow refusal remains enforced for the optional custom Grok collector.

The native subscription meter is shown before token details. A measurement is
usable only when its successful-fetch timestamp is present and recent, its
stale flag is clear, its percentage is valid, and its reset window has not
expired. The freshness allowance is twice the configured refresh interval
(with a one-minute minimum and invalid settings falling back to 15 minutes).
Reset forecasts and old cached zeroes are not new measurements. Missing or
stale quota is labelled explicitly; this applies to individual account meters
as well as the active provider. A genuine, freshly measured zero remains valid.

The same section retains native CLI model token history below the quota:
input, output, cache-read input and cache-creation input are separate buckets.
These are recorded CLI counters, not subscription usage. Unavailable history
is not described as zero usage. The underlying native records and the optional
status-line collector/ledger are left intact; their different coverage is not
silently combined. Rendering is bounded to 128 model entries and 512-character
labels, with an explicit retained-data notice rather than modifying the ledger.
Dynamic text uses PlainText.

## Verification

`tests/grok-native-panel.py --main <candidate>/Main.qml --panel <candidate>/Panel.qml`
executes the actual admission and quota/formatter JavaScript with offline
records. It checks one native Grok row, quota/model preservation, freshness,
expired and malformed windows, genuine zero, account meters and model buckets.
`tests/agents-qml.py` additionally loads the native QML data model and text tree
with isolated HOME/XDG paths and command stubs. This headless fixture is not
live popup placement or visual acceptance.

Install through the existing guarded Agents manager. For known task-created
Main.qml/Panel.qml drift, use its exact `--upgrade --preimage` procedure; do
not force, adopt unknown files, change native account data or patch the running
upstream Omarchy checkout. Reload only the shell at the approved boundary,
then inspect the actual popup separately. No router restart is required.
