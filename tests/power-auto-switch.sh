#!/bin/bash
# Power auto-switch tests: no real sudo, udev, services, firmware or power changes.
#
# Root ownership is modelled through the script's power_auto_switch_metadata
# seam: everything under $ROOTFS counts as root-owned (with its real mode bits)
# unless listed in $TMP/user-owned, and the ancestors of $ROOTFS stand in for
# the root-owned /, /etc and /etc/udev. Every privileged command is a mock that
# accepts only the exact hardened command shapes and only fixture paths.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
export HOME="$TMP/home" OMARCHY_PATH="$TMP/omarchy" ALC_TEST_TMP="$TMP"
unset HYPRLAND_INSTANCE_SIGNATURE WAYLAND_DISPLAY DISPLAY
mkdir -p "$HOME/.config/hypr" "$OMARCHY_PATH"
: > "$HOME/.config/hypr/hyprland.lua"
# Only redirect the fixed asusd config path inside this fixture. All privileged
# commands below are additionally fail-closed mocks; none execute on the host.
python3 - "$REPO/a-la-carchy.sh" "$TMP/functions.sh" <<'PY'
import pathlib,sys
s=pathlib.Path(sys.argv[1]).read_text().split('# TWO-PANEL TUI DATA STRUCTURES',1)[0]
old='local asusd_config="/etc/asusd/asusd.ron"'
assert s.count(old) == 2, 'asusd path seams changed'
s=s.replace(old,'local asusd_config="$HOME/asusd.ron"')
guards = '\n'.join(name + '() { printf "unexpected prelude ' + name + '\\n" >> "$ALC_TEST_TMP/unexpected.log"; return 97; }' for name in ['sudo','pkexec','systemctl','udevadm','asusctl','powerprofilesctl','pacman','yay'])
pathlib.Path(sys.argv[2]).write_text(guards + '\n' + s)
PY
source "$TMP/functions.sh"
clear() { :; }
pkexec() { echo "UNEXPECTED PKEXEC" >> "$TMP/unexpected.log"; return 97; }
REAL_BACKEND_CHECK="$(declare -f power_auto_switch_backend_available)"
power_auto_switch_backend_available() { [[ "$MODE" != missing-backend ]]; }
DEFAULT_RULE="$POWER_AUTO_SWITCH_UDEV_RULE"
DEFAULT_SCRIPT="$POWER_AUTO_SWITCH_SCRIPT"
ROOTFS="$TMP/rootfs"
RULES_DIR="$ROOTFS/etc/udev/rules.d"
mkdir -p "$RULES_DIR"
POWER_AUTO_SWITCH_UDEV_RULE="$RULES_DIR/99-power-profile.rules"
POWER_AUTO_SWITCH_SCRIPT="$HOME/.config/hypr/scripts/power-profile-auto-switch.sh"
STAGE_GLOB="$RULES_DIR/.a-la-carchy-power."
mkdir -p "$(dirname "$POWER_AUTO_SWITCH_SCRIPT")"
: > "$TMP/user-owned"
power_auto_switch_metadata() {
    local path="$1" mode
    case "$path" in
        "$ROOTFS"|"$ROOTFS"/*)
            [[ -e "$path" || -L "$path" ]] || return 1
            mode=$(command stat -c '%a' -- "$path") || return 1
            if grep -qxF -- "$path" "$TMP/user-owned"; then printf '%s %s\n' "$(id -u)" "$mode"
            else printf '0 %s\n' "$mode"; fi ;;
        *)
            [[ "$path" == / || "$ROOTFS" == "$path"/* ]] || return 1
            printf '0 755\n' ;;
    esac
}
LOG="$TMP/sudo.log"
# Ownership follows the file, not the name: deleting or renaming over a path
# drops/transfers its modelled owner exactly as the real filesystem would.
forget_owner() { grep -vxF -- "$1" "$TMP/user-owned" > "$TMP/user-owned.next"; command mv -f -- "$TMP/user-owned.next" "$TMP/user-owned"; }
move_owner() {
    local was_user=0
    grep -qxF -- "$1" "$TMP/user-owned" && was_user=1
    forget_owner "$1"; forget_owner "$2"
    [[ "$was_user" == 0 ]] || printf '%s\n' "$2" >> "$TMP/user-owned"
}
is_stage() { [[ "$1" == "$STAGE_GLOB"* && "${1#"$STAGE_GLOB"}" != */* ]]; }
sudo() {
    printf '%q ' "$@" >> "$LOG"; printf '\n' >> "$LOG"
    case "$1" in
        /usr/bin/mktemp)
            [[ $# == 3 && "$2" == -- && "$3" == "${STAGE_GLOB}XXXXXXXX" ]] || return 97
            case "$MODE" in
                mktemp-fail) return 1 ;;
                # Each path below would pass the ownership model (root, 0644/0755):
                # only its location, name or file type gives it away.
                mktemp-outside) mkdir -p "$ROOTFS/outside"; : > "$ROOTFS/outside/.a-la-carchy-power.stage"; printf '%s\n' "$ROOTFS/outside/.a-la-carchy-power.stage"; return 0 ;;
                mktemp-wrong-name) : > "$RULES_DIR/98-staged.rules"; printf '%s\n' "$RULES_DIR/98-staged.rules"; return 0 ;;
                mktemp-nested) mkdir -p "${STAGE_GLOB}d"; : > "${STAGE_GLOB}d/stage"; printf '%s\n' "${STAGE_GLOB}d/stage"; return 0 ;;
                mktemp-directory) mkdir -p "${STAGE_GLOB}dir"; printf '%s\n' "${STAGE_GLOB}dir"; return 0 ;;
                mktemp-symlink) mkdir -p "$ROOTFS/outside"; : > "$ROOTFS/outside/target"; ln -s "$ROOTFS/outside/target" "${STAGE_GLOB}link"; printf '%s\n' "${STAGE_GLOB}link"; return 0 ;;
                mktemp-two-lines) local made; made=$(command mktemp -- "$3") || return 1; printf '%s\n%s\n' "$made" "$made"; return 0 ;;
                mktemp-user-owned)
                    local made; made=$(command mktemp -- "$3") || return 1
                    printf '%s\n' "$made" >> "$TMP/user-owned"; printf '%s\n' "$made"; return 0 ;;
            esac
            command mktemp -- "$3" ;;
        /usr/bin/tee)
            [[ $# == 3 && "$2" == -- ]] && is_stage "$3" || return 97
            case "$MODE" in
                write-fail|write-fail-cleanup-fail) cat > /dev/null; return 1 ;;
                write-partial) head -c 20 > "$3"; return 0 ;;
                race-foreign) printf '# foreign rule appeared\n' > "$POWER_AUTO_SWITCH_UDEV_RULE" ;;
            esac
            command tee -- "$3" ;;
        /usr/bin/chown)
            [[ $# == 4 && "$2" == 0:0 && "$3" == -- ]] && is_stage "$4" || return 97
            [[ "$MODE" != chown-fail ]] ;;
        /usr/bin/chmod)
            [[ $# == 4 && "$2" == 0644 && "$3" == -- ]] && is_stage "$4" || return 97
            [[ "$MODE" != chmod-fail ]] || return 1
            [[ "$MODE" == chmod-noop ]] && return 0
            command chmod 0644 -- "$4" ;;
        /usr/bin/mv)
            [[ $# == 6 && "$2" == -f && "$3" == -T && "$4" == -- && "$6" == "$POWER_AUTO_SWITCH_UDEV_RULE" ]] && is_stage "$5" || return 97
            [[ "$MODE" != mv-fail ]] || return 1
            [[ "$MODE" != mv-noop ]] || return 0
            command mv -f -T -- "$5" "$6" && move_owner "$5" "$6"
            # Something else altered the destination right after the rename.
            [[ "$MODE" != mv-corrupt* ]] || printf 'tampered\n' >> "$6"
            [[ "$MODE" != mv-other-safe ]] || expected_rule balanced balanced > "$6"
            [[ "$MODE" != mv-foreign ]] || printf '# replaced by another tool\n' > "$6" ;;
        /usr/bin/rm)
            [[ $# == 4 && "$2" == -f && "$3" == -- ]] || return 97
            [[ "$4" == "$POWER_AUTO_SWITCH_UDEV_RULE" ]] || is_stage "$4" || return 97
            [[ "$MODE" != *remove-fail ]] || return 1
            [[ "$MODE" == write-fail-cleanup-fail ]] && is_stage "$4" && return 1
            [[ "$MODE" == *remove-noop ]] && return 0
            command rm -f -- "$4" && forget_owner "$4" ;;
        /usr/bin/udevadm)
            [[ $# == 3 && "$2" == control && "$3" == --reload-rules ]] || return 97
            [[ "$MODE" != *reload-fail ]] ;;
        /usr/bin/sed)
            [[ "$2" == -i && "${@: -1}" == "$HOME/asusd.ron" ]] || return 97
            [[ "$MODE" != asus-write-fail ]] || return 1
            shift; command sed "$@" ;;
        /usr/bin/systemctl)
            [[ $# == 3 && "$2" == restart && "$3" == asusd ]] || return 97
            [[ "$MODE" != restart-fail ]] ;;
        *) echo "UNEXPECTED PRIVILEGED COMMAND: $*" >> "$TMP/unexpected.log"; return 97 ;;
    esac
}
MODE=ok
PASSED=0 FAILED=0
check() {
    local label="$1"; shift
    if "$@"; then PASSED=$((PASSED+1)); echo "ok - $label"
    else FAILED=$((FAILED+1)); echo "FAIL - $label"; fi
}
no_green_summary() {
    local entry
    for entry in "${SUMMARY_LOG[@]}"; do [[ "$entry" != ✓* ]] || return 1; done
}
summary_has() { local entry; for entry in "${SUMMARY_LOG[@]}"; do [[ "$entry" == *"$1"* ]] && return 0; done; return 1; }
no_stage_left() { local f; for f in "$STAGE_GLOB"*; do [[ -e "$f" || -L "$f" ]] && return 1; done; return 0; }
log_has() { grep -qF -- "$1" "$LOG"; }
log_lacks() { ! grep -qF -- "$1" "$LOG"; }
privileged_count() { grep -c . "$LOG"; }
expected_rule() {
    printf '%s\n' '# Managed by A La Carchy - power profile auto-switch' \
        "# PROFILE_AC=\"$1\"" "# PROFILE_BATTERY=\"$2\"" \
        "ACTION==\"change\", SUBSYSTEM==\"power_supply\", ATTR{type}==\"Mains\", ATTR{online}==\"1\", RUN+=\"/usr/bin/powerprofilesctl set $1\"" \
        "ACTION==\"change\", SUBSYSTEM==\"power_supply\", ATTR{type}==\"Mains\", ATTR{online}==\"0\", RUN+=\"/usr/bin/powerprofilesctl set $2\""
}
install_rule() { expected_rule "$1" "$2" > "$POWER_AUTO_SWITCH_UDEV_RULE"; chmod 0644 "$POWER_AUTO_SWITCH_UDEV_RULE"; }
reset_case() {
    command rm -rf -- "$RULES_DIR" "$ROOTFS/outside"
    command rm -f -- "$HOME/asusd.ron"
    mkdir -p "$RULES_DIR"
    chmod 0755 "$ROOTFS" "$ROOTFS/etc" "$ROOTFS/etc/udev" "$RULES_DIR"
    : > "$TMP/user-owned"
    : > "$LOG"
    SUMMARY_LOG=()
    MODE=ok
    SELECTED_POWER_AUTO_SWITCH=enabled
    SELECTED_POWER_AC_PROFILE=performance
    SELECTED_POWER_BATTERY_PROFILE=power-saver
}
expect_failure() { ! apply_power_auto_switch > "$TMP/output" 2>&1; }
not() { ! "$@"; }
quiet() { "$@" > "$TMP/output" 2>&1; }
line_of() { grep -nF -- "$1" "$LOG" | head -n 1 | cut -d: -f1; }

check 'real rule default asserted' test "$DEFAULT_RULE" = /etc/udev/rules.d/99-power-profile.rules
check 'real legacy script default asserted' test "$DEFAULT_SCRIPT" = "$HOME/.config/hypr/scripts/power-profile-auto-switch.sh"

# --- Successful enable: exact content, ownership/mode, privileged sequence ---
reset_case
printf '# legacy user script, do not execute or overwrite\n' > "$POWER_AUTO_SWITCH_SCRIPT"
cp "$POWER_AUTO_SWITCH_SCRIPT" "$TMP/legacy-original"
check 'enable succeeds in isolated fixture' apply_power_auto_switch
check 'installed rule is exactly the allowlisted rendering' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" <(expected_rule performance power-saver)
check 'AC invokes absolute system binary with allowlisted profile' grep -qF 'RUN+="/usr/bin/powerprofilesctl set performance"' "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'battery invokes absolute system binary with allowlisted profile' grep -qF 'RUN+="/usr/bin/powerprofilesctl set power-saver"' "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'rule never points into HOME' bash -c '! grep -qF -- "$1" "$2"' _ "$HOME" "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'rule no longer uses legacy script' bash -c '! grep -qF -- "$1" "$2"' _ "$POWER_AUTO_SWITCH_SCRIPT" "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'device matching supports Mains supplies without AC naming requirement' grep -qF 'ATTR{type}=="Mains"' "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'installed rule mode is 0644' test "$(stat -c %a "$POWER_AUTO_SWITCH_UDEV_RULE")" = 644
check 'installed rule verifies as safe' power_auto_switch_rule_safe
check 'legacy user script preserved byte-for-byte' cmp -s "$POWER_AUTO_SWITCH_SCRIPT" "$TMP/legacy-original"
check 'successful enable summary recorded' test "${SUMMARY_LOG[-1]}" = '✓  Power auto-switch: AC=performance, battery=power-saver'
check 'no hidden staging file left behind' no_stage_left
check 'stage is created by root inside the rules directory' test "$(sed -n 1p "$LOG")" = "/usr/bin/mktemp -- ${STAGE_GLOB}XXXXXXXX "
check 'privileged sequence is mktemp, tee, chown, chmod, mv, reload' test "$(awk '{print $1}' "$LOG" | tr '\n' ' ')" = '/usr/bin/mktemp /usr/bin/tee /usr/bin/chown /usr/bin/chmod /usr/bin/mv /usr/bin/udevadm '
check 'no privileged command references HOME' bash -c '! grep -qF -- "$1" "$2"' _ "$HOME" "$LOG"
check 'staging name is hidden and never a .rules file' bash -c '[[ "$1" != *.rules* ]]' _ "${STAGE_GLOB}XXXXXXXX"

# Re-enable with identical profiles stays correct; new profiles replace atomically.
: > "$LOG"; SUMMARY_LOG=()
check 'identical re-enable succeeds' apply_power_auto_switch
check 'identical re-enable keeps exact rule' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" <(expected_rule performance power-saver)
check 'safe managed rule is not removed before replacement' log_lacks "/usr/bin/rm -f -- $POWER_AUTO_SWITCH_UDEV_RULE"
: > "$LOG"; SUMMARY_LOG=()
SELECTED_POWER_AC_PROFILE=balanced SELECTED_POWER_BATTERY_PROFILE=performance
check 'profile change succeeds' apply_power_auto_switch
check 'profile change replaced rule content atomically' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" <(expected_rule balanced performance)
check 'profile change used same-directory rename' log_has "/usr/bin/mv -f -T -- $STAGE_GLOB"
check 'profile change left no stage' no_stage_left

# --- Invalid input never reaches privileged commands ---
for profile in 'balanced" RUN+="/bin/sh' 'performance; touch /tmp/unsafe' '' 'unknown' $'balanced\nmalicious'; do
    reset_case
    SELECTED_POWER_AC_PROFILE="$profile"
    check 'invalid AC profile rejected' expect_failure
    check 'invalid AC profile has no privileged calls' test ! -s "$LOG"
    check 'invalid AC profile has no success summary' no_green_summary
    reset_case
    SELECTED_POWER_BATTERY_PROFILE="$profile"
    check 'invalid battery profile rejected' expect_failure
    check 'invalid battery profile has no privileged calls' test ! -s "$LOG"
done
check 'renderer itself rejects unlisted profiles' bash -c 'source "$1"; ! power_auto_switch_render_rule "performance; id" balanced' _ "$TMP/functions.sh"
reset_case; SELECTED_POWER_AUTO_SWITCH=''
check 'empty/unselected action is not enable' expect_failure
check 'unselected action has no privileged calls' test ! -s "$LOG"
reset_case; SELECTED_POWER_AUTO_SWITCH='enabled; disabled'
check 'unknown action rejected' expect_failure
check 'unknown action has no privileged calls' test ! -s "$LOG"
# A selection is only ever made in the dialog: exported variables must not
# survive loading the script and queue a privileged change nobody picked.
check 'exported selection variables are discarded at load' bash -c '
    export SELECTED_POWER_AUTO_SWITCH=disabled SELECTED_POWER_AC_PROFILE=performance SELECTED_POWER_BATTERY_PROFILE=balanced
    source "$1"
    [[ -z "$SELECTED_POWER_AUTO_SWITCH$SELECTED_POWER_AC_PROFILE$SELECTED_POWER_BATTERY_PROFILE" ]]' _ "$TMP/functions.sh"
check 'selection variables are assigned before the TUI reads them' bash -c '
    for name in SELECTED_POWER_AUTO_SWITCH SELECTED_POWER_AC_PROFILE SELECTED_POWER_BATTERY_PROFILE; do
        grep -qxF "$name=\"\"" "$1" || exit 1
    done' _ "$TMP/functions.sh"
reset_case; SELECTED_POWER_AC_PROFILE='performance; id'
check 'install step itself refuses an unlisted profile' not quiet power_auto_switch_install_rule
check 'install step refusal makes no privileged calls' test ! -s "$LOG"
check 'install step refusal stages nothing' no_stage_left

# --- Untrusted rules directory: nothing privileged for enable or disable ---
for action in enabled disabled; do
    for variant in user-owned-dir writable-dir writable-ancestor symlinked-dir; do
        reset_case; SELECTED_POWER_AUTO_SWITCH="$action"
        case "$variant" in
            user-owned-dir) printf '%s\n' "$RULES_DIR" >> "$TMP/user-owned" ;;
            writable-dir) chmod 0777 "$RULES_DIR" ;;
            writable-ancestor) chmod 0775 "$ROOTFS/etc" ;;
            symlinked-dir) command rm -rf -- "$RULES_DIR"; mkdir -p "$ROOTFS/real-rules"; ln -s "$ROOTFS/real-rules" "$RULES_DIR" ;;
        esac
        check "$action refused for $variant" expect_failure
        check "$action $variant has no privileged calls" test ! -s "$LOG"
        check "$action $variant has no success summary" no_green_summary
    done
