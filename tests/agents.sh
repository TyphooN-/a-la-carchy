#!/bin/bash
# Local LLM stats / Grok tokens configurator wiring in an isolated HOME.
# Never sources the top-level TUI. systemctl is a logging mock on PATH, the
# Agents sources are synthetic fixtures (tests/agents_fixture.py), and nothing
# here starts a collector service, contacts a router or launches Grok.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
export HOME="$TMP/home" OMARCHY_PATH="$TMP/omarchy" USER=fixture FIXTURE="$TMP" PYTHONDONTWRITEBYTECODE=1
export XDG_STATE_HOME="$HOME/.local/state" XDG_RUNTIME_DIR="$TMP/runtime" http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9
unset DISPLAY WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE DBUS_SESSION_BUS_ADDRESS XDG_CONFIG_HOME XDG_DATA_HOME
mkdir -p "$HOME/.config/hypr" "$HOME/.config/omarchy" "$HOME/.grok" "$TMP/bin" "$OMARCHY_PATH/shell/plugins"
: > "$HOME/.config/hypr/hyprland.lua"
cat > "$TMP/bin/systemctl" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$FIXTURE/systemctl.log"
state() { cat "$FIXTURE/unit-$1" 2>/dev/null || printf '%s' "$2"; }
case "$1 $2" in
  "--user is-active") s=$(state active inactive); printf '%s\n' "$s"; [[ "$s" == active ]] ;;
  "--user is-enabled") s=$(state enabled disabled); printf '%s\n' "$s"; [[ "$s" == enabled ]] ;;
  "--user disable") printf inactive > "$FIXTURE/unit-active"; printf disabled > "$FIXTURE/unit-enabled" ;;
  *) printf 'unexpected systemctl %s\n' "$*" >> "$FIXTURE/escapes"; exit 97 ;;
esac
EOF
for name in omarchy-shell omarchy-plugin-enable omarchy-plugin-clone omarchy-agent-usage-update grok llama-server curl sudo pkexec; do
    printf '#!/bin/sh\nprintf "unexpected %%s\\n" "%s" >> "$FIXTURE/escapes"\nexit 97\n' "$name" > "$TMP/bin/$name"
