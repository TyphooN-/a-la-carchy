#!/bin/bash
# Isolated configurator regressions. Never sources top-level TUI or live ops.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
export HOME="$TMP/home" OMARCHY_PATH="$TMP/omarchy" USER=fixture
unset DISPLAY WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE DBUS_SESSION_BUS_ADDRESS
mkdir -p "$HOME/.config/hypr" "$HOME/.config/omarchy" "$OMARCHY_PATH/shell/plugins/services"
: > "$HOME/.config/hypr/hyprland.lua"
cp -r /usr/share/omarchy/shell/plugins/services/media "$OMARCHY_PATH/shell/plugins/services/media"
sudo() { printf 'unexpected sudo\n' >> "$TMP/escapes"; return 97; }
omarchy() { printf 'unexpected omarchy\n' >> "$TMP/escapes"; return 97; }
omarchy-bar() { printf 'unexpected bar\n' >> "$TMP/escapes"; return 97; }
omarchy-plugin-enable() { printf 'unexpected enable\n' >> "$TMP/escapes"; return 97; }
hyprctl() { printf 'unexpected hyprctl\n' >> "$TMP/escapes"; return 97; }
systemctl() { printf 'unexpected systemctl\n' >> "$TMP/escapes"; return 97; }
python3 - "$REPO" "$TMP" <<'PY'
from pathlib import Path
import sys
repo, tmp = map(Path,sys.argv[1:])
s = (repo/'a-la-carchy.sh').read_text()
(tmp/'functions.sh').write_text(s.split('# TWO-PANEL TUI DATA STRUCTURES',1)[0])
assert 'supersonic|Supersonic music|Enable|Disable|toggle|' in s
(tmp/'selection.sh').write_text(s.split('# supersonic: 1=Enable, 2=Disable',1)[1].split('# window_title:',1)[0])
(tmp/'actions.sh').write_text('if [ "$ENABLE_SUPERSONIC" = true ]; then'+s.split('if [ "$ENABLE_SUPERSONIC" = true ]; then',1)[1].split('if [ "$SHOW_WINDOW_TITLE"',1)[0])
assert 'ACTION_SUMMARY+=("Enable Supersonic music")' in s
assert 'ACTION_SUMMARY+=("Disable Supersonic music")' in s
PY
[[ $? == 0 ]] || exit 1
source "$TMP/functions.sh"
ALC_SUPERSONIC_DIR="$REPO/extras/supersonic"
clear() { :; }
confirm_continue() { [[ "$MODE" != cancel ]]; }
MODE=ok
PASSED=0 FAILED=0
check() { local label="$1"; shift; if "$@"; then PASSED=$((PASSED+1)); printf 'ok - %s\n' "$label"; else FAILED=$((FAILED+1)); printf 'FAIL - %s\n' "$label"; fi; }
fails() { ! "$@" > "$TMP/output" 2>&1; }
no_green() { local s; for s in "${SUMMARY_LOG[@]}"; do [[ "$s" != ✓* ]] || return 1; done; }
fixture() {
  rm -rf -- "$HOME/.config/omarchy/plugins"
  printf '%s\n' '{"bar":{"layout":{"left":[{"id":"custom","keep":true}],"center":[{"id":"omarchy.media","label":"preserve"},{"id":"omarchy.clock"}],"right":[]}},"other":42}' > "$SHELL_JSON"
  cp "$SHELL_JSON" "$TMP/original"
  MODE=ok; SUMMARY_LOG=()
}
fixture
check 'enable applies' enable_supersonic_music
check 'enabled state read back' _supersonic_enabled
check 'native media replaced in same location' jq -e '.bar.layout.center[0].id == "alacarchy.supersonic" and .bar.layout.center[0].label == "preserve"' "$SHELL_JSON"
check 'unrelated entries preserved' jq -e '.bar.layout.left[0].keep and .bar.layout.center[1].id == "omarchy.clock" and .other == 42' "$SHELL_JSON"
check 'backup exists' bash -c 'compgen -G "$1.backup.*" >/dev/null' _ "$SHELL_JSON"
cp "$SHELL_JSON" "$TMP/enabled"
check 're-enable succeeds' enable_supersonic_music
check 're-enable exact idempotence' cmp -s "$SHELL_JSON" "$TMP/enabled"
check 'disable applies' disable_supersonic_music
check 'disabled state read back' _supersonic_disabled
check 'original layout/settings restored' jq -e '.bar.layout.center[0] == {id:"omarchy.media",label:"preserve"}' "$SHELL_JSON"
check 'disable leaves plugin files' test -f "$HOME/.config/omarchy/plugins/alacarchy.supersonic/ipc.py"
check 'repeat disable succeeds' disable_supersonic_music
fixture; MODE=cancel
check 'cancel handled' enable_supersonic_music
check 'cancel preserves bytes' cmp -s "$SHELL_JSON" "$TMP/original"
check 'cancel creates no plugin' test ! -d "$HOME/.config/omarchy/plugins"
fixture
ALC_SUPERSONIC_DIR="$TMP/missing"
check 'missing checkout helper fails honestly' fails enable_supersonic_music
check 'missing helper no success log' no_green
check 'missing helper preserves config' cmp -s "$SHELL_JSON" "$TMP/original"
ALC_SUPERSONIC_DIR="$REPO/extras/supersonic"
fixture
mkdir -p "$HOME/.config/omarchy/plugins/alacarchy.supersonic"
printf '%s\n' '{"id":"foreign"}' > "$HOME/.config/omarchy/plugins/alacarchy.supersonic/manifest.json"
check 'custom plugin refused' fails enable_supersonic_music
check 'custom refusal no success log' no_green
check 'custom refusal preserves config' cmp -s "$SHELL_JSON" "$TMP/original"
fixture
backup_file() { return 1; }
check 'backup failure propagates' fails enable_supersonic_music
check 'backup failure preserves config' cmp -s "$SHELL_JSON" "$TMP/original"
check 'backup failure creates no plugin' test ! -d "$HOME/.config/omarchy/plugins"
declare -A TOGGLE_SELECTIONS=()
for n in 0 1 2; do
  TOGGLE_SELECTIONS[supersonic]="$n"
  source "$TMP/selection.sh"
  : > "$TMP/dispatch"
  enable_supersonic_music() { printf enable >> "$TMP/dispatch"; }
  disable_supersonic_music() { printf disable >> "$TMP/dispatch"; }
  source "$TMP/actions.sh"
  case "$n" in
    0) check 'unselected dispatches nothing' test ! -s "$TMP/dispatch" ;;
    1) check 'enable selection dispatches handler' test "$(< "$TMP/dispatch")" = enable ;;
    2) check 'disable selection dispatches handler' test "$(< "$TMP/dispatch")" = disable ;;
  esac
done
check 'no live/system command escaped' test ! -e "$TMP/escapes"
printf '\n%s passed, %s failed\n' "$PASSED" "$FAILED"
[[ "$FAILED" == 0 ]]
