# Native Folio Recents privacy support

This is a narrow local patch for Folio **0.4.0**, not an upstream upgrade or a
published Folio release. It preserves the MIT-licensed upstream source and its
history index. The pinned source archive and all local build inputs are checked
by `makepkg`; `source-lock.json` also records the native preimages/postimages.

## Contract

With `org.gnome.desktop.privacy remember-recent-files=false`, patched Folio
omits its built-in Recents sidebar entry, returns an empty direct `recent:` or
`listRecent()` listing **before opening XBEL**, refuses cached listing adoption,
drops the view's watcher and rejects late worker/parser results. Ordinary
folders, Trash, bookmarks and plugins retain their upstream behavior. Restoring
the original GNOME value restores that policy, not necessarily `true`.

A La Carchy still owns exactly its existing three version-1 journal targets:
GNOME recording and GTK 3/4 configuration. It admits Folio only when the selected
system desktop launcher and ELF match the root-owned, non-user-writable
`/usr/share/folio/alacarchy-privacy.json` manifest. The launcher uses the absolute
`/usr/bin/folio` path. User/local launcher overrides, missing/stale manifests,
unsafe ownership/modes, symlinks, hardlinks and mismatched bytes refuse support
before Stop creates state. No capability probe launches an unknown GUI flag.
An ordinary upstream package update can remove this support; rebuild/review the
patch rather than copying an old manifest to the new binary.

## Safety and limits

Close **Folio, GTK applications and configuration editors** before Stop or
Restore, keep them closed until completion, and restart them yourself. Do not
upgrade/replace Folio during the transaction. Nothing automatically changes live
privacy settings, closes apps or restarts services. History is not read, copied,
cleared or backed up by the configurator/capability checker. Existing history
remains on disk; independent application/plugin/browser histories are outside
this control. This is not secure erasure or an immediate revocation of data
already visible in an old running process.

Descriptor/version checks detect observed drift, not atomic compare-and-swap
against non-cooperating configuration writers or a privileged package updater.
Restore of previously owned GNOME/GTK settings remains available even after a
handler change or removal of the compatible Folio package. Partial failures
retain the ownership journal for conflict-checked Restore.

## Build and verification

Required tools/dependencies are declared in `PKGBUILD`; do not install missing
ones automatically. The recipe never installs the result or enables policy.
Use an independently reviewed private build envelope: disposable HOME/XDG/TMP,
no live display/session/system buses, no real HOME/history/devices/network,
software/offscreen Qt, memory-only GSettings, bounded CPU/RAM/tasks and a wall
deadline. Derive compile workers from `nproc` **inside that envelope**. Supply the
pinned archive offline as `folio-0.4.0.tar.gz` when network is disabled.

Inside that envelope, set `ALACARCHY_FOLIO_PRIVATE_NATIVE=1` and run:

```sh
makepkg --nodeps --nosign --noconfirm
```

The marker is only an acknowledgement, **not proof of isolation**; the caller
must enforce the envelope. `check()` refuses an unacknowledged environment and
requires private HOME plus memory/offscreen settings. It runs the real reader
and model regressions with synthetic XBEL, enabled/restored controls, inotify
open/watch observation, cache/query/reload and late-result checks. Package
creation includes the actual `folio` executable; `!strip` is intentional because
after-the-fact stripping would invalidate the manifest's ELF hash. Do not remove
that option without moving capability generation after the final ELF transform.

Run Python controller/admission tests separately in the same kind of disposable
namespace: `tests/privacy.py` and `tests/folio-capability.py`. Their root-ownership
fixtures are explicitly synthetic; they do not prove an installed system package.
The upstream native test suite and installed-package/menu proof are separate
checks, not implied by source checks or a successful build.

Local installation requires completed proof and independent privacy acceptance.
Do not use `makepkg -i/-s`, install dependencies, activate policy, restart apps,
publish binaries/releases or push source merely because this recipe built.
