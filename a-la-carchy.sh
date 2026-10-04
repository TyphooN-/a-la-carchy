#!/bin/bash

# A La Carchy - Omarchy Linux Debloater
# Pick and choose what you want to remove, à la carte style!

# TUI color scheme
RESET='\033[0m'
BOLD='\033[1;38;5;75m'
DIM='\033[38;5;243m'
SELECTED_BG='\033[1;38;5;255;48;5;24m'
CHECKED='\033[38;5;114m'

# Modern TUI palette (256-color)
C_BORDER='\033[38;5;67m'
C_ACCENT='\033[38;5;75m'
C_TITLE='\033[1;38;5;75m'
C_SUBTITLE='\033[38;5;246m'
C_SECTION='\033[1;38;5;75m'
C_TEXT='\033[38;5;252m'
C_DIM='\033[38;5;243m'
C_SEL_ACTIVE='\033[1;38;5;255;48;5;24m'
C_SEL_INACTIVE='\033[38;5;252;48;5;237m'
C_CHECK='\033[38;5;114m'
C_MODIFIED='\033[38;5;214m'
C_FOOTER_TXT='\033[38;5;246m'

# Default packages offered for removal
# List from: https://github.com/basecamp/omarchy/blob/master/install/packages.sh
DEFAULT_APPS=(
    "1password-beta"
    "1password-cli"
    "docker"
    "docker-buildx"
    "docker-compose"
    "gnome-calculator"
    "kdenlive"
    "libreoffice-fresh"
    "localsend"
    "obs-studio"
    "obsidian"
    "omarchy-chromium"
    "pinta"
    "signal-desktop"
    "spotify"
    "typora"
    "xournalpp"
)

# Default webapps offered for removal
# List from: https://github.com/basecamp/omarchy/blob/master/install/packaging/webapps.sh
DEFAULT_WEBAPPS=(
    "Basecamp"
    "ChatGPT"
    "Discord"
    "Figma"
    "Fizzy"
    "GitHub"
    "Google Contacts"
    "Google Maps"
    "Google Messages"
    "Google Photos"
    "HEY"
    "WhatsApp"
    "X"
    "YouTube"
    "Zoom"
)

# Summary log for tracking completed actions
declare -a SUMMARY_LOG=()

# When true, skip individual confirmation prompts (user chose "Apply all")
CONFIRM_ALL=false

# Omarchy flag file that removes Suspend from the system menu (present = suspend disabled)
SUSPEND_OFF_FLAG="$HOME/.local/state/omarchy/toggles/suspend-off"

# UWSM defaults config path
UWSM_DEFAULT="$HOME/.config/uwsm/default"

# Laptop auto-off script path
LAPTOP_AUTO_SCRIPT="$HOME/.config/hypr/scripts/laptop-display-auto.sh"

# Power profile startup script path
POWER_PROFILE_SCRIPT="$HOME/.config/hypr/scripts/power-profile-default.sh"

# Battery charge limit udev rule path
BATTERY_LIMIT_UDEV_RULE="/etc/udev/rules.d/99-battery-charge-limit.rules"

# Power profile auto-switch udev rule and script paths
POWER_AUTO_SWITCH_UDEV_RULE="/etc/udev/rules.d/99-power-profile.rules"
POWER_AUTO_SWITCH_SCRIPT="$HOME/.config/hypr/scripts/power-profile-auto-switch.sh"

# Battery limit helper script path (called from the Omarchy menu, uses pkexec)
BATTERY_LIMIT_HELPER="$HOME/.config/hypr/scripts/omarchy-battery-limit.sh"

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    echo "Error: Do not run this script as root!"
    echo "The script will ask for sudo password when needed."
    exit 1
fi

# This version targets Omarchy's Lua-based Hyprland config
if [[ ! -f "$HOME/.config/hypr/hyprland.lua" ]]; then
    echo "Error: ~/.config/hypr/hyprland.lua not found."
    echo "A La Carchy needs an Omarchy release with the Lua Hyprland config."
    echo "Run 'omarchy update' first."
    exit 1
fi

# Published script, used when the menu entry has no local checkout to run
ALC_SCRIPT_URL="https://raw.githubusercontent.com/DanielCoffey1/a-la-carchy/master/a-la-carchy.sh"

# Omarchy's Hyprland config is Lua; a-la-carchy edits live in marked blocks
# inside the user files so they can be updated or removed cleanly.
HYPR_DIR="$HOME/.config/hypr"
# Omarchy menu extension (JSONC); a-la-carchy entries live between markers
MENU_JSONC="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
MENU_MARKER_START="  // >>> a-la-carchy menu entry"
MENU_MARKER_END="  // <<< a-la-carchy menu entry"
BINDINGS_LUA="$HYPR_DIR/bindings.lua"
MONITORS_LUA="$HYPR_DIR/monitors.lua"
INPUT_LUA="$HYPR_DIR/input.lua"
LOOKNFEEL_LUA="$HYPR_DIR/looknfeel.lua"
HYPRLAND_LUA="$HYPR_DIR/hyprland.lua"
OMARCHY_DIR="${OMARCHY_PATH:-/usr/share/omarchy}"

lua_block_has() {
    [[ -f "$1" ]] && grep -qxF -- "-- >>> a-la-carchy $2" "$1"
}

# Print the body of a managed Lua block
lua_block_get() {
    [[ -f "$1" ]] || return 0
    awk -v s="-- >>> a-la-carchy $2" -v e="-- <<< a-la-carchy $2" '
        $0 == e { f = 0 }
        f { print }
        $0 == s { f = 1 }
    ' "$1"
}

lua_block_remove() {
    local file="$1" start="-- >>> a-la-carchy $2" end="-- <<< a-la-carchy $2"
    [[ -f "$file" ]] || return 0
    # Drop the block and the blank line written before it. A blank line is held
    # back until we know whether a block starts right after it.
    awk -v s="$start" -v e="$end" '
        $0 == s { skip = 1; held = 0; next }
        $0 == e { skip = 0; next }
        skip { next }
        held { print ""; held = 0 }
        $0 == "" { held = 1; next }
        { print }
        END { if (held) print "" }
    ' "$file" > "${file}.tmp" && cat "${file}.tmp" > "$file"
    rm -f "${file}.tmp"
}

# Replace (or add) a managed Lua block at the end of a file
lua_block_write() {
    local file="$1" id="$2" content="$3"
    lua_block_remove "$file" "$id"
    {
        echo ""
        echo "-- >>> a-la-carchy $id"
        echo "$content"
        echo "-- <<< a-la-carchy $id"
    } >> "$file"
}

AUTOSTART_LUA="$HYPR_DIR/autostart.lua"

# Lua that runs a command once when Hyprland starts
lua_on_start() {
    printf 'hl.on("hyprland.start", function()\n  hl.exec_cmd("%s")\nend)' "$1"
}

SHELL_JSON="$HOME/.config/omarchy/shell.json"

# Is a widget on the shell bar?
shell_bar_has() {
    [[ -f "$SHELL_JSON" ]] && jq -e --arg id "$1" '[.bar.layout[]?[]? | .id] | index($id) != null' "$SHELL_JSON" &>/dev/null
}

# Read a bar widget setting (prints nothing if unset)
shell_bar_get() {
    jq -r --arg id "$1" --arg key "$2" \
        'first(.bar.layout[]?[]? | select(.id == $id) | .[$key]) // empty' "$SHELL_JSON" 2>/dev/null
}

# Rewrite shell.json through jq (args are passed to jq before the file).
# Writes in place so the file keeps its permissions; the shell reloads it itself.
shell_json_jq() {
    local tmp rc
    tmp=$(mktemp) || return 1
    jq "$@" "$SHELL_JSON" > "$tmp" && cat "$tmp" > "$SHELL_JSON"
    rc=$?
    rm -f "$tmp"
    return $rc
}

# Remove a widget from every bar section
shell_bar_remove() {
    shell_json_jq --arg id "$1" '.bar.layout |= with_entries(.value |= map(select(.id != $id)))'
}

# Run a shell-bar change with the standard confirm/backup/log flow.
# Usage: apply_shell_change <title> <explanation> <summary> <check-fn> <apply-fn>
# check-fn returns 0 when the change is already in place.
apply_shell_change() {
    local title="$1" explain="$2" summary="$3" check_fn="$4" apply_fn="$5"

    clear
    echo
    echo
    echo -e "${BOLD}  $title${RESET}"
    echo
    echo -e "  ${DIM}$explain${RESET}"
    echo
    echo

    if [[ ! -f "$SHELL_JSON" ]] || ! command -v jq &>/dev/null; then
        echo -e "  ${DIM}✗${RESET}  Omarchy shell config not found at $SHELL_JSON (or jq missing)"
        echo
        SUMMARY_LOG+=("✗  $summary -- failed (shell config not found)")
        return 1
    fi

    if "$check_fn"; then
        echo -e "  ${DIM}Already set. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  $summary -- already set")
        return 0
    fi

    confirm_continue "$summary" || return 0
    backup_file "$SHELL_JSON"

    if "$apply_fn"; then
        echo -e "  ${CHECKED}✓${RESET}  $summary"
        SUMMARY_LOG+=("✓  $summary")
    else
        echo -e "  ${DIM}✗${RESET}  $summary -- failed"
        SUMMARY_LOG+=("✗  $summary -- failed")
    fi
    echo
}

# Ask to continue unless running with confirm-all; returns 1 when cancelled
confirm_continue() {
    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
        if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
            echo
            echo "  Cancelled."
            echo
            SUMMARY_LOG+=("--  $1 -- cancelled")
            return 1
        fi
    fi
    echo
    return 0
}

backup_file() {
    local backup="${1}.backup.$(date +%Y%m%d_%H%M%S)"
    cp "$1" "$backup"
    echo -e "  ${DIM}Backup: $backup${RESET}"
}

# Reload Hyprland and report any config errors the change introduced
hypr_reload_check() {
    command -v hyprctl &>/dev/null && [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] || return 0
    hyprctl reload &>/dev/null
    sleep 0.5
    local errors
    errors=$(hyprctl configerrors 2>/dev/null | sed '/^[[:space:]]*$/d')
    if [[ -n "$errors" ]]; then
        echo -e "  ${BOLD}Hyprland reported config errors:${RESET}"
        echo "$errors" | sed 's/^/    /'
        echo
        return 1
    fi
    return 0
}

# Add or remove a managed Lua block with the standard confirm/backup/log flow.
# Usage: apply_lua_block <title> <explanation> <file> <block-id> <summary> [content]
# With content the block is written; without it the block is removed.
apply_lua_block() {
    local title="$1" explain="$2" file="$3" id="$4" summary="$5" content="${6:-}"

    clear
    echo
    echo
    echo -e "${BOLD}  $title${RESET}"
    echo
    echo -e "  ${DIM}$explain${RESET}"
    echo
    echo

    if [[ ! -f "$file" ]]; then
        echo -e "  ${DIM}✗${RESET}  ${file##*/} not found at $file"
        echo
        SUMMARY_LOG+=("✗  $summary -- failed (config not found)")
        return 1
    fi

    if [[ -z "$content" ]] && ! lua_block_has "$file" "$id"; then
        echo -e "  ${DIM}Not set. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  $summary -- not set")
        return 0
    fi
    if [[ -n "$content" ]] && lua_block_has "$file" "$id" && [[ "$(lua_block_get "$file" "$id")" == "$content" ]]; then
        echo -e "  ${DIM}Already set. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  $summary -- already set")
        return 0
    fi

    confirm_continue "$summary" || return 0

    backup_file "$file"
    if [[ -n "$content" ]]; then
        lua_block_write "$file" "$id" "$content"
    else
        lua_block_remove "$file" "$id"
    fi

    if hypr_reload_check; then
        echo -e "  ${CHECKED}✓${RESET}  $summary"
        SUMMARY_LOG+=("✓  $summary")
    else
        SUMMARY_LOG+=("✗  $summary -- Hyprland reported config errors")
    fi
    echo
}

# Function to check if package is installed
is_package_installed() {
    pacman -Qi "$1" &>/dev/null
}

# Function to check if webapp is installed
is_webapp_installed() {
    [[ -f "$HOME/.local/share/applications/$1.desktop" ]]
}

# Function to load all keybindings from config files for the Keybind Editor
# Applies unbind/rebind overrides from bindings.lua so entries show current state
# Split Lua bind keys ("SUPER + SHIFT + W") into BIND_MODS ("SUPER SHIFT") and BIND_KEY ("W")
split_lua_keys() {
    local keys="$1"
    BIND_KEY="${keys##* + }"
    if [[ "$keys" == *" + "* ]]; then
        BIND_MODS="${keys% + *}"
        BIND_MODS="${BIND_MODS// + / }"
    else
        BIND_MODS=""
    fi
}

# Join mods and key back into Lua bind keys
join_lua_keys() {
    if [[ -n "$1" ]]; then
        echo "${1// / + } + $2"
    else
        echo "$2"
    fi
}

# Parse a single-line top-level o.bind("KEYS", "Desc", action[, opts]) call.
# Sets LB_KEYS, LB_DESC, LB_ACTION; returns 1 if the line isn't one.
parse_lua_bind_line() {
    [[ "$1" =~ ^o\.bind\(\"([^\"]+)\",\ \"([^\"]*)\",\ (.+)\)[[:space:]]*$ ]] || return 1
    LB_KEYS="${BASH_REMATCH[1]}"
    LB_DESC="${BASH_REMATCH[2]}"
    LB_ACTION="${BASH_REMATCH[3]}"
}

load_all_bindings() {
    EDIT_BINDINGS_ITEMS=()
    BINDING_LUA_ACTIONS=()
    BINDING_ORIG_KEYS=()

    # Phase 1: Omarchy default bindings. Only single-line top-level o.bind calls
    # are editable; loop-generated binds (workspaces) can't be rebound one by one.
    local default_dir="$OMARCHY_DIR/default/hypr/bindings"
    local default_files=(tiling clipboard utilities media applications)
    local file_labels=("Tiling" "Clipboard" "Utilities" "Media" "Applications")

    for i in "${!default_files[@]}"; do
        local file="$default_dir/${default_files[$i]}.lua"
        [[ -f "$file" ]] || continue

        EDIT_BINDINGS_ITEMS+=("HEADER|${file_labels[$i]}")
        while IFS= read -r line; do
            parse_lua_bind_line "$line" || continue
            split_lua_keys "$LB_KEYS"
            BINDING_LUA_ACTIONS+=("$LB_ACTION")
            EDIT_BINDINGS_ITEMS+=("bindd|$BIND_MODS|$BIND_KEY|$LB_DESC|lua|$(( ${#BINDING_LUA_ACTIONS[@]} - 1 ))|$file")
        done < "$file"
    done

    [[ -f "$BINDINGS_LUA" ]] || return 0

    # Lookup: "MODS|KEY" -> EDIT_BINDINGS_ITEMS index
    local -A binding_lookup=()
    for idx in "${!EDIT_BINDINGS_ITEMS[@]}"; do
        local entry="${EDIT_BINDINGS_ITEMS[$idx]}"
        [[ "$entry" == HEADER* ]] && continue
        IFS='|' read -r _t lk_mods lk_key _rest <<< "$entry"
        binding_lookup["$lk_mods|$lk_key"]=$idx
    done

    # Phase 2: user bindings.lua. Our keybind-edits block holds unbind+rebind
    # pairs that move a binding; other top-level o.bind calls are user bindings.
    local -a user_entries=()
    local pending_unbind="" in_edits=false
    while IFS= read -r line; do
        if [[ "$line" == "-- >>> a-la-carchy keybind-edits" ]]; then in_edits=true; continue; fi
        if [[ "$line" == "-- <<< a-la-carchy keybind-edits" ]]; then in_edits=false; continue; fi

        if $in_edits && [[ "$line" =~ ^hl\.unbind\(\"([^\"]+)\"\) ]]; then
            pending_unbind="${BASH_REMATCH[1]}"
            continue
        fi
        parse_lua_bind_line "$line" || continue
        split_lua_keys "$LB_KEYS"

        if $in_edits && [[ -n "$pending_unbind" ]]; then
            split_lua_keys "$pending_unbind"
            local orig_idx="${binding_lookup["$BIND_MODS|$BIND_KEY"]:-}"
            split_lua_keys "$LB_KEYS"
            if [[ -n "$orig_idx" ]]; then
                IFS='|' read -r _t _m _k _d _disp orig_args orig_file <<< "${EDIT_BINDINGS_ITEMS[$orig_idx]}"
                EDIT_BINDINGS_ITEMS[$orig_idx]="bindd|$BIND_MODS|$BIND_KEY|$LB_DESC|lua|$orig_args|$orig_file"
                BINDING_ORIG_KEYS[$orig_idx]="$pending_unbind"
                pending_unbind=""
                continue
            fi
            pending_unbind=""
        fi

        # Skip binds written by other a-la-carchy features (they have their own toggles)
        $in_edits || user_entries+=("$LB_KEYS|$LB_DESC|$LB_ACTION")
    done < <(awk '
        /^-- >>> a-la-carchy / && $0 != "-- >>> a-la-carchy keybind-edits" { skip = 1; next }
        /^-- <<< a-la-carchy / && $0 != "-- <<< a-la-carchy keybind-edits" { skip = 0; next }
        !skip { print }
    ' "$BINDINGS_LUA")

    if [[ ${#user_entries[@]} -gt 0 ]]; then
        EDIT_BINDINGS_ITEMS+=("HEADER|User Bindings")
        local ue
        for ue in "${user_entries[@]}"; do
            local u_keys="${ue%%|*}" rest="${ue#*|}"
            local u_desc="${rest%%|*}" u_action="${rest#*|}"
            split_lua_keys "$u_keys"
            BINDING_LUA_ACTIONS+=("$u_action")
            EDIT_BINDINGS_ITEMS+=("bindd|$BIND_MODS|$BIND_KEY|$u_desc|lua|$(( ${#BINDING_LUA_ACTIONS[@]} - 1 ))|$BINDINGS_LUA")
        done
    fi
}

# Parse a Hyprland settings item string into global variables
parse_hypr_item() {
    IFS='|' read -r HYPR_ID HYPR_LABEL HYPR_TYPE HYPR_SECTION HYPR_KEY HYPR_DEFAULT HYPR_FILE HYPR_DESC <<< "$1"
}

# User config file for a Hyprland settings target (looknfeel|input)
hypr_settings_file() {
    echo "$HYPR_DIR/$1.lua"
}

# Format a Lua table key, quoting keys that aren't valid identifiers (e.g. tap-to-click)
hypr_lua_key() {
    if [[ "$1" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        echo "$1"
    else
        echo "[\"$1\"]"
    fi
}

# Convert a setting value to a Lua literal based on its item type
hypr_lua_value() {
    local type="$1" val="$2"
    case "$type" in
        bool)
            [[ "$val" =~ ^(true|yes|on|1)$ ]] && echo "true" || echo "false"
            ;;
        int*|float*)
            echo "$val"
            ;;
        color)
            # Gradients ("rgba(..) rgba(..) 45deg") become { colors = {...}, angle = N }
            if [[ "$val" == *" "* ]]; then
                local colors="" angle="" part
                for part in $val; do
                    if [[ "$part" =~ ^([0-9]+)deg$ ]]; then
                        angle="${BASH_REMATCH[1]}"
                    else
                        [[ -n "$colors" ]] && colors+=", "
                        colors+="\"$part\""
                    fi
                done
                if [[ -n "$angle" ]]; then
                    echo "{ colors = { $colors }, angle = $angle }"
                else
                    echo "{ colors = { $colors } }"
                fi
            else
                echo "\"$val\""
            fi
            ;;
        *)
            [[ "$val" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && echo "$val" || echo "\"$val\""
            ;;
    esac
}

# Convert a Lua literal from a managed block back to the display/config form
hypr_lua_to_value() {
    local v="$1"
    if [[ "$v" == \{* ]]; then
        local out="" rest="$v"
        while [[ "$rest" =~ \"([^\"]*)\"(.*) ]]; do
            [[ -n "$out" ]] && out+=" "
            out+="${BASH_REMATCH[1]}"
            rest="${BASH_REMATCH[2]}"
        done
        [[ "$v" =~ angle[[:space:]]*=[[:space:]]*([0-9]+) ]] && out+=" ${BASH_REMATCH[1]}deg"
        echo "$out"
    elif [[ "$v" =~ ^\"(.*)\"$ ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo "$v"
    fi
}

# Build an hl.config({...}) block from an assoc array of dotted path -> Lua literal
build_hypr_lua_block() {
    local -n _lua_vals="$1"
    local out="hl.config({"$'\n'
    local -a prev=() parts=()
    local path n common i
    while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        IFS='.' read -ra parts <<< "$path"
        n=$(( ${#parts[@]} - 1 ))

        # Close tables not shared with this path, then open the new ones
        common=0
        while (( common < ${#prev[@]} && common < n )) && [[ "${prev[$common]}" == "${parts[$common]}" ]]; do
            common=$((common + 1))
        done
        for (( i = ${#prev[@]}; i > common; i-- )); do
            out+="$(printf '%*s' $((i * 2)) '')},"$'\n'
        done
        for (( i = common; i < n; i++ )); do
            out+="$(printf '%*s' $(((i + 1) * 2)) '')$(hypr_lua_key "${parts[$i]}") = {"$'\n'
        done

        out+="$(printf '%*s' $(((n + 1) * 2)) '')$(hypr_lua_key "${parts[$n]}") = ${_lua_vals[$path]},"$'\n'
        prev=("${parts[@]:0:n}")
    done < <(printf '%s\n' "${!_lua_vals[@]}" | sort)
    for (( i = ${#prev[@]}; i > 0; i-- )); do
        out+="$(printf '%*s' $((i * 2)) '')},"$'\n'
    done
    out+="})"
    printf '%s' "$out"
}

# Load current values from managed blocks in config files
load_hypr_settings() {
    HYPR_CURRENT=()
    local marker_start="# === a-la-carchy hyprland settings ==="
    local marker_end="# === end a-la-carchy hyprland settings ==="

    # Lua keys use underscores; map them back to the item keys (tap_to_click -> tap-to-click)
    local -A lua_key_map=()
    local entry
    for entry in "${HYPR_GENERAL_ITEMS[@]}" "${HYPR_DECORATION_ITEMS[@]}" "${HYPR_INPUT_ITEMS[@]}" "${HYPR_GESTURES_ITEMS[@]}"; do
        parse_hypr_item "$entry"
        local item_key="${HYPR_SECTION}.${HYPR_KEY}"
        lua_key_map["${item_key//-/_}"]="$item_key"
    done

    local config_files=("$(hypr_settings_file looknfeel)" "$(hypr_settings_file input)")
    for conf in "${config_files[@]}"; do
        [[ -f "$conf" ]] || continue

        local is_lua=false
        [[ "$conf" == *.lua ]] && is_lua=true

        local in_block=false
        local section_stack=()
        while IFS= read -r line; do
            if [[ "$line" == "$marker_start" || "$line" == "-- ${marker_start#\# }" ]]; then
                in_block=true
                section_stack=()
                continue
            fi
            if [[ "$line" == "$marker_end" || "$line" == "-- ${marker_end#\# }" ]]; then
                in_block=false
                continue
            fi
            $in_block || continue

            # Skip empty lines and comments
            local trimmed="${line#"${line%%[![:space:]]*}"}"
            [[ -z "$trimmed" ]] && continue
            [[ "$trimmed" == \#* ]] && continue

            if $is_lua; then
                if [[ "$trimmed" =~ ^hl\.gesture\(\{\ fingers\ =\ ([0-9]+),.*action\ =\ \"workspace\" ]]; then
                    HYPR_CURRENT["gestures.workspace_swipe"]="true"
                    HYPR_CURRENT["gestures.workspace_swipe_fingers"]="${BASH_REMATCH[1]}"
                    continue
                fi
                if [[ "$trimmed" =~ ^--\ workspace_swipe\ =\ false,\ workspace_swipe_fingers\ =\ ([0-9]+)$ ]]; then
                    HYPR_CURRENT["gestures.workspace_swipe"]="false"
                    HYPR_CURRENT["gestures.workspace_swipe_fingers"]="${BASH_REMATCH[1]}"
                    continue
                fi
                [[ "$trimmed" == --* ]] && continue
                [[ "$trimmed" == "hl.config({" || "$trimmed" == "})" ]] && continue
                # Drop trailing comma and unquote ["key"] table keys
                trimmed="${trimmed%,}"
                [[ "$trimmed" =~ ^\[\"([^\"]+)\"\](.*)$ ]] && trimmed="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
            fi

            # Opening brace: push section ("name {" or Lua "name = {")
            if [[ "$trimmed" =~ ^([a-zA-Z_][a-zA-Z0-9_.-]*)[[:space:]]*=?[[:space:]]*\{$ ]]; then
                section_stack+=("${BASH_REMATCH[1]}")
                continue
            fi

            # Closing brace: pop section
            if [[ "$trimmed" == "}" ]]; then
                if [[ ${#section_stack[@]} -gt 0 ]]; then
                    unset 'section_stack[${#section_stack[@]}-1]'
                fi
                continue
            fi

            # Key = value assignment
            if [[ "$trimmed" =~ ^([a-zA-Z_][a-zA-Z0-9_.-]*)[[:space:]]*=[[:space:]]*(.+)$ ]]; then
                local k="${BASH_REMATCH[1]}"
                local v="${BASH_REMATCH[2]}"
                # Trim trailing whitespace from value
                v="${v%"${v##*[![:space:]]}"}"
                $is_lua && v="$(hypr_lua_to_value "$v")"

                # Build full section path
                local section_path=""
                for s in "${section_stack[@]}"; do
                    [[ -n "$section_path" ]] && section_path+="."
                    section_path+="$s"
                done

                local full_key="${section_path}.${k}"
                $is_lua && full_key="${lua_key_map[$full_key]:-$full_key}"
                HYPR_CURRENT["$full_key"]="$v"
            fi
        done < "$conf"
    done
}

# Check if a keybind editor item is a section header
is_binding_header() {
    [[ "${EDIT_BINDINGS_ITEMS[$1]}" == HEADER* ]]
}

# Guided keybinding edit dialog (3 steps: modifiers, key, confirm)
edit_binding() {
    local idx=$1
    local entry="${EDIT_BINDINGS_ITEMS[$idx]}"

    # Skip headers
    [[ "$entry" == HEADER* ]] && return

    # Parse the binding entry
    IFS='|' read -r _type cur_mods cur_key cur_desc cur_dispatcher cur_args cur_file <<< "$entry"

    # Check if there's a pending edit - use those values
    if [[ -n "${BINDING_EDITS[$idx]:-}" ]]; then
        IFS='|' read -r cur_mods cur_key <<< "${BINDING_EDITS[$idx]}"
    fi

    # Step 1: Modifier selection
    local mod_super=0 mod_shift=0 mod_ctrl=0 mod_alt=0
    [[ "$cur_mods" == *SUPER* ]] && mod_super=1
    [[ "$cur_mods" == *SHIFT* ]] && mod_shift=1
    [[ "$cur_mods" == *CTRL* ]] && mod_ctrl=1
    [[ "$cur_mods" == *ALT* ]] && mod_alt=1

    local mod_cursor=0
    local mod_names=("SUPER" "SHIFT" "CTRL" "ALT")
    local -a mod_vals=($mod_super $mod_shift $mod_ctrl $mod_alt)

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Edit Keybinding: $cur_desc${RESET}"
        echo -e "  ${DIM}Current: $cur_mods, $cur_key${RESET}"
        echo
        echo -e "  Select modifiers (Space to toggle, arrows to move):"
        echo
        printf "   "
        for i in 0 1 2 3; do
            local mark=" "
            [[ ${mod_vals[$i]} -eq 1 ]] && mark="x"
            if [[ $i -eq $mod_cursor ]]; then
                printf " ${SELECTED_BG}[%s] %s${RESET}" "$mark" "${mod_names[$i]}"
            else
                printf " ${C_TEXT}[%s] %s${RESET}" "$mark" "${mod_names[$i]}"
            fi
        done
        echo
        echo
        echo -e "  ${DIM}Enter: next   Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[C') (( mod_cursor < 3 )) && ((mod_cursor++)) ;;
                    '[D') (( mod_cursor > 0 )) && ((mod_cursor--)) ;;
                esac
                [[ -z "$key" ]] && return  # Bare escape = cancel
                ;;
            ' ')
                if [[ ${mod_vals[$mod_cursor]} -eq 0 ]]; then
                    mod_vals[$mod_cursor]=1
                else
                    mod_vals[$mod_cursor]=0
                fi
                ;;
            '')  # Enter
                break
                ;;
        esac
    done

    # Build modifier string
    local new_mods=""
    [[ ${mod_vals[0]} -eq 1 ]] && new_mods="SUPER"
    [[ ${mod_vals[1]} -eq 1 ]] && { [[ -n "$new_mods" ]] && new_mods+=" "; new_mods+="SHIFT"; }
    [[ ${mod_vals[2]} -eq 1 ]] && { [[ -n "$new_mods" ]] && new_mods+=" "; new_mods+="CTRL"; }
    [[ ${mod_vals[3]} -eq 1 ]] && { [[ -n "$new_mods" ]] && new_mods+=" "; new_mods+="ALT"; }

    # Step 2: Key input
    local new_key=""
    local key_error=""
    while true; do
        clear
        echo
        echo -e "  ${BOLD}Edit Keybinding: $cur_desc${RESET}"
        echo -e "  ${DIM}Modifiers: $new_mods${RESET}"
        echo
        echo -e "  Type key name (e.g. Q, RETURN, F1):"
        echo
        if [[ -n "$key_error" ]]; then
            echo -e "  ${DIM}x $key_error${RESET}"
            echo
        fi
        echo -e "  ${DIM}Common keys: A-Z  0-9  RETURN  SPACE  TAB  ESCAPE${RESET}"
        echo -e "  ${DIM}F1-F12  PRINT  DELETE  BACKSPACE  UP DOWN LEFT RIGHT${RESET}"
        echo
        printf "  > "

        stty echo 2>/dev/null
        tput cnorm
        read -r new_key < /dev/tty
        tput civis
        stty -echo 2>/dev/null

        # Trim whitespace and convert to uppercase
        new_key="${new_key## }"
        new_key="${new_key%% }"
        new_key="${new_key^^}"

        if [[ -z "$new_key" ]]; then
            key_error="Key cannot be empty"
            continue
        fi

        # Validate against known keys
        local valid=false
        for vk in "${VALID_KEYS[@]}"; do
            if [[ "$new_key" == "$vk" ]]; then
                valid=true
                break
            fi
        done

        if [[ "$valid" == false ]]; then
            key_error="Unknown key: $new_key"
            continue
        fi

        break
    done

    # Check for conflicts with other bindings
    local conflict_desc=""
    for ci in "${!EDIT_BINDINGS_ITEMS[@]}"; do
        [[ $ci -eq $idx ]] && continue
        local c_entry="${EDIT_BINDINGS_ITEMS[$ci]}"
        [[ "$c_entry" == HEADER* ]] && continue

        # Use pending edit if one exists, otherwise use stored mods/key
        local c_mods c_key c_desc
        if [[ -n "${BINDING_EDITS[$ci]:-}" ]]; then
            IFS='|' read -r c_mods c_key <<< "${BINDING_EDITS[$ci]}"
            IFS='|' read -r _t _m _k c_desc _rest <<< "$c_entry"
        else
            IFS='|' read -r _t c_mods c_key c_desc _rest <<< "$c_entry"
        fi

        if [[ "$c_mods" == "$new_mods" ]] && [[ "$c_key" == "$new_key" ]]; then
            conflict_desc="$c_desc"
            break
        fi
    done

    # Step 3: Preview and confirm
    clear
    echo
    echo -e "  ${BOLD}Edit Keybinding: $cur_desc${RESET}"
    echo
    echo -e "  New binding: ${BOLD}$new_mods, $new_key${RESET}  ->  $cur_desc"
    echo
    if [[ -n "$conflict_desc" ]]; then
        echo -e "  ${BOLD}Warning:${RESET} ${DIM}$new_mods+$new_key is already bound to:${RESET}"
        echo -e "  ${DIM}  $conflict_desc${RESET}"
        echo
    fi
    echo -e "  ${DIM}Enter: confirm   Escape: cancel${RESET}"

    IFS= read -rsn1 key < /dev/tty
    case "$key" in
        '')  # Enter - confirm
            BINDING_EDITS[$idx]="$new_mods|$new_key"
            ;;
    esac
}

# Guided Hyprland setting edit dialog
# Dispatches based on type_info: bool, int, float, enum, color
edit_hypr_setting() {
    local item="$1"
    parse_hypr_item "$item"

    local id="$HYPR_ID"
    local label="$HYPR_LABEL"
    local type_info="$HYPR_TYPE"
    local section="$HYPR_SECTION"
    local config_key="$HYPR_KEY"
    local default="$HYPR_DEFAULT"

    # Get current display value: pending > saved > default
    local cur_val="$default"
    local full_key="${section}.${config_key}"
    [[ -n "${HYPR_CURRENT[$full_key]:-}" ]] && cur_val="${HYPR_CURRENT[$full_key]}"
    [[ -n "${HYPR_EDITS[$id]:-}" ]] && cur_val="${HYPR_EDITS[$id]}"

    # Bool: immediate toggle, no dialog
    if [[ "$type_info" == "bool" ]]; then
        if [[ "$cur_val" == "true" ]]; then
            HYPR_EDITS[$id]="false"
        else
            HYPR_EDITS[$id]="true"
        fi
        return
    fi

    # Int/Float: text input with range validation
    if [[ "$type_info" =~ ^(int|float):(.+):(.+)$ ]]; then
        local val_type="${BASH_REMATCH[1]}"
        local range_min="${BASH_REMATCH[2]}"
        local range_max="${BASH_REMATCH[3]}"
        local input_error=""

        while true; do
            clear
            echo
            echo -e "  ${BOLD}Edit: $label${RESET}"
            echo -e "  ${DIM}Current value: $cur_val${RESET}"
            echo -e "  ${DIM}Range: $range_min - $range_max${RESET}"
            echo
            if [[ -n "$input_error" ]]; then
                echo -e "  ${DIM}x $input_error${RESET}"
                echo
            fi
            printf "  Enter new value: "

            stty echo 2>/dev/null
            tput cnorm
            read -r new_val < /dev/tty
            tput civis
            stty -echo 2>/dev/null

            new_val="${new_val## }"
            new_val="${new_val%% }"

            if [[ -z "$new_val" ]]; then
                return  # Cancel on empty input
            fi

            if [[ "$val_type" == "int" ]]; then
                if ! [[ "$new_val" =~ ^-?[0-9]+$ ]]; then
                    input_error="Must be an integer"
                    continue
                fi
                if (( new_val < range_min || new_val > range_max )); then
                    input_error="Out of range ($range_min - $range_max)"
                    continue
                fi
            else
                # float validation
                if ! [[ "$new_val" =~ ^-?[0-9]*\.?[0-9]+$ ]]; then
                    input_error="Must be a number"
                    continue
                fi
                # Use awk for float comparison
                if ! awk "BEGIN { exit !(($new_val) >= ($range_min) && ($new_val) <= ($range_max)) }"; then
                    input_error="Out of range ($range_min - $range_max)"
                    continue
                fi
            fi

            HYPR_EDITS[$id]="$new_val"
            return
        done
    fi

    # Enum: arrow-key selection dialog
    if [[ "$type_info" =~ ^enum: ]]; then
        local opts_str="${type_info#enum:}"
        IFS=':' read -ra opts <<< "$opts_str"
        local opt_cursor=0

        # Find current value in options
        for i in "${!opts[@]}"; do
            if [[ "${opts[$i]}" == "$cur_val" ]]; then
                opt_cursor=$i
                break
            fi
        done

        while true; do
            clear
            echo
            echo -e "  ${BOLD}Edit: $label${RESET}"
            echo -e "  ${DIM}Current: $cur_val${RESET}"
            echo
            echo -e "  ${DIM}Select option (Up/Down, Enter to confirm):${RESET}"
            echo

            for i in "${!opts[@]}"; do
                if [[ $i -eq $opt_cursor ]]; then
                    echo -e "    ${SELECTED_BG}> ${opts[$i]}${RESET}"
                else
                    echo -e "      ${C_TEXT}${opts[$i]}${RESET}"
                fi
            done

            echo
            echo -e "  ${DIM}Escape: cancel${RESET}"

            IFS= read -rsn1 key < /dev/tty
            case "$key" in
                $'\x1b')
                    read -rsn2 -t 0.1 key
                    case "$key" in
                        '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                        '[B') (( opt_cursor < ${#opts[@]} - 1 )) && ((opt_cursor++)) ;;
                    esac
                    [[ -z "$key" ]] && return  # Bare escape = cancel
                    ;;
                '')  # Enter - confirm
                    HYPR_EDITS[$id]="${opts[$opt_cursor]}"
                    return
                    ;;
            esac
        done
    fi

    # Color: text input dialog
    if [[ "$type_info" == "color" ]]; then
        local input_error=""

        while true; do
            clear
            echo
            echo -e "  ${BOLD}Edit: $label${RESET}"
            echo -e "  ${DIM}Current: $cur_val${RESET}"
            echo
            if [[ -n "$input_error" ]]; then
                echo -e "  ${DIM}x $input_error${RESET}"
                echo
            fi
            printf "  Enter color value: "

            stty echo 2>/dev/null
            tput cnorm
            read -r new_val < /dev/tty
            tput civis
            stty -echo 2>/dev/null

            new_val="${new_val## }"
            new_val="${new_val%% }"

            if [[ -z "$new_val" ]]; then
                return  # Cancel on empty input
            fi

            # Basic validation: must contain rgba( or rgb( or be a hex color
            if [[ "$new_val" =~ rgba?\( ]] || [[ "$new_val" =~ ^0x[0-9a-fA-F]+$ ]]; then
                HYPR_EDITS[$id]="$new_val"
                return
            else
                input_error="Supports: rgba(RRGGBBAA), rgb(RRGGBB), gradients"
                continue
            fi
        done
    fi
}

# Apply all pending Hyprland setting edits to config files
apply_hypr_edits() {
    if [[ ${#HYPR_EDITS[@]} -eq 0 ]]; then
        return
    fi

    clear
    echo
    echo
    echo -e "${BOLD}  Apply Hyprland Settings${RESET}"
    echo
    echo -e "  ${DIM}Changes to apply:${RESET}"
    echo

    # Show summary of changes
    local all_items=("${HYPR_GENERAL_ITEMS[@]}" "${HYPR_DECORATION_ITEMS[@]}" "${HYPR_INPUT_ITEMS[@]}" "${HYPR_GESTURES_ITEMS[@]}")
    for entry in "${all_items[@]}"; do
        parse_hypr_item "$entry"
        [[ -z "${HYPR_EDITS[$HYPR_ID]:-}" ]] && continue
        local new_val="${HYPR_EDITS[$HYPR_ID]}"
        local old_val="$HYPR_DEFAULT"
        local fk="${HYPR_SECTION}.${HYPR_KEY}"
        [[ -n "${HYPR_CURRENT[$fk]:-}" ]] && old_val="${HYPR_CURRENT[$fk]}"
        echo -e "    ${DIM}•${RESET}  $HYPR_LABEL: $old_val -> $new_val"
    done
    echo
    echo

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Hyprland settings -- cancelled")
        return 0
    fi

    echo

    local marker_start marker_end

    # Group edits by config file, then by section_path
    for target_file in "looknfeel" "input"; do
        local conf
        conf="$(hypr_settings_file "$target_file")"

        # Collect this file's settings: saved values first, pending edits on top,
        # since the managed block is rewritten as a whole on every apply
        local -A file_edits=()
        local -A lua_vals=()
        local -A edited_keys=()
        local has_edits=false
        for entry in "${all_items[@]}"; do
            parse_hypr_item "$entry"
            [[ "$HYPR_FILE" != "$target_file" ]] && continue
            local fk="${HYPR_SECTION}.${HYPR_KEY}"
            local val="${HYPR_CURRENT[$fk]:-}"
            if [[ -n "${HYPR_EDITS[$HYPR_ID]:-}" ]]; then
                val="${HYPR_EDITS[$HYPR_ID]}"
                edited_keys["$fk"]=1
                has_edits=true
            fi
            [[ -z "$val" ]] && continue
            file_edits["$fk"]="$val"
            # Lua config keys use underscores (tap-to-click -> tap_to_click)
            lua_vals["${fk//-/_}"]="$(hypr_lua_value "$HYPR_TYPE" "$val")"
        done
        $has_edits || continue

        if [[ ! -f "$conf" ]]; then
            echo -e "  ${DIM}✗${RESET}  ${conf##*/} not found at $conf"
            SUMMARY_LOG+=("✗  Hyprland: ${conf##*/} -- failed (config not found)")
            continue
        fi

        # Backup
        local backup_file="${conf}.backup.$(date +%Y%m%d_%H%M%S)"
        cp "$conf" "$backup_file"
        echo -e "  ${DIM}Backup: $backup_file${RESET}"

        local block=""
        marker_start="-- === a-la-carchy hyprland settings ==="
        marker_end="-- === end a-la-carchy hyprland settings ==="
        block+="$marker_start"$'\n'
        # Workspace swipe is a gesture binding in Lua configs, not a gestures option
        local swipe="${lua_vals[gestures.workspace_swipe]:-}"
        local swipe_fingers="${lua_vals[gestures.workspace_swipe_fingers]:-3}"
        unset 'lua_vals[gestures.workspace_swipe]' 'lua_vals[gestures.workspace_swipe_fingers]'
        [[ ${#lua_vals[@]} -gt 0 ]] && block+="$(build_hypr_lua_block lua_vals)"$'\n'
        if [[ "$swipe" == "true" ]]; then
            block+="hl.gesture({ fingers = ${swipe_fingers}, direction = \"horizontal\", action = \"workspace\" })"$'\n'
        elif [[ "$swipe" == "false" ]]; then
            block+="-- workspace_swipe = false, workspace_swipe_fingers = ${swipe_fingers}"$'\n'
        fi

        block+="$marker_end"

        # Remove existing managed block if present, then append new one
        if grep -qF -- "$marker_start" "$conf"; then
            # Use awk to remove the block
            awk -v start="$marker_start" -v end="$marker_end" '
                $0 == start { skip=1; next }
                $0 == end { skip=0; next }
                !skip { print }
            ' "$conf" > "${conf}.tmp"
            mv "${conf}.tmp" "$conf"
        fi

        # Append the new managed block
        echo "" >> "$conf"
        echo "$block" >> "$conf"

        # Log each changed setting
        for full_key in "${!edited_keys[@]}"; do
            local key="${full_key##*.}"
            local val="${file_edits[$full_key]}"
            echo -e "    ${CHECKED}✓${RESET}  ${full_key} = ${val}"
            SUMMARY_LOG+=("✓  Hyprland: ${full_key} = ${val}")
        done
    done

    echo
    if hypr_reload_check; then
        echo -e "  ${DIM}Hyprland reloaded the config.${RESET}"
    else
        SUMMARY_LOG+=("✗  Hyprland settings -- Hyprland reported config errors")
    fi
    echo
}

# Apply all pending keybinding edits to bindings.lua
apply_binding_edits() {
    if [[ ${#BINDING_EDITS[@]} -eq 0 ]]; then
        return
    fi

    clear
    echo
    echo
    echo -e "${BOLD}  Apply Keybinding Edits${RESET}"
    echo
    echo -e "  ${DIM}Changes to apply:${RESET}"
    echo

    for idx in "${!BINDING_EDITS[@]}"; do
        local entry="${EDIT_BINDINGS_ITEMS[$idx]}"
        IFS='|' read -r _type orig_mods orig_key desc dispatcher args source <<< "$entry"
        IFS='|' read -r new_mods new_key <<< "${BINDING_EDITS[$idx]}"
        echo -e "    ${DIM}•${RESET}  $desc: $orig_mods+$orig_key -> $new_mods+$new_key"
    done
    echo
    echo

    confirm_continue "Keybinding edits" || return 0

    if [[ ! -f "$BINDINGS_LUA" ]]; then
        echo -e "  ${DIM}✗${RESET}  bindings.lua not found at $BINDINGS_LUA"
        SUMMARY_LOG+=("✗  Keybinding edits -- failed (config not found)")
        return 1
    fi

    backup_file "$BINDINGS_LUA"
    echo

    # Rebuild the keybind-edits block from every moved binding: the key it
    # originally had (unbound) and the key it has now (rebound with its action)
    local block="" idx
    for idx in "${!EDIT_BINDINGS_ITEMS[@]}"; do
        local entry="${EDIT_BINDINGS_ITEMS[$idx]}"
        [[ "$entry" == HEADER* ]] && continue
        IFS='|' read -r _type cur_mods cur_key desc dispatcher action_idx source <<< "$entry"

        local cur_keys orig_keys new_keys
        cur_keys="$(join_lua_keys "$cur_mods" "$cur_key")"
        orig_keys="${BINDING_ORIG_KEYS[$idx]:-$cur_keys}"
        new_keys="$cur_keys"
        if [[ -n "${BINDING_EDITS[$idx]:-}" ]]; then
            IFS='|' read -r new_mods new_key <<< "${BINDING_EDITS[$idx]}"
            new_keys="$(join_lua_keys "$new_mods" "$new_key")"
        fi
        [[ "$new_keys" == "$orig_keys" ]] && continue

        block+="hl.unbind(\"$orig_keys\")"$'\n'
        block+="o.bind(\"$new_keys\", \"$desc\", ${BINDING_LUA_ACTIONS[$action_idx]})"$'\n'
    done

    if [[ -n "$block" ]]; then
        lua_block_write "$BINDINGS_LUA" "keybind-edits" "${block%$'\n'}"
    else
        lua_block_remove "$BINDINGS_LUA" "keybind-edits"
    fi

    local ok=true
    hypr_reload_check || ok=false
    for idx in "${!BINDING_EDITS[@]}"; do
        local entry="${EDIT_BINDINGS_ITEMS[$idx]}"
        IFS='|' read -r _type orig_mods orig_key desc _rest <<< "$entry"
        IFS='|' read -r new_mods new_key <<< "${BINDING_EDITS[$idx]}"
        if $ok; then
            echo -e "    ${CHECKED}✓${RESET}  $desc: $orig_mods+$orig_key -> $new_mods+$new_key"
            SUMMARY_LOG+=("✓  Rebound $desc: $new_mods+$new_key")
        else
            SUMMARY_LOG+=("✗  Rebind $desc -- Hyprland reported config errors")
        fi
    done
    echo
}

# Function to rebind close window from SUPER+W to SUPER+Q
rebind_close_window() {
    apply_lua_block "Rebind Close Window" \
        "Changes SUPER+W (Omarchy default) to SUPER+Q for closing windows." \
        "$BINDINGS_LUA" "close-window" "Rebind close window to SUPER+Q" \
        'hl.unbind("SUPER + W")
o.bind("SUPER + Q", "Close window", hl.dsp.window.close())'
}

# Function to restore close window to SUPER+W (Omarchy default)
restore_close_window() {
    apply_lua_block "Restore Close Window" \
        "Restores SUPER+W (Omarchy default) for closing windows." \
        "$BINDINGS_LUA" "close-window" "Restore close window to SUPER+W"
}

# Function to backup config directories
backup_configs() {
    clear
    echo
    echo
    echo -e "${BOLD}  Backup Config${RESET}"
    echo
    echo -e "  ${DIM}Creates an archive of your Omarchy config directories and a restore script.${RESET}"
    echo
    echo

    local timestamp=$(date +%Y%m%d_%H%M%S)
    local archive="$HOME/omarchy-backup-${timestamp}.tar.gz"
    local restore_script="$HOME/restore-omarchy-config.sh"

    # Directories to back up (relative to ~/.config)
    local config_dirs=(
        "hypr"
        "omarchy"
        "uwsm"
        "fastfetch"
        "alacritty"
        "kitty"
        "ghostty"
        "foot"
    )

    # Check which dirs exist
    local existing_dirs=()
    for d in "${config_dirs[@]}"; do
        if [[ -d "$HOME/.config/$d" ]]; then
            existing_dirs+=(".config/$d")
        fi
    done

    if [[ ${#existing_dirs[@]} -eq 0 ]]; then
        echo -e "  ${DIM}✗${RESET}  No config directories found to back up."
        echo
        SUMMARY_LOG+=("✗  Backup config -- failed (no config dirs found)")
        return 1
    fi

    echo -e "  ${DIM}Directories to back up:${RESET}"
    for d in "${existing_dirs[@]}"; do
        echo -e "    ${DIM}•${RESET}  ~/$d"
    done
    echo
    echo

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Backup config -- cancelled")
        return 0
    fi

    echo

    # Create archive (follow symlinks with -h)
    if tar -czhf "$archive" -C "$HOME" "${existing_dirs[@]}" 2>/dev/null; then
        echo -e "    ${CHECKED}✓${RESET}  Archive created: $archive"
    else
        echo -e "    ${DIM}✗${RESET}  Failed to create archive."
        echo
        SUMMARY_LOG+=("✗  Backup config -- failed (archive creation error)")
        return 1
    fi

    # Generate restore script
    cat > "$restore_script" << 'RESTORE_EOF'
#!/bin/bash
# Omarchy Config Restore Script

# Find all backup archives
mapfile -t BACKUPS < <(ls -1t ~/omarchy-backup-*.tar.gz 2>/dev/null)

if [[ ${#BACKUPS[@]} -eq 0 ]]; then
    echo "No backup archives found in ~/"
    exit 1
fi

echo "Available backups:"
echo
for i in "${!BACKUPS[@]}"; do
    # Extract timestamp from filename for display
    fname=$(basename "${BACKUPS[$i]}")
    ts="${fname#omarchy-backup-}"
    ts="${ts%.tar.gz}"
    date_part="${ts:0:4}-${ts:4:2}-${ts:6:2}"
    time_part="${ts:9:2}:${ts:11:2}:${ts:13:2}"
    size=$(du -h "${BACKUPS[$i]}" | cut -f1)
    echo "  $((i + 1))) $date_part $time_part  ($size)"
done

echo
printf "Select a backup to restore (1-%d): " "${#BACKUPS[@]}"
read -r choice

if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#BACKUPS[@]} )); then
    echo "Invalid selection."
    exit 1
fi

ARCHIVE="${BACKUPS[$((choice - 1))]}"

echo
echo "This will restore Omarchy config files from:"
echo "  $ARCHIVE"
echo
echo "Existing config files will be overwritten."
echo
printf "Continue? (yes/no) "
read -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo
if tar -xzhf "$ARCHIVE" -C "$HOME"; then
    echo "Config restored successfully."
    echo "Reload Hyprland or log out/in to apply changes."
else
    echo "Failed to restore config."
    exit 1
fi
RESTORE_EOF

    chmod +x "$restore_script"

    echo -e "    ${CHECKED}✓${RESET}  Restore script: $restore_script"
    echo
    echo -e "  ${DIM}To restore later, run: bash ~/restore-omarchy-config.sh${RESET}"
    echo
    echo
    SUMMARY_LOG+=("✓  Backup config created")
}

# Function to set monitor scaling to 4K
# Set Omarchy's global scale variables at the top of monitors.lua. Per-monitor
# hl.monitor rules keep their own scale, so explicit layouts are left alone.
set_monitor_scale() {
    local label="$1" gdk_scale="$2" monitor_scale="$3"

    clear
    echo
    echo
    echo -e "${BOLD}  Set Monitor Scaling: $label${RESET}"
    echo
    echo -e "  ${DIM}Sets GDK_SCALE=$gdk_scale and the default monitor scale to $monitor_scale.${RESET}"
    echo -e "  ${DIM}Monitors with their own rule in monitors.lua keep their scale.${RESET}"
    echo
    echo

    if [[ ! -f "$MONITORS_LUA" ]] || ! grep -q "^local omarchy_gdk_scale = " "$MONITORS_LUA"; then
        echo -e "  ${DIM}✗${RESET}  Omarchy scale settings not found in $MONITORS_LUA"
        echo -e "  ${DIM}    Run 'omarchy refresh config hypr/monitors.lua' to restore them.${RESET}"
        echo
        SUMMARY_LOG+=("✗  Monitor scaling $label -- failed (config not found)")
        return 1
    fi

    confirm_continue "Monitor scaling $label" || return 0

    backup_file "$MONITORS_LUA"
    sed -i \
        -e "s/^local omarchy_gdk_scale = .*/local omarchy_gdk_scale = $gdk_scale/" \
        -e "s/^local omarchy_monitor_scale = .*/local omarchy_monitor_scale = $monitor_scale/" \
        "$MONITORS_LUA"

    if hypr_reload_check; then
        echo -e "    ${CHECKED}✓${RESET}  Monitor scaling set to $label"
        SUMMARY_LOG+=("✓  Monitor scaling set to $label")
    else
        SUMMARY_LOG+=("✗  Monitor scaling $label -- Hyprland reported config errors")
    fi
    echo
    echo -e "  ${DIM}GDK_SCALE applies to apps started after the next login.${RESET}"
    echo
}

set_monitor_4k() {
    set_monitor_scale "4K" "1.75" "1.666667"
}

# Function to set monitor scaling to 1080p/1440p
set_monitor_1080_1440() {
    set_monitor_scale "1080p/1440p" "1" "1"
}

# Detect connected monitors via hyprctl
detect_monitors() {
    DETECTED_MONITORS=()
    MONITOR_COUNT=0
    LAPTOP_MONITOR=""

    command -v hyprctl &>/dev/null || return 1
    command -v jq &>/dev/null || return 1

    local json
    json=$(hyprctl monitors -j 2>/dev/null) || return 1

    # Entry: name|make|model|width|height|x|y|scale|desc|transform|mode|vrr|bitdepth
    # mode/vrr/bitdepth are carried over so layout changes keep refresh rate,
    # VRR and 10-bit color exactly as they are now.
    local entry
    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        DETECTED_MONITORS+=("$entry")
        MONITOR_COUNT=$((MONITOR_COUNT + 1))
        [[ "${entry%%|*}" == eDP-* ]] && LAPTOP_MONITOR="${entry%%|*}"
    done < <(jq -r '.[] | [
        .name, (.make // ""), (.model // ""), .width, .height, .x, .y, .scale,
        .description, .transform,
        "\(.width)x\(.height)@\(.refreshRate * 100 | round / 100)",
        (if .vrr then 1 else 0 end),
        (if (.currentFormat // "" | test("2101010")) then 10 else 8 end)
    ] | map(tostring | gsub("\\|"; "/")) | join("|")' <<< "$json")

    return 0
}

# Show monitor detection dialog
show_monitor_detection_dialog() {
    clear
    echo
    echo
    echo -e "${BOLD}  Detect Monitors${RESET}"
    echo

    if ! detect_monitors; then
        echo -e "  ${DIM}✗${RESET}  hyprctl not available. Is Hyprland running?"
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    if [ $MONITOR_COUNT -eq 0 ]; then
        echo -e "  ${DIM}No monitors detected.${RESET}"
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    echo -e "  ${DIM}Found $MONITOR_COUNT monitor(s):${RESET}"
    echo

    local i=0
    local -a transform_labels=("Normal" "90°" "180°" "270°" "Flipped" "Flipped 90°" "Flipped 180°" "Flipped 270°")
    for entry in "${DETECTED_MONITORS[@]}"; do
        ((i++))
        IFS='|' read -r m_name m_make m_model m_w m_h m_x m_y m_scale m_desc m_transform _extra <<< "$entry"
        local label=""
        [[ "$m_name" == eDP-* ]] && label=" (laptop)"
        local rot_label="${transform_labels[${m_transform:-0}]}"
        echo -e "    ${BOLD}$i.${RESET}  $m_name$label"
        echo -e "       ${DIM}Resolution: ${m_w}x${m_h}  Scale: ${m_scale}  Position: ${m_x},${m_y}  Rotation: ${rot_label}${RESET}"
        if [[ -n "$m_desc" ]]; then
            echo -e "       ${DIM}$m_desc${RESET}"
        fi
        echo
    done

    if [ $MONITOR_COUNT -ge 2 ]; then
        echo -e "  ${DIM}Press ${RESET}${BOLD}I${RESET}${DIM} to identify monitors (flash name on each screen)${RESET}"
    fi
    echo -e "  ${DIM}Press any other key to return...${RESET}"

    read -rsn1 key < /dev/tty
    if [[ "$key" == "i" || "$key" == "I" ]] && [ $MONITOR_COUNT -ge 2 ]; then
        identify_monitors
    fi
}

# Flash identification on each monitor
identify_monitors() {
    echo
    echo -e "  ${DIM}Identifying monitors...${RESET}"
    local i=0
    for entry in "${DETECTED_MONITORS[@]}"; do
        ((i++))
        IFS='|' read -r m_name _rest <<< "$entry"
        hyprctl dispatch "hl.dsp.focus({ monitor = \"$m_name\" })" &>/dev/null
        hyprctl notify 1 2000 "rgb(33ccff)" "fontsize:28 Monitor $i: $m_name" &>/dev/null
        sleep 2
    done
    echo -e "  ${DIM}Done. Press any key to return...${RESET}"
    read -rsn1 < /dev/tty
}

# Show position editor dialog
show_position_editor_dialog() {
    # Auto-detect if not already done
    if [ $MONITOR_COUNT -eq 0 ]; then
        if ! detect_monitors; then
            clear
            echo
            echo
            echo -e "${BOLD}  Position Monitors${RESET}"
            echo
            echo -e "  ${DIM}✗${RESET}  hyprctl not available. Is Hyprland running?"
            echo
            echo -e "  ${DIM}Press any key to return...${RESET}"
            read -rsn1 < /dev/tty
            return
        fi
    fi

    if [ $MONITOR_COUNT -le 1 ]; then
        clear
        echo
        echo
        echo -e "${BOLD}  Position Monitors${RESET}"
        echo
        echo -e "  ${DIM}Only one monitor detected. Nothing to position.${RESET}"
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    # Build arrays of monitor names, widths, heights, scales, transforms
    local -a mon_names=() mon_widths=() mon_heights=() mon_scales=() mon_transforms=()
    for entry in "${DETECTED_MONITORS[@]}"; do
        IFS='|' read -r m_name m_make m_model m_w m_h m_x m_y m_scale m_desc m_transform _extra <<< "$entry"
        mon_names+=("$m_name")
        mon_widths+=("$m_w")
        mon_heights+=("$m_h")
        mon_scales+=("$m_scale")
        mon_transforms+=("${m_transform:-0}")
    done

    # Rotation labels and values
    local -a rot_labels=("Normal (landscape)" "90° (portrait right)" "180° (inverted)" "270° (portrait left)")
    local -a rot_values=(0 1 2 3)

    # Helper: select rotation for a monitor, returns via rot_result
    select_rotation() {
        local mon_idx=$1
        local step_label="$2"
        local rot_cursor=0
        # Default to current transform if it's a simple rotation (0-3)
        local cur_t="${mon_transforms[$mon_idx]}"
        (( cur_t >= 0 && cur_t <= 3 )) && rot_cursor=$cur_t

        while true; do
            clear
            echo
            echo
            echo -e "${BOLD}  Position Monitors - ${step_label}${RESET}"
            echo
            local rlabel=""
            [[ "${mon_names[$mon_idx]}" == eDP-* ]] && rlabel=" (laptop)"
            echo -e "  ${DIM}Rotation for: ${RESET}${BOLD}${mon_names[$mon_idx]}${rlabel}${RESET}  ${DIM}${mon_widths[$mon_idx]}x${mon_heights[$mon_idx]}${RESET}"
            echo
            echo -e "  ${DIM}Select orientation:${RESET}"
            echo

            for ((r=0; r<4; r++)); do
                if [ $r -eq $rot_cursor ]; then
                    echo -e "    ${SELECTED_BG} > ${rot_labels[$r]} ${RESET}"
                else
                    echo -e "       ${C_TEXT}${rot_labels[$r]}${RESET}"
                fi
            done

            echo
            echo -e "  ${DIM}Up/Down: Select  Enter: Confirm  Esc: Cancel${RESET}"

            IFS= read -rsn1 key < /dev/tty
            case "$key" in
                $'\x1b')
                    read -rsn2 -t 0.1 key
                    case "$key" in
                        '[A') ((rot_cursor > 0)) && ((rot_cursor--)) ;;
                        '[B') ((rot_cursor < 3)) && ((rot_cursor++)) ;;
                    esac
                    [[ -z "$key" ]] && return 1  # Bare escape = cancel
                    ;;
                '')
                    rot_result=${rot_values[$rot_cursor]}
                    return 0
                    ;;
                'q'|'Q')
                    return 1
                    ;;
            esac
        done
    }

    # Step 1: Select primary monitor
    local primary_idx=0
    local cursor=0
    while true; do
        clear
        echo
        echo
        echo -e "${BOLD}  Position Monitors - Step 1/3${RESET}"
        echo
        echo -e "  ${DIM}Select your primary monitor (placed at origin 0,0):${RESET}"
        echo

        for ((i=0; i<MONITOR_COUNT; i++)); do
            local label=""
            [[ "${mon_names[$i]}" == eDP-* ]] && label=" (laptop)"
            if [ $i -eq $cursor ]; then
                echo -e "    ${SELECTED_BG} > ${mon_names[$i]}${label}  ${mon_widths[$i]}x${mon_heights[$i]} ${RESET}"
            else
                echo -e "       ${C_TEXT}${mon_names[$i]}${label}${RESET}  ${DIM}${mon_widths[$i]}x${mon_heights[$i]}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Up/Down: Select  Enter: Confirm  Esc: Cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') ((cursor > 0)) && ((cursor--)) ;;
                    '[B') ((cursor < MONITOR_COUNT - 1)) && ((cursor++)) ;;
                esac
                [[ -z "$key" ]] && return  # Bare escape = cancel
                ;;
            '')  # Enter
                primary_idx=$cursor
                break
                ;;
            'q'|'Q')
                return
                ;;
        esac
    done

    # Step 1b: Select rotation for primary monitor
    local rot_result=0
    select_rotation $primary_idx "Step 1/3" || return
    mon_transforms[$primary_idx]=$rot_result

    # Track placed monitors: index -> "x,y"
    local -A placed_positions=()
    local -a placed_order=()
    placed_positions[$primary_idx]="0,0"
    placed_order+=("$primary_idx")

    # Step 2: Position each remaining monitor
    local -a remaining=()
    for ((i=0; i<MONITOR_COUNT; i++)); do
        [ $i -ne $primary_idx ] && remaining+=("$i")
    done

    for rem_idx in "${remaining[@]}"; do
        # Select reference monitor (which placed monitor to position relative to)
        local ref_cursor=0
        local ref_idx=${placed_order[0]}
        while true; do
            clear
            echo
            echo
            echo -e "${BOLD}  Position Monitors - Step 2/3${RESET}"
            echo
            local rem_label=""
            [[ "${mon_names[$rem_idx]}" == eDP-* ]] && rem_label=" (laptop)"
            echo -e "  ${DIM}Placing: ${RESET}${BOLD}${mon_names[$rem_idx]}${rem_label}${RESET}  ${DIM}${mon_widths[$rem_idx]}x${mon_heights[$rem_idx]}${RESET}"
            echo
            echo -e "  ${DIM}Position relative to which monitor?${RESET}"
            echo

            for ((p=0; p<${#placed_order[@]}; p++)); do
                local pi=${placed_order[$p]}
                local plabel=""
                [[ "${mon_names[$pi]}" == eDP-* ]] && plabel=" (laptop)"
                local ppos="${placed_positions[$pi]}"
                if [ $p -eq $ref_cursor ]; then
                    echo -e "    ${SELECTED_BG} > ${mon_names[$pi]}${plabel}  at ${ppos} ${RESET}"
                else
                    echo -e "       ${C_TEXT}${mon_names[$pi]}${plabel}${RESET}  ${DIM}at ${ppos}${RESET}"
                fi
            done

            echo
            echo -e "  ${DIM}Up/Down: Select  Enter: Confirm  Esc: Cancel${RESET}"

            IFS= read -rsn1 key < /dev/tty
            case "$key" in
                $'\x1b')
                    read -rsn2 -t 0.1 key
                    case "$key" in
                        '[A') ((ref_cursor > 0)) && ((ref_cursor--)) ;;
                        '[B') ((ref_cursor < ${#placed_order[@]} - 1)) && ((ref_cursor++)) ;;
                    esac
                    [[ -z "$key" ]] && return  # Bare escape = cancel
                    ;;
                '')
                    ref_idx=${placed_order[$ref_cursor]}
                    break
                    ;;
                'q'|'Q')
                    return
                    ;;
            esac
        done

        # Select direction
        local -a directions=("Right of" "Left of" "Above" "Below")
        local dir_cursor=0
        while true; do
            clear
            echo
            echo
            echo -e "${BOLD}  Position Monitors - Step 2/3${RESET}"
            echo
            local rem_label=""
            [[ "${mon_names[$rem_idx]}" == eDP-* ]] && rem_label=" (laptop)"
            echo -e "  ${DIM}Placing: ${RESET}${BOLD}${mon_names[$rem_idx]}${rem_label}${RESET}"
            echo -e "  ${DIM}Relative to: ${RESET}${mon_names[$ref_idx]}"
            echo
            echo -e "  ${DIM}Which direction?${RESET}"
            echo

            for ((d=0; d<4; d++)); do
                if [ $d -eq $dir_cursor ]; then
                    echo -e "    ${SELECTED_BG} > ${directions[$d]} ${RESET}"
                else
                    echo -e "       ${C_TEXT}${directions[$d]}${RESET}"
                fi
            done

            echo
            echo -e "  ${DIM}Up/Down: Select  Enter: Confirm  Esc: Cancel${RESET}"

            IFS= read -rsn1 key < /dev/tty
            case "$key" in
                $'\x1b')
                    read -rsn2 -t 0.1 key
                    case "$key" in
                        '[A') ((dir_cursor > 0)) && ((dir_cursor--)) ;;
                        '[B') ((dir_cursor < 3)) && ((dir_cursor++)) ;;
                    esac
                    [[ -z "$key" ]] && return  # Bare escape = cancel
                    ;;
                '')
                    break
                    ;;
                'q'|'Q')
                    return
                    ;;
            esac
        done

        # Select rotation for this monitor
        select_rotation $rem_idx "Step 2/3" || return
        mon_transforms[$rem_idx]=$rot_result

        # Calculate position using Hyprland's snapped scale and rounded dimensions
        # Hyprland snaps scale to nearest 1/120: round(scale*120)/120
        # then computes logical size as round(resolution / snapped_scale)
        # For 90°/270° rotation (transform 1,3), swap width and height
        local ref_pos="${placed_positions[$ref_idx]}"
        local ref_x="${ref_pos%,*}"
        local ref_y="${ref_pos#*,}"
        local ref_scale="${mon_scales[$ref_idx]}"
        local rem_scale="${mon_scales[$rem_idx]}"
        local ref_t="${mon_transforms[$ref_idx]}"
        local rem_t="${mon_transforms[$rem_idx]}"

        # Get raw logical dimensions (before rotation)
        local ref_lw ref_lh rem_lw rem_lh
        ref_lw=$(awk "BEGIN { s=int($ref_scale*120+0.5)/120; printf \"%d\", int(${mon_widths[$ref_idx]}/s+0.5) }")
        ref_lh=$(awk "BEGIN { s=int($ref_scale*120+0.5)/120; printf \"%d\", int(${mon_heights[$ref_idx]}/s+0.5) }")
        rem_lw=$(awk "BEGIN { s=int($rem_scale*120+0.5)/120; printf \"%d\", int(${mon_widths[$rem_idx]}/s+0.5) }")
        rem_lh=$(awk "BEGIN { s=int($rem_scale*120+0.5)/120; printf \"%d\", int(${mon_heights[$rem_idx]}/s+0.5) }")

        # Apply rotation: 90° and 270° swap width/height
        local ref_ew=$ref_lw ref_eh=$ref_lh rem_ew=$rem_lw rem_eh=$rem_lh
        if (( ref_t == 1 || ref_t == 3 )); then
            ref_ew=$ref_lh
            ref_eh=$ref_lw
        fi
        if (( rem_t == 1 || rem_t == 3 )); then
            rem_ew=$rem_lh
            rem_eh=$rem_lw
        fi

        local new_x=0 new_y=0
        case $dir_cursor in
            0) # Right of
                new_x=$((ref_x + ref_ew))
                new_y=$ref_y
                ;;
            1) # Left of
                new_x=$((ref_x - rem_ew))
                new_y=$ref_y
                ;;
            2) # Above
                new_x=$ref_x
                new_y=$((ref_y - rem_eh))
                ;;
            3) # Below
                new_x=$ref_x
                new_y=$((ref_y + ref_eh))
                ;;
        esac

        placed_positions[$rem_idx]="${new_x},${new_y}"
        placed_order+=("$rem_idx")
    done

    # Step 3: Preview
    local -a preview_rot_labels=("Normal" "90°" "180°" "270°")
    clear
    echo
    echo
    echo -e "${BOLD}  Position Monitors - Step 3/3 Preview${RESET}"
    echo
    echo -e "  ${DIM}Monitor layout:${RESET}"
    echo

    for ((i=0; i<MONITOR_COUNT; i++)); do
        local pos="${placed_positions[$i]}"
        local px="${pos%,*}"
        local py="${pos#*,}"
        local label=""
        [[ "${mon_names[$i]}" == eDP-* ]] && label=" (laptop)"
        local primary_tag=""
        [ $i -eq $primary_idx ] && primary_tag=" [primary]"
        local t="${mon_transforms[$i]}"
        local rot_info="${preview_rot_labels[$t]}"
        echo -e "    ${BOLD}${mon_names[$i]}${RESET}${label}${primary_tag}"
        echo -e "      ${DIM}Resolution: ${mon_widths[$i]}x${mon_heights[$i]}  Scale: ${mon_scales[$i]}  Rotation: ${rot_info}${RESET}"
        echo -e "      ${DIM}Position: ${px}x${py}${RESET}"
        echo
    done

    echo -e "  ${DIM}Note: Displays may briefly go black while Hyprland reconfigures.${RESET}"
    echo
    printf "  ${BOLD}Apply this layout?${RESET} ${DIM}(yes/no)${RESET} "
    read -r < /dev/tty

    if [[ $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        # Populate global state
        MONITOR_POSITIONS=()
        MONITOR_TRANSFORMS=()
        for ((i=0; i<MONITOR_COUNT; i++)); do
            MONITOR_POSITIONS["${mon_names[$i]}"]="${placed_positions[$i]}"
            MONITOR_TRANSFORMS["${mon_names[$i]}"]="${mon_transforms[$i]}"
        done
        MONITORS_POSITIONED=1
        echo
        echo -e "  ${DIM}Layout queued. Will be applied on confirm.${RESET}"
    else
        echo
        echo -e "  ${DIM}Cancelled.${RESET}"
    fi
    echo
    echo -e "  ${DIM}Press any key to return...${RESET}"
    read -rsn1 < /dev/tty
}

# Apply monitor positions to monitors.lua
apply_monitor_positions() {
    clear
    echo
    echo
    echo -e "${BOLD}  Apply Monitor Positions${RESET}"
    echo

    if [[ ! -f "$MONITORS_LUA" ]]; then
        echo -e "  ${DIM}✗${RESET}  monitors.lua not found at $MONITORS_LUA"
        echo
        SUMMARY_LOG+=("✗  Monitor positions -- failed (config not found)")
        return 1
    fi

    echo
    backup_file "$MONITORS_LUA"

    # One rule per monitor, matched by description so it survives port changes.
    # Mode, VRR and bit depth come from the live state, so the layout block never
    # resets refresh rate, VRR or 10-bit color.
    local block="" entry
    for entry in "${DETECTED_MONITORS[@]}"; do
        IFS='|' read -r m_name _mk _mo _w _h _x _y m_scale m_desc _t m_mode m_vrr m_bitdepth <<< "$entry"
        local pos="${MONITOR_POSITIONS[$m_name]:-auto}"
        local transform="${MONITOR_TRANSFORMS[$m_name]:-0}"
        [[ "$pos" != "auto" ]] && pos="${pos%,*}x${pos#*,}"

        local output="$m_name"
        [[ -n "$m_desc" ]] && output="desc:${m_desc//\"/\\\"}"

        local rule="hl.monitor({ output = \"$output\", mode = \"${m_mode:-preferred}\", position = \"$pos\", scale = $m_scale, transform = $transform"
        [[ -n "$m_vrr" ]] && rule+=", vrr = $m_vrr"
        [[ "$m_bitdepth" == "10" ]] && rule+=", bitdepth = 10"
        rule+=" })"
        block+="$rule"$'\n'
    done
    block="${block%$'\n'}"

    lua_block_write "$MONITORS_LUA" "monitor-layout" "$block"
    echo -e "    ${CHECKED}✓${RESET}  Layout saved to monitors.lua"

    if hypr_reload_check; then
        echo -e "    ${CHECKED}✓${RESET}  Monitor layout applied"
        SUMMARY_LOG+=("✓  Monitor layout configured")
    else
        SUMMARY_LOG+=("✗  Monitor layout -- Hyprland reported config errors")
    fi
    echo
    echo
}

# Enable laptop auto-off (disable laptop screen when external connected)
setup_laptop_auto_off() {
    clear
    echo
    echo
    echo -e "${BOLD}  Laptop Display Auto-Off${RESET}"
    echo

    # Detect laptop monitor if not already found
    if [[ -z "$LAPTOP_MONITOR" ]]; then
        detect_monitors
    fi

    if [[ -z "$LAPTOP_MONITOR" ]]; then
        echo -e "  ${DIM}✗${RESET}  No laptop display (eDP-*) detected."
        echo
        SUMMARY_LOG+=("✗  Laptop auto-off -- no laptop display found")
        return 1
    fi

    echo -e "  ${DIM}Laptop display: $LAPTOP_MONITOR${RESET}"
    echo -e "  ${DIM}Will auto-disable when external display is connected.${RESET}"
    echo

    # Create scripts directory
    mkdir -p "$(dirname "$LAPTOP_AUTO_SCRIPT")"

    # Detect the backlight device for this laptop
    local backlight_dev=""
    for bl in /sys/class/backlight/*/; do
        [[ -d "$bl" ]] || continue
        local bl_name="${bl%/}"
        bl_name="${bl_name##*/}"
        backlight_dev="$bl_name"
        # Prefer intel_backlight over others
        [[ "$bl_name" == *intel* ]] && break
    done

    if [[ -z "$backlight_dev" ]]; then
        echo -e "  ${DIM}✗${RESET}  No backlight device found in /sys/class/backlight/"
        echo
        SUMMARY_LOG+=("✗  Laptop auto-off -- no backlight device found")
        return 1
    fi

    echo -e "  ${DIM}Backlight device: $backlight_dev${RESET}"

    # Get current brightness to use as default restore value
    local current_brightness
    current_brightness=$(brightnessctl -d "$backlight_dev" g 2>/dev/null)
    [[ -z "$current_brightness" || "$current_brightness" == "0" ]] && current_brightness=9600

    # Write watcher script
    cat > "$LAPTOP_AUTO_SCRIPT" << SCRIPTEOF
#!/bin/bash
# Managed by A La Carchy - auto-disable laptop screen on external display
LAPTOP="$LAPTOP_MONITOR"
BACKLIGHT="$backlight_dev"
DEFAULT_BRIGHTNESS=$current_brightness
SAVED_BRIGHTNESS=""

handle_change() {
    sleep 1  # debounce rapid events

    # Check actual monitor state (not a flag) to handle external config reloads
    local external_count laptop_active
    external_count=\$(hyprctl monitors | grep "^Monitor " | grep -cv "^Monitor eDP")
    laptop_active=\$(hyprctl monitors | grep -c "^Monitor eDP")

    if (( external_count >= 1 )); then
        # Skip if laptop is already off (avoids cascade from monitorremoved event)
        (( laptop_active == 0 )) && return

        # External display connected - disable laptop and turn off backlight
        if [[ -z "\$SAVED_BRIGHTNESS" ]]; then
            SAVED_BRIGHTNESS=\$(brightnessctl -d "\$BACKLIGHT" g 2>/dev/null)
            [[ -z "\$SAVED_BRIGHTNESS" || "\$SAVED_BRIGHTNESS" == "0" ]] && SAVED_BRIGHTNESS=\$DEFAULT_BRIGHTNESS
        fi
        hyprctl eval "hl.monitor({ output = \"\$LAPTOP\", disabled = true })" &>/dev/null
        brightnessctl -d "\$BACKLIGHT" s 0 &>/dev/null
        # Move workspace 1 to the remaining external monitor
        local ext_mon
        ext_mon=\$(hyprctl monitors -j | grep -oP '"name":\s*"\K[^"]+' | grep -v "^eDP" | head -1)
        if [[ -n "\$ext_mon" ]]; then
            hyprctl dispatch "hl.dsp.workspace.move({ workspace = \"1\", monitor = \"\$ext_mon\" })" &>/dev/null
        fi
    else
        # Skip if laptop is already the only display
        (( laptop_active >= 1 )) && return

        # No external display - restore laptop
        brightnessctl -d "\$BACKLIGHT" s "\${SAVED_BRIGHTNESS:-\$DEFAULT_BRIGHTNESS}" &>/dev/null
        hyprctl eval "hl.monitor({ output = \"\$LAPTOP\", mode = \"preferred\", position = \"auto\", scale = 1 })" &>/dev/null
    fi
}

# Handle initial state
handle_change

# Watch for monitor events via Hyprland IPC socket
SOCKET="\$XDG_RUNTIME_DIR/hypr/\$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock"
if command -v socat &>/dev/null; then
    socat -U - UNIX-CONNECT:"\$SOCKET" 2>/dev/null
elif command -v nc &>/dev/null; then
    nc -U "\$SOCKET" 2>/dev/null
else
    # Fallback: poll every 5 seconds
    while true; do
        sleep 5
        handle_change
    done &
    wait
    exit 0
fi | while read -r line; do
    case "\$line" in
        monitoradded*|monitorremoved*|configreloaded*) handle_change ;;
    esac
done
SCRIPTEOF

    chmod +x "$LAPTOP_AUTO_SCRIPT"
    echo -e "    ${CHECKED}✓${RESET}  Watcher script created: $LAPTOP_AUTO_SCRIPT"

    # Start the watcher with Hyprland
    lua_block_write "$AUTOSTART_LUA" "laptop-display" "$(lua_on_start "$LAPTOP_AUTO_SCRIPT")"
    echo -e "    ${CHECKED}✓${RESET}  Added startup entry to autostart.lua"

    # Start the script now (startup entries only run when Hyprland starts)
    pkill -f "laptop-display-auto.sh" 2>/dev/null
    nohup "$LAPTOP_AUTO_SCRIPT" &>/dev/null &
    echo -e "    ${CHECKED}✓${RESET}  Watcher started"

    SUMMARY_LOG+=("✓  Laptop display auto-off enabled")
    echo
    echo
}

# Disable laptop auto-off
remove_laptop_auto_off() {
    clear
    echo
    echo
    echo -e "${BOLD}  Disable Laptop Display Auto-Off${RESET}"
    echo

    if lua_block_has "$AUTOSTART_LUA" "laptop-display"; then
        lua_block_remove "$AUTOSTART_LUA" "laptop-display"
        echo -e "    ${CHECKED}✓${RESET}  Removed startup entry from autostart.lua"
    fi

    # Remove script
    if [[ -f "$LAPTOP_AUTO_SCRIPT" ]]; then
        rm -f "$LAPTOP_AUTO_SCRIPT"
        echo -e "    ${CHECKED}✓${RESET}  Removed watcher script"
    fi

    # Kill any running instance
    pkill -f "laptop-display-auto.sh" 2>/dev/null

    # Restore backlight (find the backlight device)
    for bl in /sys/class/backlight/*/; do
        [[ -d "$bl" ]] || continue
        local bl_name="${bl%/}"
        bl_name="${bl_name##*/}"
        brightnessctl -d "$bl_name" s 9600 &>/dev/null
        [[ "$bl_name" == *intel* ]] && break
    done

    # Re-enable laptop monitor if we know its name
    if [[ -z "$LAPTOP_MONITOR" ]]; then
        detect_monitors
    fi
    if [[ -n "$LAPTOP_MONITOR" ]]; then
        hyprctl eval "hl.monitor({ output = \"$LAPTOP_MONITOR\", mode = \"preferred\", position = \"auto\", scale = 1 })" &>/dev/null
        echo -e "    ${CHECKED}✓${RESET}  Re-enabled $LAPTOP_MONITOR"
    fi

    SUMMARY_LOG+=("✓  Laptop display auto-off disabled")
    echo
    echo
}

# Show primary monitor selection dialog
show_primary_monitor_dialog() {
    # Auto-detect monitors if not already done
    if [ $MONITOR_COUNT -eq 0 ]; then
        detect_monitors 2>/dev/null
    fi

    if [ $MONITOR_COUNT -eq 0 ]; then
        clear
        echo
        echo
        echo -e "${BOLD}  Primary Monitor${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  No monitors detected."
        echo -e "  ${DIM}Is Hyprland running?${RESET}"
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    # Check if a primary monitor is already configured in monitors.lua
    local configured_primary="" configured_selector=""
    configured_selector=$(lua_block_get "$MONITORS_LUA" "primary-monitor" | \
        grep -oP 'workspace = "1", monitor = "\K[^"]+')
    if [[ -n "$configured_selector" ]]; then
        local pm_entry
        for pm_entry in "${DETECTED_MONITORS[@]}"; do
            IFS='|' read -r pm_name _ _ _ _ _ _ _ pm_desc _ <<< "$pm_entry"
            if [[ "$configured_selector" == "$pm_name" || "$configured_selector" == "desc:$pm_desc" ]]; then
                configured_primary="$pm_name"
            fi
        done
    fi

    local opt_cursor=0

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Primary Monitor${RESET}"
        echo -e "  ${DIM}Set which monitor gets workspace 1 by default${RESET}"
        echo
        echo -e "  ${DIM}Select monitor (Up/Down, Enter to confirm):${RESET}"
        echo

        for i in "${!DETECTED_MONITORS[@]}"; do
            IFS='|' read -r m_name m_make m_model m_width m_height _ _ _ _ _ <<< "${DETECTED_MONITORS[$i]}"
            local label="$m_name"
            [[ -n "$m_make" ]] && label+=" - $m_make"
            [[ -n "$m_model" ]] && label+=" $m_model"
            label+=" (${m_width}x${m_height})"
            [[ "$m_name" == eDP-* ]] && label+=" [laptop]"
            local suffix=""
            [[ -n "$configured_primary" && "$m_name" == "$configured_primary" ]] && suffix=" (current)"
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${label}${suffix}${RESET}"
            else
                echo -e "      ${C_TEXT}${label}${suffix}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#DETECTED_MONITORS[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return  # Bare escape = cancel
                ;;
            ''|q|Q)  # Enter or Q
                if [[ -z "$key" ]]; then
                    # Enter - confirm selection
                    IFS='|' read -r sel_name _ <<< "${DETECTED_MONITORS[$opt_cursor]}"
                    SELECTED_PRIMARY_MONITOR="$sel_name"
                    clear
                    echo
                    echo -e "  ${BOLD}Primary Monitor${RESET}"
                    echo
                    echo -e "  ${CHECKED}✓${RESET}  $sel_name queued for apply"
                    echo
                    echo -e "  ${DIM}Press any key to return...${RESET}"
                    read -rsn1 < /dev/tty
                    return
                else
                    return  # Q = cancel
                fi
                ;;
        esac
    done
}

# Apply the selected primary monitor workspace rule
apply_primary_monitor() {
    clear
    echo
    echo
    echo -e "${BOLD}  Set Primary Monitor: $SELECTED_PRIMARY_MONITOR${RESET}"
    echo

    # Ensure monitors are detected for workspace assignment
    if [ $MONITOR_COUNT -eq 0 ]; then
        detect_monitors 2>/dev/null
    fi

    if [[ ! -f "$MONITORS_LUA" ]]; then
        echo -e "    ${DIM}✗${RESET}  monitors.lua not found at $MONITORS_LUA"
        SUMMARY_LOG+=("✗  Primary monitor -- failed (config not found)")
        return 1
    fi

    # Workspace 1 goes to the primary monitor, 2.. to the others in order.
    # Rules match by description so they follow the monitor across ports.
    local block="" ws=1 entry
    local -a order=()
    for entry in "${DETECTED_MONITORS[@]}"; do
        IFS='|' read -r m_name _ <<< "$entry"
        [[ "$m_name" == "$SELECTED_PRIMARY_MONITOR" ]] && order=("$entry" "${order[@]}") || order+=("$entry")
    done
    for entry in "${order[@]}"; do
        IFS='|' read -r m_name _ _ _ _ _ _ _ m_desc _ <<< "$entry"
        local selector="$m_name"
        [[ -n "$m_desc" ]] && selector="desc:${m_desc//\"/\\\"}"
        block+="hl.workspace_rule({ workspace = \"$ws\", monitor = \"$selector\", default = true })"$'\n'

        # Apply live
        if hyprctl dispatch "hl.dsp.workspace.move({ workspace = \"$ws\", monitor = \"$m_name\" })" &>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Workspace $ws moved to $m_name"
        fi
        ws=$((ws + 1))
    done
    hyprctl dispatch 'hl.dsp.focus({ workspace = "1" })' &>/dev/null

    backup_file "$MONITORS_LUA"
    lua_block_write "$MONITORS_LUA" "primary-monitor" "${block%$'\n'}"
    echo -e "    ${CHECKED}✓${RESET}  Workspace rules written to monitors.lua"

    if hypr_reload_check; then
        SUMMARY_LOG+=("✓  Primary monitor set to $SELECTED_PRIMARY_MONITOR")
    else
        SUMMARY_LOG+=("✗  Primary monitor -- Hyprland reported config errors")
    fi
    echo
    echo
}

# Show power profile selection dialog
show_power_profile_dialog() {
    if ! command -v powerprofilesctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  Power Profile${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  powerprofilesctl not found."
        echo -e "  ${DIM}Install power-profiles-daemon to use this feature.${RESET}"
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    # Get current active profile
    local active_profile
    active_profile=$(powerprofilesctl get 2>/dev/null)

    # Check if a default is already configured
    local configured_default=""
    if [[ -f "$POWER_PROFILE_SCRIPT" ]]; then
        configured_default=$(grep -oP 'powerprofilesctl set \K\S+' "$POWER_PROFILE_SCRIPT" 2>/dev/null)
    fi

    local -a profiles=("power-saver" "balanced" "performance")
    local -a labels=("Power saver" "Balanced" "Performance")
    local opt_cursor=1  # Default to balanced

    # Set cursor to current active profile
    for i in "${!profiles[@]}"; do
        if [[ "${profiles[$i]}" == "$active_profile" ]]; then
            opt_cursor=$i
            break
        fi
    done

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Power Profile${RESET}"
        echo -e "  ${DIM}Set default power profile restored on startup${RESET}"
        echo
        echo -e "  ${DIM}Select profile (Up/Down, Enter to confirm):${RESET}"
        echo

        for i in "${!profiles[@]}"; do
            local suffix=""
            [[ "${profiles[$i]}" == "$active_profile" ]] && suffix=" (active)"
            [[ "${profiles[$i]}" == "$configured_default" ]] && suffix="${suffix} (default)"
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${labels[$i]}${suffix}${RESET}"
            else
                echo -e "      ${C_TEXT}${labels[$i]}${suffix}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#profiles[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return  # Bare escape = cancel
                ;;
            ''|q|Q)  # Enter or Q
                if [[ -z "$key" ]]; then
                    # Enter - confirm selection
                    SELECTED_POWER_PROFILE="${profiles[$opt_cursor]}"
                    clear
                    echo
                    echo -e "  ${BOLD}Power Profile${RESET}"
                    echo
                    echo -e "  ${CHECKED}✓${RESET}  ${labels[$opt_cursor]} queued for apply"
                    echo
                    echo -e "  ${DIM}Press any key to return...${RESET}"
                    read -rsn1 < /dev/tty
                    return
                else
                    return  # Q = cancel
                fi
                ;;
        esac
    done
}

# Apply the selected power profile and configure startup persistence
apply_power_profile() {
    clear
    echo
    echo
    echo -e "${BOLD}  Set Power Profile: $SELECTED_POWER_PROFILE${RESET}"
    echo

    # Set profile immediately
    if powerprofilesctl set "$SELECTED_POWER_PROFILE" 2>/dev/null; then
        echo -e "    ${CHECKED}✓${RESET}  Profile set to $SELECTED_POWER_PROFILE"
    else
        echo -e "    ${DIM}✗${RESET}  Failed to set profile"
        SUMMARY_LOG+=("✗  Power profile -- failed to set")
        return 1
    fi

    # Create scripts directory
    mkdir -p "$(dirname "$POWER_PROFILE_SCRIPT")"

    # Write startup script
    cat > "$POWER_PROFILE_SCRIPT" << SCRIPTEOF
#!/bin/bash
# Managed by A La Carchy - default power profile on startup
powerprofilesctl set $SELECTED_POWER_PROFILE
SCRIPTEOF

    chmod +x "$POWER_PROFILE_SCRIPT"
    echo -e "    ${CHECKED}✓${RESET}  Startup script created: $POWER_PROFILE_SCRIPT"

    lua_block_write "$AUTOSTART_LUA" "power-profile" "$(lua_on_start "$POWER_PROFILE_SCRIPT")"
    echo -e "    ${CHECKED}✓${RESET}  Added startup entry to autostart.lua"

    # Sync asusd platform profile if asusd is present (prevents it overriding powerprofilesctl)
    local asusd_config="/etc/asusd/asusd.ron"
    if [[ -f "$asusd_config" ]]; then
        local asus_profile=""
        case "$SELECTED_POWER_PROFILE" in
            power-saver)  asus_profile="Quiet" ;;
            balanced)     asus_profile="Balanced" ;;
            performance)  asus_profile="Performance" ;;
        esac

        if [[ -n "$asus_profile" ]]; then
            if sudo sed -i \
                -e "s/^\([[:space:]]*\)platform_profile_on_ac: [^,]*/\1platform_profile_on_ac: $asus_profile/" \
                -e "s/^\([[:space:]]*\)platform_profile_on_battery: [^,]*/\1platform_profile_on_battery: $asus_profile/" \
                "$asusd_config" 2>/dev/null; then
                sudo systemctl restart asusd 2>/dev/null
                echo -e "    ${CHECKED}✓${RESET}  Updated asusd to enforce $asus_profile profile"
            else
                echo -e "    ${DIM}✗${RESET}  Could not update asusd config (sudo required)"
                SUMMARY_LOG+=("✗  Power profile -- failed to update asusd config")
            fi
        fi
    fi

    SUMMARY_LOG+=("✓  Power profile set to $SELECTED_POWER_PROFILE")
    echo
    echo
}

# =============================================================================
# POWER PROFILE AUTO-SWITCH
# =============================================================================

# Helper: show a profile picker sub-dialog; stores result in PICK_RESULT
_pick_auto_switch_profile() {
    local title="$1"
    local current="$2"
    local -a profiles=("performance" "balanced" "power-saver")
    local -a labels=("Performance" "Balanced" "Power saver")
    local picker_cursor=0
    PICK_RESULT=""

    for i in "${!profiles[@]}"; do
        [[ "${profiles[$i]}" == "$current" ]] && picker_cursor=$i
    done

    while true; do
        clear
        echo
        echo -e "  ${BOLD}${title}${RESET}"
        echo -e "  ${DIM}Select profile (Up/Down, Enter to confirm):${RESET}"
        echo

        for i in "${!profiles[@]}"; do
            if [[ $i -eq $picker_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${labels[$i]}${RESET}"
            else
                echo -e "      ${C_TEXT}${labels[$i]}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( picker_cursor > 0 )) && ((picker_cursor--)) ;;
                    '[B') (( picker_cursor < ${#profiles[@]} - 1 )) && ((picker_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            '')
                PICK_RESULT="${profiles[$picker_cursor]}"
                return
                ;;
        esac
    done
}

show_power_auto_switch_dialog() {
    if ! command -v powerprofilesctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  Power Profile Auto-Switch${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  powerprofilesctl not found."
        echo -e "  ${DIM}Install power-profiles-daemon to use this feature.${RESET}"
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    # Read current configured state from our script if it exists
    local cur_ac="performance"
    local cur_bat="balanced"
    local cur_enabled=0  # 0=enabled, 1=disabled

    if [[ -f "$POWER_AUTO_SWITCH_SCRIPT" ]]; then
        local parsed_ac parsed_bat
        parsed_ac=$(grep -oP 'PROFILE_AC="\K[^"]+' "$POWER_AUTO_SWITCH_SCRIPT" 2>/dev/null)
        parsed_bat=$(grep -oP 'PROFILE_BATTERY="\K[^"]+' "$POWER_AUTO_SWITCH_SCRIPT" 2>/dev/null)
        [[ -n "$parsed_ac" ]] && cur_ac="$parsed_ac"
        [[ -n "$parsed_bat" ]] && cur_bat="$parsed_bat"
    fi

    # Disabled if udev rule is missing or marked disabled
    if [[ ! -f "$POWER_AUTO_SWITCH_UDEV_RULE" ]]; then
        cur_enabled=1
    elif grep -q "auto-switch: disabled" "$POWER_AUTO_SWITCH_UDEV_RULE" 2>/dev/null; then
        cur_enabled=1
    fi

    # Pre-populate with any selections already made this session
    [[ -n "$SELECTED_POWER_AUTO_SWITCH" ]] && { [[ "$SELECTED_POWER_AUTO_SWITCH" == "disabled" ]] && cur_enabled=1 || cur_enabled=0; }
    [[ -n "$SELECTED_POWER_AC_PROFILE" ]] && cur_ac="$SELECTED_POWER_AC_PROFILE"
    [[ -n "$SELECTED_POWER_BATTERY_PROFILE" ]] && cur_bat="$SELECTED_POWER_BATTERY_PROFILE"

    local form_enabled=$cur_enabled
    local form_ac="$cur_ac"
    local form_bat="$cur_bat"
    local form_cursor=0  # 0=enabled toggle, 1=ac profile, 2=battery profile, 3=confirm

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Power Profile Auto-Switch${RESET}"
        echo -e "  ${DIM}Configure profiles used when AC power is connected or disconnected${RESET}"
        echo
        echo -e "  ${DIM}Up/Down: navigate  Enter: change  Esc: cancel${RESET}"
        echo

        local enabled_label
        [[ $form_enabled -eq 0 ]] && enabled_label="${CHECKED}Enabled${RESET}" || enabled_label="${DIM}Disabled${RESET}"
        if [[ $form_cursor -eq 0 ]]; then
            echo -e "    ${SELECTED_BG}> Auto-switching   ${enabled_label}${RESET}"
        else
            printf "      %-20s" "Auto-switching"
            [[ $form_enabled -eq 0 ]] && echo -e "${CHECKED}Enabled${RESET}" || echo -e "${DIM}Disabled${RESET}"
        fi

        local ac_label bat_label
        case "$form_ac" in
            performance) ac_label="Performance" ;;
            balanced)    ac_label="Balanced" ;;
            power-saver) ac_label="Power saver" ;;
        esac
        case "$form_bat" in
            performance) bat_label="Performance" ;;
            balanced)    bat_label="Balanced" ;;
            power-saver) bat_label="Power saver" ;;
        esac

        if [[ $form_cursor -eq 1 ]]; then
            echo -e "    ${SELECTED_BG}> On AC power      ${ac_label}${RESET}"
        else
            [[ $form_enabled -eq 1 ]] && echo -e "      ${DIM}On AC power      ${ac_label}${RESET}" || echo -e "      ${C_TEXT}On AC power      ${ac_label}${RESET}"
        fi

        if [[ $form_cursor -eq 2 ]]; then
            echo -e "    ${SELECTED_BG}> On battery       ${bat_label}${RESET}"
        else
            [[ $form_enabled -eq 1 ]] && echo -e "      ${DIM}On battery       ${bat_label}${RESET}" || echo -e "      ${C_TEXT}On battery       ${bat_label}${RESET}"
        fi

        echo
        if [[ $form_cursor -eq 3 ]]; then
            echo -e "    ${SELECTED_BG}> Confirm${RESET}"
        else
            echo -e "      ${C_TEXT}Confirm${RESET}"
        fi

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( form_cursor > 0 )) && ((form_cursor--)) ;;
                    '[B') (( form_cursor < 3 )) && ((form_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            '')
                case $form_cursor in
                    0) (( form_enabled ^= 1 )) ;;
                    1)
                        if [[ $form_enabled -eq 0 ]]; then
                            _pick_auto_switch_profile "On AC power" "$form_ac"
                            [[ -n "$PICK_RESULT" ]] && form_ac="$PICK_RESULT"
                        fi
                        ;;
                    2)
                        if [[ $form_enabled -eq 0 ]]; then
                            _pick_auto_switch_profile "On battery" "$form_bat"
                            [[ -n "$PICK_RESULT" ]] && form_bat="$PICK_RESULT"
                        fi
                        ;;
                    3)
                        if [[ $form_enabled -eq 1 ]]; then
                            SELECTED_POWER_AUTO_SWITCH="disabled"
                            SELECTED_POWER_AC_PROFILE=""
                            SELECTED_POWER_BATTERY_PROFILE=""
                            # Auto-switching off — let the user pick a static startup profile
                            show_power_profile_dialog
                        else
                            SELECTED_POWER_AUTO_SWITCH="enabled"
                            SELECTED_POWER_AC_PROFILE="$form_ac"
                            SELECTED_POWER_BATTERY_PROFILE="$form_bat"
                            clear
                            echo
                            echo -e "  ${BOLD}Power Profile Auto-Switch${RESET}"
                            echo
                            echo -e "  ${CHECKED}✓${RESET}  Auto-switching enabled — queued for apply"
                            echo -e "  ${DIM}     AC power: ${ac_label}${RESET}"
                            echo -e "  ${DIM}     Battery:  ${bat_label}${RESET}"
                            echo
                            echo -e "  ${DIM}Press any key to return...${RESET}"
                            read -rsn1 < /dev/tty
                        fi
                        return
                        ;;
                esac
                ;;
        esac
    done
}

apply_power_auto_switch() {
    clear
    echo
    echo
    echo -e "${BOLD}  Power Profile Auto-Switch${RESET}"
    echo

    if [[ "$SELECTED_POWER_AUTO_SWITCH" == "disabled" ]]; then
        if [[ -f "$POWER_AUTO_SWITCH_UDEV_RULE" ]]; then
            if sudo rm -f "$POWER_AUTO_SWITCH_UDEV_RULE" 2>/dev/null; then
                sudo udevadm control --reload-rules 2>/dev/null
                echo -e "    ${CHECKED}✓${RESET}  Udev rule removed — auto-switching disabled"
            else
                echo -e "    ${DIM}✗${RESET}  Failed to remove udev rule"
                SUMMARY_LOG+=("✗  Power auto-switch -- failed to disable")
                return 1
            fi
        else
            echo -e "    ${CHECKED}✓${RESET}  Auto-switching already disabled"
        fi
        SUMMARY_LOG+=("✓  Power auto-switch disabled")
    else
        # Create the auto-switch script
        mkdir -p "$(dirname "$POWER_AUTO_SWITCH_SCRIPT")"
        {
            echo '#!/bin/bash'
            echo "# Managed by A La Carchy - power profile auto-switch"
            echo "PROFILE_AC=\"$SELECTED_POWER_AC_PROFILE\""
            echo "PROFILE_BATTERY=\"$SELECTED_POWER_BATTERY_PROFILE\""
            echo ""
            echo 'case "$1" in'
            echo '    ac)      /usr/bin/powerprofilesctl set "$PROFILE_AC" ;;'
            echo '    battery) /usr/bin/powerprofilesctl set "$PROFILE_BATTERY" ;;'
            echo 'esac'
        } > "$POWER_AUTO_SWITCH_SCRIPT"
        chmod +x "$POWER_AUTO_SWITCH_SCRIPT"
        echo -e "    ${CHECKED}✓${RESET}  Auto-switch script created: $POWER_AUTO_SWITCH_SCRIPT"

        # Write udev rule pointing to our script
        if {
            echo "# Managed by A La Carchy - power profile auto-switch"
            echo "ACTION==\"change\", SUBSYSTEM==\"power_supply\", KERNEL==\"AC*\", ATTR{online}==\"1\", RUN+=\"${POWER_AUTO_SWITCH_SCRIPT} ac\""
            echo "ACTION==\"change\", SUBSYSTEM==\"power_supply\", KERNEL==\"AC*\", ATTR{online}==\"0\", RUN+=\"${POWER_AUTO_SWITCH_SCRIPT} battery\""
        } | sudo tee "$POWER_AUTO_SWITCH_UDEV_RULE" > /dev/null 2>&1; then
            sudo udevadm control --reload-rules 2>/dev/null
            echo -e "    ${CHECKED}✓${RESET}  Udev rule installed: $POWER_AUTO_SWITCH_UDEV_RULE"
        else
            echo -e "    ${DIM}✗${RESET}  Failed to write udev rule (sudo required)"
            SUMMARY_LOG+=("✗  Power auto-switch -- failed to write udev rule")
            return 1
        fi

        # Sync asusd platform profiles if asusd is present (prevents it overriding powerprofilesctl)
        local asusd_config="/etc/asusd/asusd.ron"
        if [[ -f "$asusd_config" ]]; then
            local asus_ac="" asus_bat=""
            case "$SELECTED_POWER_AC_PROFILE" in
                power-saver)  asus_ac="Quiet" ;;
                balanced)     asus_ac="Balanced" ;;
                performance)  asus_ac="Performance" ;;
            esac
            case "$SELECTED_POWER_BATTERY_PROFILE" in
                power-saver)  asus_bat="Quiet" ;;
                balanced)     asus_bat="Balanced" ;;
                performance)  asus_bat="Performance" ;;
            esac

            if [[ -n "$asus_ac" && -n "$asus_bat" ]]; then
                if sudo sed -i \
                    -e "s/^\([[:space:]]*\)platform_profile_on_ac: [^,]*/\1platform_profile_on_ac: $asus_ac/" \
                    -e "s/^\([[:space:]]*\)platform_profile_on_battery: [^,]*/\1platform_profile_on_battery: $asus_bat/" \
                    "$asusd_config" 2>/dev/null; then
                    sudo systemctl restart asusd 2>/dev/null
                    echo -e "    ${CHECKED}✓${RESET}  Updated asusd: AC=$asus_ac, battery=$asus_bat"
                else
                    echo -e "    ${DIM}✗${RESET}  Could not update asusd config (sudo required)"
                    SUMMARY_LOG+=("✗  Power auto-switch -- failed to update asusd config")
                fi
            fi
        fi

        SUMMARY_LOG+=("✓  Power auto-switch: AC=$SELECTED_POWER_AC_PROFILE, battery=$SELECTED_POWER_BATTERY_PROFILE")
    fi
    echo
    echo
}

show_battery_limit_dialog() {
    # Auto-detect battery device with charge limit support
    local bat_threshold=""
    for path in /sys/class/power_supply/BAT*/charge_control_end_threshold; do
        [[ -f "$path" ]] && { bat_threshold="$path"; break; }
    done

    if [[ -z "$bat_threshold" ]]; then
        clear
        echo
        echo
        echo -e "${BOLD}  Battery Charge Limit${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  No battery with charge limit support detected."
        echo -e "  ${DIM}Your hardware may not expose charge_control_end_threshold.${RESET}"
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    # Read current threshold from sysfs
    local current_threshold
    current_threshold=$(cat "$bat_threshold" 2>/dev/null)

    # Check if udev rule already exists (configured default)
    local configured_default=""
    if [[ -f "$BATTERY_LIMIT_UDEV_RULE" ]]; then
        configured_default=$(grep -oP 'charge_control_end_threshold="\K[0-9]+' "$BATTERY_LIMIT_UDEV_RULE" 2>/dev/null)
    fi

    local -a values=("60" "70" "80" "90" "100")
    local -a labels=(
        "60%  — Maximum longevity"
        "70%  — Good balance"
        "80%  — Recommended"
        "90%  — Slight protection"
        "100% — No limit (full charge)"
    )
    local opt_cursor=2  # Default to 80% (index 2)

    # Set cursor to current threshold
    for i in "${!values[@]}"; do
        if [[ "${values[$i]}" == "$current_threshold" ]]; then
            opt_cursor=$i
            break
        fi
    done

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Battery Charge Limit${RESET}"
        echo -e "  ${DIM}Set maximum charge level to extend battery lifespan${RESET}"
        echo
        echo -e "  ${DIM}Select limit (Up/Down, Enter to confirm):${RESET}"
        echo

        for i in "${!values[@]}"; do
            local suffix=""
            [[ "${values[$i]}" == "$current_threshold" ]] && suffix=" (current)"
            [[ "${values[$i]}" == "$configured_default" ]] && suffix="${suffix} (default)"
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${labels[$i]}${suffix}${RESET}"
            else
                echo -e "      ${C_TEXT}${labels[$i]}${suffix}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#values[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return  # Bare escape = cancel
                ;;
            ''|q|Q)  # Enter or Q
                if [[ -z "$key" ]]; then
                    # Enter - confirm selection
                    SELECTED_BATTERY_LIMIT="${values[$opt_cursor]}"
                    clear
                    echo
                    echo -e "  ${BOLD}Battery Charge Limit${RESET}"
                    echo
                    echo -e "  ${CHECKED}✓${RESET}  ${labels[$opt_cursor]} queued for apply"
                    echo
                    echo -e "  ${DIM}Press any key to return...${RESET}"
                    read -rsn1 < /dev/tty
                    return
                else
                    return  # Q = cancel
                fi
                ;;
        esac
    done
}

# Apply the selected battery charge limit and configure udev persistence
apply_battery_limit() {
    clear
    echo
    echo
    echo -e "${BOLD}  Set Battery Charge Limit: ${SELECTED_BATTERY_LIMIT}%${RESET}"
    echo

    # Request sudo credentials
    if ! sudo -n true 2>/dev/null; then
        echo -e "  ${DIM}Sudo access required to set charge limit...${RESET}"
        sudo true || {
            echo -e "    ${DIM}✗${RESET}  Failed to get sudo access"
            SUMMARY_LOG+=("✗  Battery charge limit -- failed (no sudo)")
            return 1
        }
    fi

    # Find battery threshold path
    local bat_threshold=""
    for path in /sys/class/power_supply/BAT*/charge_control_end_threshold; do
        [[ -f "$path" ]] && { bat_threshold="$path"; break; }
    done

    # Apply immediately
    if echo "$SELECTED_BATTERY_LIMIT" | sudo tee "$bat_threshold" > /dev/null 2>&1; then
        echo -e "    ${CHECKED}✓${RESET}  Charge limit set to ${SELECTED_BATTERY_LIMIT}%"
    else
        echo -e "    ${DIM}✗${RESET}  Failed to set charge limit"
        SUMMARY_LOG+=("✗  Battery charge limit -- failed to set")
        return 1
    fi

    # Handle udev rule for persistence
    if [[ "$SELECTED_BATTERY_LIMIT" == "100" ]]; then
        # No limit - remove udev rule if it exists
        if [[ -f "$BATTERY_LIMIT_UDEV_RULE" ]]; then
            sudo rm -f "$BATTERY_LIMIT_UDEV_RULE"
            echo -e "    ${CHECKED}✓${RESET}  Removed udev rule (no limit)"
        fi

        # Remove battery limit helper script
        if [[ -f "$BATTERY_LIMIT_HELPER" ]]; then
            rm -f "$BATTERY_LIMIT_HELPER"
            echo -e "    ${CHECKED}✓${RESET}  Removed battery limit helper script"
        fi

        # Remove the Omarchy menu charge-limit picker
        if remove_power_menu_override; then
            echo -e "    ${CHECKED}✓${RESET}  Removed charge limit picker from the Omarchy menu"
        fi
    else
        # Write udev rule for persistence
        printf '# Managed by A La Carchy - battery charge limit\nSUBSYSTEM=="power_supply", KERNEL=="BAT*", ATTR{charge_control_end_threshold}="%s"\n' \
            "$SELECTED_BATTERY_LIMIT" | sudo tee "$BATTERY_LIMIT_UDEV_RULE" > /dev/null
        echo -e "    ${CHECKED}✓${RESET}  Udev rule written: $BATTERY_LIMIT_UDEV_RULE"
    fi

    # Reload udev rules
    sudo udevadm control --reload-rules 2>/dev/null
    echo -e "    ${CHECKED}✓${RESET}  Udev rules reloaded"

    # Install helper script and the Omarchy menu charge-limit picker
    if [[ "$SELECTED_BATTERY_LIMIT" != "100" ]]; then
        install_battery_limit_helper
        install_power_menu_override
    fi

    SUMMARY_LOG+=("✓  Battery charge limit set to ${SELECTED_BATTERY_LIMIT}%")
    echo
    echo
}

# Install standalone helper script for setting battery limit from the Omarchy menu.
# Uses pkexec (not sudo) since it runs from a GUI context with no terminal.
install_battery_limit_helper() {
    local script_dir
    script_dir="$(dirname "$BATTERY_LIMIT_HELPER")"
    mkdir -p "$script_dir"

    cat > "$BATTERY_LIMIT_HELPER" << 'HELPEREOF'
#!/bin/bash
# Battery charge limit helper for Omarchy power menu
# Managed by A La Carchy - do not edit manually

LIMIT="${1:-80}"
UDEV_RULE="/etc/udev/rules.d/99-battery-charge-limit.rules"

# Find battery threshold path
BAT_THRESHOLD=""
for p in /sys/class/power_supply/BAT*/charge_control_end_threshold; do
    [[ -f "$p" ]] && { BAT_THRESHOLD="$p"; break; }
done

if [[ -z "$BAT_THRESHOLD" ]]; then
    notify-send "Battery Limit" "No supported battery found" -i dialog-error
    exit 1
fi

# Single pkexec call: write sysfs + udev rule + reload
if [[ "$LIMIT" == "100" ]]; then
    pkexec bash -c "
        echo 100 > '$BAT_THRESHOLD' &&
        rm -f '$UDEV_RULE' &&
        udevadm control --reload-rules
    "
else
    pkexec bash -c "
        echo '$LIMIT' > '$BAT_THRESHOLD' &&
        printf '# Managed by A La Carchy - battery charge limit\nSUBSYSTEM==\"power_supply\", KERNEL==\"BAT*\", ATTR{charge_control_end_threshold}=\"$LIMIT\"\n' > '$UDEV_RULE' &&
        udevadm control --reload-rules
    "
fi

if [[ $? -ne 0 ]]; then
    notify-send "Battery Limit" "Failed to set charge limit" -i dialog-error
    exit 1
fi

notify-send "Battery Limit" "Charge limit set to ${LIMIT}%" -i battery
HELPEREOF

    chmod +x "$BATTERY_LIMIT_HELPER"
    echo -e "    ${CHECKED}✓${RESET}  Battery limit helper installed: $BATTERY_LIMIT_HELPER"
}

# Add a Setup > Battery Limit picker to the Omarchy menu (JSONC extension).
# Rows run the helper, which applies the limit via pkexec.
POWER_MENU_MARKER_START="  // >>> a-la-carchy battery-limit"
POWER_MENU_MARKER_END="  // <<< a-la-carchy battery-limit"

remove_power_menu_override() {
    [[ -f "$MENU_JSONC" ]] && grep -qxF -- "$POWER_MENU_MARKER_START" "$MENU_JSONC" || return 1
    awk -v s="$POWER_MENU_MARKER_START" -v e="$POWER_MENU_MARKER_END" '
        $0 == s { skip = 1; next }
        $0 == e { skip = 0; next }
        !skip { print }
    ' "$MENU_JSONC" > "${MENU_JSONC}.tmp" && mv "${MENU_JSONC}.tmp" "$MENU_JSONC"
    omarchy-menu refresh &>/dev/null || true
}

install_power_menu_override() {
    mkdir -p "$(dirname "$MENU_JSONC")"
    [[ -f "$MENU_JSONC" ]] || printf '{\n}\n' > "$MENU_JSONC"
    remove_power_menu_override

    local threshold='cat /sys/class/power_supply/BAT*/charge_control_end_threshold 2>/dev/null | head -1'
    local lines="" pct label
    lines+="  \"setup.battery-limit\": $(jq -cn --arg when "[[ -n \"\$($threshold)\" ]]" \
        '{icon: "󰂄", label: "Battery Limit", aliases: ["charge-limit"], when: $when}'),"$'\n'
    for pct in 60 70 80 90 100; do
        case "$pct" in
            60)  label="60% - Max longevity" ;;
            80)  label="80% - Recommended" ;;
            100) label="100% - No limit" ;;
            *)   label="${pct}%" ;;
        esac
        lines+="  \"setup.battery-limit.$pct\": $(jq -cn --arg label "$label" \
            --arg checked "[[ \"\$($threshold)\" == $pct ]]" --arg action "$BATTERY_LIMIT_HELPER $pct" \
            '{icon: "󰁹", label: $label, checked: $checked, action: $action}'),"$'\n'
    done

    ALC_BODY="${lines%$'\n'}" awk -v s="$POWER_MENU_MARKER_START" -v e="$POWER_MENU_MARKER_END" '
        !done && /^[[:space:]]*\{[[:space:]]*$/ { print; print s; print ENVIRON["ALC_BODY"]; print e; done = 1; next }
        { print }
    ' "$MENU_JSONC" > "${MENU_JSONC}.tmp" && mv "${MENU_JSONC}.tmp" "$MENU_JSONC"
    omarchy-menu refresh &>/dev/null || true

    echo -e "    ${CHECKED}✓${RESET}  Charge limit picker added to Omarchy menu (Setup > Battery Limit)"
}

# =============================================================================
# ROG HARDWARE CONTROL (asusctl)
# =============================================================================

# Global state for ROG dialog selections
SELECTED_ROG_PROFILE=""
SELECTED_ROG_KBD_LEDS=""
SELECTED_ROG_AURA_EFFECT=""
SELECTED_ROG_AURA_COLOR=""
SELECTED_ROG_AURA_COLOR2=""
SELECTED_ROG_AURA_SPEED=""
SELECTED_ROG_AURA_DIRECTION=""
SELECTED_ROG_SLASH_MODE=""
SELECTED_ROG_SLASH_BRIGHT=""
ROG_SLASH_ENABLE=""
SELECTED_ROG_SLASH_INTERVAL=""
ROG_SLASH_SHOW_BOOT=""
ROG_SLASH_SHOW_SHUTDOWN=""
ROG_SLASH_SHOW_SLEEP=""
ROG_SLASH_SHOW_BATTERY=""
ROG_SLASH_SHOW_BATTERY_WARN=""
SELECTED_ROG_BATTERY_LIMIT=""
ROG_BATTERY_ONESHOT=""
SELECTED_ROG_NV_DYNAMIC_BOOST=""
SELECTED_ROG_NV_TEMP_TARGET=""
SELECTED_ROG_PPT_PL1_SPL=""
SELECTED_ROG_PPT_PL2_SPPT=""
SELECTED_ROG_ANIME_BRIGHTNESS=""
ROG_ANIME_POWERSAVE=""
ROG_ANIME_OFF_UNPLUGGED=""
ROG_ANIME_OFF_SUSPENDED=""
ROG_ANIME_OFF_LID_CLOSED=""
SELECTED_ROG_ANIME_BOOT=""
SELECTED_ROG_ANIME_AWAKE=""
SELECTED_ROG_ANIME_SLEEP=""
SELECTED_ROG_ANIME_SHUTDOWN=""
SELECTED_ROG_FAN_CURVE_FAN=""
SELECTED_ROG_FAN_CURVE_DATA=""
ROG_FAN_CURVE_DEFAULT=""
ROG_FAN_CURVE_ENABLE_SINGLE=""
SELECTED_ROG_AURA_POWER_ZONE=""
SELECTED_ROG_AURA_POWER_STATES=""

show_rog_profile_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  ROG Platform Profile${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo -e "  ${DIM}Install asusctl to use this feature.${RESET}"
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local active_profile
    active_profile=$(asusctl profile get 2>/dev/null | head -1 | sed 's/Active profile: //')

    local -a profiles=("Quiet" "Balanced" "Performance")
    local opt_cursor=1

    for i in "${!profiles[@]}"; do
        if [[ "${profiles[$i]}" == "$active_profile" ]]; then
            opt_cursor=$i
            break
        fi
    done

    while true; do
        clear
        echo
        echo -e "  ${BOLD}ROG Platform Profile${RESET}"
        echo -e "  ${DIM}Set ASUS performance profile via asusctl${RESET}"
        echo
        echo -e "  ${DIM}Select profile (Up/Down, Enter to confirm):${RESET}"
        echo

        for i in "${!profiles[@]}"; do
            local suffix=""
            [[ "${profiles[$i]}" == "$active_profile" ]] && suffix=" (active)"
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${profiles[$i]}${suffix}${RESET}"
            else
                echo -e "      ${C_TEXT}${profiles[$i]}${suffix}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#profiles[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    SELECTED_ROG_PROFILE="${profiles[$opt_cursor]}"
                    clear
                    echo
                    echo -e "  ${BOLD}ROG Platform Profile${RESET}"
                    echo
                    echo -e "  ${CHECKED}✓${RESET}  ${profiles[$opt_cursor]} queued for apply"
                    echo
                    echo -e "  ${DIM}Press any key to return...${RESET}"
                    read -rsn1 < /dev/tty
                    return
                else
                    return
                fi
                ;;
        esac
    done
}

apply_rog_profile() {
    clear
    echo
    echo
    echo -e "${BOLD}  Set ROG Profile: $SELECTED_ROG_PROFILE${RESET}"
    echo

    if asusctl profile set "$SELECTED_ROG_PROFILE" 2>/dev/null; then
        echo -e "    ${CHECKED}✓${RESET}  Profile set to $SELECTED_ROG_PROFILE"
        SUMMARY_LOG+=("✓  ROG profile set to $SELECTED_ROG_PROFILE")
    else
        echo -e "    ${DIM}✗${RESET}  Failed to set profile"
        SUMMARY_LOG+=("✗  ROG profile -- failed to set")
    fi
    echo
    echo
}

show_rog_kbd_leds_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  Keyboard LEDs${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local current_level
    current_level=$(asusctl leds get 2>/dev/null | grep -oiE '(off|low|med|high)' | tr '[:upper:]' '[:lower:]')

    local -a levels=("off" "low" "med" "high")
    local -a labels=("Off" "Low" "Medium" "High")
    local opt_cursor=2

    for i in "${!levels[@]}"; do
        if [[ "${levels[$i]}" == "$current_level" ]]; then
            opt_cursor=$i
            break
        fi
    done

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Keyboard LED Brightness${RESET}"
        echo -e "  ${DIM}Set keyboard backlight brightness${RESET}"
        echo
        echo -e "  ${DIM}Select level (Up/Down, Enter to confirm):${RESET}"
        echo

        for i in "${!levels[@]}"; do
            local suffix=""
            [[ "${levels[$i]}" == "$current_level" ]] && suffix=" (current)"
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${labels[$i]}${suffix}${RESET}"
            else
                echo -e "      ${C_TEXT}${labels[$i]}${suffix}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#levels[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    SELECTED_ROG_KBD_LEDS="${levels[$opt_cursor]}"
                    clear
                    echo
                    echo -e "  ${BOLD}Keyboard LED Brightness${RESET}"
                    echo
                    echo -e "  ${CHECKED}✓${RESET}  ${labels[$opt_cursor]} queued for apply"
                    echo
                    echo -e "  ${DIM}Press any key to return...${RESET}"
                    read -rsn1 < /dev/tty
                    return
                else
                    return
                fi
                ;;
        esac
    done
}

apply_rog_kbd_leds() {
    clear
    echo
    echo
    echo -e "${BOLD}  Set Keyboard LEDs: $SELECTED_ROG_KBD_LEDS${RESET}"
    echo

    if asusctl leds set "$SELECTED_ROG_KBD_LEDS" 2>/dev/null; then
        echo -e "    ${CHECKED}✓${RESET}  Keyboard brightness set to $SELECTED_ROG_KBD_LEDS"
        SUMMARY_LOG+=("✓  Keyboard LEDs set to $SELECTED_ROG_KBD_LEDS")
    else
        echo -e "    ${DIM}✗${RESET}  Failed to set keyboard brightness"
        SUMMARY_LOG+=("✗  Keyboard LEDs -- failed to set")
    fi
    echo
    echo
}

# Aura effect parameter requirements:
#   color-only:       static, pulse, comet, flash          (-c)
#   color+speed:      highlight, laser, ripple              (-c, --speed)
#   two-color+speed:  breathe, stars                        (--colour, --colour2, --speed)
#   speed-only:       rainbow-cycle, rain                   (--speed)
#   direction+speed:  rainbow-wave                          (--direction, --speed)

_aura_prompt_color() {
    local label="$1" var_name="$2"
    echo
    echo -e "  ${DIM}Enter hex color for ${label} (e.g. ff0000 for red):${RESET}"
    echo
    printf "    Color: "
    local input=""
    read -r input < /dev/tty
    input=$(echo "$input" | tr -d '#' | tr '[:upper:]' '[:lower:]')
    if [[ "$input" =~ ^[0-9a-f]{6}$ ]]; then
        printf -v "$var_name" '%s' "$input"
        return 0
    else
        echo
        echo -e "  ${DIM}✗${RESET}  Invalid hex color. Must be 6 hex digits."
        return 1
    fi
}

_aura_prompt_speed() {
    local -a speeds=("low" "med" "high")
    local -a slabels=("Low" "Medium" "High")
    local sc=1  # default medium

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Aura Speed${RESET}"
        echo -e "  ${DIM}Select animation speed:${RESET}"
        echo

        for i in "${!speeds[@]}"; do
            if [[ $i -eq $sc ]]; then
                echo -e "    ${SELECTED_BG}> ${slabels[$i]}${RESET}"
            else
                echo -e "      ${C_TEXT}${slabels[$i]}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( sc > 0 )) && ((sc--)) ;;
                    '[B') (( sc < 2 )) && ((sc++)) ;;
                esac
                [[ -z "$key" ]] && return 1
                ;;
            '')
                SELECTED_ROG_AURA_SPEED="${speeds[$sc]}"
                return 0
                ;;
            q|Q) return 1 ;;
        esac
    done
}

_aura_prompt_direction() {
    local -a dirs=("up" "down" "left" "right")
    local -a dlabels=("Up" "Down" "Left" "Right")
    local dc=2  # default left

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Aura Direction${RESET}"
        echo -e "  ${DIM}Select wave direction:${RESET}"
        echo

        for i in "${!dirs[@]}"; do
            if [[ $i -eq $dc ]]; then
                echo -e "    ${SELECTED_BG}> ${dlabels[$i]}${RESET}"
            else
                echo -e "      ${C_TEXT}${dlabels[$i]}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( dc > 0 )) && ((dc--)) ;;
                    '[B') (( dc < 3 )) && ((dc++)) ;;
                esac
                [[ -z "$key" ]] && return 1
                ;;
            '')
                SELECTED_ROG_AURA_DIRECTION="${dirs[$dc]}"
                return 0
                ;;
            q|Q) return 1 ;;
        esac
    done
}

show_rog_aura_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  Aura RGB Effect${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local -a effects=("static" "breathe" "rainbow-cycle" "rainbow-wave" "stars" "rain" "highlight" "laser" "ripple" "pulse" "comet" "flash")
    local -a labels=("Static" "Breathe" "Rainbow Cycle" "Rainbow Wave" "Stars" "Rain" "Highlight" "Laser" "Ripple" "Pulse" "Comet" "Flash")
    local opt_cursor=0
    local scroll_offset=0
    local visible_rows=8

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Aura RGB Effect${RESET}"
        echo -e "  ${DIM}Set keyboard RGB lighting effect${RESET}"
        echo
        echo -e "  ${DIM}Select effect (Up/Down, Enter to confirm):${RESET}"
        echo

        (( opt_cursor < scroll_offset )) && scroll_offset=$opt_cursor
        (( opt_cursor >= scroll_offset + visible_rows )) && scroll_offset=$((opt_cursor - visible_rows + 1))

        for (( i=scroll_offset; i < scroll_offset + visible_rows && i < ${#effects[@]}; i++ )); do
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${labels[$i]}${RESET}"
            else
                echo -e "      ${C_TEXT}${labels[$i]}${RESET}"
            fi
        done

        if (( ${#effects[@]} > visible_rows )); then
            echo -e "  ${DIM}  ($((opt_cursor+1))/${#effects[@]})${RESET}"
        fi
        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#effects[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    local selected_effect="${effects[$opt_cursor]}"
                    local selected_label="${labels[$opt_cursor]}"

                    # Reset all aura params
                    SELECTED_ROG_AURA_COLOR=""
                    SELECTED_ROG_AURA_COLOR2=""
                    SELECTED_ROG_AURA_SPEED=""
                    SELECTED_ROG_AURA_DIRECTION=""

                    case "$selected_effect" in
                        static|pulse|comet|flash)
                            # Color only
                            clear
                            echo
                            echo -e "  ${BOLD}Aura RGB — ${selected_label}${RESET}"
                            if ! _aura_prompt_color "color" SELECTED_ROG_AURA_COLOR; then
                                echo -e "  ${DIM}Press any key to return...${RESET}"
                                read -rsn1 < /dev/tty
                                return
                            fi
                            ;;
                        highlight|laser|ripple)
                            # Color + speed
                            clear
                            echo
                            echo -e "  ${BOLD}Aura RGB — ${selected_label}${RESET}"
                            if ! _aura_prompt_color "color" SELECTED_ROG_AURA_COLOR; then
                                echo -e "  ${DIM}Press any key to return...${RESET}"
                                read -rsn1 < /dev/tty
                                return
                            fi
                            _aura_prompt_speed || return
                            ;;
                        breathe|stars)
                            # Two colors + speed
                            clear
                            echo
                            echo -e "  ${BOLD}Aura RGB — ${selected_label}${RESET}"
                            if ! _aura_prompt_color "primary color" SELECTED_ROG_AURA_COLOR; then
                                echo -e "  ${DIM}Press any key to return...${RESET}"
                                read -rsn1 < /dev/tty
                                return
                            fi
                            echo
                            if ! _aura_prompt_color "secondary color" SELECTED_ROG_AURA_COLOR2; then
                                echo -e "  ${DIM}Press any key to return...${RESET}"
                                read -rsn1 < /dev/tty
                                return
                            fi
                            _aura_prompt_speed || return
                            ;;
                        rainbow-cycle|rain)
                            # Speed only
                            _aura_prompt_speed || return
                            ;;
                        rainbow-wave)
                            # Direction + speed
                            _aura_prompt_direction || return
                            _aura_prompt_speed || return
                            ;;
                    esac

                    SELECTED_ROG_AURA_EFFECT="$selected_effect"
                    clear
                    echo
                    echo -e "  ${BOLD}Aura RGB Effect${RESET}"
                    echo
                    echo -e "  ${CHECKED}✓${RESET}  ${selected_label} queued for apply"
                    echo
                    echo -e "  ${DIM}Press any key to return...${RESET}"
                    read -rsn1 < /dev/tty
                    return
                else
                    return
                fi
                ;;
        esac
    done
}

apply_rog_aura() {
    clear
    echo
    echo
    echo -e "${BOLD}  Set Aura RGB: $SELECTED_ROG_AURA_EFFECT${RESET}"
    echo

    local -a cmd=(asusctl aura effect "$SELECTED_ROG_AURA_EFFECT")

    case "$SELECTED_ROG_AURA_EFFECT" in
        static|pulse|comet|flash)
            cmd+=(-c "$SELECTED_ROG_AURA_COLOR")
            ;;
        highlight|laser|ripple)
            cmd+=(-c "$SELECTED_ROG_AURA_COLOR" --speed "$SELECTED_ROG_AURA_SPEED")
            ;;
        breathe|stars)
            cmd+=(--colour "$SELECTED_ROG_AURA_COLOR" --colour2 "$SELECTED_ROG_AURA_COLOR2" --speed "$SELECTED_ROG_AURA_SPEED")
            ;;
        rainbow-cycle|rain)
            cmd+=(--speed "$SELECTED_ROG_AURA_SPEED")
            ;;
        rainbow-wave)
            cmd+=(--direction "$SELECTED_ROG_AURA_DIRECTION" --speed "$SELECTED_ROG_AURA_SPEED")
            ;;
    esac

    local summary_detail="$SELECTED_ROG_AURA_EFFECT"
    [[ -n "$SELECTED_ROG_AURA_COLOR" ]] && summary_detail+=" (#${SELECTED_ROG_AURA_COLOR})"

    if "${cmd[@]}" 2>/dev/null; then
        echo -e "    ${CHECKED}✓${RESET}  Effect set to $summary_detail"
        SUMMARY_LOG+=("✓  Aura RGB set to $summary_detail")
    else
        echo -e "    ${DIM}✗${RESET}  Failed to set aura effect"
        SUMMARY_LOG+=("✗  Aura RGB -- failed to set")
    fi
    echo
    echo
}

show_rog_slash_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  Slash Ledbar${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local -a modes
    mapfile -t modes < <(asusctl slash --list 2>/dev/null | tr -d '"')

    if [[ ${#modes[@]} -eq 0 ]]; then
        clear
        echo
        echo
        echo -e "${BOLD}  Slash Ledbar${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  No Slash Ledbar detected on this device."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    # Build combined options: Enable/Disable + mode selection
    local -a options=("Enable Slash" "Disable Slash")
    for m in "${modes[@]}"; do
        [[ -n "$m" ]] && options+=("Mode: $m")
    done

    local opt_cursor=0
    local scroll_offset=0
    local visible_rows=10

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Slash Ledbar${RESET}"
        echo -e "  ${DIM}Configure the Slash LED bar${RESET}"
        echo
        echo -e "  ${DIM}Select option (Up/Down, Enter to confirm):${RESET}"
        echo

        (( opt_cursor < scroll_offset )) && scroll_offset=$opt_cursor
        (( opt_cursor >= scroll_offset + visible_rows )) && scroll_offset=$((opt_cursor - visible_rows + 1))

        for (( i=scroll_offset; i < scroll_offset + visible_rows && i < ${#options[@]}; i++ )); do
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${options[$i]}${RESET}"
            else
                echo -e "      ${C_TEXT}${options[$i]}${RESET}"
            fi
        done

        if (( ${#options[@]} > visible_rows )); then
            echo -e "  ${DIM}  ($((opt_cursor+1))/${#options[@]})${RESET}"
        fi
        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#options[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    local selection="${options[$opt_cursor]}"
                    if [[ "$selection" == "Enable Slash" ]]; then
                        ROG_SLASH_ENABLE="enable"
                        SELECTED_ROG_SLASH_MODE=""
                    elif [[ "$selection" == "Disable Slash" ]]; then
                        ROG_SLASH_ENABLE="disable"
                        SELECTED_ROG_SLASH_MODE=""
                    else
                        SELECTED_ROG_SLASH_MODE="${selection#Mode: }"
                        ROG_SLASH_ENABLE="enable"
                    fi

                    # Ask for brightness if enabling or setting mode
                    if [[ "$ROG_SLASH_ENABLE" == "enable" || -n "$SELECTED_ROG_SLASH_MODE" ]]; then
                        clear
                        echo
                        echo -e "  ${BOLD}Slash Brightness${RESET}"
                        echo
                        echo -e "  ${DIM}Enter brightness (0-255, or press Enter for default):${RESET}"
                        echo
                        printf "    Brightness: "
                        local bright_input=""
                        read -r bright_input < /dev/tty
                        if [[ "$bright_input" =~ ^[0-9]+$ ]] && (( bright_input >= 0 && bright_input <= 255 )); then
                            SELECTED_ROG_SLASH_BRIGHT="$bright_input"
                        else
                            SELECTED_ROG_SLASH_BRIGHT=""
                        fi
                    fi

                    clear
                    echo
                    echo -e "  ${BOLD}Slash Ledbar${RESET}"
                    echo
                    echo -e "  ${CHECKED}✓${RESET}  ${selection} queued for apply"
                    echo
                    echo -e "  ${DIM}Press any key to return...${RESET}"
                    read -rsn1 < /dev/tty
                    return
                else
                    return
                fi
                ;;
        esac
    done
}

apply_rog_slash() {
    clear
    echo
    echo
    echo -e "${BOLD}  Configure Slash Ledbar${RESET}"
    echo

    if [[ "$ROG_SLASH_ENABLE" == "enable" ]]; then
        if asusctl slash --enable 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash Ledbar enabled"
            SUMMARY_LOG+=("✓  Slash Ledbar enabled")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to enable Slash Ledbar"
            SUMMARY_LOG+=("✗  Slash Ledbar -- failed to enable")
        fi
    elif [[ "$ROG_SLASH_ENABLE" == "disable" ]]; then
        if asusctl slash --disable 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash Ledbar disabled"
            SUMMARY_LOG+=("✓  Slash Ledbar disabled")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to disable Slash Ledbar"
            SUMMARY_LOG+=("✗  Slash Ledbar -- failed to disable")
        fi
    fi

    if [[ -n "$SELECTED_ROG_SLASH_MODE" ]]; then
        if asusctl slash --mode "$SELECTED_ROG_SLASH_MODE" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash mode set to $SELECTED_ROG_SLASH_MODE"
            SUMMARY_LOG+=("✓  Slash mode set to $SELECTED_ROG_SLASH_MODE")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set Slash mode"
            SUMMARY_LOG+=("✗  Slash mode -- failed to set")
        fi
    fi

    if [[ -n "$SELECTED_ROG_SLASH_BRIGHT" ]]; then
        if asusctl slash -l "$SELECTED_ROG_SLASH_BRIGHT" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash brightness set to $SELECTED_ROG_SLASH_BRIGHT"
            SUMMARY_LOG+=("✓  Slash brightness set to $SELECTED_ROG_SLASH_BRIGHT")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set Slash brightness"
            SUMMARY_LOG+=("✗  Slash brightness -- failed to set")
        fi
    fi
    echo
    echo
}

apply_rog_armoury_toggle() {
    local attr="$1"
    local label="$2"
    local value="$3"
    local display_val="$4"

    if asusctl armoury set "$attr" "$value" 2>/dev/null; then
        echo -e "    ${CHECKED}✓${RESET}  $label set to $display_val"
        SUMMARY_LOG+=("✓  $label set to $display_val")
    else
        echo -e "    ${DIM}✗${RESET}  Failed to set $label"
        SUMMARY_LOG+=("✗  $label -- failed to set")
    fi
}

apply_rog_hardware_toggles() {
    local has_change=false
    [[ "$ROG_ENABLE_FAN_CURVES" == true || "$ROG_DISABLE_FAN_CURVES" == true ]] && has_change=true
    [[ "$ROG_ENABLE_BOOT_SOUND" == true || "$ROG_DISABLE_BOOT_SOUND" == true ]] && has_change=true
    [[ "$ROG_ENABLE_PANEL_OD" == true || "$ROG_DISABLE_PANEL_OD" == true ]] && has_change=true
    [[ "$ROG_ENABLE_DGPU" == true || "$ROG_DISABLE_DGPU" == true ]] && has_change=true
    [[ "$ROG_DGPU_MUX" == true || "$ROG_HYBRID_MUX" == true ]] && has_change=true
    [[ "$ROG_ENABLE_ANIME" == true || "$ROG_DISABLE_ANIME" == true ]] && has_change=true

    [[ "$has_change" != true ]] && return

    clear
    echo
    echo
    echo -e "${BOLD}  ROG Hardware Settings${RESET}"
    echo

    if [[ "$ROG_ENABLE_BOOT_SOUND" == true ]]; then
        apply_rog_armoury_toggle "boot_sound" "Boot sound" "1" "enabled"
    fi
    if [[ "$ROG_DISABLE_BOOT_SOUND" == true ]]; then
        apply_rog_armoury_toggle "boot_sound" "Boot sound" "0" "disabled"
    fi

    if [[ "$ROG_ENABLE_PANEL_OD" == true ]]; then
        apply_rog_armoury_toggle "panel_overdrive" "Panel overdrive" "1" "enabled"
    fi
    if [[ "$ROG_DISABLE_PANEL_OD" == true ]]; then
        apply_rog_armoury_toggle "panel_overdrive" "Panel overdrive" "0" "disabled"
    fi

    if [[ "$ROG_ENABLE_DGPU" == true ]]; then
        apply_rog_armoury_toggle "dgpu_disable" "Discrete GPU" "0" "enabled"
    fi
    if [[ "$ROG_DISABLE_DGPU" == true ]]; then
        apply_rog_armoury_toggle "dgpu_disable" "Discrete GPU" "1" "disabled"
    fi

    if [[ "$ROG_DGPU_MUX" == true ]]; then
        apply_rog_armoury_toggle "gpu_mux_mode" "GPU MUX" "0" "dGPU direct (reboot required)"
    fi
    if [[ "$ROG_HYBRID_MUX" == true ]]; then
        apply_rog_armoury_toggle "gpu_mux_mode" "GPU MUX" "1" "hybrid"
    fi

    if [[ "$ROG_ENABLE_FAN_CURVES" == true ]]; then
        local profile
        profile=$(asusctl profile get 2>/dev/null | head -1 | sed 's/Active profile: //')
        if asusctl fan-curve --enable-fan-curves true --mod-profile "$profile" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Fan curves enabled for $profile"
            SUMMARY_LOG+=("✓  Fan curves enabled for $profile")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to enable fan curves"
            SUMMARY_LOG+=("✗  Fan curves -- failed to enable")
        fi
    fi
    if [[ "$ROG_DISABLE_FAN_CURVES" == true ]]; then
        local profile
        profile=$(asusctl profile get 2>/dev/null | head -1 | sed 's/Active profile: //')
        if asusctl fan-curve --enable-fan-curves false --mod-profile "$profile" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Fan curves disabled for $profile"
            SUMMARY_LOG+=("✓  Fan curves disabled for $profile")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to disable fan curves"
            SUMMARY_LOG+=("✗  Fan curves -- failed to disable")
        fi
    fi

    if [[ "$ROG_ENABLE_ANIME" == true ]]; then
        if asusctl anime --enable-display true 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  AniMe Matrix enabled"
            SUMMARY_LOG+=("✓  AniMe Matrix enabled")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to enable AniMe Matrix"
            SUMMARY_LOG+=("✗  AniMe Matrix -- failed to enable")
        fi
    fi
    if [[ "$ROG_DISABLE_ANIME" == true ]]; then
        if asusctl anime --enable-display false 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  AniMe Matrix disabled"
            SUMMARY_LOG+=("✓  AniMe Matrix disabled")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to disable AniMe Matrix"
            SUMMARY_LOG+=("✗  AniMe Matrix -- failed to disable")
        fi
    fi

    echo
    echo
}

# =============================================================================
# ROG BATTERY MANAGEMENT
# =============================================================================

show_rog_battery_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  ROG Battery Management${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local current_limit
    current_limit=$(asusctl battery info 2>/dev/null | grep -oE '[0-9]+' | head -1)

    local -a options=("Set charge limit" "One-shot full charge")
    local opt_cursor=0

    while true; do
        clear
        echo
        echo -e "  ${BOLD}ROG Battery Management${RESET}"
        echo -e "  ${DIM}Control battery charging behavior${RESET}"
        if [[ -n "$current_limit" ]]; then
            echo -e "  ${DIM}Current charge limit: ${current_limit}%${RESET}"
        fi
        echo
        echo -e "  ${DIM}Select option (Up/Down, Enter to confirm):${RESET}"
        echo

        for i in "${!options[@]}"; do
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${options[$i]}${RESET}"
            else
                echo -e "      ${C_TEXT}${options[$i]}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#options[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    if [[ $opt_cursor -eq 0 ]]; then
                        # Charge limit
                        clear
                        echo
                        echo -e "  ${BOLD}Battery Charge Limit${RESET}"
                        echo
                        echo -e "  ${DIM}Enter charge limit (20-100):${RESET}"
                        echo
                        printf "    Limit: "
                        local limit_input=""
                        read -r limit_input < /dev/tty
                        if [[ "$limit_input" =~ ^[0-9]+$ ]] && (( limit_input >= 20 && limit_input <= 100 )); then
                            SELECTED_ROG_BATTERY_LIMIT="$limit_input"
                            clear
                            echo
                            echo -e "  ${BOLD}Battery Charge Limit${RESET}"
                            echo
                            echo -e "  ${CHECKED}✓${RESET}  Charge limit ${limit_input}% queued for apply"
                            echo
                            echo -e "  ${DIM}Press any key to return...${RESET}"
                            read -rsn1 < /dev/tty
                            return
                        else
                            echo
                            echo -e "  ${DIM}✗${RESET}  Invalid value. Must be 20-100."
                            echo
                            echo -e "  ${DIM}Press any key to return...${RESET}"
                            read -rsn1 < /dev/tty
                            return
                        fi
                    else
                        # One-shot full charge
                        ROG_BATTERY_ONESHOT="true"
                        clear
                        echo
                        echo -e "  ${BOLD}Battery One-Shot Charge${RESET}"
                        echo
                        echo -e "  ${CHECKED}✓${RESET}  One-shot full charge queued for apply"
                        echo
                        echo -e "  ${DIM}Press any key to return...${RESET}"
                        read -rsn1 < /dev/tty
                        return
                    fi
                else
                    return
                fi
                ;;
        esac
    done
}

apply_rog_battery() {
    clear
    echo
    echo
    echo -e "${BOLD}  ROG Battery Management${RESET}"
    echo

    if [[ -n "$SELECTED_ROG_BATTERY_LIMIT" ]]; then
        if asusctl battery limit "$SELECTED_ROG_BATTERY_LIMIT" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Battery charge limit set to ${SELECTED_ROG_BATTERY_LIMIT}%"
            SUMMARY_LOG+=("✓  Battery charge limit set to ${SELECTED_ROG_BATTERY_LIMIT}%")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set battery charge limit"
            SUMMARY_LOG+=("✗  Battery charge limit -- failed to set")
        fi
    fi

    if [[ "$ROG_BATTERY_ONESHOT" == "true" ]]; then
        if asusctl battery oneshot 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  One-shot full charge activated"
            SUMMARY_LOG+=("✓  One-shot full charge activated")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to activate one-shot charge"
            SUMMARY_LOG+=("✗  One-shot charge -- failed to activate")
        fi
    fi
    echo
    echo
}

# =============================================================================
# ROG POWER TUNING
# =============================================================================

show_rog_power_tuning_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  ROG Power Tuning${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local -a items=("nv_dynamic_boost|NVIDIA Dynamic Boost|5|20|20|W" "nv_temp_target|NVIDIA Temp Target|75|87|87|°C" "ppt_pl1_spl|CPU Sustained Power (PL1)|28|90|90|W" "ppt_pl2_sppt|CPU Short Boost (PL2)|28|135|135|W")
    local opt_cursor=0

    while true; do
        clear
        echo
        echo -e "  ${BOLD}ROG Power Tuning${RESET}"
        echo -e "  ${DIM}Adjust CPU/GPU power and thermal limits${RESET}"
        echo
        echo -e "  ${DIM}Select parameter (Up/Down, Enter to edit):${RESET}"
        echo

        for i in "${!items[@]}"; do
            IFS='|' read -r attr label min max default unit <<< "${items[$i]}"
            local current_val=""
            case "$attr" in
                nv_dynamic_boost) current_val="$SELECTED_ROG_NV_DYNAMIC_BOOST" ;;
                nv_temp_target) current_val="$SELECTED_ROG_NV_TEMP_TARGET" ;;
                ppt_pl1_spl) current_val="$SELECTED_ROG_PPT_PL1_SPL" ;;
                ppt_pl2_sppt) current_val="$SELECTED_ROG_PPT_PL2_SPPT" ;;
            esac
            local suffix=""
            [[ -n "$current_val" ]] && suffix=" → ${current_val}${unit}"
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${label} (${min}-${max}${unit})${suffix}${RESET}"
            else
                echo -e "      ${C_TEXT}${label} (${min}-${max}${unit})${suffix}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: done${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#items[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    IFS='|' read -r attr label min max default unit <<< "${items[$opt_cursor]}"
                    clear
                    echo
                    echo -e "  ${BOLD}${label}${RESET}"
                    echo
                    echo -e "  ${DIM}Range: ${min}-${max}${unit} (default: ${default}${unit})${RESET}"
                    echo
                    printf "    Value: "
                    local val_input=""
                    read -r val_input < /dev/tty
                    if [[ "$val_input" =~ ^[0-9]+$ ]] && (( val_input >= min && val_input <= max )); then
                        case "$attr" in
                            nv_dynamic_boost) SELECTED_ROG_NV_DYNAMIC_BOOST="$val_input" ;;
                            nv_temp_target) SELECTED_ROG_NV_TEMP_TARGET="$val_input" ;;
                            ppt_pl1_spl) SELECTED_ROG_PPT_PL1_SPL="$val_input" ;;
                            ppt_pl2_sppt) SELECTED_ROG_PPT_PL2_SPPT="$val_input" ;;
                        esac
                    else
                        echo
                        echo -e "  ${DIM}✗${RESET}  Invalid value. Must be ${min}-${max}."
                        echo
                        echo -e "  ${DIM}Press any key to return...${RESET}"
                        read -rsn1 < /dev/tty
                    fi
                else
                    return
                fi
                ;;
        esac
    done
}

apply_rog_power_tuning() {
    local has_change=false
    [[ -n "$SELECTED_ROG_NV_DYNAMIC_BOOST" ]] && has_change=true
    [[ -n "$SELECTED_ROG_NV_TEMP_TARGET" ]] && has_change=true
    [[ -n "$SELECTED_ROG_PPT_PL1_SPL" ]] && has_change=true
    [[ -n "$SELECTED_ROG_PPT_PL2_SPPT" ]] && has_change=true

    [[ "$has_change" != true ]] && return

    clear
    echo
    echo
    echo -e "${BOLD}  ROG Power Tuning${RESET}"
    echo

    if [[ -n "$SELECTED_ROG_NV_DYNAMIC_BOOST" ]]; then
        apply_rog_armoury_toggle "nv_dynamic_boost" "NVIDIA Dynamic Boost" "$SELECTED_ROG_NV_DYNAMIC_BOOST" "${SELECTED_ROG_NV_DYNAMIC_BOOST}W"
    fi

    if [[ -n "$SELECTED_ROG_NV_TEMP_TARGET" ]]; then
        apply_rog_armoury_toggle "nv_temp_target" "NVIDIA Temp Target" "$SELECTED_ROG_NV_TEMP_TARGET" "${SELECTED_ROG_NV_TEMP_TARGET}°C"
    fi

    if [[ -n "$SELECTED_ROG_PPT_PL1_SPL" ]]; then
        apply_rog_armoury_toggle "ppt_pl1_spl" "CPU Sustained Power" "$SELECTED_ROG_PPT_PL1_SPL" "${SELECTED_ROG_PPT_PL1_SPL}W"
    fi

    if [[ -n "$SELECTED_ROG_PPT_PL2_SPPT" ]]; then
        apply_rog_armoury_toggle "ppt_pl2_sppt" "CPU Short Boost" "$SELECTED_ROG_PPT_PL2_SPPT" "${SELECTED_ROG_PPT_PL2_SPPT}W"
    fi

    echo
    echo
}

# =============================================================================
# ROG AURA POWER ZONES
# =============================================================================

show_rog_aura_power_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  Aura Power Zones${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local -a zones=("keyboard" "logo" "lightbar" "lid" "rear-glow")
    local -a zone_labels=("Keyboard" "Logo" "Lightbar" "Lid" "Rear Glow")
    local -a states=("boot" "awake" "sleep" "shutdown")
    local -a state_labels=("Boot" "Awake" "Sleep" "Shutdown")

    local opt_cursor=0

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Aura Power Zones${RESET}"
        echo -e "  ${DIM}Control LED zones for different power states${RESET}"
        echo
        echo -e "  ${DIM}Select zone (Up/Down, Enter to configure):${RESET}"
        echo

        for i in "${!zones[@]}"; do
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${zone_labels[$i]}${RESET}"
            else
                echo -e "      ${C_TEXT}${zone_labels[$i]}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: done${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#zones[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    local zone="${zones[$opt_cursor]}"
                    local zone_label="${zone_labels[$opt_cursor]}"

                    # Toggle states for this zone
                    local -a zone_toggles=(0 0 0 0)
                    local sc=0

                    while true; do
                        clear
                        echo
                        echo -e "  ${BOLD}Aura Power — ${zone_label}${RESET}"
                        echo -e "  ${DIM}Toggle power states (Enter to toggle, Escape to save):${RESET}"
                        echo

                        for si in "${!states[@]}"; do
                            local marker="  "
                            [[ "${zone_toggles[$si]}" -eq 1 ]] && marker="●"
                            [[ "${zone_toggles[$si]}" -eq 0 ]] && marker="○"
                            if [[ $si -eq $sc ]]; then
                                echo -e "    ${SELECTED_BG}> ${marker} ${state_labels[$si]}${RESET}"
                            else
                                echo -e "      ${marker} ${C_TEXT}${state_labels[$si]}${RESET}"
                            fi
                        done

                        echo
                        echo -e "  ${DIM}Escape: save and return${RESET}"

                        IFS= read -rsn1 skey < /dev/tty
                        case "$skey" in
                            $'\x1b')
                                read -rsn2 -t 0.1 skey
                                case "$skey" in
                                    '[A') (( sc > 0 )) && ((sc--)) ;;
                                    '[B') (( sc < ${#states[@]} - 1 )) && ((sc++)) ;;
                                esac
                                if [[ -z "$skey" ]]; then
                                    # Build the state list for this zone
                                    local enabled_states=""
                                    for si in "${!states[@]}"; do
                                        if [[ "${zone_toggles[$si]}" -eq 1 ]]; then
                                            [[ -n "$enabled_states" ]] && enabled_states+=","
                                            enabled_states+="${states[$si]}"
                                        fi
                                    done
                                    if [[ -n "$enabled_states" ]]; then
                                        SELECTED_ROG_AURA_POWER_ZONE+="${zone}:${enabled_states};"
                                    fi
                                    break
                                fi
                                ;;
                            '')
                                # Toggle
                                if [[ "${zone_toggles[$sc]}" -eq 0 ]]; then
                                    zone_toggles[$sc]=1
                                else
                                    zone_toggles[$sc]=0
                                fi
                                ;;
                            q|Q) break ;;
                        esac
                    done
                else
                    return
                fi
                ;;
        esac
    done
}

apply_rog_aura_power() {
    [[ -z "$SELECTED_ROG_AURA_POWER_ZONE" ]] && return

    clear
    echo
    echo
    echo -e "${BOLD}  Aura Power Zones${RESET}"
    echo

    # Parse zone:states; pairs
    IFS=';' read -ra zone_pairs <<< "$SELECTED_ROG_AURA_POWER_ZONE"
    for pair in "${zone_pairs[@]}"; do
        [[ -z "$pair" ]] && continue
        local zone="${pair%%:*}"
        local states_str="${pair#*:}"
        IFS=',' read -ra state_list <<< "$states_str"

        local -a cmd=(asusctl aura power "$zone")
        for st in "${state_list[@]}"; do
            cmd+=("--$st")
        done

        if "${cmd[@]}" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  ${zone} zone: ${states_str} enabled"
            SUMMARY_LOG+=("✓  Aura power ${zone}: ${states_str}")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set ${zone} zone"
            SUMMARY_LOG+=("✗  Aura power ${zone} -- failed to set")
        fi
    done

    echo
    echo
}

# =============================================================================
# ROG EXTENDED SLASH LEDBAR
# =============================================================================

show_rog_slash_extra_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  Slash Ledbar Options${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local -a options=("Interval (0-5)" "Show on boot" "Show on shutdown" "Show on sleep" "Show on battery" "Battery warning")
    local -a toggle_vars=("" "ROG_SLASH_SHOW_BOOT" "ROG_SLASH_SHOW_SHUTDOWN" "ROG_SLASH_SHOW_SLEEP" "ROG_SLASH_SHOW_BATTERY" "ROG_SLASH_SHOW_BATTERY_WARN")
    local opt_cursor=0

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Slash Ledbar Options${RESET}"
        echo -e "  ${DIM}Configure when the Slash LED bar is active${RESET}"
        echo
        echo -e "  ${DIM}Select option (Up/Down, Enter to toggle/edit):${RESET}"
        echo

        for i in "${!options[@]}"; do
            local suffix=""
            if [[ $i -eq 0 ]]; then
                [[ -n "$SELECTED_ROG_SLASH_INTERVAL" ]] && suffix=" → $SELECTED_ROG_SLASH_INTERVAL"
            else
                local var="${toggle_vars[$i]}"
                local val="${!var}"
                [[ "$val" == "true" ]] && suffix=" → Enable"
                [[ "$val" == "false" ]] && suffix=" → Disable"
            fi
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${options[$i]}${suffix}${RESET}"
            else
                echo -e "      ${C_TEXT}${options[$i]}${suffix}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: done${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#options[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    if [[ $opt_cursor -eq 0 ]]; then
                        clear
                        echo
                        echo -e "  ${BOLD}Slash Interval${RESET}"
                        echo
                        echo -e "  ${DIM}Enter interval value (0-5):${RESET}"
                        echo
                        printf "    Interval: "
                        local int_input=""
                        read -r int_input < /dev/tty
                        if [[ "$int_input" =~ ^[0-5]$ ]]; then
                            SELECTED_ROG_SLASH_INTERVAL="$int_input"
                        else
                            echo
                            echo -e "  ${DIM}✗${RESET}  Invalid value. Must be 0-5."
                            echo
                            echo -e "  ${DIM}Press any key to return...${RESET}"
                            read -rsn1 < /dev/tty
                        fi
                    else
                        # Cycle: unset → true → false → unset
                        local var="${toggle_vars[$opt_cursor]}"
                        local val="${!var}"
                        if [[ -z "$val" ]]; then
                            printf -v "$var" '%s' "true"
                        elif [[ "$val" == "true" ]]; then
                            printf -v "$var" '%s' "false"
                        else
                            printf -v "$var" '%s' ""
                        fi
                    fi
                else
                    return
                fi
                ;;
        esac
    done
}

apply_rog_slash_extra() {
    local has_change=false
    [[ -n "$SELECTED_ROG_SLASH_INTERVAL" ]] && has_change=true
    [[ -n "$ROG_SLASH_SHOW_BOOT" ]] && has_change=true
    [[ -n "$ROG_SLASH_SHOW_SHUTDOWN" ]] && has_change=true
    [[ -n "$ROG_SLASH_SHOW_SLEEP" ]] && has_change=true
    [[ -n "$ROG_SLASH_SHOW_BATTERY" ]] && has_change=true
    [[ -n "$ROG_SLASH_SHOW_BATTERY_WARN" ]] && has_change=true

    [[ "$has_change" != true ]] && return

    clear
    echo
    echo
    echo -e "${BOLD}  Slash Ledbar Options${RESET}"
    echo

    if [[ -n "$SELECTED_ROG_SLASH_INTERVAL" ]]; then
        if asusctl slash --interval "$SELECTED_ROG_SLASH_INTERVAL" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash interval set to $SELECTED_ROG_SLASH_INTERVAL"
            SUMMARY_LOG+=("✓  Slash interval set to $SELECTED_ROG_SLASH_INTERVAL")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set Slash interval"
            SUMMARY_LOG+=("✗  Slash interval -- failed to set")
        fi
    fi

    if [[ -n "$ROG_SLASH_SHOW_BOOT" ]]; then
        if asusctl slash -B "$ROG_SLASH_SHOW_BOOT" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash show-on-boot: $ROG_SLASH_SHOW_BOOT"
            SUMMARY_LOG+=("✓  Slash show-on-boot: $ROG_SLASH_SHOW_BOOT")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set show-on-boot"
            SUMMARY_LOG+=("✗  Slash show-on-boot -- failed")
        fi
    fi

    if [[ -n "$ROG_SLASH_SHOW_SHUTDOWN" ]]; then
        if asusctl slash -S "$ROG_SLASH_SHOW_SHUTDOWN" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash show-on-shutdown: $ROG_SLASH_SHOW_SHUTDOWN"
            SUMMARY_LOG+=("✓  Slash show-on-shutdown: $ROG_SLASH_SHOW_SHUTDOWN")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set show-on-shutdown"
            SUMMARY_LOG+=("✗  Slash show-on-shutdown -- failed")
        fi
    fi

    if [[ -n "$ROG_SLASH_SHOW_SLEEP" ]]; then
        if asusctl slash -s "$ROG_SLASH_SHOW_SLEEP" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash show-on-sleep: $ROG_SLASH_SHOW_SLEEP"
            SUMMARY_LOG+=("✓  Slash show-on-sleep: $ROG_SLASH_SHOW_SLEEP")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set show-on-sleep"
            SUMMARY_LOG+=("✗  Slash show-on-sleep -- failed")
        fi
    fi

    if [[ -n "$ROG_SLASH_SHOW_BATTERY" ]]; then
        if asusctl slash -b "$ROG_SLASH_SHOW_BATTERY" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash show-on-battery: $ROG_SLASH_SHOW_BATTERY"
            SUMMARY_LOG+=("✓  Slash show-on-battery: $ROG_SLASH_SHOW_BATTERY")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set show-on-battery"
            SUMMARY_LOG+=("✗  Slash show-on-battery -- failed")
        fi
    fi

    if [[ -n "$ROG_SLASH_SHOW_BATTERY_WARN" ]]; then
        if asusctl slash -w "$ROG_SLASH_SHOW_BATTERY_WARN" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Slash battery warning: $ROG_SLASH_SHOW_BATTERY_WARN"
            SUMMARY_LOG+=("✓  Slash battery warning: $ROG_SLASH_SHOW_BATTERY_WARN")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set battery warning"
            SUMMARY_LOG+=("✗  Slash battery warning -- failed")
        fi
    fi

    echo
    echo
}

# =============================================================================
# ROG EXTENDED ANIME MATRIX
# =============================================================================

show_rog_anime_extra_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  AniMe Matrix Options${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local -a options=("Brightness" "Powersave animation" "Off when unplugged" "Off when suspended" "Off when lid closed" "Builtin animations")
    local opt_cursor=0

    while true; do
        clear
        echo
        echo -e "  ${BOLD}AniMe Matrix Options${RESET}"
        echo -e "  ${DIM}Configure AniMe Matrix display behavior${RESET}"
        echo
        echo -e "  ${DIM}Select option (Up/Down, Enter to configure):${RESET}"
        echo

        for i in "${!options[@]}"; do
            local suffix=""
            case $i in
                0) [[ -n "$SELECTED_ROG_ANIME_BRIGHTNESS" ]] && suffix=" → $SELECTED_ROG_ANIME_BRIGHTNESS" ;;
                1) [[ -n "$ROG_ANIME_POWERSAVE" ]] && suffix=" → $ROG_ANIME_POWERSAVE" ;;
                2) [[ -n "$ROG_ANIME_OFF_UNPLUGGED" ]] && suffix=" → $ROG_ANIME_OFF_UNPLUGGED" ;;
                3) [[ -n "$ROG_ANIME_OFF_SUSPENDED" ]] && suffix=" → $ROG_ANIME_OFF_SUSPENDED" ;;
                4) [[ -n "$ROG_ANIME_OFF_LID_CLOSED" ]] && suffix=" → $ROG_ANIME_OFF_LID_CLOSED" ;;
                5) [[ -n "$SELECTED_ROG_ANIME_BOOT" ]] && suffix=" → configured" ;;
            esac
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${options[$i]}${suffix}${RESET}"
            else
                echo -e "      ${C_TEXT}${options[$i]}${suffix}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: done${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#options[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    case $opt_cursor in
                        0) # Brightness
                            local -a blevels=("off" "low" "med" "high")
                            local -a blabels=("Off" "Low" "Medium" "High")
                            local bc=1
                            while true; do
                                clear
                                echo
                                echo -e "  ${BOLD}AniMe Brightness${RESET}"
                                echo -e "  ${DIM}Select brightness level:${RESET}"
                                echo
                                for bi in "${!blevels[@]}"; do
                                    if [[ $bi -eq $bc ]]; then
                                        echo -e "    ${SELECTED_BG}> ${blabels[$bi]}${RESET}"
                                    else
                                        echo -e "      ${C_TEXT}${blabels[$bi]}${RESET}"
                                    fi
                                done
                                echo
                                echo -e "  ${DIM}Escape: cancel${RESET}"
                                IFS= read -rsn1 bkey < /dev/tty
                                case "$bkey" in
                                    $'\x1b')
                                        read -rsn2 -t 0.1 bkey
                                        case "$bkey" in
                                            '[A') (( bc > 0 )) && ((bc--)) ;;
                                            '[B') (( bc < 3 )) && ((bc++)) ;;
                                        esac
                                        [[ -z "$bkey" ]] && break
                                        ;;
                                    '') SELECTED_ROG_ANIME_BRIGHTNESS="${blevels[$bc]}"; break ;;
                                    q|Q) break ;;
                                esac
                            done
                            ;;
                        1) # Powersave animation toggle
                            if [[ -z "$ROG_ANIME_POWERSAVE" || "$ROG_ANIME_POWERSAVE" == "false" ]]; then
                                ROG_ANIME_POWERSAVE="true"
                            else
                                ROG_ANIME_POWERSAVE="false"
                            fi
                            ;;
                        2) # Off when unplugged
                            if [[ -z "$ROG_ANIME_OFF_UNPLUGGED" || "$ROG_ANIME_OFF_UNPLUGGED" == "false" ]]; then
                                ROG_ANIME_OFF_UNPLUGGED="true"
                            else
                                ROG_ANIME_OFF_UNPLUGGED="false"
                            fi
                            ;;
                        3) # Off when suspended
                            if [[ -z "$ROG_ANIME_OFF_SUSPENDED" || "$ROG_ANIME_OFF_SUSPENDED" == "false" ]]; then
                                ROG_ANIME_OFF_SUSPENDED="true"
                            else
                                ROG_ANIME_OFF_SUSPENDED="false"
                            fi
                            ;;
                        4) # Off when lid closed
                            if [[ -z "$ROG_ANIME_OFF_LID_CLOSED" || "$ROG_ANIME_OFF_LID_CLOSED" == "false" ]]; then
                                ROG_ANIME_OFF_LID_CLOSED="true"
                            else
                                ROG_ANIME_OFF_LID_CLOSED="false"
                            fi
                            ;;
                        5) # Builtin animations
                            _anime_select_builtins
                            ;;
                    esac
                else
                    return
                fi
                ;;
        esac
    done
}

_anime_select_builtins() {
    local -a boot_opts=("default" "GlitchConstruction" "StaticEmergence")
    local -a awake_opts=("default" "BinaryBannerScroll" "RogLogoGlitch")
    local -a sleep_opts=("default" "BannerSwipe" "Starfield")
    local -a shutdown_opts=("default" "GlitchOut" "SeeYa")
    local -a phase_names=("Boot" "Awake" "Sleep" "Shutdown")

    local phase=0
    while (( phase < 4 )); do
        local -n opts_ref="${phase_names[$phase],,}_opts"
        local pc=0

        while true; do
            clear
            echo
            echo -e "  ${BOLD}AniMe Builtin — ${phase_names[$phase]}${RESET}"
            echo -e "  ${DIM}Select animation:${RESET}"
            echo

            for oi in "${!opts_ref[@]}"; do
                if [[ $oi -eq $pc ]]; then
                    echo -e "    ${SELECTED_BG}> ${opts_ref[$oi]}${RESET}"
                else
                    echo -e "      ${C_TEXT}${opts_ref[$oi]}${RESET}"
                fi
            done

            echo
            echo -e "  ${DIM}Escape: skip${RESET}"

            IFS= read -rsn1 akey < /dev/tty
            case "$akey" in
                $'\x1b')
                    read -rsn2 -t 0.1 akey
                    case "$akey" in
                        '[A') (( pc > 0 )) && ((pc--)) ;;
                        '[B') (( pc < ${#opts_ref[@]} - 1 )) && ((pc++)) ;;
                    esac
                    [[ -z "$akey" ]] && break
                    ;;
                '')
                    case $phase in
                        0) SELECTED_ROG_ANIME_BOOT="${opts_ref[$pc]}" ;;
                        1) SELECTED_ROG_ANIME_AWAKE="${opts_ref[$pc]}" ;;
                        2) SELECTED_ROG_ANIME_SLEEP="${opts_ref[$pc]}" ;;
                        3) SELECTED_ROG_ANIME_SHUTDOWN="${opts_ref[$pc]}" ;;
                    esac
                    break
                    ;;
                q|Q) return ;;
            esac
        done
        ((phase++))
    done
}

apply_rog_anime_extra() {
    local has_change=false
    [[ -n "$SELECTED_ROG_ANIME_BRIGHTNESS" ]] && has_change=true
    [[ -n "$ROG_ANIME_POWERSAVE" ]] && has_change=true
    [[ -n "$ROG_ANIME_OFF_UNPLUGGED" ]] && has_change=true
    [[ -n "$ROG_ANIME_OFF_SUSPENDED" ]] && has_change=true
    [[ -n "$ROG_ANIME_OFF_LID_CLOSED" ]] && has_change=true
    [[ -n "$SELECTED_ROG_ANIME_BOOT" ]] && has_change=true

    [[ "$has_change" != true ]] && return

    clear
    echo
    echo
    echo -e "${BOLD}  AniMe Matrix Options${RESET}"
    echo

    if [[ -n "$SELECTED_ROG_ANIME_BRIGHTNESS" ]]; then
        if asusctl anime --brightness "$SELECTED_ROG_ANIME_BRIGHTNESS" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  AniMe brightness set to $SELECTED_ROG_ANIME_BRIGHTNESS"
            SUMMARY_LOG+=("✓  AniMe brightness: $SELECTED_ROG_ANIME_BRIGHTNESS")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set AniMe brightness"
            SUMMARY_LOG+=("✗  AniMe brightness -- failed")
        fi
    fi

    if [[ -n "$ROG_ANIME_POWERSAVE" ]]; then
        if asusctl anime --enable-powersave-anim "$ROG_ANIME_POWERSAVE" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  AniMe powersave animation: $ROG_ANIME_POWERSAVE"
            SUMMARY_LOG+=("✓  AniMe powersave: $ROG_ANIME_POWERSAVE")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set powersave animation"
            SUMMARY_LOG+=("✗  AniMe powersave -- failed")
        fi
    fi

    if [[ -n "$ROG_ANIME_OFF_UNPLUGGED" ]]; then
        if asusctl anime --off-when-unplugged "$ROG_ANIME_OFF_UNPLUGGED" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  AniMe off-when-unplugged: $ROG_ANIME_OFF_UNPLUGGED"
            SUMMARY_LOG+=("✓  AniMe off-when-unplugged: $ROG_ANIME_OFF_UNPLUGGED")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set off-when-unplugged"
            SUMMARY_LOG+=("✗  AniMe off-when-unplugged -- failed")
        fi
    fi

    if [[ -n "$ROG_ANIME_OFF_SUSPENDED" ]]; then
        if asusctl anime --off-when-suspended "$ROG_ANIME_OFF_SUSPENDED" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  AniMe off-when-suspended: $ROG_ANIME_OFF_SUSPENDED"
            SUMMARY_LOG+=("✓  AniMe off-when-suspended: $ROG_ANIME_OFF_SUSPENDED")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set off-when-suspended"
            SUMMARY_LOG+=("✗  AniMe off-when-suspended -- failed")
        fi
    fi

    if [[ -n "$ROG_ANIME_OFF_LID_CLOSED" ]]; then
        if asusctl anime --off-when-lid-closed "$ROG_ANIME_OFF_LID_CLOSED" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  AniMe off-when-lid-closed: $ROG_ANIME_OFF_LID_CLOSED"
            SUMMARY_LOG+=("✓  AniMe off-when-lid-closed: $ROG_ANIME_OFF_LID_CLOSED")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set off-when-lid-closed"
            SUMMARY_LOG+=("✗  AniMe off-when-lid-closed -- failed")
        fi
    fi

    if [[ -n "$SELECTED_ROG_ANIME_BOOT" || -n "$SELECTED_ROG_ANIME_AWAKE" || -n "$SELECTED_ROG_ANIME_SLEEP" || -n "$SELECTED_ROG_ANIME_SHUTDOWN" ]]; then
        local -a builtin_cmd=(asusctl anime set-builtins)
        builtin_cmd+=(--boot "${SELECTED_ROG_ANIME_BOOT:-default}")
        builtin_cmd+=(--awake "${SELECTED_ROG_ANIME_AWAKE:-default}")
        builtin_cmd+=(--sleep "${SELECTED_ROG_ANIME_SLEEP:-default}")
        builtin_cmd+=(--shutdown "${SELECTED_ROG_ANIME_SHUTDOWN:-default}")
        builtin_cmd+=(--set true)

        if "${builtin_cmd[@]}" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  AniMe builtin animations configured"
            SUMMARY_LOG+=("✓  AniMe builtin animations set")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set builtin animations"
            SUMMARY_LOG+=("✗  AniMe builtins -- failed")
        fi
    fi

    echo
    echo
}

# =============================================================================
# ROG FAN CURVE EDITOR
# =============================================================================

_fan_curve_parse_current() {
    local profile="$1" fan="$2"
    local output
    output=$(asusctl fan-curve --mod-profile "$profile" --fan "$fan" 2>/dev/null)

    # Extract the block for the matching fan
    local fan_upper="${fan^^}"
    local in_block=false pwm_line="" temp_line=""
    while IFS= read -r line; do
        if [[ "$line" =~ fan:\ $fan_upper ]]; then
            in_block=true
        elif [[ "$in_block" == true ]]; then
            if [[ "$line" =~ pwm:\ \((.+)\) ]]; then
                pwm_line="${BASH_REMATCH[1]}"
            elif [[ "$line" =~ temp:\ \((.+)\) ]]; then
                temp_line="${BASH_REMATCH[1]}"
                break
            fi
        fi
    done <<< "$output"

    # Convert PWM (0-255) to percentage (0-100) for the UI
    if [[ -n "$pwm_line" && -n "$temp_line" ]]; then
        local -a raw_pwm=()
        IFS=', ' read -ra raw_pwm <<< "$pwm_line"
        IFS=', ' read -ra FC_TEMP <<< "$temp_line"
        FC_PCT=()
        for val in "${raw_pwm[@]}"; do
            FC_PCT+=( $(( val * 100 / 255 )) )
        done
    else
        FC_PCT=(5 10 25 35 50 60 80 90)
        FC_TEMP=(30 40 50 60 70 80 90 100)
    fi
}

_fan_curve_draw_graph() {
    local -n pct_ref=$1 temp_ref=$2
    local selected=$3
    local graph_h=16
    local graph_w=40

    # Y-axis: 0-100% mapped to graph_h rows
    # X-axis: 8 points spread across graph_w columns

    local -a col_positions=()
    for (( i=0; i<8; i++ )); do
        col_positions+=( $(( i * (graph_w - 1) / 7 )) )
    done

    # Build point row positions (0 = bottom, graph_h-1 = top)
    local -a point_rows=()
    for (( i=0; i<8; i++ )); do
        local row=$(( pct_ref[i] * (graph_h - 1) / 100 ))
        (( row > graph_h - 1 )) && row=$((graph_h - 1))
        (( row < 0 )) && row=0
        point_rows+=( "$row" )
    done

    # Build the grid row by row (top to bottom = high % to low)
    for (( row=graph_h-1; row>=0; row-- )); do
        # Y-axis label
        if (( row == graph_h - 1 )); then
            printf "  ${DIM}100%%${RESET} ${C_BORDER}│${RESET}"
        elif (( row == (graph_h - 1) * 3 / 4 )); then
            printf "  ${DIM} 75%%${RESET} ${C_BORDER}│${RESET}"
        elif (( row == (graph_h - 1) / 2 )); then
            printf "  ${DIM} 50%%${RESET} ${C_BORDER}│${RESET}"
        elif (( row == (graph_h - 1) / 4 )); then
            printf "  ${DIM} 25%%${RESET} ${C_BORDER}│${RESET}"
        elif (( row == 0 )); then
            printf "  ${DIM}  0%%${RESET} ${C_BORDER}│${RESET}"
        else
            printf "  ${DIM}    ${RESET} ${C_BORDER}│${RESET}"
        fi

        # Build this row's content
        local -a row_chars=()
        for (( c=0; c<graph_w; c++ )); do
            row_chars+=(" ")
        done

        # Fill in the curve line between points
        for (( i=0; i<7; i++ )); do
            local ni=$((i+1))
            local c1=${col_positions[$i]} r1=${point_rows[$i]}
            local c2=${col_positions[$ni]} r2=${point_rows[$ni]}
            local dc=$(( c2 - c1 ))
            for (( c=c1; c<=c2; c++ )); do
                local interp_row
                if (( dc == 0 )); then
                    interp_row=$r1
                else
                    interp_row=$(( r1 + (r2 - r1) * (c - c1) / dc ))
                fi
                if (( interp_row == row )); then
                    row_chars[$c]="─"
                fi
            done
        done

        # Place the point markers (overwrite line chars)
        for (( i=0; i<8; i++ )); do
            if (( point_rows[i] == row )); then
                if (( i == selected )); then
                    row_chars[${col_positions[$i]}]="◆"
                else
                    row_chars[${col_positions[$i]}]="●"
                fi
            fi
        done

        # Print the row
        for (( c=0; c<graph_w; c++ )); do
            local ch="${row_chars[$c]}"
            local is_point=false pi_match=-1
            for (( pi=0; pi<8; pi++ )); do
                if (( col_positions[pi] == c && point_rows[pi] == row )); then
                    is_point=true
                    pi_match=$pi
                    break
                fi
            done
            if [[ "$is_point" == true ]]; then
                if (( pi_match == selected )); then
                    printf "${C_ACCENT}◆${RESET}"
                else
                    printf "${CHECKED}●${RESET}"
                fi
            elif [[ "$ch" == "─" ]]; then
                printf "${DIM}─${RESET}"
            else
                printf " "
            fi
        done
        echo
    done

    # X-axis line
    printf "  ${DIM}    ${RESET} ${C_BORDER}└"
    for (( c=0; c<graph_w; c++ )); do
        printf "─"
    done
    printf "${RESET}"
    echo

    # X-axis labels (temperatures)
    printf "  ${DIM}      "
    for (( i=0; i<8; i++ )); do
        local pos=${col_positions[$i]}
        local label="${temp_ref[$i]}°"
        if (( i == 0 )); then
            printf "%s" "$label"
        else
            local prev_end=$(( col_positions[i-1] + ${#prev_label} ))
            local gap=$(( pos - prev_end ))
            (( gap < 1 )) && gap=1
            printf "%*s%s" "$gap" "" "$label"
        fi
        local prev_label="$label"
    done
    printf "${RESET}"
    echo
}

_fan_curve_graph_editor() {
    local fan="$1" profile="$2"
    local fan_upper="${fan^^}"

    # Parse current curve data (populates FC_PCT and FC_TEMP)
    local -a FC_PCT=() FC_TEMP=()
    _fan_curve_parse_current "$profile" "$fan"

    local selected=0
    local step=5  # Percentage adjustment step

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Fan Curve Editor — ${fan_upper}${RESET}"
        echo -e "  ${DIM}Profile: ${profile}${RESET}"
        echo

        _fan_curve_draw_graph FC_PCT FC_TEMP "$selected"

        echo
        echo -e "  ${C_ACCENT}Point $((selected+1))/8:${RESET}  ${FC_TEMP[$selected]}°C → ${FC_PCT[$selected]}% fan speed"
        echo
        echo -e "  ${DIM}←/→ select point   ↑/↓ adjust speed (±${step}%)${RESET}"
        echo -e "  ${DIM}T: edit temperature   S: toggle step (1%/5%/10%)${RESET}"
        echo -e "  ${DIM}Enter: save   Escape: cancel${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A')  # Up - increase fan %
                        (( FC_PCT[selected] += step ))
                        (( FC_PCT[selected] > 100 )) && FC_PCT[$selected]=100
                        ;;
                    '[B')  # Down - decrease fan %
                        (( FC_PCT[selected] -= step ))
                        (( FC_PCT[selected] < 0 )) && FC_PCT[$selected]=0
                        ;;
                    '[C')  # Right - next point
                        (( selected < 7 )) && ((selected++))
                        ;;
                    '[D')  # Left - previous point
                        (( selected > 0 )) && ((selected--))
                        ;;
                esac
                [[ -z "$key" ]] && return 1  # Escape = cancel
                ;;
            t|T)
                # Edit temperature for selected point
                echo
                printf "    New temperature for point $((selected+1)) (°C): "
                local temp_input=""
                read -r temp_input < /dev/tty
                if [[ "$temp_input" =~ ^[0-9]+$ ]] && (( temp_input >= 0 && temp_input <= 120 )); then
                    FC_TEMP[$selected]=$temp_input
                fi
                ;;
            s|S)
                # Cycle step size: 5% → 1% → 10% → 5%
                if (( step == 5 )); then step=1
                elif (( step == 1 )); then step=10
                else step=5; fi
                ;;
            '')  # Enter = save
                # Convert percentages back to PWM (0-255) for asusctl
                local data=""
                for (( i=0; i<8; i++ )); do
                    (( i > 0 )) && data+=","
                    local pwm_val=$(( FC_PCT[i] * 255 / 100 ))
                    data+="${FC_TEMP[$i]}c:${pwm_val}"
                done
                SELECTED_ROG_FAN_CURVE_FAN="$fan"
                SELECTED_ROG_FAN_CURVE_DATA="$data"
                return 0
                ;;
            q|Q) return 1 ;;
        esac
    done
}

show_rog_fan_curve_dialog() {
    if ! command -v asusctl &>/dev/null; then
        clear
        echo
        echo
        echo -e "${BOLD}  Fan Curve Editor${RESET}"
        echo
        echo -e "  ${DIM}✗${RESET}  asusctl not found."
        echo
        echo -e "  ${DIM}Press any key to return...${RESET}"
        read -rsn1 < /dev/tty
        return
    fi

    local profile
    profile=$(asusctl profile get 2>/dev/null | head -1 | sed 's/Active profile: //')

    local -a options=("Edit CPU fan curve" "Edit GPU fan curve" "Reset to default")
    local opt_cursor=0

    while true; do
        clear
        echo
        echo -e "  ${BOLD}Fan Curve Editor${RESET}"
        echo -e "  ${DIM}Profile: ${profile:-unknown}${RESET}"
        echo
        echo -e "  ${DIM}Select option (Up/Down, Enter to configure):${RESET}"
        echo

        for i in "${!options[@]}"; do
            if [[ $i -eq $opt_cursor ]]; then
                echo -e "    ${SELECTED_BG}> ${options[$i]}${RESET}"
            else
                echo -e "      ${C_TEXT}${options[$i]}${RESET}"
            fi
        done

        echo
        echo -e "  ${DIM}Escape: done${RESET}"

        IFS= read -rsn1 key < /dev/tty
        case "$key" in
            $'\x1b')
                read -rsn2 -t 0.1 key
                case "$key" in
                    '[A') (( opt_cursor > 0 )) && ((opt_cursor--)) ;;
                    '[B') (( opt_cursor < ${#options[@]} - 1 )) && ((opt_cursor++)) ;;
                esac
                [[ -z "$key" ]] && return
                ;;
            ''|q|Q)
                if [[ -z "$key" ]]; then
                    if [[ $opt_cursor -eq 2 ]]; then
                        ROG_FAN_CURVE_DEFAULT="true"
                        clear
                        echo
                        echo -e "  ${BOLD}Fan Curve Editor${RESET}"
                        echo
                        echo -e "  ${CHECKED}✓${RESET}  Reset to default queued"
                        echo
                        echo -e "  ${DIM}Press any key to return...${RESET}"
                        read -rsn1 < /dev/tty
                    else
                        local -a fans=("cpu" "gpu")
                        local fan="${fans[$opt_cursor]}"
                        if _fan_curve_graph_editor "$fan" "${profile:-Balanced}"; then
                            clear
                            echo
                            echo -e "  ${BOLD}Fan Curve Editor${RESET}"
                            echo
                            echo -e "  ${CHECKED}✓${RESET}  ${fan^^} fan curve queued for apply"
                            echo
                            echo -e "  ${DIM}Press any key to return...${RESET}"
                            read -rsn1 < /dev/tty
                        fi
                    fi
                else
                    return
                fi
                ;;
        esac
    done
}

apply_rog_fan_curve() {
    local has_change=false
    [[ -n "$SELECTED_ROG_FAN_CURVE_DATA" ]] && has_change=true
    [[ "$ROG_FAN_CURVE_DEFAULT" == "true" ]] && has_change=true

    [[ "$has_change" != true ]] && return

    clear
    echo
    echo
    echo -e "${BOLD}  Fan Curve Editor${RESET}"
    echo

    local profile
    profile=$(asusctl profile get 2>/dev/null | head -1 | sed 's/Active profile: //')

    if [[ "$ROG_FAN_CURVE_DEFAULT" == "true" ]]; then
        if asusctl fan-curve --default --mod-profile "${profile:-Balanced}" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Fan curves reset to default for ${profile:-Balanced}"
            SUMMARY_LOG+=("✓  Fan curves reset to default")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to reset fan curves"
            SUMMARY_LOG+=("✗  Fan curves -- failed to reset")
        fi
    fi

    if [[ -n "$SELECTED_ROG_FAN_CURVE_DATA" && -n "$SELECTED_ROG_FAN_CURVE_FAN" ]]; then
        if asusctl fan-curve --mod-profile "${profile:-Balanced}" --fan "$SELECTED_ROG_FAN_CURVE_FAN" --data "$SELECTED_ROG_FAN_CURVE_DATA" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  ${SELECTED_ROG_FAN_CURVE_FAN^^} fan curve set"
            SUMMARY_LOG+=("✓  ${SELECTED_ROG_FAN_CURVE_FAN^^} fan curve set")
        else
            echo -e "    ${DIM}✗${RESET}  Failed to set ${SELECTED_ROG_FAN_CURVE_FAN^^} fan curve"
            SUMMARY_LOG+=("✗  ${SELECTED_ROG_FAN_CURVE_FAN^^} fan curve -- failed")
        fi

        # Auto-enable the single fan curve
        if asusctl fan-curve --enable-fan-curve true --mod-profile "${profile:-Balanced}" --fan "$SELECTED_ROG_FAN_CURVE_FAN" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  ${SELECTED_ROG_FAN_CURVE_FAN^^} fan curve enabled"
        fi
    fi

    echo
    echo
}

bind_shutdown() {
    apply_lua_block "Bind Shutdown to SUPER+ALT+S" \
        "Adds a keybinding to shut down the system with SUPER+ALT+S (replaces 'Move window to scratchpad')." \
        "$BINDINGS_LUA" "shutdown" "Bound shutdown to SUPER+ALT+S" \
        'hl.unbind("SUPER + ALT + S")
o.bind("SUPER + ALT + S", "Shutdown", "systemctl poweroff")'
}

bind_restart() {
    apply_lua_block "Bind Restart to SUPER+ALT+R" \
        "Adds a keybinding to restart the system with SUPER+ALT+R." \
        "$BINDINGS_LUA" "restart" "Bound restart to SUPER+ALT+R" \
        'hl.unbind("SUPER + ALT + R")
o.bind("SUPER + ALT + R", "Restart", "systemctl reboot")'
}

unbind_shutdown() {
    apply_lua_block "Unbind Shutdown (SUPER+ALT+S)" \
        "Removes the shutdown keybinding and restores the Omarchy default." \
        "$BINDINGS_LUA" "shutdown" "Unbound shutdown (SUPER+ALT+S)"
}

unbind_restart() {
    apply_lua_block "Unbind Restart (SUPER+ALT+R)" \
        "Removes the restart keybinding." \
        "$BINDINGS_LUA" "restart" "Unbound restart (SUPER+ALT+R)"
}

bind_theme_menu() {
    apply_lua_block "Bind Theme Menu to ALT+T" \
        "Adds a keybinding to open the theme menu with ALT+T." \
        "$BINDINGS_LUA" "theme-menu" "Bound theme menu to ALT+T" \
        'hl.unbind("ALT + T")
o.bind("ALT + T", "Theme menu", "omarchy-menu toggle theme")'
}

unbind_theme_menu() {
    apply_lua_block "Unbind Theme Menu (ALT+T)" \
        "Removes the theme menu keybinding." \
        "$BINDINGS_LUA" "theme-menu" "Unbound theme menu (ALT+T)"
}

# Omarchy's default kb_options (default/hypr/input.lua)
OMARCHY_KB_OPTIONS="compose:caps,shift:both_capslock_cancel"

# Current effective kb_options: our managed block if present, else Hyprland's live value
current_kb_options() {
    local v
    v=$(lua_block_get "$INPUT_LUA" "kb-options" | grep -oP 'kb_options = "\K[^"]*')
    if [[ -z "$v" ]] && command -v hyprctl &>/dev/null; then
        v=$(hyprctl getoption input:kb_options 2>/dev/null | grep -oP '^str: \K.*')
    fi
    [[ -z "$v" ]] && v="$OMARCHY_KB_OPTIONS"
    echo "$v"
}

# Rewrite kb_options: drop the options in $3 and add those in $4 (space-separated)
edit_kb_options() {
    local title="$1" summary="$2" remove="$3" add="$4" explain="$5"
    local current new opt
    current="$(current_kb_options)"

    local -a kept=()
    IFS=',' read -ra opts <<< "$current"
    for opt in "${opts[@]}"; do
        [[ -z "$opt" || " $remove $add " == *" $opt "* ]] && continue
        kept+=("$opt")
    done
    for opt in $add; do
        kept+=("$opt")
    done
    new=$(IFS=','; echo "${kept[*]}")

    # Back to Omarchy's default set: remove our block instead of pinning it
    local sorted_new sorted_default
    sorted_new=$(tr ',' '\n' <<< "$new" | sort | paste -sd,)
    sorted_default=$(tr ',' '\n' <<< "$OMARCHY_KB_OPTIONS" | sort | paste -sd,)
    if [[ "$sorted_new" == "$sorted_default" ]]; then
        apply_lua_block "$title" "$explain" "$INPUT_LUA" "kb-options" "$summary"
        return
    fi

    apply_lua_block "$title" "$explain" "$INPUT_LUA" "kb-options" "$summary" \
        "hl.config({ input = { kb_options = \"$new\" } })"
}

restore_capslock() {
    edit_kb_options "Restore Caps Lock Key" "Caps Lock restored (compose on Right Alt)" \
        "compose:caps" "compose:ralt" \
        "Returns Caps Lock to normal behavior. Compose moves to Right Alt (Right Alt + Space + Space → —)."
}

use_capslock_compose() {
    edit_kb_options "Use Caps Lock for Compose" "Caps Lock set as compose key" \
        "compose:ralt" "compose:caps" \
        "Returns to Omarchy default: Caps Lock as compose key (Caps Lock + Space + Space → —)."
}

swap_alt_super() {
    edit_kb_options "Swap Alt and Super Keys" "Alt and Super swapped" \
        "" "altwin:swap_alt_win" \
        "Makes Alt behave as Super and Super behave as Alt (macOS-like shortcuts)."
}

restore_alt_super() {
    edit_kb_options "Restore Alt and Super Keys" "Alt and Super restored" \
        "altwin:swap_alt_win" "" \
        "Returns Alt and Super to their normal behavior (Omarchy default)."
}

enable_suspend() {
    clear
    echo
    echo
    echo -e "${BOLD}  Enable Suspend${RESET}"
    echo
    echo -e "  ${DIM}Adds the Suspend option to the Omarchy system menu.${RESET}"
    echo -e "  ${DIM}Access via: Super+Alt+Space → System → Suspend${RESET}"
    echo
    echo

    # Check if already enabled
    if [[ ! -f "$SUSPEND_OFF_FLAG" ]]; then
        echo -e "  ${DIM}Suspend already enabled. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  Enable suspend -- already enabled")
        return 0
    fi

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Enable suspend -- cancelled")
        return 0
    fi

    echo

    rm -f "$SUSPEND_OFF_FLAG"

    echo -e "  ${CHECKED}✓${RESET}  Suspend enabled in system menu"
    SUMMARY_LOG+=("✓  Enabled suspend")
    echo
}

disable_suspend() {
    clear
    echo
    echo
    echo -e "${BOLD}  Disable Suspend${RESET}"
    echo
    echo -e "  ${DIM}Removes the Suspend option from the Omarchy system menu.${RESET}"
    echo
    echo

    # Check if already disabled
    if [[ -f "$SUSPEND_OFF_FLAG" ]]; then
        echo -e "  ${DIM}Suspend already disabled. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  Disable suspend -- already disabled")
        return 0
    fi

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Disable suspend -- cancelled")
        return 0
    fi

    echo

    mkdir -p "$(dirname "$SUSPEND_OFF_FLAG")"
    touch "$SUSPEND_OFF_FLAG"

    echo -e "  ${CHECKED}✓${RESET}  Suspend disabled in system menu"
    SUMMARY_LOG+=("✓  Disabled suspend")
    echo
}

enable_hibernation() {
    clear
    echo
    echo
    echo -e "${BOLD}  Enable Hibernation${RESET}"
    echo
    echo -e "  ${DIM}Creates a swap subvolume on your boot drive sized to match your RAM.${RESET}"
    echo -e "  ${DIM}Adds Hibernate option to system menu and enables suspend-to-hibernate.${RESET}"
    echo
    echo -e "  ${DIM}Note: Requires free space equal to your RAM (e.g., 32GB RAM = 32GB space).${RESET}"
    echo
    echo

    # Check if already enabled
    if omarchy-hibernation-available 2>/dev/null; then
        echo -e "  ${DIM}Hibernation already enabled. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  Enable hibernation -- already enabled")
        return 0
    fi

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Enable hibernation -- cancelled")
        return 0
    fi

    echo
    echo -e "  ${DIM}Running omarchy-hibernation-setup...${RESET}"
    echo

    # Run the setup script (it has its own confirmation via gum)
    if omarchy-hibernation-setup; then
        echo
        echo -e "  ${CHECKED}✓${RESET}  Hibernation enabled"
        SUMMARY_LOG+=("✓  Enabled hibernation")
    else
        echo
        echo -e "  ${DIM}Hibernation setup was cancelled or failed.${RESET}"
        SUMMARY_LOG+=("--  Enable hibernation -- cancelled or failed")
    fi
    echo
}

disable_hibernation() {
    clear
    echo
    echo
    echo -e "${BOLD}  Disable Hibernation${RESET}"
    echo
    echo -e "  ${DIM}Removes the swap subvolume and hibernation configuration.${RESET}"
    echo -e "  ${DIM}Frees up disk space equal to your RAM size.${RESET}"
    echo
    echo

    # Check if hibernation is enabled
    if ! omarchy-hibernation-available 2>/dev/null; then
        echo -e "  ${DIM}Hibernation not enabled. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  Disable hibernation -- not enabled")
        return 0
    fi

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Disable hibernation -- cancelled")
        return 0
    fi

    echo
    echo -e "  ${DIM}Running omarchy-hibernation-remove...${RESET}"
    echo

    # Run the remove script (it has its own confirmation via gum)
    if omarchy-hibernation-remove; then
        echo
        echo -e "  ${CHECKED}✓${RESET}  Hibernation disabled"
        SUMMARY_LOG+=("✓  Disabled hibernation")
    else
        echo
        echo -e "  ${DIM}Hibernation removal was cancelled or failed.${RESET}"
        SUMMARY_LOG+=("--  Disable hibernation -- cancelled or failed")
    fi
    echo
}

enable_fingerprint() {
    clear
    echo
    echo
    echo -e "${BOLD}  Enable Fingerprint Authentication${RESET}"
    echo
    echo -e "  ${DIM}Sets up fingerprint scanner for authentication.${RESET}"
    echo -e "  ${DIM}Works for: sudo, polkit prompts, and lock screen.${RESET}"
    echo
    echo -e "  ${DIM}Note: Requires a fingerprint sensor and you'll need to enroll your finger.${RESET}"
    echo
    echo

    # Check if already enabled (fprintd installed)
    if pacman -Qi fprintd &>/dev/null; then
        echo -e "  ${DIM}Fingerprint authentication already set up.${RESET}"
        echo -e "  ${DIM}To re-enroll, disable first then enable again.${RESET}"
        echo
        SUMMARY_LOG+=("--  Enable fingerprint -- already enabled")
        return 0
    fi

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Enable fingerprint -- cancelled")
        return 0
    fi

    echo
    echo -e "  ${DIM}Running omarchy-setup-security-fingerprint...${RESET}"
    echo

    # Run the setup script
    if omarchy-setup-security-fingerprint; then
        echo
        echo -e "  ${CHECKED}✓${RESET}  Fingerprint authentication enabled"
        SUMMARY_LOG+=("✓  Enabled fingerprint authentication")
    else
        echo
        echo -e "  ${DIM}Fingerprint setup failed or was cancelled.${RESET}"
        SUMMARY_LOG+=("--  Enable fingerprint -- failed or cancelled")
    fi
    echo
}

disable_fingerprint() {
    clear
    echo
    echo
    echo -e "${BOLD}  Disable Fingerprint Authentication${RESET}"
    echo
    echo -e "  ${DIM}Removes fingerprint authentication and uninstalls packages.${RESET}"
    echo
    echo

    # Check if fingerprint is enabled
    if ! pacman -Qi fprintd &>/dev/null; then
        echo -e "  ${DIM}Fingerprint authentication not set up. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  Disable fingerprint -- not enabled")
        return 0
    fi

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Disable fingerprint -- cancelled")
        return 0
    fi

    echo
    echo -e "  ${DIM}Running omarchy-remove-security-fingerprint...${RESET}"
    echo

    # Run the remove script
    if omarchy-remove-security-fingerprint; then
        echo
        echo -e "  ${CHECKED}✓${RESET}  Fingerprint authentication disabled"
        SUMMARY_LOG+=("✓  Disabled fingerprint authentication")
    else
        echo
        echo -e "  ${DIM}Fingerprint removal failed.${RESET}"
        SUMMARY_LOG+=("--  Disable fingerprint -- failed")
    fi
    echo
}

enable_fido2() {
    clear
    echo
    echo
    echo -e "${BOLD}  Enable FIDO2 Authentication${RESET}"
    echo
    echo -e "  ${DIM}Sets up FIDO2 security key for sudo authentication.${RESET}"
    echo -e "  ${DIM}Works for: sudo and polkit prompts (not lock screen).${RESET}"
    echo
    echo -e "  ${DIM}Note: Requires a FIDO2 device (YubiKey, etc) plugged in.${RESET}"
    echo
    echo

    # Check if already enabled (pam-u2f installed)
    if pacman -Qi pam-u2f &>/dev/null; then
        echo -e "  ${DIM}FIDO2 authentication already set up.${RESET}"
        echo -e "  ${DIM}To re-register, disable first then enable again.${RESET}"
        echo
        SUMMARY_LOG+=("--  Enable FIDO2 -- already enabled")
        return 0
    fi

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Enable FIDO2 -- cancelled")
        return 0
    fi

    echo
    echo -e "  ${DIM}Running omarchy-setup-security-fido2...${RESET}"
    echo

    # Run the setup script
    if omarchy-setup-security-fido2; then
        echo
        echo -e "  ${CHECKED}✓${RESET}  FIDO2 authentication enabled"
        SUMMARY_LOG+=("✓  Enabled FIDO2 authentication")
    else
        echo
        echo -e "  ${DIM}FIDO2 setup failed or was cancelled.${RESET}"
        SUMMARY_LOG+=("--  Enable FIDO2 -- failed or cancelled")
    fi
    echo
}

disable_fido2() {
    clear
    echo
    echo
    echo -e "${BOLD}  Disable FIDO2 Authentication${RESET}"
    echo
    echo -e "  ${DIM}Removes FIDO2 authentication and uninstalls packages.${RESET}"
    echo
    echo

    # Check if FIDO2 is enabled
    if ! pacman -Qi pam-u2f &>/dev/null; then
        echo -e "  ${DIM}FIDO2 authentication not set up. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  Disable FIDO2 -- not enabled")
        return 0
    fi

    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Cancelled."
        echo
        SUMMARY_LOG+=("--  Disable FIDO2 -- cancelled")
        return 0
    fi

    echo
    echo -e "  ${DIM}Running omarchy-remove-security-fido2...${RESET}"
    echo

    # Run the remove script
    if omarchy-remove-security-fido2; then
        echo
        echo -e "  ${CHECKED}✓${RESET}  FIDO2 authentication disabled"
        SUMMARY_LOG+=("✓  Disabled FIDO2 authentication")
    else
        echo
        echo -e "  ${DIM}FIDO2 removal failed.${RESET}"
        SUMMARY_LOG+=("--  Disable FIDO2 -- failed")
    fi
    echo
}

# Ids of the tray items currently registered with the StatusNotifier watcher
tray_item_ids() {
    local item svc path
    for item in $(busctl --user get-property org.kde.StatusNotifierWatcher /StatusNotifierWatcher \
            org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems 2>/dev/null | grep -o '"[^"]*"' | tr -d '"'); do
        svc="${item%%/*}"
        path="/StatusNotifierItem"
        [[ "$item" == */* ]] && path="/${item#*/}"
        busctl --user get-property "$svc" "$path" org.kde.StatusNotifierItem Id 2>/dev/null | grep -oP '^s "\K[^"]+'
    done
}

_tray_all_pinned() {
    local ids pinned id
    ids=$(tray_item_ids)
    [[ -z "$ids" ]] && return 0
    pinned=$(jq -r '[.bar.layout[]?[]? | select(.id == "omarchy.tray") | .pinned[]?] | .[]' "$SHELL_JSON" 2>/dev/null)
    for id in $ids; do
        grep -qxF "$id" <<< "$pinned" || return 1
    done
    return 0
}

_tray_pin_all() {
    local ids_json
    ids_json=$(tray_item_ids | jq -R . | jq -s .)
    shell_json_jq --argjson ids "$ids_json" '
        .bar.layout |= with_entries(.value |= map(
            if .id == "omarchy.tray" then .pinned = ((.pinned // []) + $ids | unique) | .hidden = ((.hidden // []) - $ids) else . end))
    '
}

show_all_tray_icons() {
    apply_shell_change "Show All Tray Icons" \
        "Pins every running tray app so its icon stays visible instead of collapsing into the tray drawer." \
        "Pinned all tray icons" _tray_all_pinned _tray_pin_all
}

_tray_none_pinned() {
    [[ -z "$(jq -r '[.bar.layout[]?[]? | select(.id == "omarchy.tray") | .pinned[]?] | .[]' "$SHELL_JSON" 2>/dev/null)" ]]
}

_tray_unpin_all() {
    shell_json_jq '.bar.layout |= with_entries(.value |= map(if .id == "omarchy.tray" then .pinned = [] else . end))'
}

hide_tray_icons() {
    apply_shell_change "Hide Tray Icons" \
        "Unpins all tray icons so they collapse into the tray drawer (Omarchy default)." \
        "Tray icons collapsed into drawer" _tray_none_pinned _tray_unpin_all
}

_logo_absent() { ! shell_bar_has omarchy.menu; }
_logo_remove() { shell_bar_remove omarchy.menu; }
_logo_present() { shell_bar_has omarchy.menu; }
_logo_restore() { omarchy-bar put omarchy.menu --section left --index 0 &>/dev/null; }

remove_omarchy_logo() {
    apply_shell_change "Remove Omarchy Logo" \
        "Removes the Omarchy logo menu button from the bar. The menu stays on SUPER+ALT+SPACE." \
        "Removed Omarchy logo from bar" _logo_absent _logo_remove
}

restore_omarchy_logo() {
    apply_shell_change "Restore Omarchy Logo" \
        "Puts the Omarchy logo menu button back at the left of the bar." \
        "Restored Omarchy logo to bar" _logo_present _logo_restore
}

_update_absent() { ! shell_bar_has omarchy.system-update; }
_update_remove() { shell_bar_remove omarchy.system-update; }
_update_present() { shell_bar_has omarchy.system-update; }
_update_restore() { omarchy-bar put omarchy.system-update &>/dev/null; }

remove_update_icon() {
    apply_shell_change "Remove Update Icon" \
        "Removes the system update indicator from the bar." \
        "Removed update icon from bar" _update_absent _update_remove
}

restore_update_icon() {
    apply_shell_change "Restore Update Icon" \
        "Puts the system update indicator back on the bar." \
        "Restored update icon to bar" _update_present _update_restore
}

enable_rounded_corners() {
    apply_lua_block "Enable Rounded Corners" \
        "Rounds windows (rounding = 8). The Omarchy shell (bar, menus, notifications, OSD, lock screen) follows Hyprland's rounding automatically." \
        "$LOOKNFEEL_LUA" "rounded-corners" "Enabled rounded corners" \
        'hl.config({ decoration = { rounding = 8 } })'
}

disable_rounded_corners() {
    apply_lua_block "Disable Rounded Corners" \
        "Returns to Omarchy's square corners for windows and the shell UI." \
        "$LOOKNFEEL_LUA" "rounded-corners" "Disabled rounded corners"
}

remove_window_gaps() {
    apply_lua_block "Remove Window Gaps" \
        "Removes gaps and borders between tiled windows (gaps_in, gaps_out and border_size = 0)." \
        "$LOOKNFEEL_LUA" "window-gaps" "Removed window gaps" \
        'hl.config({ general = { gaps_in = 0, gaps_out = 0, border_size = 0 } })'
}

restore_window_gaps() {
    apply_lua_block "Restore Window Gaps" \
        "Restores Omarchy's default gaps and borders between windows." \
        "$LOOKNFEEL_LUA" "window-gaps" "Restored window gaps"
}

remove_transparency() {
    apply_lua_block "Remove Transparency" \
        "Makes all windows fully opaque, overriding Omarchy's default and browser opacity rules." \
        "$LOOKNFEEL_LUA" "transparency" "Removed transparency" \
        'o.window(".*", { opacity = "1.0 override 1.0 override" })'
}

restore_transparency() {
    apply_lua_block "Restore Transparency" \
        "Restores Omarchy's default window opacity." \
        "$LOOKNFEEL_LUA" "transparency" "Restored transparency"
}



_title_present() { shell_bar_has omarchy.active-window; }
_title_add() { omarchy-bar put omarchy.active-window --after omarchy.workspaces &>/dev/null; }
_title_absent() { ! shell_bar_has omarchy.active-window; }
_title_remove() { shell_bar_remove omarchy.active-window; }

show_window_title() {
    apply_shell_change "Show Window Title" \
        "Shows the focused window's title on the bar next to the workspaces." \
        "Window title shown on bar" _title_present _title_add
}

hide_window_title() {
    apply_shell_change "Hide Window Title" \
        "Removes the focused window's title from the bar." \
        "Window title hidden from bar" _title_absent _title_remove
}

# Clock format as stored in shell.json (Qt date format), with the widget default
clock_format() {
    local f
    f=$(shell_bar_get omarchy.clock format)
    echo "${f:-dddd HH:mm}"
}

_clock_set_format() { omarchy-bar set omarchy.clock format "$1" &>/dev/null; }

_date_shown() { [[ "$(clock_format)" == *dddd* ]]; }
_date_show() { _clock_set_format "dddd $(clock_format)"; }
_date_hidden() { [[ "$(clock_format)" != *dddd* ]]; }
_date_hide() { local f; f="$(clock_format)"; f="${f//dddd /}"; _clock_set_format "${f//dddd/}"; }

show_clock_date() {
    apply_shell_change "Show Clock Day" \
        "Shows the day name on the bar clock (e.g. Monday 14:30)." \
        "Clock day name shown" _date_shown _date_show
}

hide_clock_date() {
    apply_shell_change "Hide Clock Day" \
        "Hides the day name on the bar clock (e.g. 14:30)." \
        "Clock day name hidden" _date_hidden _date_hide
}

_clock_is_12h() { [[ "$(clock_format)" == *AP* || "$(clock_format)" == *ap* ]]; }
_clock_to_12h() { local f; f="$(clock_format)"; _clock_set_format "${f/HH:mm/h:mm AP}"; }
_clock_is_24h() { ! _clock_is_12h; }
_clock_to_24h() { local f; f="$(clock_format)"; f="${f/h:mm AP/HH:mm}"; _clock_set_format "${f/h:mm ap/HH:mm}"; }

enable_12h_clock() {
    apply_shell_change "12-Hour Clock" \
        "Shows the bar clock in 12-hour format (e.g. 2:30 PM)." \
        "Clock set to 12-hour" _clock_is_12h _clock_to_12h
}

disable_12h_clock() {
    apply_shell_change "24-Hour Clock" \
        "Shows the bar clock in 24-hour format (e.g. 14:30)." \
        "Clock set to 24-hour" _clock_is_24h _clock_to_24h
}

MEDIA_DIRS_MARKER_START="# >>> a-la-carchy media-dirs"
MEDIA_DIRS_MARKER_END="# <<< a-la-carchy media-dirs"

_media_dirs_remove() {
    [[ -f "$UWSM_DEFAULT" ]] || return 0
    awk -v s="$MEDIA_DIRS_MARKER_START" -v e="$MEDIA_DIRS_MARKER_END" '
        $0 == s { skip = 1; next }
        $0 == e { skip = 0; next }
        !skip { print }
    ' "$UWSM_DEFAULT" > "${UWSM_DEFAULT}.tmp" && mv "${UWSM_DEFAULT}.tmp" "$UWSM_DEFAULT"
}

enable_media_directories() {
    clear
    echo
    echo
    echo -e "${BOLD}  Enable Media Directories${RESET}"
    echo
    echo -e "  ${DIM}Saves screenshots to ~/Pictures/Screenshots and recordings to ~/Videos/Screencasts.${RESET}"
    echo -e "  ${DIM}Written to ~/.config/uwsm/default; takes effect after the next login.${RESET}"
    echo
    echo

    if [[ -f "$UWSM_DEFAULT" ]] && grep -qxF "$MEDIA_DIRS_MARKER_START" "$UWSM_DEFAULT"; then
        echo -e "  ${DIM}Already enabled. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  Enable media directories -- already enabled")
        return 0
    fi

    confirm_continue "Enable media directories" || return 0

    mkdir -p "$HOME/Pictures/Screenshots" "$HOME/Videos/Screencasts" "$(dirname "$UWSM_DEFAULT")"
    [[ -f "$UWSM_DEFAULT" ]] && backup_file "$UWSM_DEFAULT"
    {
        echo "$MEDIA_DIRS_MARKER_START"
        echo 'export OMARCHY_SCREENSHOT_DIR="$HOME/Pictures/Screenshots"'
        echo 'export OMARCHY_SCREENRECORD_DIR="$HOME/Videos/Screencasts"'
        echo "$MEDIA_DIRS_MARKER_END"
    } >> "$UWSM_DEFAULT"

    echo -e "  ${CHECKED}✓${RESET}  Media directories enabled (log out and back in to apply)"
    SUMMARY_LOG+=("✓  Enabled screenshot/recording directories")
    echo
}

disable_media_directories() {
    clear
    echo
    echo
    echo -e "${BOLD}  Disable Media Directories${RESET}"
    echo
    echo -e "  ${DIM}Screenshots and recordings go back to ~/Pictures and ~/Videos (Omarchy default).${RESET}"
    echo
    echo

    if [[ ! -f "$UWSM_DEFAULT" ]] || ! grep -qxF "$MEDIA_DIRS_MARKER_START" "$UWSM_DEFAULT"; then
        echo -e "  ${DIM}Not enabled. Nothing to do.${RESET}"
        echo
        SUMMARY_LOG+=("--  Disable media directories -- not enabled")
        return 0
    fi

    confirm_continue "Disable media directories" || return 0

    backup_file "$UWSM_DEFAULT"
    _media_dirs_remove
    # The override file is optional; drop it if only our block was in it
    [[ -s "$UWSM_DEFAULT" ]] || rm -f "$UWSM_DEFAULT"

    echo -e "  ${CHECKED}✓${RESET}  Media directories disabled (log out and back in to apply)"
    SUMMARY_LOG+=("✓  Disabled screenshot/recording directories")
    echo
}

# Command the Omarchy menu entry runs: this checkout when run from a file,
# otherwise the published script
alc_launch_command() {
    local self
    self="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null)"
    if [[ -f "$self" && "$self" != /dev/* && "$self" != /proc/* ]]; then
        echo "omarchy-launch-tui --app-id=org.omarchy.a-la-carchy $self"
    else
        echo "omarchy-launch-tui --app-id=org.omarchy.a-la-carchy bash -c 'bash <(curl -fsSL $ALC_SCRIPT_URL)'"
    fi
}


_menu_entry_remove() {
    [[ -f "$MENU_JSONC" ]] || return 0
    awk -v s="$MENU_MARKER_START" -v e="$MENU_MARKER_END" '
        $0 == s { skip = 1; next }
        $0 == e { skip = 0; next }
        !skip { print }
    ' "$MENU_JSONC" > "${MENU_JSONC}.tmp" && mv "${MENU_JSONC}.tmp" "$MENU_JSONC"
}

add_to_omarchy_menu() {
    echo
    echo -e "  ${BOLD}Adding A La Carchy to Omarchy menu...${RESET}"

    mkdir -p "$(dirname "$MENU_JSONC")"
    [[ -f "$MENU_JSONC" ]] || printf '{\n}\n' > "$MENU_JSONC"
    backup_file "$MENU_JSONC"
    _menu_entry_remove

    # Root-level entry, inserted right after the opening brace so it is always
    # followed by valid JSONC (the menu parser drops trailing commas)
    local action entry
    action="$(alc_launch_command)"
    entry=$(jq -cn --arg action "$action" \
        '{icon: "󰒓", label: "A La Carchy", aliases: ["carchy"], description: "Omarchy debloater and optimizer", action: $action}')

    ALC_LINE="  \"alacarchy\": $entry," awk -v s="$MENU_MARKER_START" -v e="$MENU_MARKER_END" '
        !done && /^[[:space:]]*\{[[:space:]]*$/ { print; print s; print ENVIRON["ALC_LINE"]; print e; done = 1; next }
        { print }
    ' "$MENU_JSONC" > "${MENU_JSONC}.tmp" && mv "${MENU_JSONC}.tmp" "$MENU_JSONC"

    if grep -qxF -- "$MENU_MARKER_START" "$MENU_JSONC"; then
        omarchy-menu refresh &>/dev/null || true
        echo -e "  ${CHECKED}✓${RESET}  A La Carchy added to the Omarchy menu"
        SUMMARY_LOG+=("✓  Add menu shortcut -- added A La Carchy to Omarchy menu")
    else
        echo -e "  ${DIM}✗${RESET}  Could not find the menu's opening brace in $MENU_JSONC"
        SUMMARY_LOG+=("✗  Add menu shortcut -- could not edit $MENU_JSONC")
        return 1
    fi
    echo
}

remove_from_omarchy_menu() {
    echo
    echo -e "  ${BOLD}Removing A La Carchy from Omarchy menu...${RESET}"

    if [[ ! -f "$MENU_JSONC" ]] || ! grep -qxF -- "$MENU_MARKER_START" "$MENU_JSONC"; then
        echo -e "  ${DIM}Not in the menu. Nothing to do.${RESET}"
        SUMMARY_LOG+=("--  Remove menu shortcut -- not found")
        return 0
    fi

    backup_file "$MENU_JSONC"
    _menu_entry_remove
    omarchy-menu refresh &>/dev/null || true

    echo -e "  ${CHECKED}✓${RESET}  A La Carchy removed from the Omarchy menu"
    SUMMARY_LOG+=("✓  Remove menu shortcut -- removed A La Carchy from Omarchy menu")
    echo
}

# =============================================================================
# THEMARCHY — WALLPAPER COLOR THEME GENERATION
# =============================================================================

THEMARCHY_SCRIPT="$HOME/.config/hypr/scripts/themarchy.sh"
APPLY_THEMARCHY=false

write_themarchy_script() {
    mkdir -p "$(dirname "$THEMARCHY_SCRIPT")"
    cat > "$THEMARCHY_SCRIPT" << 'THEMARCHY_EOF'
#!/bin/bash
# Themarchy — Generate and apply Omarchy theme from current wallpaper colors

THEMARCHY_DIR="$HOME/.config/omarchy/themes/themarchy"

WALLPAPER=$(readlink -f "$HOME/.local/state/omarchy/current/background" 2>/dev/null)
if [[ -z "$WALLPAPER" || ! -f "$WALLPAPER" ]]; then
    WALLPAPER=$(pgrep -a swaybg 2>/dev/null | grep -oP '(?<=-i )\S+' | head -1)
fi
if [[ -z "$WALLPAPER" || ! -f "$WALLPAPER" ]]; then
    notify-send "Themarchy" "Could not detect current wallpaper" -u critical 2>/dev/null
    exit 1
fi

if ! command -v wal &>/dev/null; then
    notify-send "Themarchy" "pywal required (wal not found — install python-pywal)" -u critical 2>/dev/null
    exit 1
fi

mkdir -p "$THEMARCHY_DIR"

if ! wal -i "$WALLPAPER" -n -q 2>/dev/null; then
    notify-send "Themarchy" "pywal failed to generate palette" -u critical 2>/dev/null
    exit 1
fi

python3 << 'PYEOF' > "$THEMARCHY_DIR/colors.toml"
import json, sys, os

wal_json = os.path.expanduser("~/.cache/wal/colors.json")
try:
    with open(wal_json) as f:
        data = json.load(f)
except (OSError, json.JSONDecodeError):
    sys.exit(1)

special = data.get("special", {})
colors  = data.get("colors", {})

bg     = special.get("background", colors.get("color0", "#000000"))
fg     = special.get("foreground", colors.get("color7", "#ffffff"))
cursor = special.get("cursor", fg)
accent = colors.get("color5", colors.get("color1", fg))
sel_bg = accent
sel_fg = colors.get("color0", bg)

print(f'accent = "{accent}"')
print(f'cursor = "{cursor}"')
print(f'foreground = "{fg}"')
print(f'background = "{bg}"')
print(f'selection_foreground = "{sel_fg}"')
print(f'selection_background = "{sel_bg}"')
print()
for i in range(16):
    key = f"color{i}"
    val = colors.get(key, "#000000")
    print(f'{key} = "{val}"')
PYEOF

if [[ $? -ne 0 || ! -s "$THEMARCHY_DIR/colors.toml" ]]; then
    notify-send "Themarchy" "Failed to generate theme palette" -u critical 2>/dev/null
    exit 1
fi

# Generate themed fastfetch config from wallpaper palette
python3 << 'FASTFETCH_EOF'
import os, re, json

colors_file = os.path.expanduser("~/.config/omarchy/themes/themarchy/colors.toml")
colors = {}
try:
    with open(colors_file) as f:
        for line in f:
            m = re.match(r'^(\w+)\s*=\s*"(#[0-9a-fA-F]{6})"', line)
            if m:
                colors[m.group(1)] = m.group(2)
except OSError:
    pass

if not colors:
    exit(0)

accent  = colors.get('accent', None)
hw      = colors.get('color2', None)
sw      = colors.get('color4', None)
sys_col = colors.get('color5', None)

if not all([accent, hw, sw, sys_col]):
    exit(0)

config_path = os.path.expanduser("~/.config/fastfetch/config.jsonc")
try:
    with open(config_path) as f:
        config = json.loads(f.read())
except (OSError, json.JSONDecodeError):
    exit(0)

# Update logo accent color
if "logo" in config and isinstance(config["logo"].get("color"), dict):
    config["logo"]["color"]["1"] = accent

# Update keyColors by section: scan for custom separator headers to determine
# which color role to apply to the modules that follow.
section_colors = {"hardware": hw, "software": sw, "system": sys_col}
current_section = None

for module in config.get("modules", []):
    if not isinstance(module, dict):
        continue
    if module.get("type") == "custom":
        fmt = module.get("format", "")
        if "Hardware" in fmt:
            current_section = "hardware"
        elif "Software" in fmt:
            current_section = "software"
        elif "Age" in fmt:
            current_section = "system"
        continue
    if current_section and "keyColor" in module:
        module["keyColor"] = section_colors[current_section]

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
    f.write('\n')
FASTFETCH_EOF


# Copy the current wallpaper into the themarchy theme BEFORE the theme swap,
# so omarchy-theme-bg-next finds it after current/theme is replaced.
WALLPAPER_REAL=$(readlink -f "$HOME/.local/state/omarchy/current/background" 2>/dev/null)
[[ -z "$WALLPAPER_REAL" || ! -f "$WALLPAPER_REAL" ]] && WALLPAPER_REAL="$WALLPAPER"
if [[ -n "$WALLPAPER_REAL" && -f "$WALLPAPER_REAL" ]]; then
    WALLPAPER_EXT="${WALLPAPER_REAL##*.}"
    mkdir -p "$THEMARCHY_DIR/backgrounds"
    rm -f "$THEMARCHY_DIR/backgrounds/"*
    cp "$WALLPAPER_REAL" "$THEMARCHY_DIR/backgrounds/0-wallpaper.$WALLPAPER_EXT"
fi

if omarchy-theme-set themarchy; then
    notify-send "Themarchy" "Theme applied from wallpaper!" 2>/dev/null
    exit 0
else
    notify-send "Themarchy" "Failed to apply theme" -u critical 2>/dev/null
    exit 1
fi
THEMARCHY_EOF
    chmod +x "$THEMARCHY_SCRIPT"
}

ensure_pywal_installed() {
    command -v wal &>/dev/null && return 0

    echo -e "  Themarchy requires ${BOLD}python-pywal${RESET}${DIM}, which is not installed.${RESET}"
    echo

    local aur_helper=""
    if command -v yay &>/dev/null; then
        aur_helper="yay"
    elif command -v paru &>/dev/null; then
        aur_helper="paru"
    fi

    if [[ -z "$aur_helper" ]]; then
        echo -e "  ${DIM}✗${RESET}  No AUR helper found (yay or paru required)"
        echo -e "  ${DIM}    Install manually: yay -S python-pywal${RESET}"
        echo
        return 1
    fi

    printf "  ${BOLD}Install python-pywal via $aur_helper?${RESET} ${DIM}(yes/no)${RESET} "
    read -r < /dev/tty
    echo

    if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo -e "  Cancelled."
        echo
        return 1
    fi

    echo -e "  ${DIM}Installing python-pywal...${RESET}"
    echo
    if "$aur_helper" -S --noconfirm python-pywal; then
        echo
        echo -e "  ${CHECKED}✓${RESET}  python-pywal installed"
        echo
        return 0
    else
        echo
        echo -e "  ${DIM}✗${RESET}  Installation failed"
        echo
        return 1
    fi
}

show_themarchy_preview_dialog() {
    clear
    echo
    echo
    echo -e "${BOLD}  Themarchy — Theme from Wallpaper${RESET}"
    echo

    local wallpaper
    wallpaper=$(readlink -f "$HOME/.local/state/omarchy/current/background" 2>/dev/null)
    if [[ -z "$wallpaper" || ! -f "$wallpaper" ]]; then
        wallpaper=$(pgrep -a swaybg 2>/dev/null | grep -oP '(?<=-i )\S+' | head -1)
    fi

    if [[ -z "$wallpaper" || ! -f "$wallpaper" ]]; then
        echo -e "  ${DIM}✗${RESET}  Could not detect current wallpaper"
        echo
        printf "  Press any key to continue..."
        read -r -n1 < /dev/tty
        return 1
    fi

    if ! ensure_pywal_installed; then
        printf "  Press any key to continue..."
        read -r -n1 < /dev/tty
        return 1
    fi

    echo -e "  ${DIM}Wallpaper: ${wallpaper##*/}${RESET}"
    echo
    printf "  ${DIM}Scanning colors...${RESET}"

    local colors
    if ! wal -i "$wallpaper" -n -q 2>/dev/null; then
        echo
        echo -e "  ${DIM}✗${RESET}  pywal failed to extract colors"
        echo
        printf "  Press any key to continue..."
        read -r -n1 < /dev/tty
        return 1
    fi
    colors=$(cat "$HOME/.cache/wal/colors" 2>/dev/null)

    if [[ -z "$colors" ]]; then
        echo
        echo -e "  ${DIM}✗${RESET}  Failed to read pywal palette"
        echo
        printf "  Press any key to continue..."
        read -r -n1 < /dev/tty
        return 1
    fi

    printf "\r  ${DIM}Palette preview:${RESET}              \n"
    echo
    printf "  "
    while IFS= read -r hex; do
        [[ "$hex" =~ ^#[0-9a-fA-F]{6}$ ]] || continue
        local r=$((16#${hex:1:2})) g=$((16#${hex:3:2})) b=$((16#${hex:5:2}))
        printf "\e[48;2;%d;%d;%dm    \e[0m" "$r" "$g" "$b"
    done <<< "$colors"
    echo
    echo
    echo -e "  ${DIM}Generates a full system theme (terminals, bar, menus, notifications,${RESET}"
    echo -e "  ${DIM}Hyprland borders) tuned to these colors. Previous theme survives${RESET}"
    echo -e "  ${DIM}in Extra Themes and can be re-applied at any time.${RESET}"
    echo
    echo

    printf "  ${BOLD}Apply theme from wallpaper?${RESET} ${DIM}(yes/no)${RESET} "
    read -r < /dev/tty

    if [[ $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        APPLY_THEMARCHY=true
    fi
}

apply_themarchy_now() {
    clear
    echo
    echo
    echo -e "${BOLD}  Themarchy — Applying Theme from Wallpaper${RESET}"
    echo

    write_themarchy_script

    echo -e "  ${DIM}Extracting colors and generating theme...${RESET}"
    echo

    if bash "$THEMARCHY_SCRIPT"; then
        echo
        echo -e "  ${CHECKED}✓${RESET}  Theme applied from wallpaper"
        SUMMARY_LOG+=("✓  Themarchy -- applied theme from wallpaper")
    else
        echo -e "  ${DIM}✗${RESET}  Theme application failed"
        SUMMARY_LOG+=("✗  Themarchy -- failed to apply theme")
    fi
    echo
}

setup_themarchy_keybind() {
    if ! ensure_pywal_installed; then
        SUMMARY_LOG+=("✗  Themarchy keybind -- failed (python-pywal not installed)")
        return 1
    fi

    # Always redeploy so the bound script is current
    write_themarchy_script

    apply_lua_block "Enable Themarchy Keybind (SUPER+SHIFT+T)" \
        "Binds SUPER+SHIFT+T to apply a theme generated from the current wallpaper ($THEMARCHY_SCRIPT)." \
        "$BINDINGS_LUA" "themarchy" "Themarchy keybind -- bound SUPER+SHIFT+T" \
        "hl.unbind(\"SUPER + SHIFT + T\")
o.bind(\"SUPER + SHIFT + T\", \"Themarchy\", \"$THEMARCHY_SCRIPT\")"
}

remove_themarchy_keybind() {
    apply_lua_block "Disable Themarchy Keybind" \
        "Removes the SUPER+SHIFT+T Themarchy keybinding." \
        "$BINDINGS_LUA" "themarchy" "Themarchy keybind -- removed"
}

# =============================================================================
# TWO-PANEL TUI DATA STRUCTURES
# =============================================================================

# Categories for left panel (lines starting with "---" are section headers)
declare -a CATEGORIES=(
    "--- Uninstall ---"
    "  Apps"
    "  Web Apps"
    "--- Tweaks ---"
    "  Keybindings"
    "  Keybind Editor"
    "  Display"
    "  System"
    "  Appearance"
    "  Keyboard"
    "  Utilities"
    "--- Hyprland ---"
    "  General"
    "  Decoration"
    "  Input"
    "  Gestures"
    "--- ROG ---"
    "  Hardware"
    "  Lighting"
    "--- Install ---"
    "  Extra Themes"
    "--- Themarchy ---"
    "  Themarchy"
)

# Check if a category index is a section header
is_section_header() {
    [[ "${CATEGORIES[$1]}" == ---* ]]
}

# Build installed packages list
declare -a INSTALLED_PACKAGES=()
for pkg in "${DEFAULT_APPS[@]}"; do
    if is_package_installed "$pkg"; then
        INSTALLED_PACKAGES+=("$pkg")
    fi
done

# Build installed webapps list
declare -a INSTALLED_WEBAPPS=()
for webapp in "${DEFAULT_WEBAPPS[@]}"; do
    if is_webapp_installed "$webapp"; then
        INSTALLED_WEBAPPS+=("$webapp")
    fi
done

# Toggle pairs: each entry is "item_id|display_name|option1|option2|type"
# type: "toggle" for enable/disable pairs, "radio" for mutually exclusive options
# Format: "id|name|opt1|opt2|type|description"
declare -a KEYBINDINGS_ITEMS=(
    "close_window|Close window|SUPER+Q|SUPER+W|radio|Choose which key combo closes the active window"
    "shutdown|Shutdown|Bind|Unbind|toggle|Bind SUPER+ALT+S to shutdown the system"
    "restart|Restart|Bind|Unbind|toggle|Bind SUPER+ALT+R to restart the system"
    "theme_menu|Theme menu|Bind|Unbind|toggle|Bind ALT+T to open the theme selector"
)

declare -a DISPLAY_ITEMS=(
    "monitor_scale|Monitor scale|4K|1080p/1440p|radio|Set monitor scaling for your resolution"
    "detect_monitors|Detect monitors|[Open]||action|Detect connected displays and identify them"
    "position_monitors|Position monitors|[Open]||action|Arrange monitor positions relative to each other"
    "laptop_display|Laptop display|Auto off|Normal|toggle|Auto-disable laptop screen when external display connected"
    "primary_monitor|Primary monitor|[Open]||action|Set which monitor gets workspace 1 by default"
)

declare -a SYSTEM_ITEMS=(
    "suspend|Suspend|Enable|Disable|toggle|Allow system to suspend/sleep when idle"
    "hibernation|Hibernation|Enable|Disable|toggle|Allow system to hibernate to disk"
    "fingerprint|Fingerprint|Enable|Disable|toggle|Enable fingerprint authentication for login"
    "fido2|FIDO2|Enable|Disable|toggle|Enable FIDO2 security key authentication"
    "power_profile|Power profile|[Open]||action|Set default power profile for startup"
    "power_auto_switch|Auto-switch profiles|[Open]||action|Configure profiles used when AC power is connected or disconnected"
    "battery_limit|Battery limit|[Open]||action|Set maximum battery charge level for longer lifespan"
)

declare -a APPEARANCE_ITEMS=(
    "rounded_corners|Rounded corners|Enable|Disable|toggle|Round or square corners on windows, menus, notifications, and UI"
    "window_gaps|Window gaps|Remove|Restore|toggle|Remove or restore gaps between tiled windows"
    "transparency|Transparency|Remove|Restore|toggle|Remove or restore window transparency effects"
    "tray_icons|Tray icons|Show all|Hide|toggle|Pin every tray icon to the bar, or collapse them into the tray drawer"
    "omarchy_logo|Omarchy logo|Remove|Restore|toggle|Remove or restore the Omarchy logo menu button on the bar"
    "update_icon|Update icon|Remove|Restore|toggle|Remove or restore the system update icon on the bar"
    "clock_format|Clock format|12h|24h|radio|Set the bar clock to 12-hour or 24-hour format"
    "clock_date|Clock date|Show|Hide|toggle|Show or hide the day name on the bar clock"
    "window_title|Window title|Show|Hide|toggle|Show active window title on the bar next to workspaces"
    "media_dirs|Media dirs|Enable|Disable|toggle|Organize screenshots and recordings into subdirs"
)

declare -a KEYBOARD_ITEMS=(
    "caps_lock|Caps Lock|Normal|Compose|radio|Use Caps Lock normally or as Compose key"
    "alt_super|Alt/Super|Swap|Normal|radio|Swap Alt and Super keys (useful for Mac keyboards)"
)

declare -a UTILITIES_ITEMS=(
    "backup_config|Backup config|[Select]||action|Create a backup of your Omarchy configuration"
    "menu_shortcut|Menu shortcut|Add|Remove|toggle|Add A La Carchy to the Omarchy menu"
)

declare -a ROG_HARDWARE_ITEMS=(
    "rog_profile|Platform profile|[Open]||action|Set ASUS performance profile (Quiet/Balanced/Performance)"
    "rog_fan_curves|Fan curves|Enable|Disable|toggle|Enable custom fan curves for the active profile"
    "rog_fan_curve_edit|Fan curve editor|[Open]||action|Edit custom fan curve data points per fan"
    "rog_boot_sound|Boot sound|Enable|Disable|toggle|Play the POST boot sound on startup"
    "rog_panel_od|Panel overdrive|Enable|Disable|toggle|Reduce display ghosting with panel overdrive"
    "rog_dgpu|Discrete GPU|Enable|Disable|toggle|Enable or disable the dedicated NVIDIA GPU"
    "rog_gpu_mux|GPU MUX|dGPU|Hybrid|radio|Direct display to dGPU or hybrid mode (reboot required)"
    "rog_battery|Battery management|[Open]||action|Set charge limit and one-shot full charge"
    "rog_power_tuning|Power tuning|[Open]||action|Adjust CPU/GPU power and thermal limits"
)

declare -a ROG_LIGHTING_ITEMS=(
    "rog_kbd_leds|Keyboard LEDs|[Open]||action|Set keyboard backlight brightness (off/low/med/high)"
    "rog_aura|Aura RGB effect|[Open]||action|Set keyboard RGB lighting effect and color"
    "rog_aura_power|Aura power zones|[Open]||action|Control LED zones for different power states"
    "rog_slash|Slash Ledbar|[Open]||action|Configure the Slash LED bar animations"
    "rog_slash_extra|Slash options|[Open]||action|Set interval and conditional show settings"
    "rog_anime|AniMe Matrix|Enable|Disable|toggle|Enable or disable the AniMe Matrix display"
    "rog_anime_extra|AniMe options|[Open]||action|Brightness, powersave, and builtin animations"
)

declare -a THEMARCHY_ITEMS=(
    "themarchy_apply|Apply from wallpaper|[Apply]||action|Extract colors from current wallpaper and apply as system theme"
    "themarchy_keybind|Keybind SUPER+SHIFT+T|Enable|Disable|toggle|Bind SUPER+SHIFT+T to instantly retheme from wallpaper"
)

# Extra themes: each entry is "Display Name|github_url"
declare -a EXTRA_THEMES=(
    "Aamis|https://github.com/vyrx-dev/omarchy-aamis-theme"
    "Ado|https://github.com/errantProgrammer/omarchy-ado-theme"
    "Adrift|https://github.com/jaredb1011/omarchy-adrift-theme"
    "Aetheria|https://github.com/JJDizz1L/aetheria"
    "Agentuity|https://github.com/rblalock/omarchy-agentuity.theme"
    "Akaito|https://github.com/stannorbvb-cmd/akaito"
    "Akane|https://github.com/Grenish/omarchy-akane-theme"
    "All Hallow's Eve|https://github.com/guilhermetk/omarchy-all-hallows-eve-theme"
    "Amberbyte|https://github.com/tahfizhabib/omarchy-amberbyte-theme"
    "Amekoji|https://github.com/atif-1402/omarchy-amekoji-theme"
    "Anonymous|https://github.com/j4v3l/omarchy-anonymous-theme"
    "Apocalypse|https://github.com/atif-1402/omarchy-apocalypse-theme"
    "Arc Blueberry|https://github.com/vale-c/omarchy-arc-blueberry"
    "Arc Raiders|https://github.com/rondilley/omarchy-arc_raiders-theme"
    "ArchRiot|https://github.com/CyphrRiot/omarchy-archriot-theme"
    "Archwave|https://github.com/davidguttman/archwave"
    "Artzen|https://github.com/tahfizhabib/omarchy-artzen-theme"
    "Ash|https://github.com/bjarneo/omarchy-ash-theme"
    "Astrochy|https://github.com/Nanjiifr/omarchy-astrochy-theme"
    "Astrodark|https://github.com/JamsMendez/omarchy-astrodark-theme"
    "Atari|https://github.com/atif-1402/omarchy-atari-theme"
    "Atelier|https://github.com/atif-1402/omarchy-atelier-theme"
    "Aura|https://github.com/bjarneo/omarchy-aura-theme"
    "Aureth|https://github.com/atif-1402/omarchy-aureth-theme"
    "Ayaka|https://github.com/abhijeet-swami/omarchy-ayaka-theme"
    "Ayu Dark|https://github.com/fdidron/omarchy-ayu-dark-theme"
    "Ayu Light|https://github.com/fdidron/omarchy-ayu-light-theme"
    "Ayu Mirage|https://github.com/fdidron/omarchy-ayu-mirage-theme"
    "Azure|https://github.com/tahfizhabib/omarchy-azure-theme"
    "Azure Glow|https://github.com/Hydradevx/omarchy-azure-glow-theme"
    "Bad Hand|https://github.com/bjornramberg/omarchy-bad-hand-theme"
    "Batman|https://github.com/OldJobobo/omarchy-batman-theme"
    "Batou|https://github.com/HANCORE-linux/omarchy-batou-theme"
    "Bauhaus|https://github.com/somerocketeer/omarchy-bauhaus-theme"
    "BeachVan|https://github.com/haripako/omarchy-BeachVan-theme"
    "Beta|https://github.com/jjdizz1l/beta"
    "Biscuit de Mar Dark|https://github.com/OldJobobo/omarchy-biscuit-de-mar-dark-theme"
    "Black Arch|https://github.com/ankur311sudo/black_arch"
    "Black Gold|https://github.com/HANCORE-linux/omarchy-blackgold-theme"
    "Black Money|https://github.com/HANCORE-linux/omarchy-blackmoney-theme"
    "Black Turq|https://github.com/HANCORE-linux/omarchy-blackturq-theme"
    "Blackwall|https://github.com/rlind3r/omarchy-blackwall-theme"
    "Blue Ridge Dark|https://github.com/hipsterusername/omarchy-blueridge-dark-theme"
    "bluedotrb|https://github.com/dotsilva/omarchy-bluedotrb-theme"
    "Boring|https://github.com/geohot/omarchy-boring-theme"
    "Brutalism|https://github.com/bjornramberg/omarchy-brutalism-theme"
    "C64|https://github.com/scar45/omarchy-c64-theme"
    "Caroline Skyline|https://github.com/OldJobobo/omarchy-caroline-skyline-theme"
    "Catppuccin Mocha|https://github.com/KidDogDad/omarchy-catppuccin-mocha-theme"
    "Catppuccin Mocha Dark|https://github.com/Luquatic/omarchy-catppuccin-dark"
    "Cattpuccin Glass|https://github.com/Luquatic/omarchy-catppuccin-glass"
    "Citrus Cynapse|https://github.com/Grey-007/citrus-cynapse"
    "City-783|https://github.com/OldJobobo/omarchy-city-783-theme"
    "Cobalt2|https://github.com/hoblin/omarchy-cobalt2-theme"
    "Colored Darkness|https://github.com/Palccod/colored-darkness"
    "Copper Night|https://github.com/hembramnishant50-glitch/omarchy-coppernight-theme"
    "Corporate|https://github.com/defer/omarchy-corporate-theme"
    "Covenant|https://github.com/dotsilva/omarchy-covenant-theme"
    "CpUnk|https://github.com/stannorbvb-cmd/cpunk"
    "Crimson|https://github.com/tahfizhabib/omarchy-crimson-theme"
    "Crimson Gold|https://github.com/knappkevin/omarchy-crimson-gold-theme"
    "Cyberpunk Cyan|https://github.com/Matcraft94/cyberpunk-cyan"
    "Darcula|https://github.com/noahljungberg/omarchy-darcula-theme"
    "Dark XP|https://github.com/ITSZXY/dark-xp-omarchy"
    "Dayfox|https://github.com/defer/omarchy-dayfox-theme"
    "Deadspace|https://github.com/atif-1402/omarchy-deadspace-theme"
    "Deckard|https://github.com/OldJobobo/omarchy-deckard-theme"
    "DeLorean|https://github.com/jbnunn/omarchy-delorean-theme"
    "Demon|https://github.com/HANCORE-linux/omarchy-demon-theme"
    "Desert Twilight|https://github.com/avis3nna/desert-twilight"
    "Dotrb|https://github.com/dotsilva/omarchy-dotrb-theme"
    "Drac|https://github.com/ShehabShaef/omarchy-drac-theme"
    "Dracula|https://github.com/catlee/omarchy-dracula-theme"
    "Dracula Official|https://github.com/dracula/omarchy"
    "Dragon|https://github.com/thatmechguy/omarchy-dragon-theme"
    "Dragon Frost|https://github.com/Grey-007/dragon-frost"
    "Dreamwave|https://github.com/RiO7MAKK3R/omarchy-dreamwave-theme"
    "Duskwire|https://github.com/Grey-007/duskwire"
    "Dustyfog|https://github.com/atif-1402/omarchy-dustyfog-theme"
    "Eldritch|https://github.com/eldritch-theme/omarchy"
    "Elysian|https://github.com/bjarneo/omarchy-elysian-theme"
    "Ember n Ash|https://github.com/Hydradevx/omarchy-ember-n-ash-theme"
    "Eva-01|https://github.com/Ludurn/omarchy-eva01-theme"
    "Event Horizon|https://github.com/OldJobobo/omarchy-event-horizon-theme"
    "Everblush|https://github.com/Swarnim114/omarchy-everblush-theme"
    "Evergarden|https://github.com/celsobenedetti/omarchy-evergarden"
    "Farline|https://github.com/atif-1402/omarchy-farline-theme"
    "Felix|https://github.com/TyRichards/omarchy-felix-theme"
    "Fenrir|https://github.com/imbypass/omarchy-fenrir-theme"
    "Fiery Ocean|https://github.com/bjarneo/omarchy-fiery-ocean-theme"
    "Fireside|https://github.com/bjarneo/omarchy-fireside-theme"
    "Firesky|https://github.com/bjarneo/omarchy-firesky-theme"
    "Flat Dracula|https://github.com/OldJobobo/omarchy-flat-dracula-theme"
    "Flexoki Dark|https://github.com/euandeas/omarchy-flexoki-dark-theme"
    "Florida Man|https://github.com/OldJobobo/omarchy-florida-man-theme"
    "Fluid Glass|https://github.com/ripple0328/omarchy-fluid-glass-theme"
    "Forest Green|https://github.com/abhijeet-swami/omarchy-forest-green-theme"
    "Frost|https://github.com/bjarneo/omarchy-frost-theme"
    "Futurism|https://github.com/bjarneo/omarchy-futurism-theme"
    "Ghost Pastel|https://github.com/row-huh/omarchy-ghost-pastel-theme"
    "Glory Antic|https://github.com/clement-rtfm/glory-antic"
    "Gold Rush|https://github.com/tahayvr/omarchy-gold-rush-theme"
    "Gotham City|https://github.com/JustArmaan/omarchy-gotham-city-theme"
    "Greek Noir|https://github.com/HANCORE-linux/omarchy-greek-noir-theme"
    "Green City|https://github.com/zillamtt/omarchy-green-city"
    "Green Garden|https://github.com/kalk-ak/omarchy-green-garden-theme"
    "Grimdark Solarized|https://github.com/OldJobobo/omarchy-grimdark-solarized-theme"
    "Gruber Darker|https://github.com/celsobenedetti/omarchy-gruber-darker"
    "Gruber Tsoding|https://github.com/davide-ferrara/omarchy-gruberdark-tsoding-theme"
    "Grudark|https://github.com/zillamtt/omarchy-grudark"
    "Gruvy Glass|https://github.com/signaldirective/gruvy-glass"
    "GTA|https://github.com/jordan-ops/omarchy-GTA-theme"
    "Hakkar Green|https://github.com/JonasAllenCodes/omarchy-hakkar-green-better-contrast-theme"
    "Harbor|https://github.com/HANCORE-linux/omarchy-harbor-theme"
    "Harbor Dark|https://github.com/HANCORE-linux/omarchy-harbordark-theme"
    "Hex|https://github.com/OldJobobo/omarchy-hex-theme"
    "Himalaya|https://github.com/rondilley/omarchy-himalaya-theme"
    "Hinterlands|https://github.com/OldJobobo/omarchy-hinterlands-theme"
    "Hollow Knight|https://github.com/bjarneo/omarchy-hollow-knight-theme"
    "Hydra Pressure|https://github.com/monoooki/omarchy-hydra-pressure-theme"
    "HyprBlue|https://github.com/Grey-007/hyprblue"
    "IBM|https://github.com/DimaZbr/omarchy-ibm-theme"
    "Infernium|https://github.com/RiO7MAKK3R/omarchy-infernium-dark-theme"
    "Infernium Light|https://github.com/RiO7MAKK3R/omarchy-infernium-theme"
    "InkyPinky|https://github.com/HANCORE-linux/omarchy-inkypinky-theme"
    "Kawasaki Foundry|https://github.com/komagata/omarchy-kawasaki-foundry-theme"
    "Kimiko|https://github.com/krymzonn/omarchy-kimiko-theme"
    "Koda|https://github.com/celsobenedetti/omarchy-koda"
    "Koyanagi|https://github.com/YutaKoyanagi10/omarchy-koyanagi-theme"
    "Krishna|https://github.com/tanishenigma/omarchy-krishna-theme"
    "Kurayami|https://github.com/bjornramberg/omarchy-kurayami-theme"
    "Kurumi|https://github.com/borgox/omarchy-kurumi-theme"
    "lain|https://github.com/ITSZXY/lain-omarchy"
    "Lairetam|https://github.com/chrisintheshell/omarchy-lairetam-theme"
    "Last Horizon|https://github.com/HANCORE-linux/omarchy-lasthorizon-theme"
    "Latch|https://github.com/atif-1402/omarchy-latch-theme"
    "Latchdark|https://github.com/atif-1402/omarchy-latchdark-theme"
    "Lilly|https://github.com/JJDizz1L/lilly"
    "Lowlight|https://github.com/atif-1402/omarchy-lowlight-theme"
    "Lumon|https://github.com/OldJobobo/omarchy-lumon-theme"
    "Lunar|https://github.com/pdfosborne/omarchy-lunar-theme"
    "Mac Transparent|https://github.com/notzeman/Omarchy-Mac-Transparent-theme"
    "Manga|https://github.com/atif-1402/omarchy-manga-theme"
    "Map Quest|https://github.com/ItsABigIgloo/omarchy-mapquest-theme"
    "Mars|https://github.com/steve-lohmeyer/omarchy-mars-theme"
    "Mechanoonna|https://github.com/HANCORE-linux/omarchy-mechanoonna-theme"
    "Memento Mori|https://github.com/hipsterusername/omarchy-memento-mori-theme"
    "Miasma|https://github.com/OldJobobo/omarchy-miasma-theme"
    "Midnight|https://github.com/JaxonWright/omarchy-midnight-theme"
    "Miles Morales|https://github.com/ahmed-z0/omarchy-miles-morales-theme"
    "Milky Matcha|https://github.com/hipsterusername/omarchy-milkmatcha-light-theme"
    "Monochrome|https://github.com/Swarnim114/omarchy-monochrome-theme"
    "Monokai|https://github.com/bjarneo/omarchy-monokai-theme"
    "Monokai Dark|https://github.com/ericrswanny/omarchy-monokai-dark-theme"
    "Monolith|https://github.com/OldJobobo/omarchy-monolith-theme"
    "Moodpeak|https://github.com/HANCORE-linux/omarchy-moodpeak-theme"
    "Moon Orbit|https://github.com/JJDizz1L/moon-orbit"
    "Motivator|https://github.com/rondilley/omarchy-motivator-theme"
    "Nagai Poolside|https://github.com/somerocketeer/omarchy-nagai-poolside-theme"
    "Nagai Twilight|https://github.com/somerocketeer/omarchy-nagai-twilight-theme"
    "Nebulite|https://github.com/atif-1402/omarchy-nebulite-theme"
    "Neo Sploosh|https://github.com/monoooki/omarchy-neo-sploosh-theme"
    "Neonstreet|https://github.com/atif-1402/omarchy-neonstreet-theme"
    "Neovoid|https://github.com/RiO7MAKK3R/omarchy-neovoid-theme"
    "NES|https://github.com/bjarneo/omarchy-nes-theme"
    "Night Cat|https://github.com/maxberggren/omarchy-night-cat-theme"
    "Night Owl|https://github.com/janhesters/omarchy-night-owl-theme"
    "NYC|https://github.com/WillyV3/omarchy-nyc-theme"
    "Oasis|https://github.com/joaofelipegalvao/omarchy-oasis"
    "Omacarchy|https://github.com/RiO7MAKK3R/omarchy-omacarchy-theme"
    "Omarchy95|https://github.com/atif-1402/omarchy-omarchy95-theme"
    "One Dark Pro|https://github.com/sc0ttman/omarchy-one-dark-pro-theme"
    "Oxford|https://github.com/HANCORE-linux/omarchy-oxford-theme"
    "Oxo Carbon|https://github.com/HANCORE-linux/omarchy-oxocarbon-theme"
    "Pandora|https://github.com/imbypass/omarchy-pandora-theme"
    "PhosphorOS|https://github.com/OldJobobo/omarchy-phosphor-os-theme"
    "Pina|https://github.com/bjarneo/omarchy-pina-theme"
    "Pink Blood|https://github.com/ITSZXY/pink-blood-omarchy-theme"
    "pmndrs|https://github.com/leweyse/omarchy-pmndrs-theme"
    "Pulsar|https://github.com/bjarneo/omarchy-pulsar-theme"
    "Purple Moon|https://github.com/Grey-007/purple-moon"
    "Purplewave|https://github.com/dotsilva/omarchy-purplewave-theme"
    "Rainy Night|https://github.com/atif-1402/omarchy-rainynight-theme"
    "Red Monarch|https://github.com/kamatealif/omarchy-red-monarch-theme"
    "REDDCS|https://github.com/mohamedredachakir/LINUX-OMARCHY-REDDCS"
    "Retro '82|https://github.com/OldJobobo/omarchy-retro-82-theme"
    "Retro Fallout|https://github.com/zdravkodanailov7/omarchy-retro-fallout-theme"
    "RetroPC|https://github.com/rondilley/omarchy-retropc-theme"
    "Reverie|https://github.com/bjarneo/omarchy-reverie-theme"
    "RobCo|https://github.com/signaldirective/robco-theme"
    "RobCo Mojave|https://github.com/signaldirective/robco-mojave"
    "RobZee84|https://github.com/robzolkos/omarchy-robzee84-theme"
    "Rose of Dune|https://github.com/HANCORE-linux/omarchy-roseofdune-theme"
    "Rose Pine Dark|https://github.com/guilhermetk/omarchy-rose-pine-dark"
    "Royal|https://github.com/notSagyo/omarchy-royal-theme"
    "Rustleaf|https://github.com/tahfizhabib/omarchy-rustleaf-theme"
    "Sakura|https://github.com/bjarneo/omarchy-sakura-theme"
    "Sapphire|https://github.com/HANCORE-linux/omarchy-sapphire-theme"
    "SeaShells|https://github.com/odysseyalive/omarchy-seashells-theme"
    "Serenity|https://github.com/bjarneo/omarchy-serenity-theme"
    "Shades of Jade|https://github.com/HANCORE-linux/omarchy-shadesofjade-theme"
    "Shuiro|https://github.com/Grenish/omarchy-shuiro-theme"
    "Snow|https://github.com/bjarneo/omarchy-snow-theme"
    "Snow Black|https://github.com/ankur311sudo/snow_black"
    "Softteal|https://github.com/atif-1402/omarchy-softteal-theme"
    "Soho|https://github.com/bjarneo/omarchy-soho-theme"
    "Solarized|https://github.com/Gazler/omarchy-solarized-theme"
    "Solarized Light|https://github.com/dfrico/omarchy-solarized-light-theme"
    "Solarized Osaka|https://github.com/motorsss/omarchy-solarizedosaka-theme"
    "Solitude|https://github.com/HANCORE-linux/omarchy-solitude-theme"
    "Space Monkey|https://github.com/TyRichards/omarchy-space-monkey-theme"
    "Spark|https://github.com/stefanomainardi/omarchy-sf-theme"
    "Spectra|https://github.com/abhijeet-swami/omarchy-spectra-theme"
    "Spectral Violet|https://github.com/shmall03/omarchy-spectral-violet-theme"
    "Stellar|https://github.com/cicorias/omarchy-stellar-theme"
    "Stillmoon|https://github.com/atif-1402/omarchy-stillmoon-theme"
    "Stillwood|https://github.com/shresth-dwivedi/omarchy-stillwood-theme"
    "Sunkissed|https://github.com/loeclos/omarchy-sunkissed-theme"
    "Sunset|https://github.com/rondilley/omarchy-sunset-theme"
    "Sunset Drive|https://github.com/tahayvr/omarchy-sunset-drive-theme"
    "Super Game Bro|https://github.com/TyRichards/omarchy-super-game-bro-theme"
    "Synthwave '84|https://github.com/omacom-io/omarchy-synthwave84-theme"
    "Taikami|https://github.com/SamuelCam14/taikami"
    "Tarot|https://github.com/jjdizz1l/base16-tarot"
    "Temerald|https://github.com/Ahmad-Mtr/omarchy-temerald-theme"
    "Terramour|https://github.com/atif-1402/omarchy-terramour-theme"
    "The Greek|https://github.com/HANCORE-linux/omarchy-thegreek-theme"
    "Tokyo Night OLED|https://github.com/Justin-De-Sio/omarchy-tokyoled-theme"
    "Tycho|https://github.com/leonardobetti/omarchy-tycho"
    "Type17|https://github.com/atif-1402/omarchy-type17-theme"
    "Van Gogh|https://github.com/Nirmal314/omarchy-van-gogh-theme"
    "Velocity|https://github.com/perfektnacht/omarchy-velocity-theme"
    "Velvet Night|https://github.com/HANCORE-linux/omarchy-velvetnight-theme"
    "Vengeance|https://github.com/Grey-007/vengeance"
    "Vesper|https://github.com/thmoee/omarchy-vesper-theme"
    "VHS 80|https://github.com/tahayvr/omarchy-vhs80-theme"
    "Vice City|https://github.com/lavarinimoreira/omarchy-vice-city-theme"
    "Void|https://github.com/vyrx-dev/omarchy-void-theme"
    "Vurple|https://github.com/tahfizhabib/omarchy-vurple-theme"
    "Waffle Cat|https://github.com/OldJobobo/omarchy-waffle-cat-theme"
    "Wasteland|https://github.com/perfektnacht/omarchy-wasteland-theme"
    "Waveform Dark|https://github.com/hipsterusername/omarchy-waveform-dark-theme"
    "White Gold|https://github.com/HANCORE-linux/omarchy-whitegold-theme"
    "Windows Dark Mode|https://github.com/OldJobobo/omarchy-windows-dark-mode-theme"
    "X-1632|https://github.com/OldJobobo/omarchy-x-1632-theme"
    "Yuugure|https://github.com/ItsABigIgloo/omarchy-yuugure-theme"
)

# Selection state for themes (by display name): 0=not selected, 1=selected
declare -A THEME_SELECTIONS=()

# Hyprland General settings (written to looknfeel.lua)
declare -a HYPR_GENERAL_ITEMS=(
    "gaps_in|Gap between windows|int:0:100|general|gaps_in|5|looknfeel|Gap size between tiled windows"
    "gaps_out|Gap from edges|int:0:100|general|gaps_out|10|looknfeel|Gap size from screen edges"
    "border_size|Border width|int:0:10|general|border_size|2|looknfeel|Window border thickness in pixels"
    "active_border|Active border color|color|general|col.active_border|rgba(33ccffee) rgba(00ff99ee) 45deg|looknfeel|Border color of focused window"
    "inactive_border|Inactive border color|color|general|col.inactive_border|rgba(595959aa)|looknfeel|Border color of unfocused windows"
    "resize_on_border|Drag-resize borders|bool|general|resize_on_border|false|looknfeel|Allow resizing windows by dragging borders"
    "allow_tearing|Allow screen tearing|bool|general|allow_tearing|false|looknfeel|Allow tearing for reduced input lag"
    "layout|Window layout|enum:dwindle:master|general|layout|dwindle|looknfeel|Tiling layout algorithm"
    "preserve_split|Keep split direction|bool|dwindle|preserve_split|true|looknfeel|Maintain split direction on resize"
    "force_split|Split direction|enum:0:1:2|dwindle|force_split|2|looknfeel|0=follow mouse 1=left/top 2=right/bottom"
    "smart_split|Smart split|bool|dwindle|smart_split|false|looknfeel|Split direction follows cursor position"
    "new_status|New window status|enum:master:slave|master|new_status|master|looknfeel|Where new windows appear in master layout"
    "focus_on_activate|Focus on activation|bool|misc|focus_on_activate|true|looknfeel|Focus windows when they request activation"
    "disable_logo|Disable startup logo|bool|misc|disable_hyprland_logo|true|looknfeel|Hide the Hyprland logo on startup"
    "vrr|Variable refresh rate|enum:0:1:2|misc|vrr|0|looknfeel|0=off 1=on 2=fullscreen only (FreeSync/G-Sync)"
    "new_window_fullscreen|Focus under fullscreen|enum:0:1:2|misc|on_focus_under_fullscreen|2|looknfeel|0=stay behind 1=take over 2=unfullscreen"
    "extend_border_grab|Border grab area|int:0:50|general|extend_border_grab_area|15|looknfeel|Extra pixels for grabbing window borders"
    "middle_click_paste|Middle click paste|bool|misc|middle_click_paste|true|looknfeel|Paste clipboard on middle mouse click"
    "enable_swallow|Window swallowing|bool|misc|enable_swallow|false|looknfeel|Terminal windows absorb spawned child windows"
    "workspace_back_forth|Workspace back-forth|bool|binds|workspace_back_and_forth|false|looknfeel|Same workspace key toggles to previous"
    "allow_ws_cycles|Workspace cycles|bool|binds|allow_workspace_cycles|false|looknfeel|Allow cycling through workspaces with binds"
    "force_zero_scaling|XWayland zero scale|bool|xwayland|force_zero_scaling|false|looknfeel|Fix blurry XWayland apps on scaled displays"
    "key_dpms|Keypress wakes display|bool|misc|key_press_enables_dpms|true|looknfeel|Keypress wakes display from DPMS off"
    "mouse_dpms|Mouse wakes display|bool|misc|mouse_move_enables_dpms|true|looknfeel|Mouse movement wakes display from DPMS off"
)

# Hyprland Decoration settings (written to looknfeel.lua)
declare -a HYPR_DECORATION_ITEMS=(
    "rounding|Corner radius|int:0:30|decoration|rounding|0|looknfeel|Window corner rounding in pixels. See also: Appearance"
    "shadow_enabled|Shadows|bool|decoration.shadow|enabled|true|looknfeel|Enable window drop shadows"
    "shadow_range|Shadow range|int:1:100|decoration.shadow|range|2|looknfeel|Shadow spread distance in pixels"
    "shadow_power|Shadow sharpness|int:1:4|decoration.shadow|render_power|3|looknfeel|Shadow falloff power (1=soft 4=sharp)"
    "shadow_color|Shadow color|color|decoration.shadow|color|rgba(1a1a1aee)|looknfeel|Shadow color in rgba format"
    "blur_enabled|Blur|bool|decoration.blur|enabled|true|looknfeel|Enable background blur on transparent windows"
    "blur_size|Blur radius|int:1:20|decoration.blur|size|2|looknfeel|Blur kernel size (higher=more blur)"
    "blur_passes|Blur iterations|int:1:10|decoration.blur|passes|2|looknfeel|Blur render passes (higher=smoother)"
    "blur_special|Blur special ws|bool|decoration.blur|special|true|looknfeel|Apply blur to special workspace background"
    "blur_brightness|Blur brightness|float:0.0:2.0|decoration.blur|brightness|0.60|looknfeel|Brightness of blurred background"
    "blur_contrast|Blur contrast|float:0.0:2.0|decoration.blur|contrast|0.75|looknfeel|Contrast of blurred background"
    "blur_noise|Blur noise|float:0.0:1.0|decoration.blur|noise|0.0117|looknfeel|Noise applied to blur"
    "blur_popups|Blur popups|bool|decoration.blur|popups|false|looknfeel|Apply blur to popup windows and tooltips"
    "anim_enabled|Animations|bool|animations|enabled|true|looknfeel|Enable window animations"
    "dim_inactive|Dim inactive|bool|decoration|dim_inactive|false|looknfeel|Dim unfocused windows"
    "dim_strength|Dim strength|float:0.0:1.0|decoration|dim_strength|0.5|looknfeel|How much to dim inactive windows"
    "dim_special|Dim special ws bg|float:0.0:1.0|decoration|dim_special|0.2|looknfeel|Dim amount for special workspace background"
    "cursor_hide|Hide cursor on type|bool|cursor|hide_on_key_press|true|looknfeel|Hide cursor when typing"
    "active_opacity|Active opacity|float:0.0:1.0|decoration|active_opacity|1.0|looknfeel|Opacity of focused window (1.0=opaque)"
    "inactive_opacity|Inactive opacity|float:0.0:1.0|decoration|inactive_opacity|1.0|looknfeel|Opacity of unfocused windows (1.0=opaque)"
    "fullscreen_opacity|Fullscreen opacity|float:0.0:1.0|decoration|fullscreen_opacity|1.0|looknfeel|Opacity of fullscreen windows (1.0=opaque)"
)

# Hyprland Input settings (written to input.lua)
declare -a HYPR_INPUT_ITEMS=(
    "sensitivity|Mouse sensitivity|float:-1.0:1.0|input|sensitivity|0|input|Mouse sensitivity (-1.0 to 1.0)"
    "follow_mouse|Focus follows mouse|enum:0:1:2:3|input|follow_mouse|1|input|0=off 1=always 2=click-unfocus 3=lock-unfocus"
    "accel_profile|Accel profile|enum:flat:adaptive|input|accel_profile||input|Mouse acceleration profile"
    "force_no_accel|Disable acceleration|bool|input|force_no_accel|false|input|Force disable mouse acceleration entirely"
    "left_handed|Left handed mouse|bool|input|left_handed|false|input|Swap left and right mouse buttons"
    "repeat_rate|Key repeat speed|int:1:100|input|repeat_rate|40|input|Key repeat rate in characters per second"
    "repeat_delay|Key repeat delay|int:100:2000|input|repeat_delay|600|input|Delay before key repeat starts (ms)"
    "numlock_default|Numlock on start|bool|input|numlock_by_default|true|input|Enable numlock on startup"
    "natural_scroll|Natural scroll|bool|input.touchpad|natural_scroll|false|input|Reverse scroll direction (natural/Apple-style)"
    "scroll_factor|Scroll speed|float:0.1:5.0|input.touchpad|scroll_factor|0.4|input|Touchpad scroll speed multiplier"
    "disable_typing|Off while typing|bool|input.touchpad|disable_while_typing|true|input|Disable touchpad while typing"
    "tap_to_click|Tap to click|bool|input.touchpad|tap-to-click|true|input|Enable tap-to-click on touchpad"
    "drag_lock|Drag lock|bool|input.touchpad|drag_lock|false|input|Keep drag active after lifting finger"
    "middle_emulation|Middle btn emulation|bool|input.touchpad|middle_button_emulation|false|input|Emulate middle click with two-finger tap"
    "scroll_button|Scroll button|int:0:999|input|scroll_button|0|input|Button for on-button-down scrolling (0=disable)"
    "scroll_method|Scroll method|enum:2fg:edge:on_button_down:no_scroll|input|scroll_method|2fg|input|Touchpad scroll method"
)

# Hyprland Gestures settings (written to looknfeel.lua)
declare -a HYPR_GESTURES_ITEMS=(
    "ws_swipe|Workspace swipe|bool|gestures|workspace_swipe|false|looknfeel|Swipe between workspaces on touchpad"
    "ws_swipe_fingers|Swipe fingers|int:2:5|gestures|workspace_swipe_fingers|3|looknfeel|Number of fingers for workspace swipe"
    "ws_swipe_distance|Swipe distance|int:50:1000|gestures|workspace_swipe_distance|300|looknfeel|Distance in pixels to trigger swipe"
    "ws_swipe_invert|Invert swipe|bool|gestures|workspace_swipe_invert|true|looknfeel|Reverse workspace swipe direction"
    "ws_swipe_create|Swipe new workspace|bool|gestures|workspace_swipe_create_new|true|looknfeel|Create new workspace at end of swipe"
)

# Hyprland pending edits and current values
declare -A HYPR_EDITS=()
declare -A HYPR_CURRENT=()

# Load saved Hyprland settings from managed blocks
load_hypr_settings

# Extract installed directory name from a GitHub URL
# omarchy-theme-install strips "omarchy-" prefix and "-theme" suffix
get_theme_dir_name() {
    local url="$1"
    local repo_name="${url##*/}"
    # Remove trailing slash if present
    repo_name="${repo_name%/}"
    # Match omarchy-theme-install: drop .git, strip omarchy- prefix and -theme
    # suffix, then lowercase
    repo_name="${repo_name%.git}"
    repo_name="${repo_name#omarchy-}"
    repo_name="${repo_name%-theme}"
    echo "${repo_name,,}"
}

# Check if a theme is already installed
is_theme_installed() {
    local url="$1"
    local dir_name
    dir_name=$(get_theme_dir_name "$url")
    [[ -d "$HOME/.config/omarchy/themes/$dir_name" ]]
}

# Build installed themes lookup set for fast rendering
declare -A INSTALLED_THEME_SET=()
if [[ -d "$HOME/.config/omarchy/themes" ]]; then
    for dir in "$HOME/.config/omarchy/themes"/*/; do
        [[ -d "$dir" ]] || continue
        local_name="${dir%/}"
        local_name="${local_name##*/}"
        INSTALLED_THEME_SET["$local_name"]=1
    done
fi

# Descriptions for packages (shown when highlighted)
declare -A PKG_DESCRIPTIONS=(
    ["1password-beta"]="Password manager (beta version)"
    ["1password-cli"]="1Password command-line tool"
    ["docker"]="Container runtime for running isolated apps"
    ["docker-buildx"]="Docker CLI plugin for extended builds"
    ["docker-compose"]="Multi-container Docker orchestration"
    ["gnome-calculator"]="GNOME desktop calculator app"
    ["kdenlive"]="Professional video editing software"
    ["libreoffice-fresh"]="Full office suite (docs, sheets, slides)"
    ["localsend"]="Share files to nearby devices over WiFi"
    ["obs-studio"]="Screen recording and streaming software"
    ["obsidian"]="Markdown-based note-taking app"
    ["omarchy-chromium"]="Chromium web browser"
    ["pinta"]="Simple image editing program"
    ["signal-desktop"]="Encrypted messaging app"
    ["spotify"]="Music streaming service"
    ["typora"]="Markdown editor"
    ["xournalpp"]="Handwriting and PDF annotation app"
)

# Descriptions for webapps
declare -A WEBAPP_DESCRIPTIONS=(
    ["basecamp"]="Project management and team communication"
    ["chatgpt"]="OpenAI's AI chat assistant"
    ["discord"]="Voice, video and text communication"
    ["figma"]="Collaborative design tool"
    ["fizzy"]="Sparkling water tracking app"
    ["github"]="Code hosting and collaboration platform"
    ["google-contacts"]="Google contacts manager"
    ["google-maps"]="Google maps and navigation"
    ["google-messages"]="Google SMS/RCS messaging"
    ["google-photos"]="Google photo storage and sharing"
    ["hey"]="Email service by Basecamp"
    ["whatsapp"]="Encrypted messaging app"
    ["x"]="Social media platform (formerly Twitter)"
    ["youtube"]="Video streaming platform"
    ["zoom"]="Video conferencing software"
)

# Multi-monitor management state
declare -a DETECTED_MONITORS=()       # "name|make|model|width|height|x|y|scale|desc|transform"
declare -A MONITOR_POSITIONS=()       # name -> "x,y"
declare -A MONITOR_TRANSFORMS=()      # name -> transform (0-7)
declare -i MONITOR_COUNT=0
declare LAPTOP_MONITOR=""             # eDP-* name if found
declare -i MONITORS_POSITIONED=0      # 1 if position editor was completed
declare SELECTED_POWER_PROFILE=""     # "", "power-saver", "balanced", "performance"
declare SELECTED_BATTERY_LIMIT=""     # "", "60", "70", "80", "90", "100"
declare SELECTED_PRIMARY_MONITOR=""  # "", monitor name (e.g. "HDMI-A-1")

# Keybind Editor data structures
declare -a EDIT_BINDINGS_ITEMS=()
declare -A BINDING_EDITS=()  # idx -> "new_mods|new_key"
declare -a BINDING_LUA_ACTIONS=()  # action expressions copied verbatim when rebinding
declare -A BINDING_ORIG_KEYS=()    # idx -> original Lua keys of a moved binding
declare -a VALID_KEYS=(
    A B C D E F G H I J K L M N O P Q R S T U V W X Y Z
    0 1 2 3 4 5 6 7 8 9
    F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 F11 F12
    RETURN SPACE TAB ESCAPE BACKSPACE DELETE INSERT HOME END
    PAGEUP PAGEDOWN UP DOWN LEFT RIGHT
    PRINT SCROLL_LOCK PAUSE
    KP_0 KP_1 KP_2 KP_3 KP_4 KP_5 KP_6 KP_7 KP_8 KP_9
    KP_ADD KP_SUBTRACT KP_MULTIPLY KP_DIVIDE KP_ENTER KP_DECIMAL
    MINUS EQUAL BRACKETLEFT BRACKETRIGHT BACKSLASH SEMICOLON
    APOSTROPHE GRAVE COMMA PERIOD SLASH
    XF86AUDIOMUTE XF86AUDIOLOWERVOLUME XF86AUDIORAISEVOLUME
    XF86AUDIOPLAY XF86AUDIOPREV XF86AUDIONEXT
    XF86MONBRIGHTNESSUP XF86MONBRIGHTNESSDOWN
)

# Load keybindings for the editor
load_all_bindings

# Selection state for toggle items: 0=none, 1=option1, 2=option2
declare -A TOGGLE_SELECTIONS=()

# Selection state for packages/webapps (by name)
declare -A PKG_SELECTIONS=()
declare -A WEBAPP_SELECTIONS=()

# Navigation state
CURRENT_PANEL=0          # 0=left (categories), 1=right (items)
CATEGORY_CURSOR=1        # Current category in left panel (start at first non-header)
ITEM_CURSOR=0            # Current item in right panel
ITEM_SCROLL_OFFSET=0     # Scroll offset for right panel
CAT_SCROLL_OFFSET=0      # Scroll offset for left panel

# =============================================================================
# HELPER FUNCTIONS FOR TWO-PANEL UI
# =============================================================================

# Get items array for a category
get_category_items() {
    local cat_idx=$1
    # Section headers (0, 3, 11, 15) have no items
    case $cat_idx in
        1) echo "PACKAGES" ;;
        2) echo "WEBAPPS" ;;
        4) echo "KEYBINDINGS" ;;
        5) echo "EDIT_BINDINGS" ;;
        6) echo "DISPLAY" ;;
        7) echo "SYSTEM" ;;
        8) echo "APPEARANCE" ;;
        9) echo "KEYBOARD" ;;
        10) echo "UTILITIES" ;;
        12) echo "HYPR_GENERAL" ;;
        13) echo "HYPR_DECORATION" ;;
        14) echo "HYPR_INPUT" ;;
        15) echo "HYPR_GESTURES" ;;
        17) echo "ROG_HARDWARE" ;;
        18) echo "ROG_LIGHTING" ;;
        20) echo "EXTRA_THEMES" ;;
        22) echo "THEMARCHY" ;;
    esac
}

# Get item count for current category
get_current_item_count() {
    # Section headers (0, 3, 11, 15) return 0
    case $CATEGORY_CURSOR in
        0|3|11|16|19|21) echo 0 ;;
        1) echo ${#INSTALLED_PACKAGES[@]} ;;
        2) echo ${#INSTALLED_WEBAPPS[@]} ;;
        4) echo ${#KEYBINDINGS_ITEMS[@]} ;;
        5) echo ${#EDIT_BINDINGS_ITEMS[@]} ;;
        6) echo ${#DISPLAY_ITEMS[@]} ;;
        7) echo ${#SYSTEM_ITEMS[@]} ;;
        8) echo ${#APPEARANCE_ITEMS[@]} ;;
        9) echo ${#KEYBOARD_ITEMS[@]} ;;
        10) echo ${#UTILITIES_ITEMS[@]} ;;
        12) echo ${#HYPR_GENERAL_ITEMS[@]} ;;
        13) echo ${#HYPR_DECORATION_ITEMS[@]} ;;
        14) echo ${#HYPR_INPUT_ITEMS[@]} ;;
        15) echo ${#HYPR_GESTURES_ITEMS[@]} ;;
        17) echo ${#ROG_HARDWARE_ITEMS[@]} ;;
        18) echo ${#ROG_LIGHTING_ITEMS[@]} ;;
        20) echo ${#EXTRA_THEMES[@]} ;;
        22) echo ${#THEMARCHY_ITEMS[@]} ;;
    esac
}

# Parse toggle item: returns id, name, opt1, opt2, type via global vars
parse_toggle_item() {
    local item="$1"
    IFS='|' read -r TOGGLE_ID TOGGLE_NAME TOGGLE_OPT1 TOGGLE_OPT2 TOGGLE_TYPE TOGGLE_DESC <<< "$item"
}

# Get description for currently highlighted item
get_current_description() {
    local item_count=$(get_current_item_count)
    if [ $item_count -eq 0 ] || [ $ITEM_CURSOR -ge $item_count ]; then
        echo ""
        return
    fi

    case $CATEGORY_CURSOR in
        1)  # Packages
            local pkg="${INSTALLED_PACKAGES[$ITEM_CURSOR]}"
            echo "${PKG_DESCRIPTIONS[$pkg]:-}"
            ;;
        2)  # Webapps
            local webapp="${INSTALLED_WEBAPPS[$ITEM_CURSOR]}"
            echo "${WEBAPP_DESCRIPTIONS[$webapp]:-}"
            ;;
        4|8|9)  # Toggle items
            local arr
            case $CATEGORY_CURSOR in
                4) arr="KEYBINDINGS_ITEMS" ;;
                8) arr="APPEARANCE_ITEMS" ;; 9) arr="KEYBOARD_ITEMS" ;;
            esac
            local -n ref="$arr"
            parse_toggle_item "${ref[$ITEM_CURSOR]}"
            echo "$TOGGLE_DESC"
            ;;
        7)  # System (mixed: toggle + action)
            parse_toggle_item "${SYSTEM_ITEMS[$ITEM_CURSOR]}"
            echo "$TOGGLE_DESC"
            ;;
        5)  # Keybind Editor
            local be_entry="${EDIT_BINDINGS_ITEMS[$ITEM_CURSOR]}"
            if [[ "$be_entry" == HEADER* ]]; then
                echo ""
            else
                IFS='|' read -r _t _m _k _d be_disp be_args be_file <<< "$be_entry"
                local base_file="${be_file##*/}"
                if [[ -n "$be_args" ]]; then
                    echo "[$base_file] $be_disp,$be_args"
                else
                    echo "[$base_file] $be_disp"
                fi
            fi
            ;;
        6)  # Display (mixed: toggle/radio + action)
            parse_toggle_item "${DISPLAY_ITEMS[$ITEM_CURSOR]}"
            echo "$TOGGLE_DESC"
            ;;
        10)  # Utilities
            parse_toggle_item "${UTILITIES_ITEMS[$ITEM_CURSOR]}"
            echo "$TOGGLE_DESC"
            ;;
        12|13|14|15)  # Hyprland settings
            local hypr_arr
            case $CATEGORY_CURSOR in
                12) hypr_arr="HYPR_GENERAL_ITEMS" ;;
                13) hypr_arr="HYPR_DECORATION_ITEMS" ;;
                14) hypr_arr="HYPR_INPUT_ITEMS" ;;
                15) hypr_arr="HYPR_GESTURES_ITEMS" ;;
            esac
            local -n hypr_ref="$hypr_arr"
            parse_hypr_item "${hypr_ref[$ITEM_CURSOR]}"
            echo "$HYPR_DESC"
            ;;
        17|18)  # ROG items (mixed: toggle + action)
            local rog_arr
            case $CATEGORY_CURSOR in
                17) rog_arr="ROG_HARDWARE_ITEMS" ;;
                18) rog_arr="ROG_LIGHTING_ITEMS" ;;
            esac
            local -n rog_ref="$rog_arr"
            parse_toggle_item "${rog_ref[$ITEM_CURSOR]}"
            echo "$TOGGLE_DESC"
            ;;
        20) # Extra Themes
            local entry="${EXTRA_THEMES[$ITEM_CURSOR]}"
            local theme_url="${entry#*|}"
            local repo_name="${theme_url##*/}"
            repo_name="${repo_name%/}"
            echo "Install from $repo_name"
            ;;
        22) # Themarchy
            parse_toggle_item "${THEMARCHY_ITEMS[$ITEM_CURSOR]}"
            echo "$TOGGLE_DESC"
            ;;
        *)
            echo ""
            ;;
    esac
}

# Helper function to center text (truncates to fit terminal width)
center_text() {
    local text="$1"
    local width="${2:-$(tput cols)}"
    local text_length=${#text}
    if [ $text_length -gt $width ]; then
        text="${text:0:$width}"
        text_length=$width
    fi
    local padding=$(( (width - text_length) / 2 ))
    if [ $padding -lt 0 ]; then
        padding=0
    fi
    printf "%*s%s" $padding "" "$text"
}

# Draw a horizontal line with box characters
draw_hline() {
    local width=$1
    local left="$2"
    local right="$3"
    local mid="${4:-}"
    local mid_pos="${5:-0}"

    printf "%s" "$left"
    for ((i=1; i<width-1; i++)); do
        if [ $mid_pos -gt 0 ] && [ $i -eq $mid_pos ]; then
            printf "%s" "$mid"
        else
            printf "─"
        fi
    done
    printf "%s" "$right"
}

# Function to draw the two-panel interface (dynamic layout)
draw_interface() {
    # Dynamic terminal dimensions
    local TERM_W TERM_H
    TERM_W=$(tput cols)
    TERM_H=$(tput lines)
    (( TERM_W < 60 )) && TERM_W=60
    (( TERM_H < 16 )) && TERM_H=16

    # Layout: │<LEFT_W>│<RIGHT_W>│ = TERM_W total
    local LEFT_W=26
    local INNER_W=$((TERM_W - 2))
    local RIGHT_W=$((INNER_W - LEFT_W - 1))
    local ROWS=$((TERM_H - 12))
    (( ROWS < 4 )) && ROWS=4

    # Clamp cursors
    local cat_count=${#CATEGORIES[@]}
    (( CATEGORY_CURSOR >= cat_count )) && CATEGORY_CURSOR=$((cat_count - 1))
    (( CATEGORY_CURSOR < 0 )) && CATEGORY_CURSOR=0

    local item_count=$(get_current_item_count)
    (( ITEM_CURSOR >= item_count )) && ITEM_CURSOR=$((item_count - 1))
    (( ITEM_CURSOR < 0 )) && ITEM_CURSOR=0

    # Scroll (right panel)
    (( ITEM_CURSOR < ITEM_SCROLL_OFFSET )) && ITEM_SCROLL_OFFSET=$ITEM_CURSOR
    (( ITEM_CURSOR >= ITEM_SCROLL_OFFSET + ROWS )) && ITEM_SCROLL_OFFSET=$((ITEM_CURSOR - ROWS + 1))
    (( ITEM_SCROLL_OFFSET < 0 )) && ITEM_SCROLL_OFFSET=0

    # Scroll (left panel)
    (( CATEGORY_CURSOR < CAT_SCROLL_OFFSET )) && CAT_SCROLL_OFFSET=$CATEGORY_CURSOR
    (( CATEGORY_CURSOR >= CAT_SCROLL_OFFSET + ROWS )) && CAT_SCROLL_OFFSET=$((CATEGORY_CURSOR - ROWS + 1))
    (( CAT_SCROLL_OFFSET < 0 )) && CAT_SCROLL_OFFSET=0

    # Scroll state for indicators
    local L_scroll_up=$(( CAT_SCROLL_OFFSET > 0 ? 1 : 0 ))
    local L_scroll_down=$(( CAT_SCROLL_OFFSET + ROWS < cat_count ? 1 : 0 ))
    local R_scroll_up=$(( ITEM_SCROLL_OFFSET > 0 ? 1 : 0 ))
    local R_scroll_down=$(( ITEM_SCROLL_OFFSET + ROWS < item_count ? 1 : 0 ))

    clear

    # Pre-compute dash strings
    local left_dashes="" right_dashes="" full_dashes=""
    for ((i=0; i<LEFT_W; i++)); do left_dashes+="─"; done
    for ((i=0; i<RIGHT_W; i++)); do right_dashes+="─"; done
    for ((i=0; i<INNER_W; i++)); do full_dashes+="─"; done

    # ── HEADER ──
    printf "${C_BORDER}╭%s╮${RESET}\n" "$full_dashes"

    # Helper: center text and pad to exact INNER_W characters
    _center_pad() {
        local raw
        raw="$(center_text "$1" $INNER_W)"
        local len=${#raw}
        if (( len >= INNER_W )); then
            printf '%s' "${raw:0:$INNER_W}"
        else
            printf "%s%*s" "$raw" $((INNER_W - len)) ""
        fi
    }

    local title_padded
    title_padded="$(_center_pad "A  L A  C A R C H Y")"
    printf "${C_BORDER}│${C_TITLE}%s${RESET}${C_BORDER}│${RESET}\n" "$title_padded"

    local sub_padded
    sub_padded="$(_center_pad "Omarchy Linux Debloater & Optimizer")"
    printf "${C_BORDER}│${C_SUBTITLE}%s${RESET}${C_BORDER}│${RESET}\n" "$sub_padded"

    local author_padded
    author_padded="$(_center_pad "by Daniel Coffey")"
    printf "${C_BORDER}│${C_DIM}%s${RESET}${C_BORDER}│${RESET}\n" "$author_padded"

    local spaces_inner
    printf -v spaces_inner "%*s" $INNER_W ""
    printf "${C_BORDER}│${RESET}%s${C_BORDER}│${RESET}\n" "$spaces_inner"

    # ── PANEL DIVIDER WITH CATEGORY NAME ──
    local cat_display="${CATEGORIES[$CATEGORY_CURSOR]}"
    cat_display="${cat_display#"${cat_display%%[![:space:]]*}"}"
    local right_label=" ${cat_display} "
    local right_label_len=${#right_label}
    local right_fill_count=$((RIGHT_W - right_label_len - 1))
    (( right_fill_count < 0 )) && right_fill_count=0
    local right_fill=""
    for ((i=0; i<right_fill_count; i++)); do right_fill+="─"; done

    printf "${C_BORDER}├%s┬─${RESET}${C_ACCENT}%s${RESET}${C_BORDER}%s┤${RESET}\n" \
        "$left_dashes" "$right_label" "$right_fill"

    # Get current description for display
    local cur_desc=$(get_current_description)

    # Content rows
    for ((row=0; row<ROWS; row++)); do
        local L="" R=""
        local Lhl=0 Rhl=0 Rchecked=0 Rmodified=0

        # Left panel (with scroll offset)
        local Lsection=0
        local cat_idx=$((CAT_SCROLL_OFFSET + row))
        if (( cat_idx < cat_count )); then
            local cat_text="${CATEGORIES[$cat_idx]}"
            if [[ "$cat_text" == ---* ]]; then
                # Section header: extract name and uppercase
                local section_name="${cat_text#---}"
                section_name="${section_name%---}"
                section_name="${section_name#"${section_name%%[![:space:]]*}"}"
                section_name="${section_name%"${section_name##*[![:space:]]}"}"
                section_name="${section_name^^}"
                L="  ${section_name}"
                Lsection=1
            elif (( cat_idx == CATEGORY_CURSOR )); then
                local trimmed="${cat_text#"${cat_text%%[![:space:]]*}"}"
                L="  ▸ ${trimmed}"
                (( CURRENT_PANEL == 0 )) && Lhl=2 || Lhl=1
            else
                local trimmed="${cat_text#"${cat_text%%[![:space:]]*}"}"
                L="    ${trimmed}"
            fi
        fi

        # Right panel
        local Rsection=0
        local idx=$((ITEM_SCROLL_OFFSET + row))
        if (( idx < item_count )); then
            (( CURRENT_PANEL == 1 && idx == ITEM_CURSOR )) && Rhl=1
            case $CATEGORY_CURSOR in
                1) local p="${INSTALLED_PACKAGES[$idx]}"
                   if [[ "${PKG_SELECTIONS[$p]:-0}" == "1" ]]; then
                       R="  ● $p"; Rchecked=1
                   else
                       R="  ○ $p"
                   fi ;;
                2) local w="${INSTALLED_WEBAPPS[$idx]}"
                   if [[ "${WEBAPP_SELECTIONS[$w]:-0}" == "1" ]]; then
                       R="  ● $w"; Rchecked=1
                   else
                       R="  ○ $w"
                   fi ;;
                4|8|9)
                    local arr
                    case $CATEGORY_CURSOR in
                        4) arr="KEYBINDINGS_ITEMS" ;;
                        8) arr="APPEARANCE_ITEMS" ;; 9) arr="KEYBOARD_ITEMS" ;;
                    esac
                    local -n ref="$arr"
                    parse_toggle_item "${ref[$idx]}"
                    R=$(format_toggle_item "$TOGGLE_NAME" "$TOGGLE_OPT1" "$TOGGLE_OPT2" "${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}") ;;
                7)  # System (mixed: toggle + action)
                    parse_toggle_item "${SYSTEM_ITEMS[$idx]}"
                    if [ "$TOGGLE_TYPE" = "action" ]; then
                        local sys_suffix=""
                        if [[ "$TOGGLE_ID" == "power_profile" && -n "$SELECTED_POWER_PROFILE" ]]; then
                            sys_suffix="$SELECTED_POWER_PROFILE"
                        elif [[ "$TOGGLE_ID" == "power_auto_switch" && -n "$SELECTED_POWER_AUTO_SWITCH" ]]; then
                            if [[ "$SELECTED_POWER_AUTO_SWITCH" == "disabled" ]]; then
                                sys_suffix="off"
                            else
                                local _as="${SELECTED_POWER_AC_PROFILE/power-saver/saver}"
                                _as="${_as/performance/perf}"
                                local _bs="${SELECTED_POWER_BATTERY_PROFILE/power-saver/saver}"
                                _bs="${_bs/performance/perf}"
                                sys_suffix="${_as} / ${_bs}"
                            fi
                        elif [[ "$TOGGLE_ID" == "battery_limit" && -n "$SELECTED_BATTERY_LIMIT" ]]; then
                            sys_suffix="${SELECTED_BATTERY_LIMIT}%"
                        fi
                        if [[ -n "$sys_suffix" ]]; then
                            R=$(printf "  %-24s %s" "$TOGGLE_NAME" "$sys_suffix")
                        else
                            R="  $TOGGLE_NAME"
                        fi
                    else
                        R=$(format_toggle_item "$TOGGLE_NAME" "$TOGGLE_OPT1" "$TOGGLE_OPT2" "${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}")
                    fi ;;
                6)  # Display (mixed: toggle/radio + action)
                    parse_toggle_item "${DISPLAY_ITEMS[$idx]}"
                    if [ "$TOGGLE_TYPE" = "action" ]; then
                        local d_suffix=""
                        if [ "$TOGGLE_ID" = "detect_monitors" ] && [ $MONITOR_COUNT -gt 0 ]; then
                            d_suffix="$MONITOR_COUNT found"
                        elif [ "$TOGGLE_ID" = "position_monitors" ] && [ $MONITORS_POSITIONED -eq 1 ]; then
                            d_suffix="[set]"
                        elif [[ "$TOGGLE_ID" == "primary_monitor" && -n "$SELECTED_PRIMARY_MONITOR" ]]; then
                            d_suffix="$SELECTED_PRIMARY_MONITOR"
                        fi
                        if [[ -n "$d_suffix" ]]; then
                            R=$(printf "  %-24s %s" "$TOGGLE_NAME" "$d_suffix")
                        else
                            R="  $TOGGLE_NAME"
                        fi
                    else
                        R=$(format_toggle_item "$TOGGLE_NAME" "$TOGGLE_OPT1" "$TOGGLE_OPT2" "${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}")
                    fi ;;
                5)  # Keybind Editor
                    local be_entry="${EDIT_BINDINGS_ITEMS[$idx]}"
                    if [[ "$be_entry" == HEADER* ]]; then
                        local header_name="${be_entry#HEADER|}"
                        R="  ── ${header_name} ──"
                        Rsection=1
                    else
                        IFS='|' read -r _t be_mods be_key be_desc _rest <<< "$be_entry"
                        local be_prefix=" "
                        local display_mods="$be_mods"
                        local display_key="$be_key"
                        if [[ -n "${BINDING_EDITS[$idx]:-}" ]]; then
                            IFS='|' read -r display_mods display_key <<< "${BINDING_EDITS[$idx]}"
                            be_prefix="◆"
                            Rmodified=1
                        fi
                        local keycombo="${display_mods}+${display_key}"
                        R=$(printf "%s%-17s %s" "$be_prefix" "$keycombo" "$be_desc")
                    fi ;;
                10) parse_toggle_item "${UTILITIES_ITEMS[$idx]}"
                    if [ "$TOGGLE_TYPE" = "toggle" ]; then
                        R=$(format_toggle_item "$TOGGLE_NAME" "$TOGGLE_OPT1" "$TOGGLE_OPT2" "${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}")
                    else
                        if [[ "${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}" == "1" ]]; then
                            R="  ● $TOGGLE_NAME"; Rchecked=1
                        else
                            R="  ○ $TOGGLE_NAME"
                        fi
                    fi ;;
                12|13|14|15)  # Hyprland settings
                    local hypr_arr
                    case $CATEGORY_CURSOR in
                        12) hypr_arr="HYPR_GENERAL_ITEMS" ;;
                        13) hypr_arr="HYPR_DECORATION_ITEMS" ;;
                        14) hypr_arr="HYPR_INPUT_ITEMS" ;;
                        15) hypr_arr="HYPR_GESTURES_ITEMS" ;;
                    esac
                    local -n hypr_ref="$hypr_arr"
                    local h_entry="${hypr_ref[$idx]}"
                    parse_hypr_item "$h_entry"
                    # Get display value: pending edit > current > default
                    local h_val="$HYPR_DEFAULT"
                    local full_key="${HYPR_SECTION}.${HYPR_KEY}"
                    [[ -n "${HYPR_CURRENT[$full_key]:-}" ]] && h_val="${HYPR_CURRENT[$full_key]}"
                    local h_prefix=" "
                    if [[ -n "${HYPR_EDITS[$HYPR_ID]:-}" ]]; then
                        local h_new="${HYPR_EDITS[$HYPR_ID]}"
                        h_prefix="◆"
                        Rmodified=1
                        if [[ "$HYPR_TYPE" == "bool" ]]; then
                            [[ "$h_val" == "true" ]] && h_val="ON" || h_val="OFF"
                            [[ "$h_new" == "true" ]] && h_new="ON" || h_new="OFF"
                        fi
                        R=$(printf "%s%-24s %s > %s" "$h_prefix" "$HYPR_LABEL" "$h_val" "$h_new")
                    else
                        if [[ "$HYPR_TYPE" == "bool" ]]; then
                            [[ "$h_val" == "true" ]] && h_val="ON" || h_val="OFF"
                        fi
                        R=$(printf "  %-24s %s" "$HYPR_LABEL" "$h_val")
                    fi ;;
                17|18)  # ROG items (mixed: toggle + action)
                    local rog_arr
                    case $CATEGORY_CURSOR in
                        17) rog_arr="ROG_HARDWARE_ITEMS" ;;
                        18) rog_arr="ROG_LIGHTING_ITEMS" ;;
                    esac
                    local -n rog_ref="$rog_arr"
                    parse_toggle_item "${rog_ref[$idx]}"
                    if [ "$TOGGLE_TYPE" = "action" ]; then
                        local rog_suffix=""
                        if [[ "$TOGGLE_ID" == "rog_profile" && -n "$SELECTED_ROG_PROFILE" ]]; then
                            rog_suffix="$SELECTED_ROG_PROFILE"
                        elif [[ "$TOGGLE_ID" == "rog_kbd_leds" && -n "$SELECTED_ROG_KBD_LEDS" ]]; then
                            rog_suffix="$SELECTED_ROG_KBD_LEDS"
                        elif [[ "$TOGGLE_ID" == "rog_aura" && -n "$SELECTED_ROG_AURA_EFFECT" ]]; then
                            rog_suffix="$SELECTED_ROG_AURA_EFFECT"
                        elif [[ "$TOGGLE_ID" == "rog_aura_power" && -n "$SELECTED_ROG_AURA_POWER_ZONE" ]]; then
                            rog_suffix="configured"
                        elif [[ "$TOGGLE_ID" == "rog_slash" && -n "$ROG_SLASH_ENABLE" ]]; then
                            rog_suffix="$ROG_SLASH_ENABLE"
                        elif [[ "$TOGGLE_ID" == "rog_slash" && -n "$SELECTED_ROG_SLASH_MODE" ]]; then
                            rog_suffix="$SELECTED_ROG_SLASH_MODE"
                        elif [[ "$TOGGLE_ID" == "rog_slash_extra" && -n "$SELECTED_ROG_SLASH_INTERVAL$ROG_SLASH_SHOW_BOOT$ROG_SLASH_SHOW_SHUTDOWN$ROG_SLASH_SHOW_SLEEP$ROG_SLASH_SHOW_BATTERY$ROG_SLASH_SHOW_BATTERY_WARN" ]]; then
                            rog_suffix="configured"
                        elif [[ "$TOGGLE_ID" == "rog_battery" && -n "$SELECTED_ROG_BATTERY_LIMIT$ROG_BATTERY_ONESHOT" ]]; then
                            [[ -n "$SELECTED_ROG_BATTERY_LIMIT" ]] && rog_suffix="limit ${SELECTED_ROG_BATTERY_LIMIT}%"
                            [[ "$ROG_BATTERY_ONESHOT" == "true" ]] && rog_suffix="${rog_suffix:+$rog_suffix, }one-shot"
                        elif [[ "$TOGGLE_ID" == "rog_power_tuning" && -n "$SELECTED_ROG_NV_DYNAMIC_BOOST$SELECTED_ROG_NV_TEMP_TARGET$SELECTED_ROG_PPT_PL1_SPL$SELECTED_ROG_PPT_PL2_SPPT" ]]; then
                            rog_suffix="configured"
                        elif [[ "$TOGGLE_ID" == "rog_fan_curve_edit" && -n "$SELECTED_ROG_FAN_CURVE_DATA$ROG_FAN_CURVE_DEFAULT" ]]; then
                            [[ -n "$SELECTED_ROG_FAN_CURVE_FAN" ]] && rog_suffix="${SELECTED_ROG_FAN_CURVE_FAN^^}"
                            [[ "$ROG_FAN_CURVE_DEFAULT" == "true" ]] && rog_suffix="${rog_suffix:+$rog_suffix, }reset"
                        elif [[ "$TOGGLE_ID" == "rog_anime_extra" && -n "$SELECTED_ROG_ANIME_BRIGHTNESS$ROG_ANIME_POWERSAVE$ROG_ANIME_OFF_UNPLUGGED$ROG_ANIME_OFF_SUSPENDED$ROG_ANIME_OFF_LID_CLOSED$SELECTED_ROG_ANIME_BOOT" ]]; then
                            rog_suffix="configured"
                        fi
                        if [[ -n "$rog_suffix" ]]; then
                            R=$(printf "  %-24s %s" "$TOGGLE_NAME" "$rog_suffix")
                        else
                            R="  $TOGGLE_NAME"
                        fi
                    else
                        R=$(format_toggle_item "$TOGGLE_NAME" "$TOGGLE_OPT1" "$TOGGLE_OPT2" "${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}")
                    fi ;;
                20) local entry="${EXTRA_THEMES[$idx]}"
                    local tname="${entry%%|*}"
                    local turl="${entry#*|}"
                    local tdir
                    tdir=$(get_theme_dir_name "$turl")
                    local installed_suffix=""
                    [[ -n "${INSTALLED_THEME_SET[$tdir]:-}" ]] && installed_suffix=" (installed)"
                    if [[ "${THEME_SELECTIONS[$tname]:-0}" == "1" ]]; then
                        R="  ● ${tname}${installed_suffix}"; Rchecked=1
                    else
                        R="  ○ ${tname}${installed_suffix}"
                    fi ;;
                22) parse_toggle_item "${THEMARCHY_ITEMS[$idx]}"
                    if [ "$TOGGLE_TYPE" = "action" ]; then
                        local th_suffix=""
                        [[ "$TOGGLE_ID" == "themarchy_apply" && "$APPLY_THEMARCHY" == "true" ]] && th_suffix="[queued]"
                        if [[ -n "$th_suffix" ]]; then
                            R=$(printf "  %-24s %s" "$TOGGLE_NAME" "$th_suffix")
                        else
                            R="  $TOGGLE_NAME"
                        fi
                    else
                        R=$(format_toggle_item "$TOGGLE_NAME" "$TOGGLE_OPT1" "$TOGGLE_OPT2" "${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}")
                    fi ;;
            esac
        fi

        # Format cells to exact width (character-based to handle Unicode)
        local Lfmt Rfmt
        local L_len=${#L} R_len=${#R}
        if (( L_len >= LEFT_W )); then
            Lfmt="${L:0:$LEFT_W}"
        else
            printf -v Lfmt "%s%*s" "$L" $((LEFT_W - L_len)) ""
        fi
        if (( R_len >= RIGHT_W )); then
            Rfmt="${R:0:$RIGHT_W}"
        else
            printf -v Rfmt "%s%*s" "$R" $((RIGHT_W - R_len)) ""
        fi

        # Scroll indicators
        if (( row == 0 && L_scroll_up )); then
            Lfmt="${Lfmt:0:$((LEFT_W-2))} ▲"
        fi
        if (( row == ROWS-1 && L_scroll_down )); then
            Lfmt="${Lfmt:0:$((LEFT_W-2))} ▼"
        fi
        if (( row == 0 && R_scroll_up )); then
            Rfmt="${Rfmt:0:$((RIGHT_W-2))} ▲"
        fi
        if (( row == ROWS-1 && R_scroll_down )); then
            Rfmt="${Rfmt:0:$((RIGHT_W-2))} ▼"
        fi

        # ── Output left cell ──
        if (( Lhl == 2 )); then
            printf "${C_BORDER}│${C_SEL_ACTIVE}%s${RESET}${C_BORDER}│${RESET}" "$Lfmt"
        elif (( Lhl == 1 )); then
            printf "${C_BORDER}│${C_SEL_INACTIVE}%s${RESET}${C_BORDER}│${RESET}" "$Lfmt"
        elif (( Lsection )); then
            printf "${C_BORDER}│${C_SECTION}%s${RESET}${C_BORDER}│${RESET}" "$Lfmt"
        else
            printf "${C_BORDER}│${C_TEXT}%s${RESET}${C_BORDER}│${RESET}" "$Lfmt"
        fi

        # ── Output right cell ──
        if (( Rhl )); then
            printf "${C_SEL_ACTIVE}%s${RESET}${C_BORDER}│${RESET}\n" "$Rfmt"
        elif (( Rsection )); then
            printf "${C_DIM}%s${RESET}${C_BORDER}│${RESET}\n" "$Rfmt"
        elif (( Rchecked )); then
            # Highlight the ● character in green
            printf "${C_TEXT}%s${C_CHECK}%s${RESET}${C_TEXT}%s${RESET}${C_BORDER}│${RESET}\n" \
                "${Rfmt:0:2}" "${Rfmt:2:1}" "${Rfmt:3}"
        elif (( Rmodified )); then
            # Highlight the ◆ prefix in orange
            printf "${C_MODIFIED}%s${RESET}${C_TEXT}%s${RESET}${C_BORDER}│${RESET}\n" \
                "${Rfmt:0:1}" "${Rfmt:1}"
        else
            printf "${C_TEXT}%s${RESET}${C_BORDER}│${RESET}\n" "$Rfmt"
        fi
    done

    # ── DESCRIPTION ROW ──
    printf "${C_BORDER}├%s┴%s┤${RESET}\n" "$left_dashes" "$right_dashes"
    if [[ -n "$cur_desc" ]]; then
        local desc_raw
        desc_raw="$(center_text "${cur_desc:0:$((INNER_W - 4))}" $INNER_W)"
        local desc_len=${#desc_raw}
        local desc_centered
        if (( desc_len >= INNER_W )); then
            desc_centered="${desc_raw:0:$INNER_W}"
        else
            printf -v desc_centered "%s%*s" "$desc_raw" $((INNER_W - desc_len)) ""
        fi
        printf "${C_BORDER}│${C_DIM}%s${RESET}${C_BORDER}│${RESET}\n" "$desc_centered"
    else
        printf "${C_BORDER}│${RESET}%-${INNER_W}s${C_BORDER}│${RESET}\n" ""
    fi

    # ── FOOTER ──
    printf "${C_BORDER}├%s┤${RESET}\n" "$full_dashes"
    local footer_text=""
    if [ $CATEGORY_CURSOR -eq 20 ]; then
        footer_text="←→ Navigate   ↑↓ Move   Space Select   A All   Enter Confirm   Q Quit"
    elif [ $CATEGORY_CURSOR -eq 5 ] || [ $CATEGORY_CURSOR -eq 6 ] || [ $CATEGORY_CURSOR -eq 7 ] || [ $CATEGORY_CURSOR -eq 12 ] || [ $CATEGORY_CURSOR -eq 13 ] || [ $CATEGORY_CURSOR -eq 14 ] || [ $CATEGORY_CURSOR -eq 15 ] || [ $CATEGORY_CURSOR -eq 17 ] || [ $CATEGORY_CURSOR -eq 18 ]; then
        footer_text="←→ Navigate   ↑↓ Move   Space Edit   R Reset   Enter Confirm   Q Quit"
    else
        footer_text="←→ Navigate   ↑↓ Move   Space Select   Enter Confirm   Q Quit"
    fi
    local footer_centered
    footer_centered="$(center_text "$footer_text" $INNER_W)"
    local footer_len=${#footer_centered}
    local footer_padded
    if (( footer_len >= INNER_W )); then
        footer_padded="${footer_centered:0:$INNER_W}"
    else
        printf -v footer_padded "%s%*s" "$footer_centered" $((INNER_W - footer_len)) ""
    fi
    printf "${C_BORDER}│${C_FOOTER_TXT}%s${RESET}${C_BORDER}│${RESET}\n" "$footer_padded"
    printf "${C_BORDER}╰%s╯${RESET}\n" "$full_dashes"
}

# Format a toggle item for display
format_toggle_item() {
    local name="$1"
    local opt1="$2"
    local opt2="$3"
    local sel="$4"  # 0=none, 1=opt1, 2=opt2

    if [ -z "$opt2" ]; then
        # Action item (like backup)
        if [ "$sel" -eq 1 ]; then
            printf "  ● %s" "$name"
        else
            printf "  ○ %s" "$name"
        fi
    else
        # Toggle item with two options
        local m1="○" m2="○"
        [ "$sel" -eq 1 ] && m1="●"
        [ "$sel" -eq 2 ] && m2="●"
        printf "  %-14s  %s %-7s  %s %s" "$name" "$m1" "$opt1" "$m2" "$opt2"
    fi
}

# Function to handle key input for two-panel navigation
handle_input() {
    local key
    while true; do
        IFS= read -rsn1 -t 1 key < /dev/tty
        local rs=$?
        if [ $rs -le 1 ]; then
            break
        fi
    done

    local item_count=$(get_current_item_count)

    case "$key" in
        $'\x1b')  # ESC sequence
            read -rsn2 -t 0.1 key
            case "$key" in
                '[A')  # Up arrow
                    if [ $CURRENT_PANEL -eq 0 ]; then
                        # Left panel - navigate categories, skip section headers
                        local new_cursor=$((CATEGORY_CURSOR - 1))
                        while [ $new_cursor -ge 0 ] && is_section_header $new_cursor; do
                            ((new_cursor--))
                        done
                        if [ $new_cursor -ge 0 ]; then
                            CATEGORY_CURSOR=$new_cursor
                            ITEM_CURSOR=0
                            ITEM_SCROLL_OFFSET=0
                        fi
                    else
                        # Right panel - navigate items
                        if [ $ITEM_CURSOR -gt 0 ]; then
                            ((ITEM_CURSOR--))
                            # Skip headers in Keybind Editor
                            if [ $CATEGORY_CURSOR -eq 5 ]; then
                                while [ $ITEM_CURSOR -gt 0 ] && is_binding_header $ITEM_CURSOR; do
                                    ((ITEM_CURSOR--))
                                done
                                if is_binding_header $ITEM_CURSOR; then
                                    ((ITEM_CURSOR++))
                                fi
                            fi
                        fi
                    fi
                    ;;
                '[B')  # Down arrow
                    if [ $CURRENT_PANEL -eq 0 ]; then
                        # Left panel - navigate categories, skip section headers
                        local new_cursor=$((CATEGORY_CURSOR + 1))
                        while [ $new_cursor -lt ${#CATEGORIES[@]} ] && is_section_header $new_cursor; do
                            ((new_cursor++))
                        done
                        if [ $new_cursor -lt ${#CATEGORIES[@]} ]; then
                            CATEGORY_CURSOR=$new_cursor
                            ITEM_CURSOR=0
                            ITEM_SCROLL_OFFSET=0
                        fi
                    else
                        # Right panel - navigate items
                        if [ $ITEM_CURSOR -lt $((item_count - 1)) ]; then
                            ((ITEM_CURSOR++))
                            # Skip headers in Keybind Editor
                            if [ $CATEGORY_CURSOR -eq 5 ]; then
                                while [ $ITEM_CURSOR -lt $((item_count - 1)) ] && is_binding_header $ITEM_CURSOR; do
                                    ((ITEM_CURSOR++))
                                done
                            fi
                        fi
                    fi
                    ;;
                '[C')  # Right arrow - switch to right panel
                    if [ $CURRENT_PANEL -eq 0 ] && [ $item_count -gt 0 ]; then
                        CURRENT_PANEL=1
                        # Skip headers in Keybind Editor
                        if [ $CATEGORY_CURSOR -eq 5 ] && is_binding_header $ITEM_CURSOR; then
                            while [ $ITEM_CURSOR -lt $((item_count - 1)) ] && is_binding_header $ITEM_CURSOR; do
                                ((ITEM_CURSOR++))
                            done
                        fi
                    fi
                    ;;
                '[D')  # Left arrow - switch to left panel
                    if [ $CURRENT_PANEL -eq 1 ]; then
                        CURRENT_PANEL=0
                    fi
                    ;;
            esac
            ;;
        ' ')  # Space - toggle selection
            if [ $CURRENT_PANEL -eq 1 ] && [ $item_count -gt 0 ]; then
                toggle_current_item
            fi
            ;;
        '')  # Enter - confirm
            return 1
            ;;
        'a'|'A')  # Select all / deselect all (Extra Themes only)
            if [ $CATEGORY_CURSOR -eq 20 ]; then
                toggle_all_themes
            fi
            ;;
        'r'|'R')  # Reset pending edit (Keybind Editor / Hyprland settings)
            if [ $CATEGORY_CURSOR -eq 5 ] && [ $CURRENT_PANEL -eq 1 ]; then
                unset 'BINDING_EDITS[$ITEM_CURSOR]'
            elif [ $CURRENT_PANEL -eq 1 ] && { [ $CATEGORY_CURSOR -eq 12 ] || [ $CATEGORY_CURSOR -eq 13 ] || [ $CATEGORY_CURSOR -eq 14 ] || [ $CATEGORY_CURSOR -eq 15 ]; }; then
                local hypr_arr
                case $CATEGORY_CURSOR in
                    12) hypr_arr="HYPR_GENERAL_ITEMS" ;;
                    13) hypr_arr="HYPR_DECORATION_ITEMS" ;;
                    14) hypr_arr="HYPR_INPUT_ITEMS" ;;
                    15) hypr_arr="HYPR_GESTURES_ITEMS" ;;
                esac
                local -n hypr_reset_ref="$hypr_arr"
                parse_hypr_item "${hypr_reset_ref[$ITEM_CURSOR]}"
                unset 'HYPR_EDITS[$HYPR_ID]'
            fi
            ;;
        'q'|'Q')  # Quit
            return 2
            ;;
    esac
    return 0
}

# Toggle all themes: if any are selected, deselect all; otherwise select all
toggle_all_themes() {
    local any_selected=false
    for entry in "${EXTRA_THEMES[@]}"; do
        local tname="${entry%%|*}"
        if [ "${THEME_SELECTIONS[$tname]:-0}" -eq 1 ]; then
            any_selected=true
            break
        fi
    done

    if [ "$any_selected" = true ]; then
        # Deselect all
        for entry in "${EXTRA_THEMES[@]}"; do
            local tname="${entry%%|*}"
            THEME_SELECTIONS[$tname]=0
        done
    else
        # Select all
        for entry in "${EXTRA_THEMES[@]}"; do
            local tname="${entry%%|*}"
            THEME_SELECTIONS[$tname]=1
        done
    fi
}

# Toggle the currently selected item
toggle_current_item() {
    local item_count=$(get_current_item_count)
    if [ $ITEM_CURSOR -ge $item_count ]; then
        return
    fi

    case $CATEGORY_CURSOR in
        1)  # Packages
            local pkg="${INSTALLED_PACKAGES[$ITEM_CURSOR]}"
            local cur="${PKG_SELECTIONS[$pkg]:-0}"
            if [ "$cur" -eq 0 ]; then
                PKG_SELECTIONS[$pkg]=1
            else
                PKG_SELECTIONS[$pkg]=0
            fi
            ;;
        2)  # Webapps
            local webapp="${INSTALLED_WEBAPPS[$ITEM_CURSOR]}"
            local cur="${WEBAPP_SELECTIONS[$webapp]:-0}"
            if [ "$cur" -eq 0 ]; then
                WEBAPP_SELECTIONS[$webapp]=1
            else
                WEBAPP_SELECTIONS[$webapp]=0
            fi
            ;;
        4|8|9)  # Toggle items (Keybindings, Appearance, Keyboard)
            local items_var=""
            case $CATEGORY_CURSOR in
                4) items_var="KEYBINDINGS_ITEMS" ;;
                8) items_var="APPEARANCE_ITEMS" ;;
                9) items_var="KEYBOARD_ITEMS" ;;
            esac
            local -n items_arr="$items_var"
            local item="${items_arr[$ITEM_CURSOR]}"
            parse_toggle_item "$item"

            local cur="${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}"
            # Cycle: 0 -> 1 -> 2 -> 0
            if [ "$cur" -eq 0 ]; then
                TOGGLE_SELECTIONS[$TOGGLE_ID]=1
            elif [ "$cur" -eq 1 ]; then
                TOGGLE_SELECTIONS[$TOGGLE_ID]=2
            else
                TOGGLE_SELECTIONS[$TOGGLE_ID]=0
            fi
            ;;
        7)  # System (mixed: toggle + action)
            local item="${SYSTEM_ITEMS[$ITEM_CURSOR]}"
            parse_toggle_item "$item"
            local cur="${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}"
            if [ "$TOGGLE_TYPE" = "action" ]; then
                # Action items open dialogs
                stty echo 2>/dev/null
                tput cnorm
                case "$TOGGLE_ID" in
                    power_profile)    show_power_profile_dialog ;;
                    power_auto_switch) show_power_auto_switch_dialog ;;
                    battery_limit)    show_battery_limit_dialog ;;
                esac
                tput civis
                stty -echo 2>/dev/null
            else
                # 3-state cycle: 0 -> 1 -> 2 -> 0
                if [ "$cur" -eq 0 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=1
                elif [ "$cur" -eq 1 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=2
                else
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=0
                fi
            fi
            ;;
        5)  # Keybind Editor - open edit dialog
            if ! is_binding_header $ITEM_CURSOR; then
                stty echo 2>/dev/null
                tput cnorm
                edit_binding $ITEM_CURSOR
                tput civis
                stty -echo 2>/dev/null
            fi
            ;;
        6)  # Display (mixed: toggle/radio + action)
            local item="${DISPLAY_ITEMS[$ITEM_CURSOR]}"
            parse_toggle_item "$item"
            local cur="${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}"
            if [ "$TOGGLE_TYPE" = "action" ]; then
                # Action items open dialogs
                stty echo 2>/dev/null
                tput cnorm
                case "$TOGGLE_ID" in
                    detect_monitors) show_monitor_detection_dialog ;;
                    position_monitors) show_position_editor_dialog ;;
                    primary_monitor) show_primary_monitor_dialog ;;
                esac
                tput civis
                stty -echo 2>/dev/null
            elif [ "$TOGGLE_TYPE" = "toggle" ]; then
                # 3-state cycle: 0 -> 1 -> 2 -> 0
                if [ "$cur" -eq 0 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=1
                elif [ "$cur" -eq 1 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=2
                else
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=0
                fi
            else
                # Radio: 3-state cycle
                if [ "$cur" -eq 0 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=1
                elif [ "$cur" -eq 1 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=2
                else
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=0
                fi
            fi
            ;;
        10)  # Utilities
            local item="${UTILITIES_ITEMS[$ITEM_CURSOR]}"
            parse_toggle_item "$item"
            local cur="${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}"
            if [ "$TOGGLE_TYPE" = "toggle" ]; then
                # 3-state cycle: 0 -> 1 -> 2 -> 0
                if [ "$cur" -eq 0 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=1
                elif [ "$cur" -eq 1 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=2
                else
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=0
                fi
            else
                # Simple action: 0 -> 1 -> 0
                if [ "$cur" -eq 0 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=1
                else
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=0
                fi
            fi
            ;;
        12|13|14|15)  # Hyprland settings - open edit dialog
            local hypr_arr
            case $CATEGORY_CURSOR in
                12) hypr_arr="HYPR_GENERAL_ITEMS" ;;
                13) hypr_arr="HYPR_DECORATION_ITEMS" ;;
                14) hypr_arr="HYPR_INPUT_ITEMS" ;;
                15) hypr_arr="HYPR_GESTURES_ITEMS" ;;
            esac
            local -n hypr_items_ref="$hypr_arr"
            stty echo 2>/dev/null
            tput cnorm
            edit_hypr_setting "${hypr_items_ref[$ITEM_CURSOR]}"
            tput civis
            stty -echo 2>/dev/null
            ;;
        17|18)  # ROG items (mixed: toggle + action)
            local rog_arr
            case $CATEGORY_CURSOR in
                17) rog_arr="ROG_HARDWARE_ITEMS" ;;
                18) rog_arr="ROG_LIGHTING_ITEMS" ;;
            esac
            local -n rog_items="$rog_arr"
            local item="${rog_items[$ITEM_CURSOR]}"
            parse_toggle_item "$item"
            local cur="${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}"
            if [ "$TOGGLE_TYPE" = "action" ]; then
                stty echo 2>/dev/null
                tput cnorm
                case "$TOGGLE_ID" in
                    rog_profile) show_rog_profile_dialog ;;
                    rog_kbd_leds) show_rog_kbd_leds_dialog ;;
                    rog_aura) show_rog_aura_dialog ;;
                    rog_aura_power) show_rog_aura_power_dialog ;;
                    rog_slash) show_rog_slash_dialog ;;
                    rog_slash_extra) show_rog_slash_extra_dialog ;;
                    rog_anime_extra) show_rog_anime_extra_dialog ;;
                    rog_battery) show_rog_battery_dialog ;;
                    rog_power_tuning) show_rog_power_tuning_dialog ;;
                    rog_fan_curve_edit) show_rog_fan_curve_dialog ;;
                esac
                tput civis
                stty -echo 2>/dev/null
            elif [ "$TOGGLE_TYPE" = "toggle" ]; then
                if [ "$cur" -eq 0 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=1
                elif [ "$cur" -eq 1 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=2
                else
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=0
                fi
            else
                if [ "$cur" -eq 0 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=1
                elif [ "$cur" -eq 1 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=2
                else
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=0
                fi
            fi
            ;;
        20) # Extra Themes (simple toggle)
            local entry="${EXTRA_THEMES[$ITEM_CURSOR]}"
            local tname="${entry%%|*}"
            local cur="${THEME_SELECTIONS[$tname]:-0}"
            if [ "$cur" -eq 0 ]; then
                THEME_SELECTIONS[$tname]=1
            else
                THEME_SELECTIONS[$tname]=0
            fi
            ;;
        22) # Themarchy (mixed: action + toggle)
            local item="${THEMARCHY_ITEMS[$ITEM_CURSOR]}"
            parse_toggle_item "$item"
            local cur="${TOGGLE_SELECTIONS[$TOGGLE_ID]:-0}"
            if [ "$TOGGLE_TYPE" = "action" ]; then
                stty echo 2>/dev/null
                tput cnorm
                show_themarchy_preview_dialog
                tput civis
                stty -echo 2>/dev/null
            else
                if [ "$cur" -eq 0 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=1
                elif [ "$cur" -eq 1 ]; then
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=2
                else
                    TOGGLE_SELECTIONS[$TOGGLE_ID]=0
                fi
            fi
            ;;
    esac
}

# Main selection loop
cleanup() {
    tput cnorm
    clear
    stty sane
}
trap cleanup EXIT
trap 'draw_interface' WINCH

tput civis
stty -echo

while true; do
    draw_interface
    handle_input
    result=$?
    if [ $result -eq 1 ]; then
        # Enter pressed - continue to confirmation
        break
    elif [ $result -eq 2 ]; then
        # Q pressed - quit
        clear
        echo
        echo "Cancelled."
        echo
        exit 0
    fi
done

stty echo
tput cnorm

# =============================================================================
# CONVERT SELECTIONS TO ACTION FLAGS
# =============================================================================

# Build lists of selected packages and webapps
declare -a SELECTED_PACKAGES_FINAL=()
declare -a SELECTED_WEBAPPS_FINAL=()

for pkg in "${INSTALLED_PACKAGES[@]}"; do
    if [ "${PKG_SELECTIONS[$pkg]:-0}" -eq 1 ]; then
        SELECTED_PACKAGES_FINAL+=("$pkg")
    fi
done

for webapp in "${INSTALLED_WEBAPPS[@]}"; do
    if [ "${WEBAPP_SELECTIONS[$webapp]:-0}" -eq 1 ]; then
        SELECTED_WEBAPPS_FINAL+=("$webapp")
    fi
done

# Build list of selected themes (filtering out already-installed)
declare -a SELECTED_THEMES_FINAL=()
for entry in "${EXTRA_THEMES[@]}"; do
    local_tname="${entry%%|*}"
    local_turl="${entry#*|}"
    if [ "${THEME_SELECTIONS[$local_tname]:-0}" -eq 1 ]; then
        local_tdir=$(get_theme_dir_name "$local_turl")
        if [[ -z "${INSTALLED_THEME_SET[$local_tdir]:-}" ]]; then
            SELECTED_THEMES_FINAL+=("$local_tname|$local_turl")
        fi
    fi
done

# Convert toggle selections to action flags
# close_window: 1=SUPER+Q (rebind), 2=SUPER+W (restore default)
RESET_KEYBINDS=false
RESTORE_KEYBINDS=false
case "${TOGGLE_SELECTIONS[close_window]:-0}" in
    1) RESET_KEYBINDS=true ;;
    2) RESTORE_KEYBINDS=true ;;
esac

# shutdown: 1=Bind, 2=Unbind
BIND_SHUTDOWN=false
UNBIND_SHUTDOWN=false
case "${TOGGLE_SELECTIONS[shutdown]:-0}" in
    1) BIND_SHUTDOWN=true ;;
    2) UNBIND_SHUTDOWN=true ;;
esac

# restart: 1=Bind, 2=Unbind
BIND_RESTART=false
UNBIND_RESTART=false
case "${TOGGLE_SELECTIONS[restart]:-0}" in
    1) BIND_RESTART=true ;;
    2) UNBIND_RESTART=true ;;
esac

# theme_menu: 1=Bind, 2=Unbind
BIND_THEME_MENU=false
UNBIND_THEME_MENU=false
case "${TOGGLE_SELECTIONS[theme_menu]:-0}" in
    1) BIND_THEME_MENU=true ;;
    2) UNBIND_THEME_MENU=true ;;
esac

# monitor_scale: 1=4K, 2=1080p/1440p
MONITOR_4K=false
MONITOR_1080_1440=false
case "${TOGGLE_SELECTIONS[monitor_scale]:-0}" in
    1) MONITOR_4K=true ;;
    2) MONITOR_1080_1440=true ;;
esac

# laptop_display: 1=Auto off, 2=Normal (remove)
LAPTOP_AUTO_OFF=false
LAPTOP_AUTO_NORMAL=false
case "${TOGGLE_SELECTIONS[laptop_display]:-0}" in
    1) LAPTOP_AUTO_OFF=true ;;
    2) LAPTOP_AUTO_NORMAL=true ;;
esac

# suspend: 1=Enable, 2=Disable
ENABLE_SUSPEND=false
DISABLE_SUSPEND=false
case "${TOGGLE_SELECTIONS[suspend]:-0}" in
    1) ENABLE_SUSPEND=true ;;
    2) DISABLE_SUSPEND=true ;;
esac

# hibernation: 1=Enable, 2=Disable
ENABLE_HIBERNATION=false
DISABLE_HIBERNATION=false
case "${TOGGLE_SELECTIONS[hibernation]:-0}" in
    1) ENABLE_HIBERNATION=true ;;
    2) DISABLE_HIBERNATION=true ;;
esac

# fingerprint: 1=Enable, 2=Disable
ENABLE_FINGERPRINT=false
DISABLE_FINGERPRINT=false
case "${TOGGLE_SELECTIONS[fingerprint]:-0}" in
    1) ENABLE_FINGERPRINT=true ;;
    2) DISABLE_FINGERPRINT=true ;;
esac

# fido2: 1=Enable, 2=Disable
ENABLE_FIDO2=false
DISABLE_FIDO2=false
case "${TOGGLE_SELECTIONS[fido2]:-0}" in
    1) ENABLE_FIDO2=true ;;
    2) DISABLE_FIDO2=true ;;
esac

# rounded_corners: 1=Enable, 2=Disable
ENABLE_ROUNDED_CORNERS=false
DISABLE_ROUNDED_CORNERS=false
case "${TOGGLE_SELECTIONS[rounded_corners]:-0}" in
    1) ENABLE_ROUNDED_CORNERS=true ;;
    2) DISABLE_ROUNDED_CORNERS=true ;;
esac

# window_gaps: 1=Remove, 2=Restore
REMOVE_WINDOW_GAPS=false
RESTORE_WINDOW_GAPS=false
case "${TOGGLE_SELECTIONS[window_gaps]:-0}" in
    1) REMOVE_WINDOW_GAPS=true ;;
    2) RESTORE_WINDOW_GAPS=true ;;
esac

# transparency: 1=Remove, 2=Restore
REMOVE_TRANSPARENCY=false
RESTORE_TRANSPARENCY=false
case "${TOGGLE_SELECTIONS[transparency]:-0}" in
    1) REMOVE_TRANSPARENCY=true ;;
    2) RESTORE_TRANSPARENCY=true ;;
esac

# tray_icons: 1=Show all, 2=Hide
SHOW_ALL_TRAY_ICONS=false
HIDE_TRAY_ICONS=false
case "${TOGGLE_SELECTIONS[tray_icons]:-0}" in
    1) SHOW_ALL_TRAY_ICONS=true ;;
    2) HIDE_TRAY_ICONS=true ;;
esac

# omarchy_logo: 1=Remove, 2=Restore
REMOVE_OMARCHY_LOGO=false
RESTORE_OMARCHY_LOGO=false
case "${TOGGLE_SELECTIONS[omarchy_logo]:-0}" in
    1) REMOVE_OMARCHY_LOGO=true ;;
    2) RESTORE_OMARCHY_LOGO=true ;;
esac

# update_icon: 1=Remove, 2=Restore
REMOVE_UPDATE_ICON=false
RESTORE_UPDATE_ICON=false
case "${TOGGLE_SELECTIONS[update_icon]:-0}" in
    1) REMOVE_UPDATE_ICON=true ;;
    2) RESTORE_UPDATE_ICON=true ;;
esac

# clock_format: 1=12h, 2=24h
ENABLE_12H_CLOCK=false
DISABLE_12H_CLOCK=false
case "${TOGGLE_SELECTIONS[clock_format]:-0}" in
    1) ENABLE_12H_CLOCK=true ;;
    2) DISABLE_12H_CLOCK=true ;;
esac

# media_dirs: 1=Enable, 2=Disable
ENABLE_MEDIA_DIRECTORIES=false
DISABLE_MEDIA_DIRECTORIES=false
case "${TOGGLE_SELECTIONS[media_dirs]:-0}" in
    1) ENABLE_MEDIA_DIRECTORIES=true ;;
    2) DISABLE_MEDIA_DIRECTORIES=true ;;
esac

# clock_date: 1=Show, 2=Hide
SHOW_CLOCK_DATE=false
HIDE_CLOCK_DATE=false
case "${TOGGLE_SELECTIONS[clock_date]:-0}" in
    1) SHOW_CLOCK_DATE=true ;;
    2) HIDE_CLOCK_DATE=true ;;
esac

# window_title: 1=Show, 2=Hide
SHOW_WINDOW_TITLE=false
HIDE_WINDOW_TITLE=false
case "${TOGGLE_SELECTIONS[window_title]:-0}" in
    1) SHOW_WINDOW_TITLE=true ;;
    2) HIDE_WINDOW_TITLE=true ;;
esac

# caps_lock: 1=Normal (restore), 2=Compose
RESTORE_CAPSLOCK=false
USE_CAPSLOCK_COMPOSE=false
case "${TOGGLE_SELECTIONS[caps_lock]:-0}" in
    1) RESTORE_CAPSLOCK=true ;;
    2) USE_CAPSLOCK_COMPOSE=true ;;
esac

# alt_super: 1=Swap, 2=Normal (restore)
SWAP_ALT_SUPER=false
RESTORE_ALT_SUPER=false
case "${TOGGLE_SELECTIONS[alt_super]:-0}" in
    1) SWAP_ALT_SUPER=true ;;
    2) RESTORE_ALT_SUPER=true ;;
esac

# backup_config: 1=Select
BACKUP_CONFIGS=false
if [ "${TOGGLE_SELECTIONS[backup_config]:-0}" -eq 1 ]; then
    BACKUP_CONFIGS=true
fi

# menu_shortcut: 1=Add, 2=Remove
ADD_MENU_SHORTCUT=false
REMOVE_MENU_SHORTCUT=false
case "${TOGGLE_SELECTIONS[menu_shortcut]:-0}" in
    1) ADD_MENU_SHORTCUT=true ;;
    2) REMOVE_MENU_SHORTCUT=true ;;
esac

# themarchy_keybind: 1=Enable, 2=Disable
BIND_THEMARCHY=false
UNBIND_THEMARCHY=false
case "${TOGGLE_SELECTIONS[themarchy_keybind]:-0}" in
    1) BIND_THEMARCHY=true ;;
    2) UNBIND_THEMARCHY=true ;;
esac

# rog_fan_curves: 1=Enable, 2=Disable
ROG_ENABLE_FAN_CURVES=false
ROG_DISABLE_FAN_CURVES=false
case "${TOGGLE_SELECTIONS[rog_fan_curves]:-0}" in
    1) ROG_ENABLE_FAN_CURVES=true ;;
    2) ROG_DISABLE_FAN_CURVES=true ;;
esac

# rog_boot_sound: 1=Enable, 2=Disable
ROG_ENABLE_BOOT_SOUND=false
ROG_DISABLE_BOOT_SOUND=false
case "${TOGGLE_SELECTIONS[rog_boot_sound]:-0}" in
    1) ROG_ENABLE_BOOT_SOUND=true ;;
    2) ROG_DISABLE_BOOT_SOUND=true ;;
esac

# rog_panel_od: 1=Enable, 2=Disable
ROG_ENABLE_PANEL_OD=false
ROG_DISABLE_PANEL_OD=false
case "${TOGGLE_SELECTIONS[rog_panel_od]:-0}" in
    1) ROG_ENABLE_PANEL_OD=true ;;
    2) ROG_DISABLE_PANEL_OD=true ;;
esac

# rog_dgpu: 1=Enable, 2=Disable
ROG_ENABLE_DGPU=false
ROG_DISABLE_DGPU=false
case "${TOGGLE_SELECTIONS[rog_dgpu]:-0}" in
    1) ROG_ENABLE_DGPU=true ;;
    2) ROG_DISABLE_DGPU=true ;;
esac

# rog_gpu_mux: 1=dGPU, 2=Hybrid
ROG_DGPU_MUX=false
ROG_HYBRID_MUX=false
case "${TOGGLE_SELECTIONS[rog_gpu_mux]:-0}" in
    1) ROG_DGPU_MUX=true ;;
    2) ROG_HYBRID_MUX=true ;;
esac

# rog_anime: 1=Enable, 2=Disable
ROG_ENABLE_ANIME=false
ROG_DISABLE_ANIME=false
case "${TOGGLE_SELECTIONS[rog_anime]:-0}" in
    1) ROG_ENABLE_ANIME=true ;;
    2) ROG_DISABLE_ANIME=true ;;
esac

# Check if anything was selected
has_selection=false
if [ ${#SELECTED_PACKAGES_FINAL[@]} -gt 0 ] || [ ${#SELECTED_WEBAPPS_FINAL[@]} -gt 0 ] || [ ${#SELECTED_THEMES_FINAL[@]} -gt 0 ]; then
    has_selection=true
fi
for key in "${!TOGGLE_SELECTIONS[@]}"; do
    if [ "${TOGGLE_SELECTIONS[$key]}" -ne 0 ]; then
        has_selection=true
        break
    fi
done
if [ ${#BINDING_EDITS[@]} -gt 0 ]; then
    has_selection=true
fi
if [ ${#HYPR_EDITS[@]} -gt 0 ]; then
    has_selection=true
fi
if [ "$MONITORS_POSITIONED" -eq 1 ]; then
    has_selection=true
fi
if [[ -n "$SELECTED_POWER_PROFILE" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_POWER_AUTO_SWITCH" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_BATTERY_LIMIT" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_PRIMARY_MONITOR" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_ROG_PROFILE" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_ROG_KBD_LEDS" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_ROG_AURA_EFFECT" ]]; then
    has_selection=true
fi
if [[ -n "$ROG_SLASH_ENABLE" || -n "$SELECTED_ROG_SLASH_MODE" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_ROG_SLASH_INTERVAL$ROG_SLASH_SHOW_BOOT$ROG_SLASH_SHOW_SHUTDOWN$ROG_SLASH_SHOW_SLEEP$ROG_SLASH_SHOW_BATTERY$ROG_SLASH_SHOW_BATTERY_WARN" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_ROG_BATTERY_LIMIT" || "$ROG_BATTERY_ONESHOT" == "true" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_ROG_NV_DYNAMIC_BOOST$SELECTED_ROG_NV_TEMP_TARGET$SELECTED_ROG_PPT_PL1_SPL$SELECTED_ROG_PPT_PL2_SPPT" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_ROG_AURA_POWER_ZONE" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_ROG_ANIME_BRIGHTNESS$ROG_ANIME_POWERSAVE$ROG_ANIME_OFF_UNPLUGGED$ROG_ANIME_OFF_SUSPENDED$ROG_ANIME_OFF_LID_CLOSED$SELECTED_ROG_ANIME_BOOT" ]]; then
    has_selection=true
fi
if [[ -n "$SELECTED_ROG_FAN_CURVE_DATA" || "$ROG_FAN_CURVE_DEFAULT" == "true" ]]; then
    has_selection=true
fi
if [ "$APPLY_THEMARCHY" = true ] || [ "$BIND_THEMARCHY" = true ] || [ "$UNBIND_THEMARCHY" = true ]; then
    has_selection=true
fi

if [ "$has_selection" = false ]; then
    clear
    echo
    echo "Nothing selected."
    echo
    exit 0
fi

# Handle backup timing
BACKUP_BEFORE=false
BACKUP_AFTER=false
if [ "$BACKUP_CONFIGS" = true ]; then
    # Check if any tweaks are also selected (config-modifying actions)
    has_tweaks=false
    for key in "${!TOGGLE_SELECTIONS[@]}"; do
        if [ "$key" != "backup_config" ] && [ "${TOGGLE_SELECTIONS[$key]}" -ne 0 ]; then
            has_tweaks=true
            break
        fi
    done
    if [ ${#BINDING_EDITS[@]} -gt 0 ] || [ ${#HYPR_EDITS[@]} -gt 0 ]; then
        has_tweaks=true
    fi

    if [ "$has_tweaks" = true ]; then
        clear
        echo
        echo
        echo -e "${BOLD}  Backup Timing${RESET}"
        echo
        echo -e "  ${DIM}You selected tweaks that will modify config files.${RESET}"
        echo -e "  ${DIM}When would you like to back up?${RESET}"
        echo
        echo -e "    ${BOLD}1)${RESET}  Before changes  ${DIM}(preserve current state)${RESET}"
        echo -e "    ${BOLD}2)${RESET}  After changes   ${DIM}(save new configuration)${RESET}"
        echo -e "    ${BOLD}3)${RESET}  Both            ${DIM}(before and after)${RESET}"
        echo
        while true; do
            printf "  ${BOLD}Select (1-3):${RESET} "
            read -r < /dev/tty
            case "$REPLY" in
                1) BACKUP_BEFORE=true; break ;;
                2) BACKUP_AFTER=true; break ;;
                3) BACKUP_BEFORE=true; BACKUP_AFTER=true; break ;;
                *) echo -e "  ${DIM}Invalid selection. Please enter 1, 2, or 3.${RESET}" ;;
            esac
        done
    else
        BACKUP_BEFORE=true
    fi
fi

# =============================================================================
# MASTER CONFIRMATION — apply all at once or confirm each individually
# =============================================================================
clear
echo
echo
echo -e "${BOLD}  Confirm Actions${RESET}"
echo

declare -a ACTION_SUMMARY=()

[ ${#SELECTED_PACKAGES_FINAL[@]} -gt 0 ] && ACTION_SUMMARY+=("Remove ${#SELECTED_PACKAGES_FINAL[@]} package(s)")
[ ${#SELECTED_WEBAPPS_FINAL[@]} -gt 0 ] && ACTION_SUMMARY+=("Remove ${#SELECTED_WEBAPPS_FINAL[@]} web app(s)")
[ "$RESET_KEYBINDS" = true ] && ACTION_SUMMARY+=("Rebind close window to SUPER+Q")
[ "$RESTORE_KEYBINDS" = true ] && ACTION_SUMMARY+=("Restore close window to SUPER+W")
[ "$BIND_SHUTDOWN" = true ] && ACTION_SUMMARY+=("Bind shutdown to SUPER+ALT+S")
[ "$UNBIND_SHUTDOWN" = true ] && ACTION_SUMMARY+=("Unbind shutdown")
[ "$BIND_RESTART" = true ] && ACTION_SUMMARY+=("Bind restart to SUPER+ALT+R")
[ "$UNBIND_RESTART" = true ] && ACTION_SUMMARY+=("Unbind restart")
[ "$BIND_THEME_MENU" = true ] && ACTION_SUMMARY+=("Bind theme menu to ALT+T")
[ "$UNBIND_THEME_MENU" = true ] && ACTION_SUMMARY+=("Unbind theme menu")
[ ${#BINDING_EDITS[@]} -gt 0 ] && ACTION_SUMMARY+=("Apply ${#BINDING_EDITS[@]} keybinding edit(s)")
[ ${#HYPR_EDITS[@]} -gt 0 ] && ACTION_SUMMARY+=("Apply ${#HYPR_EDITS[@]} Hyprland setting(s)")
[ "$RESTORE_CAPSLOCK" = true ] && ACTION_SUMMARY+=("Restore Caps Lock")
[ "$USE_CAPSLOCK_COMPOSE" = true ] && ACTION_SUMMARY+=("Use Caps Lock for compose")
[ "$SWAP_ALT_SUPER" = true ] && ACTION_SUMMARY+=("Swap Alt and Super keys")
[ "$RESTORE_ALT_SUPER" = true ] && ACTION_SUMMARY+=("Restore Alt/Super keys")
[ "$MONITOR_4K" = true ] && ACTION_SUMMARY+=("Set monitor scaling: 4K")
[ "$MONITOR_1080_1440" = true ] && ACTION_SUMMARY+=("Set monitor scaling: 1080p/1440p")
[ "$MONITORS_POSITIONED" -eq 1 ] 2>/dev/null && ACTION_SUMMARY+=("Apply monitor positions")
[ "$LAPTOP_AUTO_OFF" = true ] && ACTION_SUMMARY+=("Enable laptop display auto-off")
[ "$LAPTOP_AUTO_NORMAL" = true ] && ACTION_SUMMARY+=("Disable laptop display auto-off")
[[ -n "$SELECTED_POWER_PROFILE" ]] && ACTION_SUMMARY+=("Set power profile: $SELECTED_POWER_PROFILE")
if [[ "$SELECTED_POWER_AUTO_SWITCH" == "disabled" ]]; then
    ACTION_SUMMARY+=("Disable power profile auto-switching")
elif [[ "$SELECTED_POWER_AUTO_SWITCH" == "enabled" ]]; then
    ACTION_SUMMARY+=("Set power auto-switch: AC=$SELECTED_POWER_AC_PROFILE, battery=$SELECTED_POWER_BATTERY_PROFILE")
fi
[[ -n "$SELECTED_BATTERY_LIMIT" ]] && ACTION_SUMMARY+=("Set battery limit: ${SELECTED_BATTERY_LIMIT}%")
[[ -n "$SELECTED_PRIMARY_MONITOR" ]] && ACTION_SUMMARY+=("Set primary monitor: $SELECTED_PRIMARY_MONITOR")
[[ -n "$SELECTED_ROG_PROFILE" ]] && ACTION_SUMMARY+=("Set ROG profile: $SELECTED_ROG_PROFILE")
[[ -n "$SELECTED_ROG_KBD_LEDS" ]] && ACTION_SUMMARY+=("Set keyboard LEDs: $SELECTED_ROG_KBD_LEDS")
[[ -n "$SELECTED_ROG_AURA_EFFECT" ]] && ACTION_SUMMARY+=("Set Aura RGB: $SELECTED_ROG_AURA_EFFECT")
[[ -n "$ROG_SLASH_ENABLE" ]] && ACTION_SUMMARY+=("${ROG_SLASH_ENABLE^} Slash Ledbar")
[[ -n "$SELECTED_ROG_SLASH_MODE" ]] && ACTION_SUMMARY+=("Set Slash mode: $SELECTED_ROG_SLASH_MODE")
[ "$ROG_ENABLE_FAN_CURVES" = true ] && ACTION_SUMMARY+=("Enable fan curves")
[ "$ROG_DISABLE_FAN_CURVES" = true ] && ACTION_SUMMARY+=("Disable fan curves")
[ "$ROG_ENABLE_BOOT_SOUND" = true ] && ACTION_SUMMARY+=("Enable boot sound")
[ "$ROG_DISABLE_BOOT_SOUND" = true ] && ACTION_SUMMARY+=("Disable boot sound")
[ "$ROG_ENABLE_PANEL_OD" = true ] && ACTION_SUMMARY+=("Enable panel overdrive")
[ "$ROG_DISABLE_PANEL_OD" = true ] && ACTION_SUMMARY+=("Disable panel overdrive")
[ "$ROG_ENABLE_DGPU" = true ] && ACTION_SUMMARY+=("Enable discrete GPU")
[ "$ROG_DISABLE_DGPU" = true ] && ACTION_SUMMARY+=("Disable discrete GPU")
[ "$ROG_DGPU_MUX" = true ] && ACTION_SUMMARY+=("Set GPU MUX to dGPU direct")
[ "$ROG_HYBRID_MUX" = true ] && ACTION_SUMMARY+=("Set GPU MUX to hybrid")
[ "$ROG_ENABLE_ANIME" = true ] && ACTION_SUMMARY+=("Enable AniMe Matrix")
[ "$ROG_DISABLE_ANIME" = true ] && ACTION_SUMMARY+=("Disable AniMe Matrix")
[[ -n "$SELECTED_ROG_BATTERY_LIMIT" ]] && ACTION_SUMMARY+=("Set ROG battery limit: ${SELECTED_ROG_BATTERY_LIMIT}%")
[[ "$ROG_BATTERY_ONESHOT" == "true" ]] && ACTION_SUMMARY+=("One-shot full charge")
[[ -n "$SELECTED_ROG_NV_DYNAMIC_BOOST" ]] && ACTION_SUMMARY+=("Set NVIDIA dynamic boost: ${SELECTED_ROG_NV_DYNAMIC_BOOST}W")
[[ -n "$SELECTED_ROG_NV_TEMP_TARGET" ]] && ACTION_SUMMARY+=("Set NVIDIA temp target: ${SELECTED_ROG_NV_TEMP_TARGET}°C")
[[ -n "$SELECTED_ROG_PPT_PL1_SPL" ]] && ACTION_SUMMARY+=("Set CPU sustained power: ${SELECTED_ROG_PPT_PL1_SPL}W")
[[ -n "$SELECTED_ROG_PPT_PL2_SPPT" ]] && ACTION_SUMMARY+=("Set CPU short boost: ${SELECTED_ROG_PPT_PL2_SPPT}W")
[[ -n "$SELECTED_ROG_AURA_POWER_ZONE" ]] && ACTION_SUMMARY+=("Configure Aura power zones")
[[ -n "$SELECTED_ROG_SLASH_INTERVAL" ]] && ACTION_SUMMARY+=("Set Slash interval: $SELECTED_ROG_SLASH_INTERVAL")
[[ -n "$ROG_SLASH_SHOW_BOOT" ]] && ACTION_SUMMARY+=("Slash show-on-boot: $ROG_SLASH_SHOW_BOOT")
[[ -n "$ROG_SLASH_SHOW_SHUTDOWN" ]] && ACTION_SUMMARY+=("Slash show-on-shutdown: $ROG_SLASH_SHOW_SHUTDOWN")
[[ -n "$ROG_SLASH_SHOW_SLEEP" ]] && ACTION_SUMMARY+=("Slash show-on-sleep: $ROG_SLASH_SHOW_SLEEP")
[[ -n "$ROG_SLASH_SHOW_BATTERY" ]] && ACTION_SUMMARY+=("Slash show-on-battery: $ROG_SLASH_SHOW_BATTERY")
[[ -n "$ROG_SLASH_SHOW_BATTERY_WARN" ]] && ACTION_SUMMARY+=("Slash battery warning: $ROG_SLASH_SHOW_BATTERY_WARN")
[[ -n "$SELECTED_ROG_ANIME_BRIGHTNESS" ]] && ACTION_SUMMARY+=("Set AniMe brightness: $SELECTED_ROG_ANIME_BRIGHTNESS")
[[ -n "$ROG_ANIME_POWERSAVE" ]] && ACTION_SUMMARY+=("AniMe powersave animation: $ROG_ANIME_POWERSAVE")
[[ -n "$ROG_ANIME_OFF_UNPLUGGED" ]] && ACTION_SUMMARY+=("AniMe off-when-unplugged: $ROG_ANIME_OFF_UNPLUGGED")
[[ -n "$ROG_ANIME_OFF_SUSPENDED" ]] && ACTION_SUMMARY+=("AniMe off-when-suspended: $ROG_ANIME_OFF_SUSPENDED")
[[ -n "$ROG_ANIME_OFF_LID_CLOSED" ]] && ACTION_SUMMARY+=("AniMe off-when-lid-closed: $ROG_ANIME_OFF_LID_CLOSED")
[[ -n "$SELECTED_ROG_ANIME_BOOT" ]] && ACTION_SUMMARY+=("Set AniMe builtin animations")
[[ -n "$SELECTED_ROG_FAN_CURVE_DATA" ]] && ACTION_SUMMARY+=("Set ${SELECTED_ROG_FAN_CURVE_FAN^^} fan curve data")
[[ "$ROG_FAN_CURVE_DEFAULT" == "true" ]] && ACTION_SUMMARY+=("Reset fan curves to default")
[ "$ENABLE_SUSPEND" = true ] && ACTION_SUMMARY+=("Enable suspend")
[ "$DISABLE_SUSPEND" = true ] && ACTION_SUMMARY+=("Disable suspend")
[ "$ENABLE_HIBERNATION" = true ] && ACTION_SUMMARY+=("Enable hibernation")
[ "$DISABLE_HIBERNATION" = true ] && ACTION_SUMMARY+=("Disable hibernation")
[ "$ENABLE_FINGERPRINT" = true ] && ACTION_SUMMARY+=("Enable fingerprint auth")
[ "$DISABLE_FINGERPRINT" = true ] && ACTION_SUMMARY+=("Disable fingerprint auth")
[ "$ENABLE_FIDO2" = true ] && ACTION_SUMMARY+=("Enable FIDO2 auth")
[ "$DISABLE_FIDO2" = true ] && ACTION_SUMMARY+=("Disable FIDO2 auth")
[ "$SHOW_ALL_TRAY_ICONS" = true ] && ACTION_SUMMARY+=("Show all tray icons")
[ "$HIDE_TRAY_ICONS" = true ] && ACTION_SUMMARY+=("Hide tray icons")
[ "$REMOVE_OMARCHY_LOGO" = true ] && ACTION_SUMMARY+=("Remove Omarchy logo from bar")
[ "$RESTORE_OMARCHY_LOGO" = true ] && ACTION_SUMMARY+=("Restore Omarchy logo to bar")
[ "$REMOVE_UPDATE_ICON" = true ] && ACTION_SUMMARY+=("Remove update icon from bar")
[ "$RESTORE_UPDATE_ICON" = true ] && ACTION_SUMMARY+=("Restore update icon to bar")
[ "$ENABLE_ROUNDED_CORNERS" = true ] && ACTION_SUMMARY+=("Enable rounded corners")
[ "$DISABLE_ROUNDED_CORNERS" = true ] && ACTION_SUMMARY+=("Disable rounded corners")
[ "$REMOVE_WINDOW_GAPS" = true ] && ACTION_SUMMARY+=("Remove window gaps")
[ "$RESTORE_WINDOW_GAPS" = true ] && ACTION_SUMMARY+=("Restore window gaps")
[ "$REMOVE_TRANSPARENCY" = true ] && ACTION_SUMMARY+=("Remove transparency")
[ "$RESTORE_TRANSPARENCY" = true ] && ACTION_SUMMARY+=("Restore transparency")
[ "$ENABLE_12H_CLOCK" = true ] && ACTION_SUMMARY+=("Enable 12-hour clock")
[ "$DISABLE_12H_CLOCK" = true ] && ACTION_SUMMARY+=("Disable 12-hour clock")
[ "$SHOW_CLOCK_DATE" = true ] && ACTION_SUMMARY+=("Show clock date")
[ "$HIDE_CLOCK_DATE" = true ] && ACTION_SUMMARY+=("Hide clock date")
[ "$SHOW_WINDOW_TITLE" = true ] && ACTION_SUMMARY+=("Show window title")
[ "$HIDE_WINDOW_TITLE" = true ] && ACTION_SUMMARY+=("Hide window title")
[ "$ENABLE_MEDIA_DIRECTORIES" = true ] && ACTION_SUMMARY+=("Enable media directories")
[ "$DISABLE_MEDIA_DIRECTORIES" = true ] && ACTION_SUMMARY+=("Disable media directories")
[ "$ADD_MENU_SHORTCUT" = true ] && ACTION_SUMMARY+=("Add menu shortcut")
[ "$REMOVE_MENU_SHORTCUT" = true ] && ACTION_SUMMARY+=("Remove menu shortcut")
[ ${#SELECTED_THEMES_FINAL[@]} -gt 0 ] && ACTION_SUMMARY+=("Install ${#SELECTED_THEMES_FINAL[@]} theme(s)")
[ "$APPLY_THEMARCHY" = true ] && ACTION_SUMMARY+=("Apply Themarchy theme from wallpaper")
[ "$BIND_THEMARCHY" = true ] && ACTION_SUMMARY+=("Enable Themarchy keybind (SUPER+SHIFT+T)")
[ "$UNBIND_THEMARCHY" = true ] && ACTION_SUMMARY+=("Disable Themarchy keybind (SUPER+SHIFT+T)")
[ "$BACKUP_BEFORE" = true ] && ACTION_SUMMARY+=("Backup config (before changes)")
[ "$BACKUP_AFTER" = true ] && ACTION_SUMMARY+=("Backup config (after changes)")

for item in "${ACTION_SUMMARY[@]}"; do
    echo -e "    ${DIM}•${RESET}  $item"
done

echo
echo
echo -e "    ${BOLD}1)${RESET}  Apply all       ${DIM}(no further prompts)${RESET}"
echo -e "    ${BOLD}2)${RESET}  Confirm each    ${DIM}(review each action individually)${RESET}"
echo -e "    ${BOLD}3)${RESET}  Cancel"
echo

while true; do
    printf "  ${BOLD}Select (1-3):${RESET} "
    read -r < /dev/tty
    case "$REPLY" in
        1) CONFIRM_ALL=true; break ;;
        2) break ;;
        3) echo; echo "  Cancelled."; echo; exit 0 ;;
        *) echo -e "  ${DIM}Invalid selection. Please enter 1, 2, or 3.${RESET}" ;;
    esac
done

# Run backup before changes if requested
if [ "$BACKUP_BEFORE" = true ]; then
    backup_configs
fi

# Handle keybind reset (runs its own confirmation flow)
if [ "$RESET_KEYBINDS" = true ]; then
    rebind_close_window
fi

# Handle keybind restore (runs its own confirmation flow)
if [ "$RESTORE_KEYBINDS" = true ]; then
    restore_close_window
fi

# Handle monitor scaling (runs its own confirmation flow)
if [ "$MONITOR_4K" = true ]; then
    set_monitor_4k
fi

if [ "$MONITOR_1080_1440" = true ]; then
    set_monitor_1080_1440
fi

# Apply monitor positions (from position editor)
if [ "$MONITORS_POSITIONED" -eq 1 ]; then
    apply_monitor_positions
fi

if [ "$LAPTOP_AUTO_OFF" = true ]; then
    setup_laptop_auto_off
fi

if [ "$LAPTOP_AUTO_NORMAL" = true ]; then
    remove_laptop_auto_off
fi

if [[ -n "$SELECTED_POWER_PROFILE" ]]; then
    apply_power_profile
fi

if [[ -n "$SELECTED_POWER_AUTO_SWITCH" ]]; then
    apply_power_auto_switch
fi

if [[ -n "$SELECTED_BATTERY_LIMIT" ]]; then
    apply_battery_limit
fi

if [[ -n "$SELECTED_PRIMARY_MONITOR" ]]; then
    apply_primary_monitor
fi

if [[ -n "$SELECTED_ROG_PROFILE" ]]; then
    apply_rog_profile
fi

if [[ -n "$SELECTED_ROG_KBD_LEDS" ]]; then
    apply_rog_kbd_leds
fi

if [[ -n "$SELECTED_ROG_AURA_EFFECT" ]]; then
    apply_rog_aura
fi

if [[ -n "$ROG_SLASH_ENABLE" || -n "$SELECTED_ROG_SLASH_MODE" ]]; then
    apply_rog_slash
fi

apply_rog_slash_extra

apply_rog_hardware_toggles

if [[ -n "$SELECTED_ROG_BATTERY_LIMIT" || "$ROG_BATTERY_ONESHOT" == "true" ]]; then
    apply_rog_battery
fi

apply_rog_power_tuning

apply_rog_aura_power

apply_rog_anime_extra

apply_rog_fan_curve

if [ "$BIND_SHUTDOWN" = true ]; then
    bind_shutdown
fi

if [ "$BIND_RESTART" = true ]; then
    bind_restart
fi

if [ "$UNBIND_SHUTDOWN" = true ]; then
    unbind_shutdown
fi

if [ "$UNBIND_RESTART" = true ]; then
    unbind_restart
fi

if [ "$BIND_THEME_MENU" = true ]; then
    bind_theme_menu
fi

if [ "$UNBIND_THEME_MENU" = true ]; then
    unbind_theme_menu
fi

# Apply keybind editor changes
apply_binding_edits

# Apply Hyprland setting changes
apply_hypr_edits

if [ "$RESTORE_CAPSLOCK" = true ]; then
    restore_capslock
fi

if [ "$USE_CAPSLOCK_COMPOSE" = true ]; then
    use_capslock_compose
fi

if [ "$SWAP_ALT_SUPER" = true ]; then
    swap_alt_super
fi

if [ "$RESTORE_ALT_SUPER" = true ]; then
    restore_alt_super
fi

if [ "$ENABLE_SUSPEND" = true ]; then
    enable_suspend
fi

if [ "$DISABLE_SUSPEND" = true ]; then
    disable_suspend
fi

if [ "$ENABLE_HIBERNATION" = true ]; then
    enable_hibernation
fi

if [ "$DISABLE_HIBERNATION" = true ]; then
    disable_hibernation
fi

if [ "$ENABLE_FINGERPRINT" = true ]; then
    enable_fingerprint
fi

if [ "$DISABLE_FINGERPRINT" = true ]; then
    disable_fingerprint
fi

if [ "$ENABLE_FIDO2" = true ]; then
    enable_fido2
fi

if [ "$DISABLE_FIDO2" = true ]; then
    disable_fido2
fi

if [ "$SHOW_ALL_TRAY_ICONS" = true ]; then
    show_all_tray_icons
fi

if [ "$HIDE_TRAY_ICONS" = true ]; then
    hide_tray_icons
fi

if [ "$REMOVE_OMARCHY_LOGO" = true ]; then
    remove_omarchy_logo
fi

if [ "$RESTORE_OMARCHY_LOGO" = true ]; then
    restore_omarchy_logo
fi

if [ "$REMOVE_UPDATE_ICON" = true ]; then
    remove_update_icon
fi

if [ "$RESTORE_UPDATE_ICON" = true ]; then
    restore_update_icon
fi

if [ "$ENABLE_ROUNDED_CORNERS" = true ]; then
    enable_rounded_corners
fi

if [ "$DISABLE_ROUNDED_CORNERS" = true ]; then
    disable_rounded_corners
fi

if [ "$REMOVE_WINDOW_GAPS" = true ]; then
    remove_window_gaps
fi

if [ "$RESTORE_WINDOW_GAPS" = true ]; then
    restore_window_gaps
fi

if [ "$REMOVE_TRANSPARENCY" = true ]; then
    remove_transparency
fi

if [ "$RESTORE_TRANSPARENCY" = true ]; then
    restore_transparency
fi

if [ "$ENABLE_12H_CLOCK" = true ]; then
    enable_12h_clock
fi

if [ "$DISABLE_12H_CLOCK" = true ]; then
    disable_12h_clock
fi

if [ "$ENABLE_MEDIA_DIRECTORIES" = true ]; then
    enable_media_directories
fi

if [ "$DISABLE_MEDIA_DIRECTORIES" = true ]; then
    disable_media_directories
fi

if [ "$SHOW_CLOCK_DATE" = true ]; then
    show_clock_date
fi

if [ "$HIDE_CLOCK_DATE" = true ]; then
    hide_clock_date
fi

if [ "$SHOW_WINDOW_TITLE" = true ]; then
    show_window_title
fi

if [ "$HIDE_WINDOW_TITLE" = true ]; then
    hide_window_title
fi

if [ "$ADD_MENU_SHORTCUT" = true ]; then
    add_to_omarchy_menu
fi

if [ "$REMOVE_MENU_SHORTCUT" = true ]; then
    remove_from_omarchy_menu
fi

if [ "$APPLY_THEMARCHY" = true ]; then
    apply_themarchy_now
fi

if [ "$BIND_THEMARCHY" = true ]; then
    setup_themarchy_keybind
fi

if [ "$UNBIND_THEMARCHY" = true ]; then
    remove_themarchy_keybind
fi

# Run backup after changes if requested
if [ "$BACKUP_AFTER" = true ]; then
    backup_configs
fi

# Handle theme installations
if [ ${#SELECTED_THEMES_FINAL[@]} -gt 0 ]; then
    clear
    echo
    echo
    echo -e "${BOLD}  Confirm Theme Installation${RESET}"
    echo
    echo
    echo -e "${DIM}  Themes (${#SELECTED_THEMES_FINAL[@]}):${RESET}"
    for entry in "${SELECTED_THEMES_FINAL[@]}"; do
        local_tname="${entry%%|*}"
        echo -e "    ${DIM}•${RESET}  $local_tname"
    done
    echo
    echo
    echo -e "${DIM}  The last theme installed will become the active theme.${RESET}"
    echo
    echo
    if [[ "$CONFIRM_ALL" != true ]]; then
        printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
        read -r < /dev/tty
    fi

    if [[ "$CONFIRM_ALL" == true ]] || [[ $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        echo
        echo "  Installing themes..."
        echo

        local_current=0
        local_total=${#SELECTED_THEMES_FINAL[@]}
        local_timeout=30

        for entry in "${SELECTED_THEMES_FINAL[@]}"; do
            local_tname="${entry%%|*}"
            local_turl="${entry#*|}"
            ((local_current++))

            echo -e "  ${DIM}[$local_current/$local_total]${RESET} Installing $local_tname..."

            if timeout $local_timeout omarchy-theme-install "$local_turl" >/dev/null 2>&1; then
                echo -e "    ${CHECKED}✓${RESET}  Installed: $local_tname"
                SUMMARY_LOG+=("✓  Installed theme: $local_tname")
            elif [ $? -eq 124 ]; then
                echo -e "    ${DIM}✗${RESET}  Skipped: $local_tname (timed out -- may require GitHub auth)"
                SUMMARY_LOG+=("✗  Skipped theme: $local_tname (timed out)")
                local_timeout=15
            else
                echo -e "    ${DIM}✗${RESET}  Failed: $local_tname"
                SUMMARY_LOG+=("✗  Failed to install theme: $local_tname")
            fi
        done
        echo
    else
        SUMMARY_LOG+=("–  Theme installation cancelled")
    fi
fi

# If only action items were selected, show summary and exit
if [ ${#SELECTED_PACKAGES_FINAL[@]} -eq 0 ] && [ ${#SELECTED_WEBAPPS_FINAL[@]} -eq 0 ]; then
    trap - EXIT
    clear
    echo
    echo
    echo -e "${BOLD}  Summary${RESET}"
    echo
    for entry in "${SUMMARY_LOG[@]}"; do
        echo -e "  $entry"
    done
    echo
    echo
    exit 0
fi

# Confirmation screen
clear
echo
echo
echo -e "${BOLD}  Confirm Removal${RESET}"
echo
echo

if [ ${#SELECTED_PACKAGES_FINAL[@]} -gt 0 ]; then
    echo -e "${DIM}  Packages (${#SELECTED_PACKAGES_FINAL[@]}):${RESET}"
    for pkg in "${SELECTED_PACKAGES_FINAL[@]}"; do
        echo -e "    ${DIM}•${RESET}  $pkg"
    done
    echo
fi

if [ ${#SELECTED_WEBAPPS_FINAL[@]} -gt 0 ]; then
    echo -e "${DIM}  Web Apps (${#SELECTED_WEBAPPS_FINAL[@]}):${RESET}"
    for webapp in "${SELECTED_WEBAPPS_FINAL[@]}"; do
        echo -e "    ${DIM}•${RESET}  $webapp"
    done
    echo
fi

echo
if [ ${#SELECTED_PACKAGES_FINAL[@]} -gt 0 ]; then
    echo -e "${DIM}  Packages will be removed with their dependencies.${RESET}"
fi
if [ ${#SELECTED_WEBAPPS_FINAL[@]} -gt 0 ]; then
    echo -e "${DIM}  Web apps will be removed via omarchy-webapp-remove.${RESET}"
fi
echo
echo
if [[ "$CONFIRM_ALL" != true ]]; then
    printf "  ${BOLD}Continue?${RESET} ${DIM}(yes/no)${RESET} "
    read -r < /dev/tty
fi

if [[ "$CONFIRM_ALL" != true ]] && [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo
    echo "  Cancelled."
    echo
    exit 0
fi

# Remove items
echo
TOTAL_ATTEMPTED=0
TOTAL_FAILED=0

# Remove packages one by one
if [ ${#SELECTED_PACKAGES_FINAL[@]} -gt 0 ]; then
    echo "  Removing packages..."
    echo

    # Ensure we have sudo credentials before starting
    if ! sudo -n true 2>/dev/null; then
        echo -e "  ${DIM}Administrator privileges required for package removal${RESET}"
        if ! sudo true; then
            echo "  Failed to obtain sudo privileges"
            exit 1
        fi
        echo
    fi

    local_current=0
    local_total=${#SELECTED_PACKAGES_FINAL[@]}

    for pkg in "${SELECTED_PACKAGES_FINAL[@]}"; do
        ((local_current++))
        ((TOTAL_ATTEMPTED++))

        echo -e "  ${DIM}[$local_current/$local_total]${RESET} Removing $pkg..."

        if sudo pacman -Rns --noconfirm "$pkg" 2>/dev/null; then
            echo -e "    ${CHECKED}✓${RESET}  Removed: $pkg"
            SUMMARY_LOG+=("✓  Removed package: $pkg")
        else
            echo -e "    ${DIM}✗${RESET}  Failed: $pkg (may have dependencies)"
            SUMMARY_LOG+=("✗  Failed to remove package: $pkg")
            ((TOTAL_FAILED++))
        fi
    done
    echo
fi

# Remove webapps one by one
if [ ${#SELECTED_WEBAPPS_FINAL[@]} -gt 0 ]; then
    echo "  Removing web apps..."
    echo

    local_current=0
    local_total=${#SELECTED_WEBAPPS_FINAL[@]}

    for webapp in "${SELECTED_WEBAPPS_FINAL[@]}"; do
        ((local_current++))
        ((TOTAL_ATTEMPTED++))

        echo -e "  ${DIM}[$local_current/$local_total]${RESET} Removing $webapp..."

        if omarchy-webapp-remove "$webapp" >/dev/null 2>&1; then
            echo -e "    ${CHECKED}✓${RESET}  Removed: $webapp"
            SUMMARY_LOG+=("✓  Removed web app: $webapp")
        else
            echo -e "    ${DIM}✗${RESET}  Failed: $webapp"
            SUMMARY_LOG+=("✗  Failed to remove web app: $webapp")
            ((TOTAL_FAILED++))
        fi
    done
    echo
fi

# Summary
TOTAL_SUCCESS=$((TOTAL_ATTEMPTED - TOTAL_FAILED))

trap - EXIT
clear
echo
echo
echo -e "${BOLD}  Summary${RESET}"
echo

for entry in "${SUMMARY_LOG[@]}"; do
    echo -e "  $entry"
done

echo
if [ $TOTAL_FAILED -eq 0 ]; then
    echo -e "  ${CHECKED}All $TOTAL_ATTEMPTED item(s) removed successfully.${RESET}"
    if [ ${#SELECTED_PACKAGES_FINAL[@]} -gt 0 ]; then
        echo
        echo -e "  ${DIM}Optionally, clean your package cache:${RESET}"
        echo "  sudo pacman -Sc"
    fi
elif [ $TOTAL_SUCCESS -gt 0 ]; then
    echo -e "  ⚠  $TOTAL_SUCCESS of $TOTAL_ATTEMPTED item(s) removed. $TOTAL_FAILED failed."
else
    echo -e "  ✗  Could not remove any items. Check dependencies and permissions."
fi
echo
echo
