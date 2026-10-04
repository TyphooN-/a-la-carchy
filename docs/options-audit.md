# Existing-options audit — checkpoint, not a completion claim

## Scope and safety

This audit covers the current local Lua/Quickshell port. Tests use isolated HOME fixtures and mocked system commands. No live package removal, power/firmware action, authentication change, monitor change or shell reload was performed. Hardware/runtime behavior is not certified by these tests.

The full option-by-option inspection is **incomplete**. Claude’s attempted execution hit a provider session limit; the continuation must finish this matrix and independently review the shared config-editing changes before acceptance. Existing staging is preserved; changes are not committed or published.

## Reproduced defects repaired at this checkpoint

- `hypr_reload_check`: failed reload and config-error queries previously returned success because command statuses were discarded.
- `lua_block_remove`: an unmatched start marker silently deleted the remainder of the user file; invalid/nested marker pairs are now rejected before editing. Write failures are propagated, and temporary filenames are unique.
- `backup_file`: failures previously printed a success-looking backup line; same-second backups collided. Backups now use unique names and propagate copy failures.
- `apply_shell_change`: malformed JSON could be treated as an already-applied removal; operation failures ended with a successful `echo`. JSON/layout checks and postcondition checks now reject those false positives.
- `apply_lua_block`: backup, block-write, and reload failures now return failure instead of logging success or washing failure away with trailing output.

`tests/options.sh` covers these negative paths plus managed Lua and bar-widget round trips. `tests/workspace-nav.sh` covers the relative-navigation feature, with isolated offline Hyprland validation. `tests/dictation.sh` covers allowlisted native/Flatpak removals entirely through mocks. Their logs are proof of the stated scopes, not proof every option works on real hardware.

## Power auto-switch safety checkpoint

The previous generator used a udev RUN command that executed a user-writable script from HOME as root. The revised generator uses only `/usr/bin/powerprofilesctl` with allowlisted profile arguments and matches Mains power supplies without assuming AC device names. It reads saved values as data (new rule metadata first, legacy script fallback), never executes the legacy script, and propagates install/remove/reload/asusd sync failures without a final success summary.

`tests/power-auto-switch.sh` exercises generated rules, malformed profile arguments, missing backend, rule writes/removals/reloads, profile readback, and asusd write/restart failure handling with fail-closed mocks. Independent security review is **pending**; real udev dispatch/profile switching is **not tested**. No installed rules were changed, so this repository patch alone does not remediate a previously installed unsafe rule. Transactional rollback and unrelated pre-existing rules still need review.

## Three-menu implementation checkpoint

The preserved implementation adds **Privacy & OPSEC**, **OS Hardening** and **Custom Kernel** without changing the existing category indices. Open these actions with **Space**; they do not become queued toggles or inherit **Apply all**. Source code and fixtures are present, but **independent security review remains pending**. No live GSettings/dconf, services, history/cache, sysctls, elevated commands, package installation, kernel build or boot changes were performed for this checkpoint.

