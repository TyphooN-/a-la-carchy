# Passive metrics summary

The Local sampler already fetches `/metrics` once for each uniquely observed
loaded worker. The token ledger still uses only its two cumulative counters.
This repair projects three additional gauges from that **same response**:
`prompt_tokens_seconds`, `predicted_tokens_seconds`, and `requests_processing`.
It adds no endpoint or request, and changes neither worker admission nor the
independent `/slots` no-wake/provenance guard.

The additive `engineMetrics` object has its own contract, worker identity and
observation clock. It contains only bounded numeric fields and fixed labels.
Malformed, duplicate, out-of-range, non-ASCII or unrepresentable gauges withhold
the entire optional summary without discarding valid token history. Zero phase
throughput is unobserved (an empty averaging bucket), not a fabricated zero speed;
zero processing is a legitimate observed activity value. Missing is not zero.

These are **engine-reported averages over recorded work**, not instantaneous
speed, not task-bound measurements, and not token-count deltas divided by the
poll interval. Prompt batches and completed generation crediting differ from live
slot progress; the engine may reset its averaging buckets on metrics scrapes.
There is no inferred request-completion timestamp or exact averaging interval.
The existing sampled current-task rate path remains separate and unchanged.

The generated Local text uses metrics only when its own worker/time binding is
fresh, validated and compatible with the row. It labels that path `Engine
averages` and `not current-task rates`; qualifying slot samples keep their
existing `Sampled wall-clock · current tasks` label. An independent UI clock
expires either path even when the producer stops. Metrics activity cannot become
a forged task identity. Context/cache and exact request counts are not inferred.

The native model-mix hero excludes Local by design. Its mostly-model phrase now
says `Cloud history`, rather than implying that it describes the whole current
workload. Cloud and sampled Local ledgers retain their existing deduplication and
coverage semantics. The current native `usageNote`/`UsageNote` shape is supported
only with exact complete witnesses; legacy support and ambiguity refusals remain.

Source/self-test proof, independent review, managed installation, natural live
publication and actual popup inspection are separate stages. A headless fixture
or source-review PASS does not prove that a real Heretic request ran. Never load
a model, restart the router, reset history or weaken slot admission for this proof.
