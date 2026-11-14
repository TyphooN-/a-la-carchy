#!/bin/bash
# Clock regressions: isolated HOME, no live bar/plugin/service operations.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
export HOME="$TMP/home" OMARCHY_PATH="$TMP/omarchy" USER=fixture
mkdir -p "$HOME/.config/hypr" "$HOME/.config/omarchy" "$OMARCHY_PATH"
: > "$HOME/.config/hypr/hyprland.lua"
sudo() { printf 'unexpected sudo\n' >> "$TMP/escapes"; return 97; }
omarchy-bar() { printf 'unexpected bar\n' >> "$TMP/escapes"; return 97; }
omarchy-plugin-clone() { printf 'unexpected clone\n' >> "$TMP/escapes"; return 97; }
omarchy-plugin-enable() { printf 'unexpected enable\n' >> "$TMP/escapes"; return 97; }
python3 - "$REPO/a-la-carchy.sh" "$TMP/functions.sh" "$TMP" <<'PY'
import pathlib,sys
s=pathlib.Path(sys.argv[1]).read_text()
p=pathlib.Path(sys.argv[3])
pathlib.Path(sys.argv[2]).write_text(s.split('# TWO-PANEL TUI DATA STRUCTURES',1)[0])
p.joinpath('selection.sh').write_text(s.split('# clock_seconds: 1=Show, 2=Hide',1)[1].split('# window_title:',1)[0])
p.joinpath('actions.sh').write_text('if [ "$SHOW_CLOCK_SECONDS" = true ]; then'+s.split('if [ "$SHOW_CLOCK_SECONDS" = true ]; then',1)[1].split('if [ "$SHOW_WINDOW_TITLE"',1)[0])
assert 'clock_seconds|Clock seconds|Show|Hide|toggle|' in s
assert 'ACTION_SUMMARY+=("Show clock seconds")' in s
assert 'ACTION_SUMMARY+=("Hide clock seconds")' in s
PY
source "$TMP/functions.sh"
clear() { :; }
confirm_continue() { [[ "$MODE" != cancel ]]; }
MODE=ok
LOG="$TMP/commands"
PLUGIN="$HOME/.config/omarchy/plugins/fixture.clock"
omarchy-bar() {
    printf 'bar %s\n' "$*" >> "$LOG"
    [[ $# == 4 && "$1" == set && "$2" == "$(clock_widget_id)" && "$3" == format ]] || return 97
    [[ "$MODE" != set-fail ]] || return 1
    [[ "$MODE" == no-effect ]] && return 0
    shell_json_jq --arg id "$2" --arg value "$4" '.bar.layout |= with_entries(.value |= map(if .id == $id then .format=$value else . end))'
}
omarchy-plugin-enable() {
    printf 'enable %s\n' "$*" >> "$LOG"
    [[ $# == 1 && "$1" == fixture.clock && -f "$PLUGIN/manifest.json" ]] || return 97
    shell_json_jq '.bar.layout |= with_entries(.value |= map(if .id == "omarchy.clock" then .id="fixture.clock" else . end))'
}
omarchy-plugin-clone() {
    printf 'clone %s\n' "$*" >> "$LOG"
    [[ $# == 1 && "$1" == omarchy.clock && ! -e "$PLUGIN" && "$MODE" != clone-fail ]] || return 1
    mkdir -p "$PLUGIN"
    printf '%s\n' '{"id":"fixture.clock","entryPoints":{"barWidget":"BarWidget.qml"},"omarchy":{"clonedFrom":"omarchy.clock"}}' > "$PLUGIN/manifest.json"
    printf '%s\n' 'import qs.Ui' 'BarWidget {' '  id: root' '  readonly property string configuredFormat: setting("format", "dddd HH:mm")' '  SystemClock {' '    precision: SystemClock.Minutes' '  }' '}' > "$PLUGIN/BarWidget.qml"
    omarchy-plugin-enable fixture.clock
}
PASSED=0 FAILED=0
check() { local label="$1"; shift; if "$@"; then PASSED=$((PASSED+1)); printf 'ok - %s\n' "$label"; else FAILED=$((FAILED+1)); printf 'FAIL - %s\n' "$label"; fi; }
no_green() { local s; for s in "${SUMMARY_LOG[@]}"; do [[ "$s" != ✓* ]] || return 1; done; }
fail() { ! "$@" > "$TMP/output" 2>&1; }
format_is() { [[ "$(clock_format)" == "$1" ]]; }
fixture() {
    rm -rf -- "$HOME/.config/omarchy/plugins"
    MODE=ok; SUMMARY_LOG=(); : > "$LOG"
    jq -n --arg f "$1" '{bar:{layout:{left:[{id:"omarchy.menu"}],center:[{id:"omarchy.clock",format:$f}],right:[{id:"other",custom:"preserve"}]}}}' > "$SHELL_JSON"
}
fixture 'dddd HH:mm'
check 'seconds option applies' show_clock_seconds
check '24-hour seconds with day preserved' format_is 'dddd HH:mm:ss'
check 'active clock is supported user clone' test "$(clock_widget_id)" = fixture.clock
check 'precision switches based on seconds setting' grep -qF "precision: $(_clock_precision_expression)" "$PLUGIN/BarWidget.qml"
check 'unrelated bar properties preserved' jq -e '.bar.layout.right[0].custom == "preserve"' "$SHELL_JSON"
check 'clone source backup created' bash -c 'compgen -G "$1.backup.*" >/dev/null' _ "$PLUGIN/BarWidget.qml"
check 'repeat show succeeds without duplicate seconds' show_clock_seconds
check 'repeat show remains identical' format_is 'dddd HH:mm:ss'
check 'clone created only once' test "$(grep -c '^clone ' "$LOG")" -eq 1
check '12-hour conversion preserves seconds' enable_12h_clock
check '12-hour format correctly orders AM/PM after seconds' format_is 'dddd h:mm:ss AP'
check '24-hour conversion preserves seconds' disable_12h_clock
check 'back to 24-hour seconds' format_is 'dddd HH:mm:ss'
check 'hide day preserves seconds' hide_clock_date
check 'time-only seconds' format_is 'HH:mm:ss'
check 'show day preserves seconds' show_clock_date
check 'day restored' format_is 'dddd HH:mm:ss'
check 'hide seconds succeeds' hide_clock_seconds
check 'hide seconds preserves hour/day' format_is 'dddd HH:mm'
check 'repeat hide is idempotent' hide_clock_seconds
for entry in 'HH:mm|HH:mm:ss' 'h:mm AP|h:mm:ss AP' 'dddd h:mm ap|dddd h:mm:ss ap' "'HH:mm :ss AP' HH:mm|'HH:mm :ss AP' HH:mm:ss"; do
    before="${entry%%|*}"; after="${entry#*|}"
    fixture "$before"
    check "show seconds: $before" show_clock_seconds
    check 'only real time tokens changed' format_is "$after"
    check 'hide seconds round-trips' hide_clock_seconds
    check 'original format restored' format_is "$before"
done
fixture 'HH:mm:ss'
check 'manually configured seconds also repair minute-only precision' show_clock_seconds
check 'seconds configuration verified by check predicate' _clock_seconds_shown
fixture 'dddd HH:mm'; MODE=cancel
cp "$SHELL_JSON" "$TMP/original"
check 'cancel returns without failure' show_clock_seconds
check 'cancel leaves JSON untouched' cmp -s "$SHELL_JSON" "$TMP/original"
check 'cancel does not clone' test ! -e "$PLUGIN"
for mode in clone-fail set-fail no-effect; do
    fixture 'HH:mm'; MODE="$mode"
    check "$mode does not report success" fail show_clock_seconds
    check "$mode has no success summary" no_green
done
fixture "'unterminated HH:mm"
cp "$SHELL_JSON" "$TMP/original"
check 'malformed Qt quoting rejected' fail show_clock_seconds
check 'malformed format leaves JSON untouched' cmp -s "$SHELL_JSON" "$TMP/original"
check 'malformed format does not clone' test ! -e "$PLUGIN"
fixture 'yyyy-MM-dd'
check 'date-only custom format safely refused' fail show_clock_seconds
check 'date-only custom format preserved' format_is 'yyyy-MM-dd'
fixture 'HH:mm'
omarchy-plugin-clone omarchy.clock
printf '%s\n' 'user-custom clock with unsupported precision' > "$PLUGIN/BarWidget.qml"
cp "$PLUGIN/BarWidget.qml" "$TMP/custom-original"
check 'unfamiliar user plugin refused' fail show_clock_seconds
check 'unfamiliar user plugin not overwritten' cmp -s "$PLUGIN/BarWidget.qml" "$TMP/custom-original"
fixture 'HH:mm'
omarchy-plugin-clone omarchy.clock
printf '  precision: SystemClock.Seconds\n' >> "$PLUGIN/BarWidget.qml"
cp "$PLUGIN/BarWidget.qml" "$TMP/custom-original"
check 'multiple precision declarations are safely refused' fail show_clock_seconds
check 'ambiguous user plugin preserved exactly' cmp -s "$PLUGIN/BarWidget.qml" "$TMP/custom-original"
fixture 'HH:mm'
omarchy-plugin-clone omarchy.clock
python3 - "$PLUGIN/BarWidget.qml" <<'PY'
import pathlib,sys
p=pathlib.Path(sys.argv[1]);p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n'))
PY
check 'supported CRLF clone updates correctly' show_clock_seconds
check 'CRLF line endings preserved' python3 -c 'import pathlib,sys; b=pathlib.Path(sys.argv[1]).read_bytes(); sys.exit(0 if b"\r\n" in b and b"\n" not in b.replace(b"\r\n",b"") else 1)' "$PLUGIN/BarWidget.qml"
fixture 'HH:mm'
USER='../escape'
check 'unsafe username cannot escape plugin directory' fail show_clock_seconds
check 'unsafe username cannot invoke clone' test ! -s "$LOG"
USER=fixture
fixture 'HH:mm'
mkdir -p "$PLUGIN"
printf '%s\n' '{"id":"fixture.clock","omarchy":{"clonedFrom":"unrelated"}}' > "$PLUGIN/manifest.json"
check 'pre-existing unrelated clone path refused' fail show_clock_seconds
check 'foreign manifest preserved' jq -e '.omarchy.clonedFrom == "unrelated"' "$PLUGIN/manifest.json"
fixture 'HH:mm'
printf 'not JSON\n' > "$SHELL_JSON"
check 'invalid JSON safely refused' fail show_clock_seconds
check 'invalid JSON has no success summary' no_green
# Exercise the actual selection mapping and dispatch snippets, not a mock map.
declare -A TOGGLE_SELECTIONS=()
for n in 0 1 2; do
    TOGGLE_SELECTIONS[clock_seconds]="$n"
    source "$TMP/selection.sh"
    case "$n" in
        0) check 'unselected does not show' test "$SHOW_CLOCK_SECONDS" = false; check 'unselected does not hide' test "$HIDE_CLOCK_SECONDS" = false ;;
        1) check 'show selection maps correctly' test "$SHOW_CLOCK_SECONDS" = true; check 'show does not select hide' test "$HIDE_CLOCK_SECONDS" = false ;;
        2) check 'hide selection maps correctly' test "$HIDE_CLOCK_SECONDS" = true; check 'hide does not select show' test "$SHOW_CLOCK_SECONDS" = false ;;
    esac
    : > "$TMP/dispatch"
    show_clock_seconds() { echo show >> "$TMP/dispatch"; }
    hide_clock_seconds() { echo hide >> "$TMP/dispatch"; }
    source "$TMP/actions.sh"
    case "$n" in
        0) check 'unselected dispatches nothing' test ! -s "$TMP/dispatch" ;;
        1) check 'show handler dispatched' grep -qx show "$TMP/dispatch" ;;
        2) check 'hide handler dispatched' grep -qx hide "$TMP/dispatch" ;;
    esac
done
check 'no commands escaped source guards' test ! -e "$TMP/escapes"
printf '\n%s passed, %s failed\n' "$PASSED" "$FAILED"
[[ "$FAILED" == 0 ]]
