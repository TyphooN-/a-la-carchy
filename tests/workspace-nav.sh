#!/bin/bash
# Regression tests for Keybindings > Workspace nav (HyDE workspace navigation).
#
#   bash tests/workspace-nav.sh
#
# Sources a-la-carchy.sh's functions against a throwaway $HOME and $OMARCHY_PATH.
# hyprctl is mocked, so the real ~/.config and the running Hyprland session are
# never touched. Optional checks, skipped when the tools are missing:
#   - a Lua model of Hyprland's bind/unbind rules (needs lua)
#   - `Hyprland --verify-config` (config check only, nothing starts) on Omarchy's
#     stock user config plus the block, with a clean environment (needs Hyprland
#     and /usr/share/omarchy, both only read)
# Set ALC_SKIP_HYPRLAND_VERIFY=1 for mock/source-only checks when the installed
# compositor cannot verify even an empty config. This does not qualify native behavior.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO/a-la-carchy.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
shopt -s nullglob

PASSED=0
FAILED=0
ok() { PASSED=$((PASSED + 1)); echo "ok   - $1"; }
not_ok() { FAILED=$((FAILED + 1)); echo "FAIL - $1"; [[ -n "${2:-}" ]] && echo "$2" | sed 's/^/       /'; }
assert() { local desc="$1"; shift; if "$@"; then ok "$desc"; else not_ok "$desc"; fi; }
assert_eq() {
    if [[ "$2" == "$3" ]]; then ok "$1"; else not_ok "$1" "expected: $3"$'\n'"actual:   $2"; fi
}
assert_file() {
    if cmp -s "$2" "$3"; then ok "$1"; else not_ok "$1" "$(diff "$3" "$2")"; fi
}

# --- Fixture environment -----------------------------------------------------

export HOME="$TMP/home"
export OMARCHY_PATH="$TMP/omarchy"
export HYPRLAND_INSTANCE_SIGNATURE="alc-test"  # makes hypr_reload_check use the mock
unset WAYLAND_DISPLAY
mkdir -p "$HOME/.config/hypr" "$OMARCHY_PATH/default/hypr/bindings" "$TMP/bin"
: > "$HOME/.config/hypr/hyprland.lua"  # a-la-carchy refuses to start without it

# Mock hyprctl: logs every call; configerrors prints $TMP/configerrors if present
cat > "$TMP/bin/hyprctl" <<EOF
#!/bin/bash
echo "\$*" >> "$TMP/hyprctl.log"
[[ "\$1" == configerrors && -f "$TMP/configerrors" ]] && cat "$TMP/configerrors"
exit 0
EOF
chmod +x "$TMP/bin/hyprctl"
export PATH="$TMP/bin:$PATH"

# Load every function defined before the TUI starts
MARKER='# TWO-PANEL TUI DATA STRUCTURES'
if [[ "$(grep -cxF "$MARKER" "$SCRIPT")" != 1 ]]; then
    echo "Bail out! '$MARKER' must appear exactly once in a-la-carchy.sh"
    exit 1
fi
sed "/^$MARKER\$/,\$d" "$SCRIPT" > "$TMP/alc-functions.sh"
# shellcheck source=/dev/null
source "$TMP/alc-functions.sh"

clear() { :; }
sleep() { :; }
hyprctl() { "$TMP/bin/hyprctl" "$@"; }
# Backup names have one-second resolution; keep each one in the tests
date() { command date "$@" | tr -d '\n'; echo "_$BASHPID"; }
CONFIRM_ALL=true

if [[ "$BINDINGS_LUA" != "$TMP/"* || "$HYPR_DIR" != "$TMP/"* || "$OMARCHY_DIR" != "$TMP/"* ]]; then
    echo "Bail out! paths escaped the fixture: $BINDINGS_LUA $OMARCHY_DIR"
    exit 1
fi
if [[ "$(type -t hyprctl)" != function ]]; then
    echo "Bail out! hyprctl is not mocked"
    exit 1
fi