done
reset_case

# --- Backend trust ---
reset_case; MODE=missing-backend
check 'missing system binary rejected before sudo' expect_failure
check 'missing backend has no privileged calls' test ! -s "$LOG"
check 'missing backend has no success summary' no_green_summary
reset_case; MODE=missing-backend
install_rule performance balanced
check 'missing backend with existing managed rule fails' expect_failure
check 'missing backend retires the existing root rule' test ! -e "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'missing backend retirement was followed by reload' log_has '/usr/bin/udevadm control --reload-rules'
check 'missing backend installs nothing new' log_lacks '/usr/bin/mktemp'
eval "real_${REAL_BACKEND_CHECK}"
power_auto_switch_metadata_saved="$(declare -f power_auto_switch_metadata)"
power_auto_switch_metadata() { printf '%s 755\n' "$(id -u)"; }
check 'backend owned by a user is untrusted' not real_power_auto_switch_backend_available
eval "$power_auto_switch_metadata_saved"
power_auto_switch_metadata_real() { /usr/bin/stat -c '%u %a' -- "$1" 2>/dev/null; }
if [[ -f /usr/bin/powerprofilesctl && ! -L /usr/bin/powerprofilesctl && -x /usr/bin/powerprofilesctl ]] &&
   [[ "$(stat -c %u /usr/bin/powerprofilesctl)" == 0 ]]; then
    saved="$(declare -f power_auto_switch_metadata)"
    power_auto_switch_metadata() { power_auto_switch_metadata_real "$@"; }
    check 'host root-owned powerprofilesctl in trusted /usr/bin is accepted (read-only check)' real_power_auto_switch_backend_available
    eval "$saved"
