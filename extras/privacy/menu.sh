#!/bin/bash
# Function-only menu adapter: safe to source; no discovery, writes or activation.
# The parent provides confirm_continue, clear and SUMMARY_LOG.
alc_control_path() {
    case "$1" in
        privacy) printf '%s/privacy/control.py' "$ALC_EXTRAS_DIR" ;;
        hardening) printf '%s/hardening/control.py' "$ALC_EXTRAS_DIR" ;;
        kernel) printf '%s/custom-kernel/control.py' "$ALC_EXTRAS_DIR" ;;
        *) return 1 ;;
    esac
}

alc_control_run() {
    local script
    script=$(alc_control_path "$1") || return 1
    shift
    if ! command -v python3 >/dev/null || [[ ! -f "$script" ]]; then
        echo 'Unavailable: these menus require Python 3 and the full local checkout.' >&2
        return 1
    fi
    PYTHONDONTWRITEBYTECODE=1 python3 "$script" "$@"
}

alc_menu_pause() {
    printf '\nPress Enter to return... '
    IFS= read -r < /dev/tty
}

alc_setting_dialog() {
    local group="$1" name="$2" title="$3" explanation="$4" action choice rc=0
    # Dedicated choices always confirm individually, never inherit Apply all.
    local CONFIRM_ALL=false
    clear
    printf '\n  %s\n\n%s\n\n' "$title" "$explanation"
    if [[ "$group" == hardening ]]; then
        alc_control_run "$group" "$name" status || :
        printf '\n1) Enable this control   2) Restore owned original   0) Back\n'
    else
        alc_control_run "$group" "$name" status || :
        printf '\n1) Stop future recording   2) Restore owned original   0) Back\n'
    fi
    printf 'Choice: '
    IFS= read -r choice < /dev/tty || return 1
    case "$choice" in
        1) [[ "$group" == hardening ]] && action=enable || action=stop ;;
        2) action=restore ;;
        *) return 0 ;;
    esac
    if confirm_continue "$title — $action"; then
        alc_control_run "$group" "$name" "$action" --confirm CHANGE || rc=$?
        if [[ "$rc" == 0 ]]; then
            SUMMARY_LOG+=("✓  $title — $action (readback verified/no-op)")
        else
            SUMMARY_LOG+=("✗  $title — failed/refused; partial changes may need Restore/review")
        fi
    fi
    alc_menu_pause
    return "$rc"
}

alc_purge_dialog() {
    local name="$1" title="$2" target="$3" token rc=0
    local CONFIRM_ALL=false
    clear
    printf '\n  %s\n\nExact scope: %s\n' "$title" "$target"
    echo 'Close applications that can write this data first. No applications are killed.'
    echo 'One-shot deletion is irreversible, NOT secure erasure. No history backup.'
    echo 'Stopping recording is separate; this action does not change its settings.'
    if ! confirm_continue "$title"; then return 0; fi
    printf 'Type PURGE to delete only this data (anything else cancels): '
    IFS= read -r token < /dev/tty || return 1
    if [[ "$token" != PURGE ]]; then
        SUMMARY_LOG+=("--  $title — cancelled")
        return 0
    fi
    alc_control_run privacy "$name" purge --confirm PURGE || rc=$?
    if [[ "$rc" == 0 ]]; then SUMMARY_LOG+=("✓  $title — purged/absent, not secure erasure")
    else SUMMARY_LOG+=("✗  $title — failed/refused; purge may be partial"); fi
    alc_menu_pause
    return "$rc"
}