OMARCHY_TILING='o.bind("SUPER + S", "Toggle scratchpad", hl.dsp.workspace.toggle_special("scratchpad"))
o.bind("SUPER + TAB", "Next workspace", hl.dsp.focus({ workspace = "e+1" }))
o.bind("SUPER + SHIFT + TAB", "Previous workspace", hl.dsp.focus({ workspace = "e-1" }))
o.bind("SUPER + ALT + TAB", "Next window in group", hl.dsp.group.next())
o.bind("SUPER + ALT + SHIFT + TAB", "Previous window in group", hl.dsp.group.prev())
o.bind("SUPER + CTRL + LEFT", "Move grouped window focus left", hl.dsp.group.prev())
o.bind("SUPER + CTRL + RIGHT", "Move grouped window focus right", hl.dsp.group.next())'

USER_BINDINGS='-- Keep only your personal keybinding overrides here.
-- o.bind("SUPER + CTRL + LEFT", "Commented out", "notify-send left")
o.bind("SUPER + SHIFT + R", "SSH", "alacritty -e ssh your-server")

-- Change an existing binding by unbinding it first.
hl.unbind("SUPER + SPACE")
o.bind("SUPER + SPACE", "Omarchy menu", "omarchy-menu toggle root")'

BLOCK_BODY='hl.unbind("SUPER + CTRL + LEFT")
hl.unbind("SUPER + CTRL + RIGHT")
o.bind("SUPER + CTRL + LEFT", "Previous workspace on monitor", hl.dsp.focus({ workspace = "r-1" }))
o.bind("SUPER + CTRL + RIGHT", "Next workspace on monitor", hl.dsp.focus({ workspace = "r+1" }))'
BLOCK="-- >>> a-la-carchy workspace-nav
$BLOCK_BODY
-- <<< a-la-carchy workspace-nav"

reset_fixture() {
    rm -rf "$HYPR_DIR"
    mkdir -p "$HYPR_DIR"
    : > "$HYPR_DIR/hyprland.lua"
    printf '%s\n' "$USER_BINDINGS" > "$BINDINGS_LUA"
    cp "$BINDINGS_LUA" "$TMP/bindings.orig"
    printf '%s\n' "$OMARCHY_TILING" > "$OMARCHY_PATH/default/hypr/bindings/tiling.lua"
    rm -f "$TMP/configerrors" "$TMP/hyprctl.log"
    SUMMARY_LOG=()
}

backups() { local b=("$BINDINGS_LUA".backup.*); echo "${#b[@]}"; }
last_log() { echo "${SUMMARY_LOG[${#SUMMARY_LOG[@]}-1]:-}"; }
hyprctl_calls() { [[ -f "$TMP/hyprctl.log" ]] && paste -sd' ' "$TMP/hyprctl.log"; }
block_line() { grep -nxF -- "$1" "$BINDINGS_LUA" | cut -d: -f1 | paste -sd' '; }

{ cat <<<"$USER_BINDINGS"; echo; echo "$BLOCK"; } > "$TMP/enabled.expected"

# --- Enable, repeat, disable -------------------------------------------------

echo "# enable / disable"
reset_fixture
enable_workspace_nav > "$TMP/out" 2>&1
assert_eq "enable returns 0" "$?" 0
assert_file "enable appends only the managed block; user content is untouched" "$BINDINGS_LUA" "$TMP/enabled.expected"
assert_eq "enable makes one backup first" "$(backups)" 1
for b in "$BINDINGS_LUA".backup.*; do assert_file "the backup holds the original file" "$b" "$TMP/bindings.orig"; done
assert_eq "enable reloads Hyprland and checks for config errors" "$(hyprctl_calls)" "reload configerrors"
assert_eq "enable is logged as done" "$(last_log)" "✓  Bound SUPER+CTRL+LEFT/RIGHT to previous/next workspace (HyDE)"