else
    echo "# skip host backend acceptance (no root-owned /usr/bin/powerprofilesctl)"
fi
# The predicate behind the backend check, on fixture files with modelled
# ownership: everything a root rule would execute must be root-only writable.
BIN_DIR="$ROOTFS/usr/bin"
TOOL="$BIN_DIR/tool"
reset_tool() {
    command rm -rf -- "$ROOTFS/usr" "$ROOTFS/alt"
    mkdir -p "$BIN_DIR"
    chmod 0755 "$ROOTFS" "$ROOTFS/usr" "$BIN_DIR"
    printf '#!/bin/sh\n' > "$TOOL"; chmod 0755 "$TOOL"
    : > "$TMP/user-owned"
}
trusted() { power_auto_switch_trusted_executable "$@"; }
reset_tool
check 'root-owned executable in trusted directories is accepted' trusted "$TOOL"
check 'missing executable rejected' not trusted "$BIN_DIR/absent"
check 'directory rejected as an executable' not trusted "$BIN_DIR"
check 'relative path rejected even when it resolves' bash -c 'cd "$1" && source "$2" && ! power_auto_switch_trusted_executable usr/bin/tool' _ "$ROOTFS" "$TMP/functions.sh"
reset_tool; chmod 0644 "$TOOL"
check 'non-executable file rejected' not trusted "$TOOL"
reset_tool; printf '%s\n' "$TOOL" >> "$TMP/user-owned"
check 'user-owned executable rejected' not trusted "$TOOL"
for mode in 0775 0757 0777; do
    reset_tool; chmod "$mode" "$TOOL"
    check "group/world-writable executable ($mode) rejected" not trusted "$TOOL"
