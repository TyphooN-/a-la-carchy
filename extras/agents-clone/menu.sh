#!/bin/bash
# Function-only native provider dialog. No package/service/auth commands.
show_agents_dialog() {
    local manager="$ALC_AGENTS_CLONE_DIR/manage.py" status binding choice action answer id desired result
    local -a ids=() states=()
    if [[ ! -f "$manager" ]] || ! command -v python3 >/dev/null || ! command -v jq >/dev/null; then
        printf 'Agents settings require python3, jq and the full checkout.\n' >&2
        return 1
    fi
    status=$(python3 -I -B "$manager" --source "$OMARCHY_DIR/shell/plugins/agents" \
        --shell "$SHELL_JSON" --plugins "$HOME/.config/omarchy/plugins" --providers) || {
        printf '%s\n' "$status" >&2
        SUMMARY_LOG+=("✗  Agents settings unavailable; nothing changed")
        return 1
    }
    binding=$(jq -r '.binding' <<< "$status") || return 1
    [[ "$binding" =~ ^[a-f0-9]{64}$ ]] || return 1
    while IFS=$'\t' read -r id action; do
        ids+=("$id"); states+=("$action")
    done < <(jq -r '.providers[] | [.id, (if .enabled then "enabled" else "disabled" end)] | @tsv' <<< "$status")
    (( ${#ids[@]} > 0 )) || return 1
    clear
    printf '\n  Agents — Native collectors\n\n'
    printf '  Disabled providers are hidden and skipped by the shell updater.\n'
    printf '  No installs, login changes or service actions run here.\n\n'
    local index
    for index in "${!ids[@]}"; do
        printf '  %d. %s [%s]\n' "$((index+1))" "${ids[$index]}" "${states[$index]}"
    done
    printf '\n  Provider number (0 to cancel): '
    read -r choice < /dev/tty || return 1
    [[ -n "$choice" && "$choice" != 0 ]] || return 0
    [[ "$choice" =~ ^[1-9][0-9]{0,5}$ ]] && (( choice <= ${#ids[@]} )) || return 1
    id="${ids[$((choice-1))]}"
    printf '\n  Action: 1 Enable, 2 Disable, 0 Cancel: '
    read -r action < /dev/tty || return 1
    case "$action" in 1) desired=enabled ;; 2) desired=disabled ;; ''|0) return 0 ;; *) return 1 ;; esac
    # Always ask, even if the caller's global Apply all flag is set.
    printf '\n  Set %s %s? Continue? (yes/no): ' "$id" "$desired"
    read -r answer < /dev/tty || return 1
    [[ "${answer,,}" == yes ]] || return 0
    if result=$(python3 -I -B "$manager" --source "$OMARCHY_DIR/shell/plugins/agents" \
        --shell "$SHELL_JSON" --plugins "$HOME/.config/omarchy/plugins" \
        --set-provider "$id" "$desired" --expected-shell "$binding"); then
        printf '\n  %s %s; verified. Existing independent bridge timers are unchanged.\n' "$id" "$desired"
        SUMMARY_LOG+=("✓  Agents: $id $desired")
    else
        printf '\n  %s\n' "$result" >&2
        SUMMARY_LOG+=("✗  Agents: $id change failed/refused; retained backups")
        return 1
    fi
    printf '\n  Press Enter to return: '
    read -r answer < /dev/tty
}