rm -f "$TMP/hyprctl.log"
enable_workspace_nav > "$TMP/out" 2>&1
assert_eq "enabling again returns 0" "$?" 0
assert_file "enabling again leaves the file alone" "$BINDINGS_LUA" "$TMP/enabled.expected"
assert_eq "enabling again makes no backup" "$(backups)" 1
assert_eq "enabling again does not reload Hyprland" "$(hyprctl_calls)" ""
assert_eq "enabling again reports already set" "$(last_log)" "--  Bound SUPER+CTRL+LEFT/RIGHT to previous/next workspace (HyDE) -- already set"

disable_workspace_nav > "$TMP/out" 2>&1
assert_eq "disable returns 0" "$?" 0
assert_file "disable restores the original file byte for byte" "$BINDINGS_LUA" "$TMP/bindings.orig"
assert_eq "disable makes its own backup" "$(backups)" 2
assert_eq "disable is logged as done" "$(last_log)" "✓  Restored SUPER+CTRL+LEFT/RIGHT grouped window focus (Omarchy)"

rm -f "$TMP/hyprctl.log"
disable_workspace_nav > "$TMP/out" 2>&1
assert_file "disabling again leaves the file alone" "$BINDINGS_LUA" "$TMP/bindings.orig"
assert_eq "disabling again makes no backup" "$(backups)" 2
assert_eq "disabling again does not reload Hyprland" "$(hyprctl_calls)" ""
assert_eq "disabling again reports not set" "$(last_log)" "--  Restored SUPER+CTRL+LEFT/RIGHT grouped window focus (Omarchy) -- not set"

enable_workspace_nav > /dev/null 2>&1
disable_workspace_nav > /dev/null 2>&1
enable_workspace_nav > /dev/null 2>&1
assert_file "enable, disable, enable ends with a single block" "$BINDINGS_LUA" "$TMP/enabled.expected"

# --- Conflict detection ------------------------------------------------------

echo "# conflicts"
cat > "$TMP/scan.lua" <<'EOF'
o.bind("CTRL + SUPER + Left", "Mine", "notify-send left")
hl.bind("super+control+right", hl.dsp.exec_cmd("true"))
  o.bind_toggle("SUPER + CTRL + code:113", "Toggle", "idle")
o.bind("WIN + CTRL + RIGHT", "Mine", "true") o.bind("SUPER + CTRL + LEFT", "Two on one line", "true")
-- o.bind("SUPER + CTRL + LEFT", "Comment", "true")
hl.unbind("SUPER + CTRL + LEFT")
o.bind("SUPER + CTRL + SHIFT + LEFT", "Extra modifier", "true")
o.bind("SUPER + CTRL + UP", "Other key", "true")
o.bind("SUPER + LEFT", "No CTRL", "true")
o.bind("CTRL + LEFT", "No SUPER", "true")
foo.bind("SUPER + CTRL + LEFT", "Not o.bind", "true")
-- >>> a-la-carchy workspace-nav
o.bind("SUPER + CTRL + LEFT", "Our own block", "true")
-- <<< a-la-carchy workspace-nav
EOF
assert_eq "the scanner finds every spelling, and only real binds" "$(workspace_nav_binds "$TMP/scan.lua")" \
'scan.lua:1: CTRL + SUPER + Left
scan.lua:2: super+control+right
scan.lua:3: SUPER + CTRL + code:113
scan.lua:4: WIN + CTRL + RIGHT
scan.lua:4: SUPER + CTRL + LEFT'
assert_eq "--omarchy skips binds hl.unbind removes (spaces and case ignored)" \
    "$(workspace_nav_binds --omarchy "$TMP/scan.lua")" \
'scan.lua:1: CTRL + SUPER + Left
scan.lua:2: super+control+right
scan.lua:3: SUPER + CTRL + code:113
scan.lua:4: WIN + CTRL + RIGHT'

refuses() {
    local desc="$1" file="$2" expect="$3"
    cp "$file" "$TMP/before"
    enable_workspace_nav > "$TMP/out" 2>&1
    local rc=$?
    assert_eq "$desc: refused" "$rc" 1
    assert_file "$desc: file untouched" "$file" "$TMP/before"
    assert_eq "$desc: no backup, no reload" "$(backups) $(hyprctl_calls)" "0 "
    assert "$desc: names the clashing line" grep -qF "$expect" "$TMP/out"
    assert_eq "$desc: logged as skipped" "$(last_log)" "✗  HyDE workspace navigation -- skipped (SUPER+CTRL+LEFT/RIGHT already bound)"
}