done
reset_tool; command mv -- "$TOOL" "$BIN_DIR/real"; ln -s "$BIN_DIR/real" "$TOOL"
check 'symlinked executable rejected' not trusted "$TOOL"
check 'its regular target alone would be trusted (control)' trusted "$BIN_DIR/real"
reset_tool; printf '%s\n' "$BIN_DIR" >> "$TMP/user-owned"
check 'executable in a user-owned directory rejected' not trusted "$TOOL"
reset_tool; chmod 0777 "$BIN_DIR"
check 'executable in a world-writable directory rejected' not trusted "$TOOL"
reset_tool; chmod 0775 "$ROOTFS/usr"
check 'executable below a group-writable ancestor rejected' not trusted "$TOOL"
reset_tool; mkdir -p "$ROOTFS/alt"; command mv -- "$BIN_DIR" "$ROOTFS/alt/bin"; ln -s "$ROOTFS/alt/bin" "$BIN_DIR"
check 'executable reached through a symlinked directory rejected' not trusted "$TOOL"
check 'the same file through its real directory is trusted (control)' trusted "$ROOTFS/alt/bin/tool"
reset_tool
saved_trusted="$(declare -f power_auto_switch_trusted_executable)"
power_auto_switch_trusted_executable() { printf '%s:%s\n' "$#" "$1" >> "$TMP/backend-args"; }
real_power_auto_switch_backend_available
eval "$saved_trusted"
check 'backend check validates exactly /usr/bin/powerprofilesctl' test "$(cat "$TMP/backend-args")" = '1:/usr/bin/powerprofilesctl'
check 'every program the rule runs is that validated backend' bash -c '
    [[ "$(grep -c "RUN+=" <<< "$1")" == 2 && "$(grep -c "RUN+=\"/usr/bin/powerprofilesctl set " <<< "$1")" == 2 ]]' _ \
    "$(power_auto_switch_render_rule balanced power-saver)"
metadata_verdict() {
    bash -c 'source "$1"; FAKE_STAT="$2"; power_auto_switch_metadata(){ printf "%s\n" "$FAKE_STAT"; }; power_auto_switch_root_metadata_safe /x' _ "$TMP/functions.sh" "$1"
}
for stat_out in '0 1777' '0 664' '0 646' '0 2775' '1000 644' '0 9z' '0' ''; do
    check "metadata predicate rejects '$stat_out'" not metadata_verdict "$stat_out"
done
for stat_out in '0 644' '0 755' '0 600' '0 1755'; do
    check "metadata predicate accepts '$stat_out'" metadata_verdict "$stat_out"
done

# --- Existing rule handling on enable ---
reset_case
printf '%s\n' '# Managed by A La Carchy - power profile auto-switch' \
    "ACTION==\"change\", SUBSYSTEM==\"power_supply\", KERNEL==\"AC*\", ATTR{online}==\"1\", RUN+=\"$POWER_AUTO_SWITCH_SCRIPT ac\"" > "$POWER_AUTO_SWITCH_UDEV_RULE"
printf 'PROFILE_AC="balanced"\n' > "$POWER_AUTO_SWITCH_SCRIPT"; cp "$POWER_AUTO_SWITCH_SCRIPT" "$TMP/legacy-original"
check 'legacy HOME-script rule is migrated on enable' apply_power_auto_switch
check 'legacy rule retired before staging (rm precedes mktemp)' test "$(grep -nF "/usr/bin/rm -f -- $POWER_AUTO_SWITCH_UDEV_RULE" "$LOG" | cut -d: -f1)" -lt "$(grep -nF /usr/bin/mktemp "$LOG" | cut -d: -f1)"
check 'migrated rule is the safe rendering' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" <(expected_rule performance power-saver)
check 'legacy script still preserved, never executed' cmp -s "$POWER_AUTO_SWITCH_SCRIPT" "$TMP/legacy-original"
# The profile comments alone prove nothing: a rule is trusted only when every
# byte equals the allowlisted rendering.
reset_case
{ expected_rule performance power-saver; printf 'ACTION=="change", SUBSYSTEM=="power_supply", RUN+="%s/evil"\n' "$HOME"; } > "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'valid metadata with an extra RUN line is not a safe rule' not power_auto_switch_rule_safe
expected_rule performance power-saver | sed 's|/usr/bin/powerprofilesctl set power-saver|/usr/bin/powerprofilesctl set power-saver --evil|' > "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'valid metadata with an altered RUN line is not a safe rule' not power_auto_switch_rule_safe
check 'altered managed rule is migrated on enable' apply_power_auto_switch
check 'altered rule retired before staging (rm precedes mktemp)' test "$(line_of "/usr/bin/rm -f -- $POWER_AUTO_SWITCH_UDEV_RULE")" -lt "$(line_of /usr/bin/mktemp)"
check 'altered rule replaced by the exact rendering' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" <(expected_rule performance power-saver)
for profile in 'performance; id' ''; do
    reset_case
    printf '%s\n' '# Managed by A La Carchy - power profile auto-switch' "RUN+=\"$POWER_AUTO_SWITCH_SCRIPT ac\"" > "$POWER_AUTO_SWITCH_UDEV_RULE"
    cp "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/prior"; SELECTED_POWER_BATTERY_PROFILE="$profile"
    check 'invalid profile is rejected even with a legacy rule present' expect_failure
    check 'invalid profile never retires or touches the existing rule' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/prior"
    check 'invalid profile with an existing rule makes no privileged calls' test ! -s "$LOG"
done
reset_case
install_rule performance power-saver; chmod 0666 "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'world-writable managed rule is retired and replaced' apply_power_auto_switch
check 'replacement rule mode is 0644' test "$(stat -c %a "$POWER_AUTO_SWITCH_UDEV_RULE")" = 644
check 'writable rule was removed first' log_has "/usr/bin/rm -f -- $POWER_AUTO_SWITCH_UDEV_RULE"
reset_case
install_rule performance power-saver; printf '%s\n' "$POWER_AUTO_SWITCH_UDEV_RULE" >> "$TMP/user-owned"
check 'user-owned managed rule is retired and replaced' apply_power_auto_switch
check 'user-owned rule was removed first' log_has "/usr/bin/rm -f -- $POWER_AUTO_SWITCH_UDEV_RULE"
reset_case
expected_rule performance power-saver > "$TMP/owned-target.rules"; cp "$TMP/owned-target.rules" "$TMP/target-original"
ln -s "$TMP/owned-target.rules" "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'managed symlink rule is unlinked and replaced on enable' apply_power_auto_switch
check 'symlink replaced by regular rule' bash -c '[[ -f "$1" && ! -L "$1" ]]' _ "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'symlink target preserved byte-for-byte' cmp -s "$TMP/owned-target.rules" "$TMP/target-original"
reset_case
printf '# configuration owned by another tool\n' > "$POWER_AUTO_SWITCH_UDEV_RULE"; cp "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/foreign-original"
check 'foreign rule refused on enable' expect_failure
check 'foreign rule preserved exactly on enable' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/foreign-original"
check 'foreign enable refusal has no privileged calls' test ! -s "$LOG"
check 'foreign enable refusal has no success summary' no_green_summary
reset_case
install_rule performance balanced
MODE=remove-fail; printf '%s\n' "$POWER_AUTO_SWITCH_UDEV_RULE" >> "$TMP/user-owned"
check 'failed retirement of unsafe rule stops enable' expect_failure
check 'failed retirement installs nothing' log_lacks '/usr/bin/mktemp'
check 'failed retirement warns that the old rule remains' summary_has 'Existing root rule remains'

# --- Install failure paths: destination untouched, stage cleaned, no success ---
for mode in mktemp-fail mktemp-outside mktemp-wrong-name mktemp-nested mktemp-directory mktemp-symlink mktemp-two-lines mktemp-user-owned \
            write-fail write-partial chown-fail chmod-fail chmod-noop mv-fail; do
    reset_case; install_rule balanced balanced; cp "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/prior"; MODE="$mode"
    check "$mode fails" expect_failure
    check "$mode has no success summary" no_green_summary
    check "$mode leaves the prior rule byte-identical" cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/prior"
    check "$mode never reloads udev" log_lacks /usr/bin/udevadm
    case "$mode" in
        mktemp-fail)
            check "$mode stops after mktemp" test "$(privileged_count)" -eq 1
            check "$mode names the failed step" summary_has 'failed to create root-owned staging file' ;;
        mktemp-*)
            check "$mode never writes or promotes an unverified path" bash -c '! grep -qE "^/usr/bin/(tee|chown|chmod|mv) " "$1"' _ "$LOG"
            check "$mode never deletes an unverified path" log_lacks /usr/bin/rm
            check "$mode reports possible manual cleanup" summary_has 'untrusted staging path' ;;
        mv-fail)
            check "$mode removes its root stage" no_stage_left
            check "$mode cleanup targeted only the stage" bash -c '! grep -qF -- "/usr/bin/rm -f -- $2 " "$1"' _ "$LOG" "$POWER_AUTO_SWITCH_UDEV_RULE"
            check "$mode names the failed step" summary_has 'atomic promotion failed' ;;
        *)
            check "$mode removes its root stage" no_stage_left
            check "$mode cleanup targeted only the stage" bash -c '! grep -qF -- "/usr/bin/rm -f -- $2 " "$1"' _ "$LOG" "$POWER_AUTO_SWITCH_UDEV_RULE"
            check "$mode never promotes" log_lacks /usr/bin/mv
            check "$mode names the failed step" summary_has 'staging write/ownership/mode verification failed' ;;
    esac