done
chmod +x "$TMP/bin"/*
export PATH="$TMP/bin:$PATH"
sudo() { printf 'unexpected sudo\n' >> "$TMP/escapes"; return 97; }
omarchy-bar() { printf 'unexpected bar\n' >> "$TMP/escapes"; return 97; }
hyprctl() { printf 'unexpected hyprctl\n' >> "$TMP/escapes"; return 97; }
python3 - "$REPO" "$TMP" <<'PY'
from pathlib import Path
import sys
repo, tmp = map(Path, sys.argv[1:])
s = (repo/'a-la-carchy.sh').read_text()
(tmp/'functions.sh').write_text(s.split('# TWO-PANEL TUI DATA STRUCTURES', 1)[0])
for item in ('local_stats|Local LLM stats|Enable|Disable|toggle|', 'grok_usage|Grok tokens|Enable|Disable|toggle|'):
    assert s.count('    "' + item) == 1, item
(tmp/'selection.sh').write_text(s.split('# local_stats: 1=Enable, 2=Disable', 1)[1].split('# window_title:', 1)[0])
start = 'if [ "$ENABLE_LOCAL_STATS" = true ]; then'
(tmp/'actions.sh').write_text(start + s.split(start, 1)[1].split('if [ "$SHOW_WINDOW_TITLE"', 1)[0])
for line in ('Enable Local LLM stats (collection stays off)', 'Disable Local LLM stats', 'Enable Grok tokens', 'Disable Grok tokens'):
    assert s.count('ACTION_SUMMARY+=("' + line + '")') == 1, line
PY
[[ $? == 0 ]] || exit 1
source "$TMP/functions.sh"
ALC_AGENTS_CLONE_DIR="$REPO/extras/agents-clone"
PLUGINS="$HOME/.config/omarchy/plugins"
CLONE="$PLUGINS/alacarchy.agents"
UNIT="$HOME/.config/systemd/user/local-router-stats.service"
LOCAL_RECORD="$XDG_STATE_HOME/omarchy/local-router/agents/local.json"
GROK_RECORD="$XDG_STATE_HOME/omarchy/grok-usage/agents/grok.json"
GROK_CONFIG="$HOME/.grok/config.toml"
clear() { :; }
confirm_continue() { [[ "$MODE" != cancel ]]; }
MODE=ok
PASSED=0 FAILED=0
check() { local label="$1"; shift; if "$@"; then PASSED=$((PASSED+1)); printf 'ok - %s\n' "$label"; else FAILED=$((FAILED+1)); printf 'FAIL - %s\n' "$label"; fi; }
fails() { ! "$@" > "$TMP/output" 2>&1; }
quiet() { "$@" > "$TMP/output" 2>&1; }
no_green() { local s; for s in "${SUMMARY_LOG[@]}"; do [[ "$s" != ✓* ]] || return 1; done; }
summary_has() { local s; for s in "${SUMMARY_LOG[@]}"; do [[ "$s" == *"$1"* ]] && return 0; done; return 1; }
layout_is() { jq -e --argjson want "$1" '[.bar.layout.right[].id] == $want' "$SHELL_JSON" > /dev/null; }
same_json() { cmp -s <(jq -S . "$1") <(jq -S . "$2"); }
# fixture <tabs|limits>: fresh HOME state and a synthetic packaged Agents plugin.
fixture() {
    rm -rf -- "$PLUGINS" "$HOME/.local" "$HOME/.config/systemd" "$OMARCHY_PATH/shell/plugins/agents" "$TMP/systemctl.log" "$TMP"/unit-*
    rm -f -- "$SHELL_JSON".backup.* "$HOME/.grok"/config.toml*
    python3 - "$REPO/tests/agents_fixture.py" "$OMARCHY_PATH/shell/plugins/agents" "$1" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('agents_fixture', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.make_source(sys.argv[2], sys.argv[3])
PY
    printf '%s\n' '{"bar":{"layout":{"left":[{"id":"custom","keep":true}],"right":[{"id":"omarchy.tray"},{"id":"omarchy.agents","refreshIntervalSec":300},{"id":"omarchy.power"}]}},"other":42}' > "$SHELL_JSON"
    cp "$SHELL_JSON" "$TMP/original"
    printf '# user settings\nmodel = "grok-4"\n' > "$GROK_CONFIG"
    cp "$GROK_CONFIG" "$TMP/grok-original"
    MODE=ok; SUMMARY_LOG=()
}

# --- Local: enable, repeat, disable (tabs layout; the limits layout follows) ---
fixture tabs
check 'local starts disabled' _local_stats_disabled
check 'grok starts disabled' _grok_usage_disabled
check 'enable local applies' quiet enable_local_router_stats
check 'local enabled state read back' _local_stats_enabled
check 'stock agents entry replaced in place, settings kept' jq -e '.bar.layout.right[1].id == "alacarchy.agents" and .bar.layout.right[1].refreshIntervalSec == 300 and .bar.layout.right[1]._alaCarchyAgents.features == ["local"]' "$SHELL_JSON"
check 'unrelated entries preserved' jq -e '.bar.layout.left[0].keep and .bar.layout.right[0].id == "omarchy.tray" and .bar.layout.right[2].id == "omarchy.power" and .other == 42' "$SHELL_JSON"
check 'shell.json backed up first' bash -c 'for f in "$1".backup.*; do cmp -s "$f" "$2" && exit 0; done; exit 1' _ "$SHELL_JSON" "$TMP/original"
check 'collector service unit installed but never started or enabled' bash -c '[[ -f "$1" && ! -e "$2" && ! -d "$3" ]]' _ "$UNIT" "$TMP/systemctl.log" "$HOME/.config/systemd/user/default.target.wants"
check 'panel record says collection is pending' jq -e '.usageStatusText == "Activation pending · not collecting" and .todayTotalTokens == 0' "$LOCAL_RECORD"
check 'success summary recorded' summary_has '✓  Local LLM stats staged (collection pending activation)'
check 'activation command kept in the summary' summary_has 'systemctl --user enable --now local-router-stats.service'
cp "$SHELL_JSON" "$TMP/enabled"; SUMMARY_LOG=()
check 're-enable succeeds' quiet enable_local_router_stats
check 're-enable is exactly idempotent' cmp -s "$SHELL_JSON" "$TMP/enabled"
check 're-enable reports already set, without a second hint' bash -c '[[ "$1" == 1 && "$2" == *"already set" ]]' _ "${#SUMMARY_LOG[@]}" "${SUMMARY_LOG[0]}"

# --- Grok beside Local, then each removed independently ---
check 'enable grok applies beside local' quiet enable_grok_usage
check 'grok enabled state read back' _grok_usage_enabled
check 'local still enabled' _local_stats_enabled
check 'one shared entry lists both features' jq -e '[.bar.layout[][] | select(.id == "alacarchy.agents")] | length == 1 and .[0]._alaCarchyAgents.features == ["grok","local"]' "$SHELL_JSON"
check 'grok status-line row added, user settings kept' bash -c 'grep -qxF "[ui.status_line]" "$1" && grep -qxF "model = \"grok-4\"" "$1"' _ "$GROK_CONFIG"
check 'grok record never invents a quota' jq -e '.limits == [{"key":"quota","label":"Subscription quota","percent":-1}] and .todayTotalTokens == 0' "$GROK_RECORD"
check 'disable local applies' quiet disable_local_router_stats
check 'local disabled state read back' _local_stats_disabled
check 'grok survives local removal' _grok_usage_enabled
check 'disable grok applies' quiet disable_grok_usage
check 'grok disabled state read back' _grok_usage_disabled
check 'original layout and settings restored' same_json "$SHELL_JSON" "$TMP/original"
check 'grok config restored byte for byte' cmp -s "$GROK_CONFIG" "$TMP/grok-original"
check 'plugin files kept on disable' test -f "$CLONE/Panel.qml"
SUMMARY_LOG=()
check 'repeat disable is a no-op success' quiet disable_local_router_stats
check 'repeat disable reports already set' summary_has 'already set'
check 'only a status query ever reached systemctl' bash -c '! grep -qvE "^--user (is-active|is-enabled) local-router-stats.service$" "$1"' _ "$TMP/systemctl.log"

# --- A running collector is stopped on disable, the router never touched ---
fixture tabs
quiet enable_local_router_stats
printf active > "$TMP/unit-active"; printf enabled > "$TMP/unit-enabled"
check 'disable local with a running collector applies' quiet disable_local_router_stats
check 'only the collector unit was stopped' bash -c 'grep -qxF -- "--user disable --now local-router-stats.service" "$1" && ! grep -q llama "$1"' _ "$TMP/systemctl.log"
check 'collector unit removed' test ! -e "$UNIT"

# --- Cancel, missing helper, foreign plugin, backup failure ---
fixture tabs; MODE=cancel
check 'cancel handled' quiet enable_local_router_stats
check 'cancel preserves bytes' cmp -s "$SHELL_JSON" "$TMP/original"
check 'cancel creates nothing' bash -c '[[ ! -e "$1" && ! -e "$2" && ! -e "$3" ]]' _ "$PLUGINS" "$UNIT" "$LOCAL_RECORD"
fixture tabs
ALC_AGENTS_CLONE_DIR="$TMP/missing"
for action in enable_local_router_stats enable_grok_usage; do
    SUMMARY_LOG=()
    check "$action without the checkout helper fails honestly" fails "$action"
    check "$action missing helper has no success log" no_green
    check "$action missing helper says what is required" summary_has 'full checkout'
done
check 'missing helper preserves config' cmp -s "$SHELL_JSON" "$TMP/original"
ALC_AGENTS_CLONE_DIR="$REPO/extras/agents-clone"
fixture tabs
mkdir -p "$CLONE"
printf '%s\n' '{"id":"alacarchy.agents","omarchy":{"clonedFrom":"omarchy.agents","alaCarchy":"local-router-stats/1"}}' > "$CLONE/manifest.json"
check 'custom plugin at the managed id is refused' fails enable_local_router_stats
check 'custom refusal has no success log' no_green
check 'custom refusal reason reaches the summary' summary_has 'not an unmodified managed clone'
check 'custom refusal preserves config' cmp -s "$SHELL_JSON" "$TMP/original"
check 'custom plugin preserved' bash -c '[[ "$(ls -A "$1")" == manifest.json ]]' _ "$CLONE"
fixture tabs
check 'backup failure propagates' fails bash -c 'source "$1"; ALC_AGENTS_CLONE_DIR="$2"; clear(){ :; }; confirm_continue(){ return 0; }; backup_file(){ return 1; }; enable_local_router_stats' _ "$TMP/functions.sh" "$REPO/extras/agents-clone"
check 'backup failure preserves config' cmp -s "$SHELL_JSON" "$TMP/original"
check 'backup failure creates no plugin' test ! -d "$PLUGINS"

# --- Omarchy that collects Grok itself: Local works, the Grok tab is refused ---
fixture limits
check 'enable local applies on the limits-first panel' quiet enable_local_router_stats
check 'local enabled on the limits-first panel' _local_stats_enabled
check 'staged clone is Local only' jq -e '.name == "Agents + Local" and .omarchy.alaCarchy == "local-router-stats/1"' "$CLONE/manifest.json"
cp "$SHELL_JSON" "$TMP/enabled"; SUMMARY_LOG=()
check 'grok tab refused where Omarchy shows Grok natively' fails enable_grok_usage
check 'refused grok tab has no success log' no_green
check 'refusal reason is kept in the summary' summary_has 'natively in the Agents panel'
check 'refusal leaves the layout untouched' cmp -s "$SHELL_JSON" "$TMP/enabled"
check 'refusal leaves grok config untouched' cmp -s "$GROK_CONFIG" "$TMP/grok-original"
check 'refusal installs no grok collector or record' bash -c '[[ ! -e "$1" && ! -e "$2" ]]' _ "$HOME/.local/share/a-la-carchy/grok-usage/collector.py" "$GROK_RECORD"
check 'grok still reads as disabled' _grok_usage_disabled
check 'local unaffected by the refusal' _local_stats_enabled
check 'disable local applies on the limits-first panel' quiet disable_local_router_stats
check 'limits-first layout restored' same_json "$SHELL_JSON" "$TMP/original"

# --- Selection and dispatch wiring ---
declare -A TOGGLE_SELECTIONS=()
for item in local_stats grok_usage; do
    for n in 0 1 2; do
        TOGGLE_SELECTIONS=([local_stats]=0 [grok_usage]=0); TOGGLE_SELECTIONS[$item]="$n"
        source "$TMP/selection.sh"
        : > "$TMP/dispatch"
        enable_local_router_stats() { printf 'enable-local ' >> "$TMP/dispatch"; }
        disable_local_router_stats() { printf 'disable-local ' >> "$TMP/dispatch"; }
        enable_grok_usage() { printf 'enable-grok ' >> "$TMP/dispatch"; }
        disable_grok_usage() { printf 'disable-grok ' >> "$TMP/dispatch"; }
        source "$TMP/actions.sh"
        case "$n" in
            0) check "$item unselected dispatches nothing" test ! -s "$TMP/dispatch" ;;
            1) check "$item option 1 dispatches only its enable handler" test "$(< "$TMP/dispatch")" = "enable-${item%%_*} " ;;
            2) check "$item option 2 dispatches only its disable handler" test "$(< "$TMP/dispatch")" = "disable-${item%%_*} " ;;
        esac
    done
done
check 'no live/system command escaped' test ! -e "$TMP/escapes"
printf '\n%s passed, %s failed\n' "$PASSED" "$FAILED"
[[ "$FAILED" == 0 ]]