reset_fixture
echo 'o.bind("CTRL + SUPER + Left", "Mine", "notify-send left")' >> "$BINDINGS_LUA"
refuses "user bind in bindings.lua" "$BINDINGS_LUA" "bindings.lua:8: CTRL + SUPER + Left"

reset_fixture
echo 'hl.bind("SUPER + CONTROL + RIGHT", hl.dsp.exec_cmd("true"))' >> "$HYPR_DIR/hyprland.lua"
refuses "user bind in hyprland.lua" "$HYPR_DIR/hyprland.lua" "hyprland.lua:1: SUPER + CONTROL + RIGHT"

reset_fixture
printf "%s\n" "o.bind ('CTRL + SUPER + LEFT', 'Mine', 'true')" >> "$BINDINGS_LUA"
refuses "single-quoted user bind with spaced call" "$BINDINGS_LUA" "bindings.lua:8: CTRL + SUPER + LEFT"

reset_fixture
printf '%s\n' '' '-- >>> a-la-carchy keybind-edits' 'hl.unbind("SUPER + S")' \
    'o.bind("SUPER + CTRL + LEFT", "Toggle scratchpad", hl.dsp.workspace.toggle_special("scratchpad"))' \
    '-- <<< a-la-carchy keybind-edits' >> "$BINDINGS_LUA"
refuses "Keybind Editor edit on the keys" "$BINDINGS_LUA" "bindings.lua:11: SUPER + CTRL + LEFT"

reset_fixture
sed -i 's/"SUPER + CTRL + LEFT"/"CTRL + SUPER + LEFT"/' "$OMARCHY_PATH/default/hypr/bindings/tiling.lua"
refuses "Omarchy default spelled so hl.unbind misses it" "$BINDINGS_LUA" "tiling.lua:6: CTRL + SUPER + LEFT"

reset_fixture
sed -i 's/"SUPER + CTRL + LEFT"/"SUPER+CTRL+Left"/' "$OMARCHY_PATH/default/hypr/bindings/tiling.lua"
enable_workspace_nav > /dev/null 2>&1
assert_eq "Omarchy default that hl.unbind still matches is not a conflict" "$?" 0

reset_fixture
printf '%s\n' '' '-- >>> a-la-carchy keybind-edits' 'hl.unbind("SUPER + CTRL + LEFT")' \
    'o.bind("SUPER + CTRL + COMMA", "Move grouped window focus left", hl.dsp.group.prev())' \
    '-- <<< a-la-carchy keybind-edits' >> "$BINDINGS_LUA"
cp "$BINDINGS_LUA" "$TMP/before"
enable_workspace_nav > /dev/null 2>&1
assert_eq "grouped focus already moved away: enable still works" "$?" 0
{ cat "$TMP/before"; echo; echo "$BLOCK"; } > "$TMP/expected"
assert_file "grouped focus already moved away: block goes after the edits" "$BINDINGS_LUA" "$TMP/expected"

# --- Failures ----------------------------------------------------------------

echo "# failures"
reset_fixture
rm -f "$BINDINGS_LUA"
enable_workspace_nav > "$TMP/out" 2>&1
assert_eq "missing bindings.lua: returns 1" "$?" 1
assert "missing bindings.lua: not created" test ! -e "$BINDINGS_LUA"
assert_eq "missing bindings.lua: logged as failed" "$(last_log)" "✗  Bound SUPER+CTRL+LEFT/RIGHT to previous/next workspace (HyDE) -- failed (config not found)"

reset_fixture
echo '/home/u/.config/hypr/bindings.lua:9: hl.bind: bad argument' > "$TMP/configerrors"
enable_workspace_nav > "$TMP/out" 2>&1
assert_eq "Hyprland config errors: reported in the summary" "$(last_log)" "✗  Bound SUPER+CTRL+LEFT/RIGHT to previous/next workspace (HyDE) -- Hyprland reported config errors"
assert "Hyprland config errors: shown to the user" grep -qF "hl.bind: bad argument" "$TMP/out"
assert_eq "Hyprland config errors: backup kept for rollback" "$(backups)" 1

