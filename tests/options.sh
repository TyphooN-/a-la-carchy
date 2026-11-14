#!/bin/bash
# Shared option regressions in isolated HOME. No real daemon or desktop changes.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
export HOME="$TMP/home" OMARCHY_PATH="$TMP/omarchy" HYPRLAND_INSTANCE_SIGNATURE=fixture
mkdir -p "$HOME/.config/hypr" "$HOME/.config/omarchy" "$OMARCHY_PATH"
: > "$HOME/.config/hypr/hyprland.lua"
sed '/^# TWO-PANEL TUI DATA STRUCTURES$/,$d' "$REPO/a-la-carchy.sh" > "$TMP/functions.sh"
source "$TMP/functions.sh"
clear() { :; }
sleep() { :; }
confirm_continue() { return 0; }
MODE=ok
hyprctl() {
    case "$1" in
        reload) [[ "$MODE" != reload-fail ]] ;;
        configerrors)
            [[ "$MODE" != query-fail ]] || return 1
            [[ "$MODE" == invalid ]] && echo 'invalid config'
            return 0 ;;
        getoption) printf 'str: %s\n' "compose:caps,shift:both_capslock_cancel,custom:keep" ;;
        *) return 97 ;;
    esac
}
# Restrict bar commands to fixture JSON transforms; unknown shapes fail closed.
omarchy-bar() {
    local id="${2:-}"
    case "$1" in
        put) shell_json_jq --arg id "$id" '.bar.layout.left += [{id:$id}]' ;;
        set)
            [[ "$3" == format ]] || return 97
            shell_json_jq --arg id "$id" --arg value "$4" '.bar.layout |= with_entries(.value |= map(if .id == $id then .format = $value else . end))' ;;
        *) return 97 ;;
    esac
}
SUMMARY_LOG=()
PASSED=0 FAILED=0
check() {
    local label="$1"; shift
    if "$@"; then PASSED=$((PASSED+1)); printf 'ok - %s\n' "$label"
    else FAILED=$((FAILED+1)); printf 'FAIL - %s\n' "$label"; fi
}
expect_failure() { ! "$@" > "$TMP/output" 2>&1; }
never_set() { return 1; }
failed_apply() { return 1; }
no_effect_apply() { return 0; }
fixture_shell() { printf '%s\n' '{"bar":{"layout":{"left":[{"id":"omarchy.menu"}],"center":[{"id":"omarchy.clock","format":"dddd HH:mm"},{"id":"omarchy.system-update"}],"right":[]}}}' > "$SHELL_JSON"; }
MODE=reload-fail
check 'reload failure propagates' expect_failure hypr_reload_check
MODE=query-fail
check 'configerrors query failure propagates' expect_failure hypr_reload_check
MODE=invalid
check 'reported config errors propagate' expect_failure hypr_reload_check
MODE=ok
check 'successful reload/query accepted' hypr_reload_check

printf '%s\n' '-- user config' '-- >>> a-la-carchy broken' 'user_data = true' > "$TMP/broken.lua"
cp "$TMP/broken.lua" "$TMP/original.lua"
check 'unterminated block rejected' expect_failure lua_block_remove "$TMP/broken.lua" broken
check 'unterminated config preserved byte-for-byte' cmp -s "$TMP/broken.lua" "$TMP/original.lua"
check 'write refuses malformed old block' expect_failure lua_block_write "$TMP/broken.lua" broken new
check 'failed replacement preserves original' cmp -s "$TMP/broken.lua" "$TMP/original.lua"
printf '%s\n' '-- <<< a-la-carchy broken' 'user_data = true' > "$TMP/broken.lua"
check 'orphaned end marker rejected' expect_failure lua_block_remove "$TMP/broken.lua" broken
printf '%s\n' '-- >>> a-la-carchy broken' '-- >>> a-la-carchy broken' '-- <<< a-la-carchy broken' > "$TMP/broken.lua"
check 'nested markers rejected' expect_failure lua_block_remove "$TMP/broken.lua" broken