| Group / ID | Actual behavior | Evidence boundary |
|---|---|---|
| `PRIVACY_ITEMS / privacy_recent` | GNOME `remember-recent-files=false` plus GTK 3/4 `gtk-recent-files-enabled=false`; Restore carries caller-checked bytes into descriptor-pinned writes/deletions and refuses detected newer edits. | `python3 tests/privacy.py`: round trips, missing/locked backends, malformed config, failed/no-op commands and deterministic ownership-check/read/stat interleavings, including deletion and retained recovery journal. Close GTK writers/editors for Stop/Restore; POSIX residual windows remain. Independent app histories are outside scope. |
| `PRIVACY_ITEMS / privacy_indexing` | Masks/stops only stock static `localsearch-3.service` and `localsearch-control-3.service`; Restore retains original mask/activity. Existing index stays; search may degrade. | Same suite: full two-unit transitions with faithful `LoadState=masked`/`FragmentPath=/dev/null`, owned unmask, original active/inactive and preexisting mask preservation, inconsistent/custom property refusal and partial recovery. No live service mutations or shared GVFS/portal/D-Bus/writeback/security-service changes. |
| `PRIVACY_ITEMS / privacy_thumbnails` | Nautilus `show-image-thumbnails='never'`; Restore preserves prior explicit/unset value. Cache stays; other apps may thumbnail. | Same suite: repeated stop/restore, newer edits and false-success readback refusal. All privacy actions require Nautilus as the default directory handler. |
| `PRIVACY_ITEMS / privacy_purge_recent` | Deletes only `$XDG_DATA_HOME/recently-used.xbel`. | Same suite: confirmation, absent/repeated target, no history backup, symlink/hardlink/external-XDG refusal. |
| `PRIVACY_ITEMS / privacy_purge_thumbnails` | Deletes only `$XDG_CACHE_HOME/thumbnails` without following symlinks; hardlinks/special files refused. | Same suite: synthetic cache tree and unsafe entry refusal; concurrent-writer behavior is not certified. |
| `HARDENING_ITEMS / hardening_audit` | Read-only bounded four-key runtime/persistence audit; not a security certification. | `python3 tests/security-menus.py`: renderer/dialog PTY audit with no privilege call. |
| `HARDENING_ITEMS / hardening_kptr` | Opt-in `kernel.kptr_restrict=2`; debugging/profiling cost, `%pK` scope only. Persistence bytes are checked after kernel memfd sealing and delivered on stdin, never from a mutable user payload path. | `python3 tests/hardening.py`: all four round trips with mocked privilege; real native unprivileged install under a delayed source-swap attempt copies only approved bytes. Kernel write/truncate/reopen attempts fail; construction edits/unsupported sealing refuse before privilege. No real sudo/live sysctl proof. |
| `HARDENING_ITEMS / hardening_dmesg` | Opt-in `kernel.dmesg_restrict=1`; CAP_SYSLOG required for logs, unprivileged troubleshooting cost. | Same suite: missing key, persistence conflict, permission/command failure, partial restore, newer edit/symlink and no-op install refusal. |
| `HARDENING_ITEMS / hardening_ptrace` | Opt-in Yama `kernel.yama.ptrace_scope=1`; debugger-attachment cost. Never lowers existing values 2/3. | Same suite: stronger-value refusal/no takeover. No namespaces, sandbox or authentication changes. |
| `HARDENING_ITEMS / hardening_bpf` | Opt-in reversible `kernel.unprivileged_bpf_disabled=2`; BPF-tooling cost. Never writes/lowers irreversible-until-reboot value 1. | Same suite: stronger-value refusal/no takeover; real-kernel support is not tested. |
| `CUSTOM_KERNEL_ITEMS / kernel_path` | Explicit absolute session-only checkout path; literal default `/home/git/linux-tkg` is never silently substituted. | `python3 tests/custom-kernel.py` and PTY surface suite: missing path, unsafe/symlinked source refusal, explicit path selection. |
| `CUSTOM_KERNEL_ITEMS / kernel_inspect` | Bounded data-only HEAD/loose-or-packed-ref hints, kernel/package information and unevaluated literal config hints; effective config and tracked dirty state unknown. No Git process runs on the checkout. | Kernel suite: born fixture with real configured clean-filter sentinel remains unexecuted, all process spawning forbidden during inspection/packages/preview, checkout bytes preserved, malformed/symlinked refs refused or unknown. Objects/effective worktree/ref backend, recursive untracked inventory and linked worktrees are unverified/unsupported. |
| `CUSTOM_KERNEL_ITEMS / kernel_packages` | Bounded top-level local archive `.PKGINFO` name/version/architecture only. | Kernel suite: synthetic archive metadata, malicious metadata member and symlink refusal; payload integrity/bootability unverified. |
| `CUSTOM_KERNEL_ITEMS / kernel_preview` | Local-README-derived, quoted `cd -- <root> && makepkg`, **NOT EXECUTED**; sync/install flags omitted. | Kernel and PTY suites: quoted path, no checkout-code execution. No compilation/install/initramfs/signing/boot/default/fallback changes. |