# --- Keybind Editor interplay ------------------------------------------------

echo "# keybind editor"
editor_move() {  # editor_move <description> <new mods> <new key>
    declare -ga EDIT_BINDINGS_ITEMS=() BINDING_LUA_ACTIONS=()
    declare -gA BINDING_EDITS=() BINDING_ORIG_KEYS=()
    load_all_bindings
    local i
    for i in "${!EDIT_BINDINGS_ITEMS[@]}"; do
        [[ "${EDIT_BINDINGS_ITEMS[$i]}" == *"|$1|"* ]] && BINDING_EDITS[$i]="$2|$3"
    done
    apply_binding_edits > "$TMP/out" 2>&1
}

reset_fixture
enable_workspace_nav > /dev/null 2>&1
editor_move "Move grouped window focus left" "SUPER CTRL" "COMMA"
assert "moving grouped focus away writes its unbind" grep -qxF 'hl.unbind("SUPER + CTRL + LEFT")' <(lua_block_get "$BINDINGS_LUA" keybind-edits)
assert_eq "the workspace-nav block moves back below the edits" \
    "$(block_line '-- <<< a-la-carchy keybind-edits') < $(block_line '-- >>> a-la-carchy workspace-nav')" \
    "$(( $(block_line '-- <<< a-la-carchy keybind-edits') )) < $(( $(block_line '-- <<< a-la-carchy keybind-edits') + 2 ))"
assert_eq "the block itself is unchanged and not duplicated" "$(lua_block_get "$BINDINGS_LUA" workspace-nav)" "$BLOCK_BODY"
assert_eq "the file ends with the block" "$(tail -n 1 "$BINDINGS_LUA")" "-- <<< a-la-carchy workspace-nav"
cp "$BINDINGS_LUA" "$TMP/guarded.lua"

reset_fixture
enable_workspace_nav > /dev/null 2>&1
SUMMARY_LOG=()
editor_move "Toggle scratchpad" "SUPER CTRL" "LEFT"
assert "an edit onto SUPER+CTRL+LEFT is warned about" grep -qF "bindings.lua" "$TMP/out"
assert_eq "an edit onto SUPER+CTRL+LEFT is logged" "${SUMMARY_LOG[0]}" "✗  HyDE workspace navigation -- SUPER+CTRL+LEFT/RIGHT is also bound elsewhere"
assert_eq "an edit onto SUPER+CTRL+LEFT is not unbound by the block" "$(tail -n 1 "$BINDINGS_LUA")" "-- <<< a-la-carchy keybind-edits"

# --- Structure of the TUI wiring --------------------------------------------

echo "# wiring"
item=$(grep -F '"workspace_nav|' "$SCRIPT" | sed 's/^ *"//; s/"$//')
parse_toggle_item() { IFS='|' read -r TOGGLE_ID TOGGLE_NAME TOGGLE_OPT1 TOGGLE_OPT2 TOGGLE_TYPE TOGGLE_DESC <<< "$1"; }
parse_toggle_item "$item"
assert_eq "menu item: id, options and type" "$TOGGLE_ID $TOGGLE_OPT1 $TOGGLE_OPT2 $TOGGLE_TYPE" "workspace_nav HyDE Omarchy radio"
assert "menu item: name fits the 14-column name field" test "${#TOGGLE_NAME}" -le 14
edits_at=$(grep -nx 'apply_binding_edits' "$SCRIPT" | cut -d: -f1)
enable_at=$(grep -nx '    enable_workspace_nav' "$SCRIPT" | cut -d: -f1)
assert "workspace nav is applied after the keybind edits" test "${edits_at:-0}" -gt 0 -a "${enable_at:-0}" -gt "${edits_at:-0}"