fixture_shell
check 'failed shell operation returns failure' expect_failure apply_shell_change title explanation summary never_set failed_apply
check 'no-op apply cannot pass postcondition' expect_failure apply_shell_change title explanation summary never_set no_effect_apply
printf '{invalid JSON\n' > "$SHELL_JSON"
cp "$SHELL_JSON" "$TMP/original.json"
check 'invalid shell JSON is not already-set success' expect_failure remove_omarchy_logo
check 'invalid JSON preserved' cmp -s "$SHELL_JSON" "$TMP/original.json"
printf '%s\n' '{"bar":{"layout":{"left":"not-an-array"}}}' > "$SHELL_JSON"
check 'invalid layout shape rejected' expect_failure remove_omarchy_logo
fixture_shell
check 'shell operation halts when backup fails' expect_failure bash -c 'source "$1"; clear(){ :; }; confirm_continue(){ return 0; }; backup_file(){ return 1; }; never_set(){ return 1; }; failed_apply(){ echo mutated >> "$SHELL_JSON"; }; apply_shell_change title explanation summary never_set failed_apply' _ "$TMP/functions.sh"
check 'backup file collision avoided' bash -c 'source "$1"; backup_file "$2" >/dev/null && backup_file "$2" >/dev/null; files=("$2".backup.*); [[ ${#files[@]} -eq 2 ]]' _ "$TMP/functions.sh" "$TMP/original.lua"
check 'backup of missing file fails honestly' expect_failure backup_file "$TMP/absent.lua"

# Exercise every pair that shares the managed Lua apply path.
for file in "$BINDINGS_LUA" "$LOOKNFEEL_LUA" "$INPUT_LUA"; do echo '-- untouched user setting' > "$file"; done
while read -r enable disable id file; do
    "$enable" > "$TMP/output" 2>&1
    check "$enable creates its managed block" lua_block_has "$file" "$id"
    "$enable" > "$TMP/output" 2>&1
    check "$enable repeated once only" test "$(grep -cxF -- "-- >>> a-la-carchy $id" "$file")" -eq 1
    "$disable" > "$TMP/output" 2>&1
    check "$disable removes its block" expect_failure lua_block_has "$file" "$id"
    check "$disable preserves unrelated user setting" grep -qxF -- '-- untouched user setting' "$file"
done <<PAIRS
rebind_close_window restore_close_window close-window $BINDINGS_LUA
bind_shutdown unbind_shutdown shutdown $BINDINGS_LUA
bind_restart unbind_restart restart $BINDINGS_LUA
bind_theme_menu unbind_theme_menu theme-menu $BINDINGS_LUA
enable_rounded_corners disable_rounded_corners rounded-corners $LOOKNFEEL_LUA
remove_window_gaps restore_window_gaps window-gaps $LOOKNFEEL_LUA
remove_transparency restore_transparency transparency $LOOKNFEEL_LUA
PAIRS

fixture_shell
remove_omarchy_logo > "$TMP/output" 2>&1
check 'logo removal updates JSON' expect_failure shell_bar_has omarchy.menu
restore_omarchy_logo > "$TMP/output" 2>&1
check 'logo restore updates JSON' shell_bar_has omarchy.menu
remove_update_icon > "$TMP/output" 2>&1
check 'update icon removal updates JSON' expect_failure shell_bar_has omarchy.system-update
restore_update_icon > "$TMP/output" 2>&1
check 'update icon restore updates JSON' shell_bar_has omarchy.system-update
show_window_title > "$TMP/output" 2>&1
check 'window title added' shell_bar_has omarchy.active-window
hide_window_title > "$TMP/output" 2>&1
check 'window title removed' expect_failure shell_bar_has omarchy.active-window
enable_12h_clock > "$TMP/output" 2>&1
check '12h clock format set' test "$(clock_format)" = 'dddd h:mm AP'
disable_12h_clock > "$TMP/output" 2>&1
check '24h clock restores original format' test "$(clock_format)" = 'dddd HH:mm'
hide_clock_date > "$TMP/output" 2>&1
check 'clock date hidden' test "$(clock_format)" = 'HH:mm'
show_clock_date > "$TMP/output" 2>&1
check 'clock date restored' test "$(clock_format)" = 'dddd HH:mm'

# Failures must not be washed away by an echo at function end.
MODE=reload-fail
check 'managed apply propagates reload failure' expect_failure apply_lua_block title explanation "$LOOKNFEEL_LUA" failure summary 'hl.config({})'
MODE=ok
check 'managed apply propagates backup failure' expect_failure bash -c 'source "$1"; clear(){ :; }; confirm_continue(){ return 0; }; backup_file(){ return 1; }; apply_lua_block title explanation "$INPUT_LUA" failure summary "hl.config({})"' _ "$TMP/functions.sh"
check 'managed apply propagates write failure' expect_failure bash -c 'source "$1"; clear(){ :; }; confirm_continue(){ return 0; }; lua_block_write(){ return 1; }; apply_lua_block title explanation "$INPUT_LUA" failure summary "hl.config({})"' _ "$TMP/functions.sh"

printf '\n%s passed, %s failed\n' "$PASSED" "$FAILED"
[[ "$FAILED" -eq 0 ]]