Earlier independent review reproduced four P1 defects (SEC-01 through SEC-04), despite passing happy-path suites. Regression-first repairs now have failing-before/passing-after receipts and fresh focused suites from the repair owner; **independent acceptance of the repaired delta is still required**. The PTY fixture exercises the real renderer, selection and dedicated dialogs for recent stop/restore, purge cancellation/token, hardening audit/enable/restore/privilege refusal, and explicit kernel path/preview. It extracts an inspected function-only span, **not the top-level live TUI**. This is local fixture evidence, not independent code-review approval or live behavior proof.

Privacy/hardening journals under `$XDG_STATE_HOME/a-la-carchy/{privacy,hardening}/` preserve owned-original configuration and enforce conflict checks; dedicated XDG roots must remain inside safe HOME paths. Hardening persistence is limited to `/etc/sysctl.d/99-a-la-carchy-{kptr,dmesg,ptrace,bpf}.conf`, with one-key runtime writes, no `sysctl --system`, and no takeover of unmanaged equally/stronger values. Partial failures need Restore/manual review before retrying. Purge is a separate **irreversible one-shot deletion, not secure erasure**, with **no sensitive-history/cache backup**, and does not stop future recording; close writers manually first.

Conflict checks are not universal atomic CAS against noncooperating same-UID writers. Close GTK writers/editors before Stop or Restore until completion. The target descriptor/version is pinned before byte validation and rechecked before writes/deletions, but final check-to-rename/unlink, parent/temporary-path changes and readback-to-journal completion still have POSIX residual windows. The advisory operation lock coordinates only this configurator; GSettings/service and multi-resource transitions are not atomic. Linux memfd seals and readable proc descriptors are prerequisites for persistence; actual sudo policy/transport remains outside local fixture qualification.

For this checkpoint `/home/git/linux-tkg` was absent; separately detected `/home/typhoon/git/linux-tkg` on `master` at `20f156c516d2ea5e4ceba75773bb0a4131fa0adb` had user-owned dirty configuration and remained read-only. Config/PKGBUILD and external configs are never evaluated. Source-hint precedence is customization < external config < environment, not effective running CONFIG proof. BORE/LTO/CPU tuning is performance, not hardening; future builds need separate permission, source review and verified fallback/boot evidence.