done
reset_case; MODE=write-fail-cleanup-fail; install_rule balanced balanced; cp "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/prior"
check 'failed write with failed cleanup is reported' expect_failure
check 'failed cleanup is disclosed, not hidden' summary_has 'hidden temporary file remains'
check 'failed cleanup has no success summary' no_green_summary
check 'failed cleanup leaves the prior rule byte-identical' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/prior"
check 'failed cleanup never reloads udev' log_lacks /usr/bin/udevadm
reset_case; MODE=race-foreign
check 'destination replaced by a foreign rule during staging is refused' expect_failure
check 'raced foreign rule preserved' grep -qxF '# foreign rule appeared' "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'raced foreign rule was never overwritten' log_lacks /usr/bin/mv
check 'race refusal cleans its stage' no_stage_left
check 'race refusal has no success summary' no_green_summary
# A rule that cannot be verified after the rename is never left loadable: udev
# notices changed rule files by itself, so it is removed and only then reloaded.
for prior in absent safe; do
    reset_case; [[ "$prior" == absent ]] || install_rule balanced balanced
    MODE=mv-corrupt
    check "unverifiable promotion is a failure (prior rule $prior)" expect_failure
    check "unverified promoted rule is taken back out (prior rule $prior)" bash -c '[[ ! -e "$1" && ! -L "$1" ]]' _ "$POWER_AUTO_SWITCH_UDEV_RULE"
    check "unverified rule is removed before the only reload (prior rule $prior)" bash -c '[[ -n "$1" && -n "$2" && "$1" -lt "$2" && "$3" == 1 ]]' _ \
        "$(line_of "/usr/bin/rm -f -- $POWER_AUTO_SWITCH_UDEV_RULE")" "$(line_of /usr/bin/udevadm)" "$(grep -c '^/usr/bin/udevadm ' "$LOG")"
    check "rollback is reported, not hidden (prior rule $prior)" summary_has 'unverified rule removed'
    check "unverifiable promotion has no success summary (prior rule $prior)" no_green_summary
    check "unverifiable promotion leaves no stage (prior rule $prior)" no_stage_left