show_privacy_dialog() {
    case "$1" in
        privacy_recent) alc_setting_dialog privacy recent 'Recent file recording' 'Stops GNOME remember-recent-files and GTK 3/4 gtk-recent-files-enabled. Close GTK applications and configuration editors before Stop or Restore and keep them closed until completion; restart manually afterward. Detected newer edits are refused, but final POSIX rename/unlink is not atomic compare-and-swap against other writers. Existing recent lists remain visible; no history is deleted. Folio Stop is refused because these GNOME/GTK settings do not hide its Recents view. Independent application histories are not covered. Existing GTK configuration is preserved for conflict-checked Restore, including after changing the default file manager.' ;;
        privacy_indexing) alc_setting_dialog privacy indexing 'Dedicated file indexing' 'Masks/stops ONLY stock localsearch-3.service and localsearch-control-3.service in your user manager (including D-Bus activation through those units). Search may degrade; existing index stays. Restore removes only our masks and restores prior activity. No GVFS, portals, D-Bus, writeback, shared or security services are touched.' ;;
        privacy_thumbnails) alc_setting_dialog privacy thumbnails 'Nautilus thumbnail recording' 'Sets org.gnome.nautilus.preferences show-image-thumbnails to never. Previews disappear in Nautilus; other applications can still thumbnail. Existing cache is NOT deleted. Restore preserves the previous explicit value or unset default.' ;;
        privacy_purge_recent) alc_purge_dialog recent 'Purge recent file list' 'XDG_DATA_HOME/recently-used.xbel only (default ~/.local/share/recently-used.xbel)' ;;
        privacy_purge_thumbnails) alc_purge_dialog thumbnails 'Purge thumbnail cache' 'XDG_CACHE_HOME/thumbnails only (default ~/.cache/thumbnails); symlinks/hardlinks/special files refused' ;;
        *) return 1 ;;
    esac
}

show_hardening_dialog() {
    case "$1" in
        hardening_audit)
            clear
            alc_control_run hardening audit status
            local rc=$?
            alc_menu_pause
            return "$rc" ;;
        hardening_kptr) alc_setting_dialog hardening kptr 'Kernel pointer exposure' 'Opt-in kernel.kptr_restrict=2: zero %pK kernel pointers regardless of privilege, not every address disclosure. Costs: debugging/profiling. Persists only in /etc/sysctl.d/99-a-la-carchy-kptr.conf; sudo requested after your confirmation. Existing stricter/user-managed settings are not taken over.' ;;
        hardening_dmesg) alc_setting_dialog hardening dmesg 'Kernel log access' 'Opt-in kernel.dmesg_restrict=1: CAP_SYSLOG needed to read kernel logs. Costs: unprivileged troubleshooting. Persists only in /etc/sysctl.d/99-a-la-carchy-dmesg.conf with sudo. Does NOT disable auditing or delete logs; existing restrictive settings are left alone.' ;;
        hardening_ptrace) alc_setting_dialog hardening ptrace 'Restricted ptrace' 'Opt-in kernel.yama.ptrace_scope=1: normally descendants/declared tracers only, requires Yama. Costs: debugger attachment/crash tooling. Values 2/3 are never lowered. Exact persistence: /etc/sysctl.d/99-a-la-carchy-ptrace.conf; sudo. No namespace/sandbox/authentication changes.' ;;
        hardening_bpf) alc_setting_dialog hardening bpf 'Unprivileged BPF' 'Opt-in kernel.unprivileged_bpf_disabled=2 only on supporting kernels (key/write/readback checked). Costs: unprivileged BPF tools. Value 1 is irreversible until reboot and is NEVER written/lowered. Exact persistence: /etc/sysctl.d/99-a-la-carchy-bpf.conf; sudo. No broad sysctl --system.' ;;
        *) return 1 ;;
    esac
}

show_kernel_dialog() {
    local action rc=0 path
    case "$1" in
        kernel_path)
            clear
            printf 'Read-only checkout path (current: %q)\n' "${ALC_LINUX_TKG_PATH:-/home/git/linux-tkg}"
            echo 'Requested paths are never repaired or replaced automatically. This choice lasts for this TUI session only.'
            printf 'Absolute checkout path (empty cancels): '
            IFS= read -r path < /dev/tty || return 1
            [[ -n "$path" ]] || return 0
            ALC_LINUX_TKG_PATH="$path"
            action=inspect ;;
        kernel_inspect) action=inspect ;;
        kernel_packages) action=packages ;;
        kernel_preview) action=preview ;;
        *) return 1 ;;
    esac
    clear
    echo 'Custom Kernel — READ ONLY; no build/install/initramfs/boot/fallback changes.'
    alc_control_run kernel "$action" --checkout "${ALC_LINUX_TKG_PATH:-/home/git/linux-tkg}" || rc=$?
    SUMMARY_LOG+=("--  Custom Kernel — $action read-only (exit $rc; no build/install)")
    alc_menu_pause
    return "$rc"
}