# --- Lua model of Hyprland's bind registry -----------------------------------
# Hyprland 0.56.2 (src/managers/KeybindManager.cpp): hl.bind appends; hl.unbind
# drops binds whose key string matches exactly or after removing spaces and
# lowercasing; every bind whose modifiers and key match a key press runs.

cat > "$TMP/model.lua" <<'EOF'
local binds = {}
local function flat(s) return (s:gsub(" ", "")):lower() end
local function dsp(path)
  return setmetatable({}, {
    __index = function(_, k) return dsp(path .. "." .. k) end,
    __call = function(_, args) return { path = path, args = args } end,
  })
end
hl = { dsp = dsp("hl.dsp") }
function hl.bind(keys, dispatcher, opts) binds[#binds + 1] = { keys = keys, dsp = dispatcher, desc = opts and opts.description } end
function hl.unbind(keys)
  for i = #binds, 1, -1 do
    if binds[i].keys == keys or flat(binds[i].keys) == flat(keys) then table.remove(binds, i) end
  end
end
o = {}
o.workspaces = {}
for _, method in ipairs({ "focus", "move_window", "move_to_monitor", "move_window_to_monitor" }) do
  local name = method
  o.workspaces[name] = function(...)
    return { path = "o.workspaces." .. name, args = { ... } }
  end
end
function o.bind(keys, desc, dispatcher, opts)
  opts = opts or {}
  opts.description = desc
  if type(dispatcher) == "string" then dispatcher = hl.dsp.exec_cmd(dispatcher) end
  hl.bind(keys, dispatcher, opts)
end

local alias = { CONTROL = "CTRL", WIN = "SUPER", LOGO = "SUPER", MOD4 = "SUPER", META = "SUPER", MOD1 = "ALT" }
local function combo(keys)
  local mods, key = {}, ""
  for token in keys:upper():gmatch("[^+]+") do
    local part = token:match("^%s*(.-)%s*$")
    part = alias[part] or part
    if part == "SUPER" or part == "CTRL" or part == "ALT" or part == "SHIFT" then mods[#mods + 1] = part else key = part end
  end
  table.sort(mods)
  return table.concat(mods, "+") .. "+" .. key
end

for _, file in ipairs(arg) do dofile(file) end

for _, want in ipairs({ "CTRL+SUPER+LEFT", "CTRL+SUPER+RIGHT", "CTRL+SUPER+COMMA" }) do
  for _, b in ipairs(binds) do
    if combo(b.keys) == want then
      print(want .. " -> " .. (b.desc or "") .. " (" .. b.dsp.path .. ((b.dsp.args and b.dsp.args.workspace) and (" " .. b.dsp.args.workspace) or "") .. ")")
    end
  end
end
EOF

if command -v lua > /dev/null; then
    echo "# lua model"
    TILING="$OMARCHY_PATH/default/hypr/bindings/tiling.lua"
    reset_fixture
    assert_eq "model: Omarchy alone puts grouped focus on the keys" "$(lua "$TMP/model.lua" "$TILING" "$BINDINGS_LUA")" \
'CTRL+SUPER+LEFT -> Move grouped window focus left (hl.dsp.group.prev)
CTRL+SUPER+RIGHT -> Move grouped window focus right (hl.dsp.group.next)'

    enable_workspace_nav > /dev/null 2>&1
    assert_eq "model: with the block, each key runs exactly one bind, ours" "$(lua "$TMP/model.lua" "$TILING" "$BINDINGS_LUA")" \
'CTRL+SUPER+LEFT -> Previous workspace on monitor (hl.dsp.focus r-1)
CTRL+SUPER+RIGHT -> Next workspace on monitor (hl.dsp.focus r+1)'

    assert_eq "model: after moving grouped focus in the editor, both keep working" "$(lua "$TMP/model.lua" "$TILING" "$TMP/guarded.lua")" \
'CTRL+SUPER+LEFT -> Previous workspace on monitor (hl.dsp.focus r-1)
CTRL+SUPER+RIGHT -> Next workspace on monitor (hl.dsp.focus r+1)
CTRL+SUPER+COMMA -> Move grouped window focus left (hl.dsp.group.prev)'

    # Why the block has to stay below the edits: in the other order their unbind wins

    awk '/^-- >>> a-la-carchy keybind-edits$/,/^-- <<< a-la-carchy keybind-edits$/' "$TMP/guarded.lua" > "$TMP/edits.lua"
    { cat <<<"$USER_BINDINGS"; echo; echo "$BLOCK"; echo; cat "$TMP/edits.lua"; } > "$TMP/unguarded.lua"
    assert_eq "model: with the block above the edits, SUPER+CTRL+LEFT would do nothing" "$(lua "$TMP/model.lua" "$TILING" "$TMP/unguarded.lua")" \
'CTRL+SUPER+RIGHT -> Next workspace on monitor (hl.dsp.focus r+1)
CTRL+SUPER+COMMA -> Move grouped window focus left (hl.dsp.group.prev)'

    REAL_TILING=/usr/share/omarchy/default/hypr/bindings/tiling.lua
    if [[ -f "$REAL_TILING" ]]; then
        assert_eq "model: installed Omarchy tiling.lua plus the block" "$(lua "$TMP/model.lua" "$REAL_TILING" "$BINDINGS_LUA")" \
'CTRL+SUPER+LEFT -> Previous workspace on monitor (hl.dsp.focus r-1)
CTRL+SUPER+RIGHT -> Next workspace on monitor (hl.dsp.focus r+1)'
    fi
else
    echo "# skip lua model (lua not installed)"
fi

# --- Installed Omarchy + Hyprland --------------------------------------------

REAL_OMARCHY=/usr/share/omarchy
if command -v Hyprland > /dev/null && [[ -f "$REAL_OMARCHY/config/hypr/hyprland.lua" && -d "$REAL_OMARCHY/default/hypr/bindings" ]]; then
    echo "# installed Omarchy and Hyprland (read only)"
    INT="$TMP/int"
    mkdir -p "$INT/home/.config/hypr" "$INT/run"
    chmod 700 "$INT/run"
    cp "$REAL_OMARCHY"/config/hypr/*.lua "$INT/home/.config/hypr/"
    HYPR_DIR="$INT/home/.config/hypr" BINDINGS_LUA="$INT/home/.config/hypr/bindings.lua" OMARCHY_DIR="$REAL_OMARCHY"

    assert_eq "installed Omarchy defaults: nothing our hl.unbind would miss" "$(workspace_nav_conflicts)" ""
    SUMMARY_LOG=()
    enable_workspace_nav > /dev/null 2>&1
    assert_eq "installed Omarchy defaults: enable works on the stock user config" "$(last_log)" "✓  Bound SUPER+CTRL+LEFT/RIGHT to previous/next workspace (HyDE)"

    verify() {
        env -i HOME="$INT/home" PATH=/usr/bin:/bin OMARCHY_PATH="$REAL_OMARCHY" XDG_RUNTIME_DIR="$INT/run" \
            timeout 60 Hyprland --verify-config -c "$INT/home/.config/hypr/hyprland.lua" > "$TMP/verify.out" 2>&1
    }
    if [[ "${ALC_SKIP_HYPRLAND_VERIFY:-0}" == 1 ]]; then
        echo "# skip 3 native Hyprland checks (ALC_SKIP_HYPRLAND_VERIFY=1; native behavior unverified)"
    else
        verify
        assert_eq "Hyprland --verify-config accepts the config with the block" "$?" 0
        assert "Hyprland --verify-config says config ok" grep -qx "config ok" "$TMP/verify.out"

        cp "$BINDINGS_LUA" "$TMP/int-bindings.lua"
        echo 'hl.bind("SUPER + CTRL + LEFT", nil)' >> "$BINDINGS_LUA"
        verify
        assert "control: Hyprland --verify-config rejects a broken bind" test "$?" -ne 0
        cp "$TMP/int-bindings.lua" "$BINDINGS_LUA"
    fi
else
    echo "# skip installed Omarchy/Hyprland checks (not installed)"
fi

echo
echo "$PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