done
reset_case; MODE=mv-corrupt-remove-fail
check 'failed rollback of an unverified rule is a failure' expect_failure
check 'failed rollback never reloads the unverified rule' log_lacks /usr/bin/udevadm
check 'failed rollback says the rule remains' summary_has 'Existing root rule remains'
check 'failed rollback has no success summary' no_green_summary
reset_case; MODE=mv-corrupt-remove-noop
check 'unconfirmed rollback of an unverified rule is a failure' expect_failure
check 'unconfirmed rollback never reloads the unverified rule' log_lacks /usr/bin/udevadm
check 'unconfirmed rollback has no success summary' no_green_summary
reset_case; MODE=mv-corrupt-reload-fail
check 'rollback with a failed reload is a failure' expect_failure
check 'rollback with a failed reload still removes the rule' test ! -e "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'rollback reload failure is disclosed' summary_has 'udev reload failed after removing the unverified rule'
check 'rollback reload failure has no success summary' no_green_summary
reset_case; install_rule balanced balanced; cp "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/prior"; MODE=mv-noop
check 'rename that reports success but did nothing is a failure' expect_failure
check 'a still-verified previous rule is kept, not rolled back' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/prior"
check 'no-op rename leaves no stage' no_stage_left
check 'no-op rename is not reloaded' log_lacks /usr/bin/udevadm
check 'no-op rename says the verified rule remains' summary_has 'Verified managed rule remains'
check 'no-op rename has no success summary' no_green_summary
reset_case; MODE=mv-noop
check 'no-op rename without a previous rule is a failure' expect_failure
check 'no-op rename without a previous rule installs nothing' bash -c '[[ ! -e "$1" && ! -L "$1" ]]' _ "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'no-op rename without a previous rule leaves no stage' no_stage_left
reset_case; install_rule performance power-saver; MODE=mv-noop
check 'no-op rename onto an identical rule is still not reported as done' expect_failure
check 'no-op rename onto an identical rule leaves no stage' no_stage_left
check 'no-op rename onto an identical rule has no success summary' no_green_summary
reset_case; install_rule performance performance; MODE=mv-other-safe
check 'a different verified rule in place of the requested one is a failure' expect_failure
check 'the other verified rule is kept, not rolled back' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" <(expected_rule balanced balanced)
check 'profiles that were not applied are never reported as applied' no_green_summary
check 'the other verified rule is not reloaded as the requested one' log_lacks /usr/bin/udevadm
reset_case; MODE=mv-foreign
check 'destination replaced by a foreign file after the rename is a failure' expect_failure
check 'foreign replacement is preserved, not deleted' grep -qxF '# replaced by another tool' "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'foreign replacement is never removed by us' log_lacks "/usr/bin/rm -f -- $POWER_AUTO_SWITCH_UDEV_RULE"
check 'foreign replacement is not reloaded' log_lacks /usr/bin/udevadm
check 'foreign replacement has no success summary' no_green_summary
reset_case; MODE=reload-fail
check 'reload failure after install propagates' expect_failure
check 'reload failure still leaves only a verified safe rule' power_auto_switch_rule_safe
check 'reload failure has no success summary' no_green_summary
check 'reload failure explains the loaded state is unconfirmed' summary_has 'reload failed'
MODE=ok; SUMMARY_LOG=()
check 'retry after reload failure succeeds' apply_power_auto_switch

