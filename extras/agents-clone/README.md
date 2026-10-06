# Managed Agents clone: installation and guarded updates

The installed native source is read-only. The clone admits exactly the two
published slots **Codex (Hermes 1)** and **Codex (Hermes 2)**; native Codex and
other `codex-*` IDs are excluded locally, from synced admission and from local
snapshots, without deleting native account data. Hermes slots remain read-only
through normalization; no native sign-in, switch or rename action is available.
The limits-first panel reuses the native Codex dark/light marks. First-party
Grok is retained once, including its quota; the optional custom Grok fallback
is refused when the native manifest supports it. Local's sampled evaluated-input
and generated-output counters and recording gaps stay on this machine.

## No activation by installation

The commands below are **future installation instructions**, not evidence of a
live install. They copy user-owned tools/units or update a managed clone. They
never read auth, collect quotas, start services, reload the shell, contact the
router, enable metrics or load a model. A separately authorized activation and
live popup check remain necessary. The legacy configurator menu uses the full
checkout; the installed CLI below is the standalone maintenance path.

Substitute the exact verified repository, native source, shell configuration
and plugin directory. Keep source/candidate fingerprints and the independent
review receipt before using any installation command.

```sh
# From a reviewed complete checkout: install the entire standalone tool bundle.
/usr/bin/python3 -B extras/agents-clone/manage.py \
  --source /path/to/omarchy/shell/plugins/agents \
  --shell /path/to/shell.json --plugins /path/to/user/plugins \
  --install-tools local

# New deployment only: enable Local (installs an INACTIVE local collector unit).
/usr/bin/python3 -B "$HOME/.local/share/a-la-carchy/agents-clone/manage.py" \
  --source /path/to/omarchy/shell/plugins/agents \
  --shell /path/to/shell.json --plugins /path/to/user/plugins --enable local

# Existing managed deployment: explicitly rebuild the enabled clone.
/usr/bin/python3 -B "$HOME/.local/share/a-la-carchy/agents-clone/manage.py" \
  --source /path/to/omarchy/shell/plugins/agents \
  --shell /path/to/shell.json --plugins /path/to/user/plugins --upgrade local
```

`--install-tools` installs `manage.py`, `upgrade.py`, both stagers, both Local
and optional Grok collectors, the Local service template and the Hermes quota
collector under `~/.local/share/a-la-carchy/`. Every file is registered by hash
in `managed-files.json`. Unknown differing programs are refused **before any
writes**. Byte-identical copies may be registered; registered prior versions
can be replaced. Installing from a newly reviewed checkout updates the installed
bundle; `--upgrade` then regenerates from the explicit native source using those
installed stagers. Repeating `--enable` alone does not regenerate an in-use clone.
The installed CLI needs neither repository paths nor the configurator to run.

## Hermes scanner publication: explicit and inactive

```sh
# Also installs the tool bundle. Choose the exact Hermes profile explicitly.
/usr/bin/python3 -B extras/agents-clone/manage.py \
  --source /path/to/omarchy/shell/plugins/agents --shell /path/to/shell.json \
  --plugins /path/to/user/plugins --hermes-home /path/to/hermes/profile \
  --install-bridge local
```

This generates an **inactive** `hermes-codex-usage.service` and `.timer` under
`XDG_CONFIG_HOME/systemd/user` (or `~/.config/systemd/user`). The service invokes
the installed collector with explicit `--hermes-home`, `--state-dir` and
`--usage-dir` paths frozen from this installation's HOME/XDG values. The scanner
destination is `XDG_STATE_HOME/omarchy/agents/usage`; it is never inferred from a
fixture HOME at collection time. Percent/dollar expansion in unit paths is
escaped. A different profile or XDG location requires explicit reinstallation.
No systemd command is run by the installer; it neither enables nor starts the
timer. After separately approved activation, the timer checks every five minutes.
Do not install a second bridge timer for the same scanner/state directory.
The collector uses existing credentials read-only, does not refresh/login, and
publishes matching last-known limits as stale when a check fails. Omitting
`--usage-dir` in a direct collector invocation writes private state only.

## Existing task-edited deployments: narrow adoption, never force

Default upgrade accepts only a clone whose complete file set (including its
manifest) matches the separate managed-file registry. The clone's editable
`managedFiles` manifest is a shape check, not an ownership witness. Initial
staging records the generated clone hashes; editing that manifest cannot
redeclare an unknown `Agent.qml` or extra asset as managed. A known earlier task
may have changed `Main.qml`/`Panel.qml` or a **registered enabled-feature**
collector/unit. Only after a human/parent has reviewed and authorized those
exact bytes, pass a private preimage JSON to the reviewed checkout manager:

```sh
/usr/bin/python3 -B extras/agents-clone/manage.py \
  --source /path/to/omarchy/shell/plugins/agents --shell /path/to/shell.json \
  --plugins /path/to/user/plugins --upgrade --preimage /private/approved.json local
```

Schema (hashes are SHA-256, no file contents or secrets):

```json
{"version":1,"clone":{"Main.qml":"<hash>","Panel.qml":"<hash>","manifest.json":"<hash>","<every-other-clone-file>":"<hash>"},"registry":"<exact-registry-hash>","shell":"<exact-shell-hash>","files":{"/absolute/registered/enabled-feature/collector.py":"<hash>"}}
```

`clone` must enumerate **every** file. `files` contains only explicitly approved
modified enabled-feature collector/unit paths; unchanged registered files need
no entry. This cannot adopt unknown assets, `Agent.qml`, extra clone files,
unregistered collectors or changed configuration/registry bytes. Any subsequent
edit invalidates approval. There is no force flag. Never create the approval by
blindly hashing a deployment and treating the result as authorization.
Legacy deployments without registered clone hashes require version **2** with
one additional `clone_baseline` hash map. That map must come from a separately
retained, reviewed original task snapshot, never the editable clone manifest
or a fresh inventory of unknown current files. Both maps enumerate every file;
only `Main.qml`/`Panel.qml` may differ between the trusted baseline and approved
current preimage. The manifest, `Agent.qml`, assets and file set remain bound to
the baseline. A supplied baseline cannot override existing registry witnesses.
Missing trustworthy historical bytes are a refusal, not permission to adopt
the whole directory. After the approved upgrade, all clone files are registered.
If an old registered collector was task-edited, perform this approved upgrade
**before** `--install-tools`/`--install-bridge`, which otherwise correctly refuses
that differing file. Freeze any preimage after all intended prerequisite writes.

## Guarantees, recovery and limits

- Enabled upgrade preserves exact shell layout/configuration bytes and does not
  drop enabled features. A transition to native Grok requires explicitly
  disabling an old custom Grok feature before regeneration.
- Unused-clone rebuilding through ordinary disable/re-enable uses the guarded
  clone-upgrade transaction too. Clone and registry commit together before the
  feature's later enable steps; staging-time clone, registry, shell or native
  source changes cause refusal. This is not atomicity across the entire enable
  operation, subsequent tool installation, layout changes or service activation.
- Tools and clone upgrades use the same advisory lock, private backups, exact
  pre-write checks and readback. No-follow paths reject symlinks, FIFOs, linked,
  oversized or foreign files. Unsupported/duplicate QML anchors fail closed.
- Clone ownership validates the complete bounded node inventory. Unknown sockets,
  devices, linked files and unrepresented empty directories are refused rather
  than ignored by a regular-file hash list and deleted during replacement.
- Native staging validates the whole tree before content reads: no linked root,
  ancestors, files or directories, hardlinks or nonregular nodes. No-follow
  reads/copies are bounded to 1,024 entries, depth 32, 4 MiB per file and 32 MiB
  total. These limits are not a hostile concurrent-writer atomic snapshot.
- Detected write failures roll back only still-matching bytes; concurrent user
  edits are preserved. Backups under `agents-upgrade-backups/` retain original
  clone, registry, shell and replaced files. Incomplete rollback is a HOLD;
  inspect the backup and reconcile exact paths before retrying. No blind copy
  over newer edits. Disable restores the original native widget entry and keeps
  history, installed programs and the clone for review.
- These are cooperating same-user installer safeguards, not an OS-level atomic
  compare-and-swap against hostile non-cooperating writers. A process crash can
  leave a partial transaction; backups are retained recovery evidence, not an automatic
  crash-recovery journal. Tool bootstrap and clone regeneration are separate
  transactions. No claim of atomicity across activation or multiple JSON scanner
  files; publication uses private per-file atomics, ownership checks and rollback.
- Native QML fixtures exercise the real installed Main/Panel with isolated
  HOME/XDG, stub commands and offscreen Qt. Only the layer-shell KeyboardPanel
  is replaced with an Item. They verify instantiated visible text, quota/status
  records, controls and marks, not live popup focus/placement or visual quality.
  Historical tab compatibility is synthetic regression coverage; native runtime
  qualification is bound to the fingerprinted limits-first source tested here.