Manual narrow enable/restore commands and exact history/service targets are documented in [README: Privacy & OPSEC](../README.md#privacy--opsec). They are **instructions only**, not an applied change. Independent security review, real-desktop acceptance and any later live action remain separate gates. The existing-options audit below remains incomplete; the new fixtures do not certify unrelated legacy handlers.

## Remaining audit targets

Read each handler and callers, reproduce remaining defects with safe fixtures, then update status/proof below. Existing code still contains unchecked direct writes outside the shared helpers; these are not fixed merely because the helpers were repaired. Priority examples: monitor edits, settings/editor writes, power startup/persistence, battery udev/helper writes, menu extensions, auth result propagation, and tray D-Bus failure handling. Review current installed command sources instead of assuming old APIs.

## Menu inventory

Status legend: **fixture** means targeted mocked behavior exercised, not real runtime; **pending** requires handler-level review and proof.

| Group | ID | Label | Choices | Current evidence |
|---|---|---|---|---|
| KEYBINDINGS_ITEMS | `close_window` | Close window | SUPER+Q / SUPER+W | fixture: shared-options suite |
| KEYBINDINGS_ITEMS | `shutdown` | Shutdown | Bind / Unbind | fixture: shared-options suite |
| KEYBINDINGS_ITEMS | `restart` | Restart | Bind / Unbind | fixture: shared-options suite |
| KEYBINDINGS_ITEMS | `theme_menu` | Theme menu | Bind / Unbind | fixture: shared-options suite |
| KEYBINDINGS_ITEMS | `workspace_nav` | Workspace nav | HyDE / Omarchy | fixture: workspace suite |
| DISPLAY_ITEMS | `monitor_scale` | Monitor scale | 4K / 1080p/1440p | **pending** |
| DISPLAY_ITEMS | `detect_monitors` | Detect monitors | [Open] /  | **pending** |
| DISPLAY_ITEMS | `position_monitors` | Position monitors | [Open] /  | **pending** |
| DISPLAY_ITEMS | `laptop_display` | Laptop display | Auto off / Normal | **pending** |
| DISPLAY_ITEMS | `primary_monitor` | Primary monitor | [Open] /  | **pending** |
| SYSTEM_ITEMS | `voice_dictation` | Dictation | Remove / Keep | fixture: dictation suite |
| SYSTEM_ITEMS | `suspend` | Suspend | Enable / Disable | **pending** |
| SYSTEM_ITEMS | `hibernation` | Hibernation | Enable / Disable | **pending** |
| SYSTEM_ITEMS | `fingerprint` | Fingerprint | Enable / Disable | **pending** |
| SYSTEM_ITEMS | `fido2` | FIDO2 | Enable / Disable | **pending** |
| SYSTEM_ITEMS | `power_profile` | Power profile | [Open] /  | **pending** |
| SYSTEM_ITEMS | `power_auto_switch` | Auto-switch profiles | [Open] /  | fixture: power-auto-switch suite; independent review pending, no hardware/runtime proof |
| SYSTEM_ITEMS | `battery_limit` | Battery limit | [Open] /  | **pending** |
| APPEARANCE_ITEMS | `rounded_corners` | Rounded corners | Enable / Disable | fixture: shared-options suite |
| APPEARANCE_ITEMS | `window_gaps` | Window gaps | Remove / Restore | fixture: shared-options suite |
| APPEARANCE_ITEMS | `transparency` | Transparency | Remove / Restore | fixture: shared-options suite |
| APPEARANCE_ITEMS | `tray_icons` | Tray icons | Show all / Hide | **pending** |
| APPEARANCE_ITEMS | `omarchy_logo` | Omarchy logo | Remove / Restore | fixture: shared-options suite |
| APPEARANCE_ITEMS | `update_icon` | Update icon | Remove / Restore | fixture: shared-options suite |
| APPEARANCE_ITEMS | `clock_format` | Clock format | 12h / 24h | fixture: shared-options suite |
| APPEARANCE_ITEMS | `clock_date` | Clock date | Show / Hide | fixture: shared-options suite |
| APPEARANCE_ITEMS | `window_title` | Window title | Show / Hide | fixture: shared-options suite |
| APPEARANCE_ITEMS | `media_dirs` | Media dirs | Enable / Disable | **pending** |
| KEYBOARD_ITEMS | `caps_lock` | Caps Lock | Normal / Compose | **pending** |
| KEYBOARD_ITEMS | `alt_super` | Alt/Super | Swap / Normal | **pending** |
| UTILITIES_ITEMS | `backup_config` | Backup config | [Select] /  | **pending** |
| UTILITIES_ITEMS | `menu_shortcut` | Menu shortcut | Add / Remove | **pending** |
| ROG_HARDWARE_ITEMS | `rog_profile` | Platform profile | [Open] /  | **pending** |
| ROG_HARDWARE_ITEMS | `rog_fan_curves` | Fan curves | Enable / Disable | **pending** |
| ROG_HARDWARE_ITEMS | `rog_fan_curve_edit` | Fan curve editor | [Open] /  | **pending** |
| ROG_HARDWARE_ITEMS | `rog_boot_sound` | Boot sound | Enable / Disable | **pending** |
| ROG_HARDWARE_ITEMS | `rog_panel_od` | Panel overdrive | Enable / Disable | **pending** |
| ROG_HARDWARE_ITEMS | `rog_dgpu` | Discrete GPU | Enable / Disable | **pending** |
| ROG_HARDWARE_ITEMS | `rog_gpu_mux` | GPU MUX | dGPU / Hybrid | **pending** |
| ROG_HARDWARE_ITEMS | `rog_battery` | Battery management | [Open] /  | **pending** |
| ROG_HARDWARE_ITEMS | `rog_power_tuning` | Power tuning | [Open] /  | **pending** |
| ROG_LIGHTING_ITEMS | `rog_kbd_leds` | Keyboard LEDs | [Open] /  | **pending** |
| ROG_LIGHTING_ITEMS | `rog_aura` | Aura RGB effect | [Open] /  | **pending** |
| ROG_LIGHTING_ITEMS | `rog_aura_power` | Aura power zones | [Open] /  | **pending** |
| ROG_LIGHTING_ITEMS | `rog_slash` | Slash Ledbar | [Open] /  | **pending** |
| ROG_LIGHTING_ITEMS | `rog_slash_extra` | Slash options | [Open] /  | **pending** |
| ROG_LIGHTING_ITEMS | `rog_anime` | AniMe Matrix | Enable / Disable | **pending** |
| ROG_LIGHTING_ITEMS | `rog_anime_extra` | AniMe options | [Open] /  | **pending** |
| THEMARCHY_ITEMS | `themarchy_apply` | Apply from wallpaper | [Apply] /  | **pending** |
| THEMARCHY_ITEMS | `themarchy_keybind` | Keybind SUPER+SHIFT+T | Enable / Disable | **pending** |

## Hyprland configurator inventory

All individual setting mappings/validation/load-save behavior below remain **pending** full audit. Shared write-helper proof does not certify these editor-specific write paths.

| Group | ID | Section/key | Type | Default | Target |
|---|---|---|---|---|---|
| HYPR_GENERAL_ITEMS | `gaps_in` | `general.gaps_in` | int:0:100 | 5 | looknfeel |
| HYPR_GENERAL_ITEMS | `gaps_out` | `general.gaps_out` | int:0:100 | 10 | looknfeel |
| HYPR_GENERAL_ITEMS | `border_size` | `general.border_size` | int:0:10 | 2 | looknfeel |
| HYPR_GENERAL_ITEMS | `active_border` | `general.col.active_border` | color | rgba(33ccffee) rgba(00ff99ee) 45deg | looknfeel |
| HYPR_GENERAL_ITEMS | `inactive_border` | `general.col.inactive_border` | color | rgba(595959aa) | looknfeel |
| HYPR_GENERAL_ITEMS | `resize_on_border` | `general.resize_on_border` | bool | false | looknfeel |
| HYPR_GENERAL_ITEMS | `allow_tearing` | `general.allow_tearing` | bool | false | looknfeel |
| HYPR_GENERAL_ITEMS | `layout` | `general.layout` | enum:dwindle:master | dwindle | looknfeel |
| HYPR_GENERAL_ITEMS | `preserve_split` | `dwindle.preserve_split` | bool | true | looknfeel |
| HYPR_GENERAL_ITEMS | `force_split` | `dwindle.force_split` | enum:0:1:2 | 2 | looknfeel |
| HYPR_GENERAL_ITEMS | `smart_split` | `dwindle.smart_split` | bool | false | looknfeel |
| HYPR_GENERAL_ITEMS | `new_status` | `master.new_status` | enum:master:slave | master | looknfeel |
| HYPR_GENERAL_ITEMS | `focus_on_activate` | `misc.focus_on_activate` | bool | true | looknfeel |
| HYPR_GENERAL_ITEMS | `disable_logo` | `misc.disable_hyprland_logo` | bool | true | looknfeel |
| HYPR_GENERAL_ITEMS | `vrr` | `misc.vrr` | enum:0:1:2 | 0 | looknfeel |
| HYPR_GENERAL_ITEMS | `new_window_fullscreen` | `misc.on_focus_under_fullscreen` | enum:0:1:2 | 2 | looknfeel |
| HYPR_GENERAL_ITEMS | `extend_border_grab` | `general.extend_border_grab_area` | int:0:50 | 15 | looknfeel |
| HYPR_GENERAL_ITEMS | `middle_click_paste` | `misc.middle_click_paste` | bool | true | looknfeel |
| HYPR_GENERAL_ITEMS | `enable_swallow` | `misc.enable_swallow` | bool | false | looknfeel |
| HYPR_GENERAL_ITEMS | `workspace_back_forth` | `binds.workspace_back_and_forth` | bool | false | looknfeel |
| HYPR_GENERAL_ITEMS | `allow_ws_cycles` | `binds.allow_workspace_cycles` | bool | false | looknfeel |
| HYPR_GENERAL_ITEMS | `force_zero_scaling` | `xwayland.force_zero_scaling` | bool | false | looknfeel |
| HYPR_GENERAL_ITEMS | `key_dpms` | `misc.key_press_enables_dpms` | bool | true | looknfeel |
| HYPR_GENERAL_ITEMS | `mouse_dpms` | `misc.mouse_move_enables_dpms` | bool | true | looknfeel |
| HYPR_DECORATION_ITEMS | `rounding` | `decoration.rounding` | int:0:30 | 0 | looknfeel |
| HYPR_DECORATION_ITEMS | `shadow_enabled` | `decoration.shadow.enabled` | bool | true | looknfeel |
| HYPR_DECORATION_ITEMS | `shadow_range` | `decoration.shadow.range` | int:1:100 | 2 | looknfeel |
| HYPR_DECORATION_ITEMS | `shadow_power` | `decoration.shadow.render_power` | int:1:4 | 3 | looknfeel |
| HYPR_DECORATION_ITEMS | `shadow_color` | `decoration.shadow.color` | color | rgba(1a1a1aee) | looknfeel |
| HYPR_DECORATION_ITEMS | `blur_enabled` | `decoration.blur.enabled` | bool | true | looknfeel |
| HYPR_DECORATION_ITEMS | `blur_size` | `decoration.blur.size` | int:1:20 | 2 | looknfeel |
| HYPR_DECORATION_ITEMS | `blur_passes` | `decoration.blur.passes` | int:1:10 | 2 | looknfeel |
| HYPR_DECORATION_ITEMS | `blur_special` | `decoration.blur.special` | bool | true | looknfeel |
| HYPR_DECORATION_ITEMS | `blur_brightness` | `decoration.blur.brightness` | float:0.0:2.0 | 0.60 | looknfeel |
| HYPR_DECORATION_ITEMS | `blur_contrast` | `decoration.blur.contrast` | float:0.0:2.0 | 0.75 | looknfeel |
| HYPR_DECORATION_ITEMS | `blur_noise` | `decoration.blur.noise` | float:0.0:1.0 | 0.0117 | looknfeel |
| HYPR_DECORATION_ITEMS | `blur_popups` | `decoration.blur.popups` | bool | false | looknfeel |
| HYPR_DECORATION_ITEMS | `anim_enabled` | `animations.enabled` | bool | true | looknfeel |
| HYPR_DECORATION_ITEMS | `dim_inactive` | `decoration.dim_inactive` | bool | false | looknfeel |
| HYPR_DECORATION_ITEMS | `dim_strength` | `decoration.dim_strength` | float:0.0:1.0 | 0.5 | looknfeel |
| HYPR_DECORATION_ITEMS | `dim_special` | `decoration.dim_special` | float:0.0:1.0 | 0.2 | looknfeel |
| HYPR_DECORATION_ITEMS | `cursor_hide` | `cursor.hide_on_key_press` | bool | true | looknfeel |
| HYPR_DECORATION_ITEMS | `active_opacity` | `decoration.active_opacity` | float:0.0:1.0 | 1.0 | looknfeel |
| HYPR_DECORATION_ITEMS | `inactive_opacity` | `decoration.inactive_opacity` | float:0.0:1.0 | 1.0 | looknfeel |
| HYPR_DECORATION_ITEMS | `fullscreen_opacity` | `decoration.fullscreen_opacity` | float:0.0:1.0 | 1.0 | looknfeel |
| HYPR_INPUT_ITEMS | `sensitivity` | `input.sensitivity` | float:-1.0:1.0 | 0 | input |
| HYPR_INPUT_ITEMS | `follow_mouse` | `input.follow_mouse` | enum:0:1:2:3 | 1 | input |
| HYPR_INPUT_ITEMS | `accel_profile` | `input.accel_profile` | enum:flat:adaptive |  | input |
| HYPR_INPUT_ITEMS | `force_no_accel` | `input.force_no_accel` | bool | false | input |
| HYPR_INPUT_ITEMS | `left_handed` | `input.left_handed` | bool | false | input |
| HYPR_INPUT_ITEMS | `repeat_rate` | `input.repeat_rate` | int:1:100 | 40 | input |
| HYPR_INPUT_ITEMS | `repeat_delay` | `input.repeat_delay` | int:100:2000 | 600 | input |
| HYPR_INPUT_ITEMS | `numlock_default` | `input.numlock_by_default` | bool | true | input |
| HYPR_INPUT_ITEMS | `natural_scroll` | `input.touchpad.natural_scroll` | bool | false | input |
| HYPR_INPUT_ITEMS | `scroll_factor` | `input.touchpad.scroll_factor` | float:0.1:5.0 | 0.4 | input |
| HYPR_INPUT_ITEMS | `disable_typing` | `input.touchpad.disable_while_typing` | bool | true | input |
| HYPR_INPUT_ITEMS | `tap_to_click` | `input.touchpad.tap-to-click` | bool | true | input |
| HYPR_INPUT_ITEMS | `drag_lock` | `input.touchpad.drag_lock` | bool | false | input |
| HYPR_INPUT_ITEMS | `middle_emulation` | `input.touchpad.middle_button_emulation` | bool | false | input |
| HYPR_INPUT_ITEMS | `scroll_button` | `input.scroll_button` | int:0:999 | 0 | input |
| HYPR_INPUT_ITEMS | `scroll_method` | `input.scroll_method` | enum:2fg:edge:on_button_down:no_scroll | 2fg | input |
| HYPR_GESTURES_ITEMS | `ws_swipe` | `gestures.workspace_swipe` | bool | false | looknfeel |
| HYPR_GESTURES_ITEMS | `ws_swipe_fingers` | `gestures.workspace_swipe_fingers` | int:2:5 | 3 | looknfeel |
| HYPR_GESTURES_ITEMS | `ws_swipe_distance` | `gestures.workspace_swipe_distance` | int:50:1000 | 300 | looknfeel |
| HYPR_GESTURES_ITEMS | `ws_swipe_invert` | `gestures.workspace_swipe_invert` | bool | true | looknfeel |
| HYPR_GESTURES_ITEMS | `ws_swipe_create` | `gestures.workspace_swipe_create_new` | bool | true | looknfeel |

## Dynamic/category workflows not covered by the fixture round trips

- Package selection/removal: inventory, aliases, dependency refusal, failure reporting and cancellation.
- Webapp selection/removal: desktop file ownership, icon/data handling and cancellation.
- Theme catalogue: URL/directory mapping, detection and installation handler; no remote theme repositories executed or installed for this checkpoint.
- Keybind editor: parser, modifiers, duplicate/conflict handling, binding types and failure propagation.
- Monitor dialogs: detection, identify overlays, transform/scale/mode preservation, positions and primary workspaces; runtime monitor changes not exercised.
- Power, battery, ASUS ROG: capability probing, selection, safe command construction and persistence; hardware writes not exercised.
- Suspend/hibernation/auth: source/API review and mocked failure propagation only; security or system-state changes require explicit approval and are not part of test execution.
- Backup utilities: before/after selection, missing directories, paths with spaces, copy failures and restore expectations.
- Menu integration and Themarchy: JSONC quoting/insertion/removal, dependency errors, wallpaper paths and generated scripts; real theme application not exercised.
- TUI: all category cursors, action dialogs, toggles, select-all, apply-summary, confirm each/apply all/cancel, terminal cleanup.