# --- Disable / retire ---
reset_case; SELECTED_POWER_AUTO_SWITCH=disabled
check 'already absent disable succeeds after reload' apply_power_auto_switch
check 'already absent disable confirms reload' grep -qF '/usr/bin/udevadm control --reload-rules' "$LOG"
check 'already absent disable makes no other privileged calls' test "$(privileged_count)" -eq 1
for mode in ok remove-fail reload-fail; do
    reset_case; SELECTED_POWER_AUTO_SWITCH=disabled; MODE="$mode"
    printf '# Managed by A La Carchy - power profile auto-switch\n' > "$POWER_AUTO_SWITCH_UDEV_RULE"
    if [[ "$mode" == ok ]]; then
        check 'disable removes fixture rule and reloads' apply_power_auto_switch
        check 'fixture rule removed' test ! -e "$POWER_AUTO_SWITCH_UDEV_RULE"
        check 'disable success summary recorded' test "${SUMMARY_LOG[-1]}" = '✓  Power auto-switch disabled'
    else
        check "disable $mode failure propagates" expect_failure
        check "disable $mode has no success summary" no_green_summary
        [[ "$mode" != remove-fail ]] || check 'disable remove-fail reports the failed removal' summary_has 'failed to remove managed rule'
        [[ "$mode" != reload-fail ]] || check 'disable reload-fail reports the unconfirmed loaded state' summary_has 'reload failed'
    fi
