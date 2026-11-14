#!/bin/bash
# Battery charge limit: fixture sysfs/rules only; sudo, udev, pkexec and menu are mocks.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
export HOME="$TMP/home" OMARCHY_PATH="$TMP/omarchy" ALC_TEST_TMP="$TMP"
unset HYPRLAND_INSTANCE_SIGNATURE WAYLAND_DISPLAY DISPLAY
mkdir -p "$HOME/.config/hypr" "$OMARCHY_PATH"
: > "$HOME/.config/hypr/hyprland.lua"
python3 - "$REPO/a-la-carchy.sh" "$TMP/functions.sh" <<'PY'
import pathlib,sys
s=pathlib.Path(sys.argv[1]).read_text().split('# TWO-PANEL TUI DATA STRUCTURES',1)[0]
guards='\n'.join(n+'() { printf "unexpected prelude '+n+'\\n" >> "$ALC_TEST_TMP/unexpected.log"; return 97; }' for n in ['sudo','pkexec','udevadm','notify-send','omarchy-menu'])
pathlib.Path(sys.argv[2]).write_text(guards+'\n'+s)
PY
source "$TMP/functions.sh"
clear() { :; }
POWER_SUPPLY_DIR="$TMP/sys/class/power_supply"
BATTERY_LIMIT_UDEV_RULE="$TMP/rules.d/99-battery-charge-limit.rules"
BATTERY_LIMIT_HELPER="$HOME/.config/hypr/scripts/omarchy-battery-limit.sh"
MENU_JSONC="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
mkdir -p "$POWER_SUPPLY_DIR/BAT0" "$TMP/rules.d"
LOG="$TMP/sudo.log"
omarchy-menu() { :; }
sudo() {
    printf '%q ' "$@" >> "$LOG"; printf '\n' >> "$LOG"
    case "$1" in
        -n|true) return 0 ;;
        /usr/bin/tee)
            [[ $# == 3 && "$2" == -- ]] || return 97
            [[ "$3" == "$POWER_SUPPLY_DIR/BAT0/charge_control_end_threshold" || "$3" == "$BATTERY_LIMIT_UDEV_RULE" ]] || return 97
            [[ "$MODE" == sysfs-fail && "$3" == "$POWER_SUPPLY_DIR"/* ]] && { cat > /dev/null; return 1; }
            [[ "$MODE" == rule-fail && "$3" == "$BATTERY_LIMIT_UDEV_RULE" ]] && { cat > /dev/null; return 1; }
            command tee -- "$3" ;;
        /usr/bin/rm)
            [[ $# == 4 && "$2" == -f && "$3" == -- && "$4" == "$BATTERY_LIMIT_UDEV_RULE" ]] || return 97
            [[ "$MODE" != remove-fail ]] || return 1
            [[ "$MODE" == remove-noop ]] && return 0
            command rm -f -- "$4" ;;
        /usr/bin/udevadm)
            [[ $# == 3 && "$2" == control && "$3" == --reload-rules ]] || return 97
            [[ "$MODE" != reload-fail ]] ;;
        *) echo "UNEXPECTED PRIVILEGED COMMAND: $*" >> "$TMP/unexpected.log"; return 97 ;;
    esac
}
PASSED=0 FAILED=0
check() { local label="$1"; shift; if "$@"; then PASSED=$((PASSED+1)); echo "ok - $label"; else FAILED=$((FAILED+1)); echo "FAIL - $label"; fi; }
no_green() { local s; for s in "${SUMMARY_LOG[@]}"; do [[ "$s" != ✓* ]] || return 1; done; }
fails() { ! apply_battery_limit > "$TMP/output" 2>&1; }
reset_case() {
    MODE=ok; SUMMARY_LOG=(); : > "$LOG"
    printf '100\n' > "$POWER_SUPPLY_DIR/BAT0/charge_control_end_threshold"
    rm -f -- "$BATTERY_LIMIT_UDEV_RULE" "$BATTERY_LIMIT_HELPER"
    SELECTED_BATTERY_LIMIT=80
}
reset_case
check 'limit applies through mocks' apply_battery_limit
check 'sysfs threshold written' test "$(< "$POWER_SUPPLY_DIR/BAT0/charge_control_end_threshold")" = 80
check 'persistent rule written with allowlisted value' grep -qxF 'SUBSYSTEM=="power_supply", KERNEL=="BAT*", ATTR{charge_control_end_threshold}="80"' "$BATTERY_LIMIT_UDEV_RULE"
check 'rule has no RUN program' bash -c '! grep -q RUN "$1"' _ "$BATTERY_LIMIT_UDEV_RULE"
check 'udev reloaded' grep -qF '/usr/bin/udevadm control --reload-rules' "$LOG"
check 'success summary only after every step' test "${SUMMARY_LOG[-1]}" = '✓  Battery charge limit set to 80%'
check 'helper installed' test -x "$BATTERY_LIMIT_HELPER"
check 'menu picker installed' grep -qF 'setup.battery-limit.80' "$MENU_JSONC"
check 'privileged writes use absolute tee' bash -c '! grep -qE "^(tee|rm|udevadm) " "$1"' _ "$LOG"
for value in '' 85 '80; id' $'80\n90' 'abc'; do
    reset_case; SELECTED_BATTERY_LIMIT="$value"
    check "invalid limit '${value//$'\n'/\\n}' rejected" fails
    check 'invalid limit makes no privileged calls' test ! -s "$LOG"
    check 'invalid limit has no success summary' no_green
done
for mode in sysfs-fail rule-fail reload-fail; do
    reset_case; MODE="$mode"
    check "$mode propagates" fails
    check "$mode has no success summary" no_green
done
reset_case; MODE=reload-fail
fails
check 'reload failure explains persistence state' bash -c 'printf "%s\n" "${@}" | grep -qF "udev rules were not reloaded"' _ "${SUMMARY_LOG[@]}"
reset_case
printf 'old\n' > "$BATTERY_LIMIT_UDEV_RULE"; SELECTED_BATTERY_LIMIT=100
check 'no-limit removes persistent rule' apply_battery_limit
check 'rule removed' test ! -e "$BATTERY_LIMIT_UDEV_RULE"
for mode in remove-fail remove-noop; do
    reset_case; printf 'old\n' > "$BATTERY_LIMIT_UDEV_RULE"; SELECTED_BATTERY_LIMIT=100; MODE="$mode"
    check "no-limit $mode propagates" fails
    check "no-limit $mode has no success summary" no_green
    check "no-limit $mode does not claim removal" bash -c '! grep -qF "Removed udev rule" "$1"' _ "$TMP/output"
done
reset_case
mkdir -p "$TMP/no-battery"; POWER_SUPPLY_DIR="$TMP/no-battery"
check 'missing battery threshold fails before privileged writes' fails
check 'missing battery makes no tee call' bash -c '! grep -qF /usr/bin/tee "$1"' _ "$LOG"
POWER_SUPPLY_DIR="$TMP/sys/class/power_supply"
# The generated helper only ever interpolates allowlisted values.
reset_case; apply_battery_limit > /dev/null 2>&1
mkdir -p "$TMP/bin"
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s/pkexec.log"\n' "$TMP" > "$TMP/bin/pkexec"
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s/notify.log"\n' "$TMP" > "$TMP/bin/notify-send"
chmod +x "$TMP/bin/pkexec" "$TMP/bin/notify-send"
# Point only the test copy at the fixture battery so valid values reach the
# (logging-only) pkexec mock while invalid ones must stop before it.
sed "s|/sys/class/power_supply|$POWER_SUPPLY_DIR|g" "$BATTERY_LIMIT_HELPER" > "$TMP/helper.sh"
check 'helper copy targets only the fixture battery' bash -c 'grep -qF "for p in $2/BAT*/" "$1" && ! grep -qF "for p in /sys/" "$1"' _ "$TMP/helper.sh" "$POWER_SUPPLY_DIR"
for value in "80'; touch $TMP/pwned; '" 85; do
    PATH="$TMP/bin:$PATH" bash "$TMP/helper.sh" "$value" > /dev/null 2>&1
done
check 'helper refuses non-allowlisted values before pkexec' test ! -e "$TMP/pkexec.log"
check 'helper injection attempt never executed' test ! -e "$TMP/pwned"
check 'helper reports refusals to the user' test "$(grep -c 'Unsupported charge limit' "$TMP/notify.log")" -eq 2
PATH="$TMP/bin:$PATH" bash "$TMP/helper.sh" 70 > /dev/null 2>&1
check 'allowlisted value reaches pkexec exactly once' test "$(grep -c "echo '70'" "$TMP/pkexec.log")" -eq 1
check 'no unexpected privileged commands escaped mocks' test ! -s "$TMP/unexpected.log"
printf '\n%s passed, %s failed\n' "$PASSED" "$FAILED"
[[ "$FAILED" -eq 0 ]]
