# Passive Local slot admission — one byte-associated runtime

The managed collector now has a conditional admission path rather than an
unconditional refusal. This does **not** authorize installation, a router
restart, model loading, a live `/slots` probe, or a change in sleeping policy.
Unknown/unqualified observations remain unavailable, never zero activity.

## Source and build association

Only the maintained HIP bundle associated with upstream
`bed0a856606ee4a24a164066f73d2379447033f5` is represented by
`RUNTIME_ARTIFACTS` in `collector.py`. This is a maintained build/source
association, **not** reproducible-build attestation. A version string, source
pin, `argv[0]`, release-directory name, flag or historical PID alone is not
accepted as deployed provenance.

Retained association (read-only evidence under Hermes's verification cache):

- `llama-upstream-refresh-4zkv5gta/latest-hip-build.json`: matching revision,
  fresh whole-runtime build and recorded build exit 0.
- `llama-upstream-refresh-4zkv5gta/llama-latest-runtime-hg5ugeyn/manifest.json`:
  per-artifact sizes/SHA-256 from that build, including the launcher,
  `libllama-server-impl.so` and `libllama-common.so.0.5.0` (not only a launcher hash).
- `llama-upstream-refresh-4zkv5gta/handoff-final.json`: source/build/release
  association, local source integration `32bf74b8ab673c646a7b7e6a892a869edfd5a7e5`,
  patched source tree `b1278e5d9b820e2597c5f98a0551c2e5f2fb4cfe` and final patch
  SHA-256 `24a75e977498cfeeb9a50742c56dd33687d1bd95742c3027b369698f3e35e439`.
  The final patch is the committed `upstream/patches/0001-convolution-kernel-types-bed0a8566.patch`,
  **not** the intermediate `ported-latest-source.patch`. Its model/CTest changes
  do not touch the parser or server/queue sources below.

This repair's `source-contract.json` / corrected projection in the evidence
packet records the exact source hashes and all eleven original bundle artifact
hashes. Static reads matched every installed disk artifact to that retained
manifest. That observation is **not** current-process/mapped-library qualification.
Only nine launcher/engine libraries are eligible in the collector; CLI and
quantizer implementation libraries are deliberately not admitted as server code.

Version-matched semantics, with line numbers in that exact upstream revision:

| Source | Evidence |
| --- | --- |
| `common/arg.cpp:819–869` | Names normalize `_` to `-`; consumed values are not reinterpreted as options; handlers consume zero, one or two split values. This pin does not accept attached `--name=value` syntax. |
| `common/arg.cpp:3810–3818` | The sleep option's disabled value is **-1**; 0 and values below -1 are invalid. |
| `tools/server/server-context.cpp:4422–4431,4923–4934` | `/slots` normally creates a response that can wake a sleeping queue; negative sleep disables that wake path. |
| `tools/server/server-queue.cpp:278–296,326–350` | Negative idle-sleep duration prevents entering sleeping state. |
| `tools/server/server-context.cpp:694–719,2640–2660` | Slot projection contains per-slot/task identities, evaluated prompt counters and current-task decoded-token counters. No lifetime averages are substituted. |

The argument schema reuses the maintained bounded collector reference's
arity/alias projection. It is intentionally **not** the engine's complete CLI.
Unknown options deny slot admission until a separately reviewed schema extension
is proved against this exact parser. Historical `/metrics` sampling retains its
existing path and accounting behavior; it does not gain a `/slots` exception.

## Request-time checks

For every pre/post slot observation, the collector requires:

1. One router child matched by alias/port across all router threads; a stable
   boot/PID/start generation, parent, child inventory and in-memory argv snapshot.
2. Value-aware listed/child argument projections, no duplicate/conflicting
   alias/port/host/sleep/slots spelling (short aliases and normalized names
   included), and an actual child **explicitly** specifying sleep `-1`.
   An option-looking path or two-value argument cannot supply that policy.
3. A unique literal IPv4 loopback listener at the selected port, owned by that
   child, in the collector's network namespace. Wildcard/ambiguous/unowned
   listeners deny admission.
4. The kernel-held `/proc/PID/exe` bytes matching the associated launcher, plus
   the actual relevant mapped server/parser/engine libraries matching associated
   bytes and device/inode relationships. Necessary libraries must be present;
   unknown, deleted, missing, changed or ambiguous engine mappings deny admission.
5. Revalidation after hashing, including kernel-held mapping relationships, and
   the existing generation checks on both sides of the slot read. Same-size
   changes invalidate the bounded metadata-keyed hash cache.

Btrfs `maps` can report a superblock device different from `stat`'s subvolume
device. As in the maintained serving-provenance validator, only a readable
kernel-held `map_files` object matching the disk object resolves this case.
If permissions prevent that proof, rates remain **unavailable**. This repair
does not grant capabilities, invoke the separate native attestor prototype,
weaken device checks, or infer association from an equal inode or pathname.

These are bounded point-in-time checks in the existing cooperative same-UID
operating model, not an atomic reservation or protection against hostile
same-UID process/loader modification. The collector never reads environments,
prompts, model files or credential stores for admission; raw argv/maps and
exception payloads are not published.

## Offline proof and remaining gate

`tests/local-slot-admission.py` exercises the real admission and sampler/export
path with a fake process tree, synthetic byte pins, fixture SQLite and recorded
transport callbacks. Network, native subprocesses and live `/proc`/SQLite are
denied by an audit hook; attempted forbidden entrypoints are checked outside
production exception handlers. Synthetic success is **not** engine qualification.

The original positive regression fails with null prompt/generation rates before
the correction, then publishes task-bound rates through the real production
sampler/ledger/export. Negative controls cover unqualified bytes, duplicates,
consumed-value decoys, listener/mapping uncertainty, cache changes and worker
changes. A separate retained RED exposed the final-hash mapped-object race.
The fixture timestamp discriminator is a harness failure, not a product failure;
its repaired monotonic clock and admission counters prevent later matrix cases
from passing through an earlier ledger refusal.

Generated Main/Panel code, optional account features and composition anchors
are unchanged. Independent source acceptance remains open. The parent must
qualify a naturally present worker's current executable/mappings/parser options
under its own authority before any guarded collector/shell installation and
activation. No model should be loaded or sleep policy changed just to make
this proof pass.