done
reset_case; SELECTED_POWER_AUTO_SWITCH=disabled; MODE=remove-noop
printf '# Managed by A La Carchy - power profile auto-switch\n' > "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'reported unlink success must also confirm absence' expect_failure
check 'no-op unlink preserves rule' test -f "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'unconfirmed unlink has no success summary' no_green_summary
check 'unconfirmed unlink never reloads as if disabled' log_lacks /usr/bin/udevadm
reset_case; SELECTED_POWER_AUTO_SWITCH=disabled
printf '# configuration owned by another tool\n' > "$POWER_AUTO_SWITCH_UDEV_RULE"
cp "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/foreign-original"
check 'foreign rule is refused rather than deleted' expect_failure
check 'foreign rule is preserved exactly' cmp -s "$POWER_AUTO_SWITCH_UDEV_RULE" "$TMP/foreign-original"
check 'foreign refusal has no privileged calls' test ! -s "$LOG"
check 'foreign refusal has no success summary' no_green_summary
reset_case; SELECTED_POWER_AUTO_SWITCH=disabled
ln -s "$TMP/nonexistent-target" "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'unrecognized dangling link is refused, not reported disabled' expect_failure
check 'dangling link preserved for manual review' test -L "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'dangling-link refusal has no privileged calls' test ! -s "$LOG"
check 'dangling-link refusal has no success summary' no_green_summary
reset_case; SELECTED_POWER_AUTO_SWITCH=disabled
mkdir "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'directory at rule path is safely refused' expect_failure
check 'directory preserved' test -d "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'directory refusal has no privileged calls' test ! -s "$LOG"
check 'directory refusal has no success summary' no_green_summary
reset_case; SELECTED_POWER_AUTO_SWITCH=disabled
printf '# Managed by A La Carchy - power profile auto-switch\n' > "$TMP/owned-target.rules"
cp "$TMP/owned-target.rules" "$TMP/target-original"
ln -s "$TMP/owned-target.rules" "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'recognized managed symlink disabled by unlinking only the link' apply_power_auto_switch
check 'managed symlink is absent' test ! -L "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'managed symlink target preserved byte-for-byte' cmp -s "$TMP/owned-target.rules" "$TMP/target-original"
reset_case; SELECTED_POWER_AUTO_SWITCH=disabled; MODE=reload-fail
printf '# Managed by A La Carchy - power profile auto-switch\n' > "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'initial removal with failed reload returns failure' expect_failure
check 'file was removed before reload failed' test ! -e "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'retry with absent file and still-failed reload remains failure' expect_failure
check 'failed retry cannot emit a success summary' no_green_summary
MODE=ok
check 'retry succeeds only when reload eventually succeeds' apply_power_auto_switch
check 'each of three disable attempts reloads' test "$(grep -c '^/usr/bin/udevadm ' "$LOG")" -eq 3

# --- asusd profile sync (fixture config only) ---
for mode in ok asus-write-fail restart-fail; do
    reset_case; MODE="$mode"
    printf 'platform_profile_on_ac: Quiet,\nplatform_profile_on_battery: Performance,\n' > "$HOME/asusd.ron"
    if [[ "$mode" == ok ]]; then
        check 'asus config sync succeeds through mocks' apply_power_auto_switch
        check 'AC asus profile updated' grep -qF 'platform_profile_on_ac: Performance,' "$HOME/asusd.ron"
        check 'battery asus profile updated' grep -qF 'platform_profile_on_battery: Quiet,' "$HOME/asusd.ron"
        check 'asusd restarted through absolute systemctl' log_has '/usr/bin/systemctl restart asusd'
    else
        check "$mode propagates" expect_failure
        check "$mode has no double success summary" no_green_summary
    fi
done

# --- Profile metadata is read, never executed ---
reset_case
printf 'PROFILE_AC="balanced"\nPROFILE_BATTERY="performance"\n' > "$POWER_AUTO_SWITCH_SCRIPT"
check 'legacy AC value can be read without execution' test "$(power_auto_switch_configured_profile AC)" = balanced
check 'legacy battery value can be read without execution' test "$(power_auto_switch_configured_profile BATTERY)" = performance
apply_power_auto_switch > "$TMP/output" 2>&1
check 'new rule AC metadata takes priority over stale user script' test "$(power_auto_switch_configured_profile AC)" = performance
check 'new rule battery metadata takes priority over stale user script' test "$(power_auto_switch_configured_profile BATTERY)" = power-saver
check 'unknown profile field is rejected' bash -c 'source "$1"; ! power_auto_switch_configured_profile INVALID' _ "$TMP/functions.sh"
for field in 'A.' '(AC|BATTERY)' 'AC|BATTERY' '.*' ''; do
    check "profile field '$field' is never used as a pattern" not power_auto_switch_configured_profile "$field"
done
printf '# PROFILE_AC="invalid"\n' > "$POWER_AUTO_SWITCH_UDEV_RULE"
check 'invalid rule metadata rejected instead of stale fallback' bash -c 'source "$1"; POWER_AUTO_SWITCH_UDEV_RULE="$2"; ! power_auto_switch_configured_profile AC' _ "$TMP/functions.sh" "$POWER_AUTO_SWITCH_UDEV_RULE"
command rm -f -- "$POWER_AUTO_SWITCH_UDEV_RULE"
printf '%s\n' '#!/bin/bash' 'touch "$HOME/legacy-script-was-executed"' 'PROFILE_AC="performance"' > "$POWER_AUTO_SWITCH_SCRIPT"
chmod +x "$POWER_AUTO_SWITCH_SCRIPT"
check 'legacy metadata parsed when only the user script remains' test "$(power_auto_switch_configured_profile AC)" = performance
check 'reading legacy metadata never executes the script' test ! -e "$HOME/legacy-script-was-executed"
check 'no unexpected privileged or prelude commands escaped mocks' test ! -s "$TMP/unexpected.log"
printf '\n%s passed, %s failed\n' "$PASSED" "$FAILED"
[[ "$FAILED" -eq 0 ]]
