#!/bin/bash
# Isolated regression tests; all package, service and desktop commands are mocked.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
export HOME="$TMP/home" OMARCHY_PATH="$TMP/omarchy"
export HYPRLAND_INSTANCE_SIGNATURE=dictation-test
mkdir -p "$HOME/.config/hypr" "$OMARCHY_PATH"
: > "$HOME/.config/hypr/hyprland.lua"
sed '/^# TWO-PANEL TUI DATA STRUCTURES$/,$d' "$REPO/a-la-carchy.sh" > "$TMP/functions.sh"
source "$TMP/functions.sh"

# Refuse unexpected command shapes. No mock delegates to a real system command.
pacman() {
    case "$*" in
        -Qq) [[ "$QUERY_FAIL" == 0 ]] || return 1; cat "$TMP/packages" ;;
        '-R -- '*)
            printf 'pacman %s\n' "$*" >> "$TMP/actions"
            [[ "$REMOVE_FAIL" == 0 ]] || return 1
            [[ "$REMOVE_NOOP" == 0 ]] || return 0
            shift 2
            local pkg
            for pkg in "$@"; do
                grep -Fxv -- "$pkg" "$TMP/packages" > "$TMP/next" || true
                mv "$TMP/next" "$TMP/packages"
            done ;;
        *) echo "Unexpected pacman: $*" >&2; return 97 ;;
    esac
}
sudo() { [[ "$1" == pacman ]] || return 97; shift; pacman "$@"; }
flatpak() {
    local scope="${2#--}"
    case "$1" in
        list) [[ "$FP_QUERY_FAIL" == 0 ]] || return 1; cat "$TMP/$scope" ;;
        uninstall)
            [[ "$*" == "uninstall --$scope --noninteractive net.mkiol.SpeechNote" ]] || return 97
            printf 'flatpak %s\n' "$*" >> "$TMP/actions"
            [[ "$FP_REMOVE_FAIL" == 0 ]] || return 1
            grep -Fxv net.mkiol.SpeechNote "$TMP/$scope" > "$TMP/next" || true
            mv "$TMP/next" "$TMP/$scope" ;;
        *) return 97 ;;
    esac
}
systemctl() {
    case "$*" in
        '--user show --property=LoadState --value '*) printf '%s\n' "$SERVICE_STATE" ;;
        '--user disable --now '*) printf 'systemctl %s\n' "$*" >> "$TMP/actions"; [[ "$SERVICE_FAIL" == 0 ]] ;;
        *) return 97 ;;
    esac
}
hyprctl() { [[ "$*" == reload ]] || return 97; echo 'hyprctl reload' >> "$TMP/actions"; [[ "$RELOAD_FAIL" == 0 ]]; }
confirm_continue() { [[ "$CONFIRM" == yes ]]; }

PASSED=0
FAILED=0
check() {
    local description="$1"; shift
    if "$@"; then
        PASSED=$((PASSED + 1)); printf 'ok - %s\n' "$description"
    else
        FAILED=$((FAILED + 1)); printf 'FAIL - %s\n' "$description"
    fi
}
reset_fixture() {
    QUERY_FAIL=0 REMOVE_FAIL=0 REMOVE_NOOP=0 FP_QUERY_FAIL=0 FP_REMOVE_FAIL=0
    SERVICE_STATE=loaded SERVICE_FAIL=0 RELOAD_FAIL=0 CONFIRM=yes
    SUMMARY_LOG=()
    : > "$TMP/packages"; : > "$TMP/user"; : > "$TMP/system"; : > "$TMP/actions"
    unset -f voxtype 2>/dev/null || true
}
run_removal() { remove_voice_dictation > "$TMP/output" 2>&1; RC=$?; }

reset_fixture
printf '%s\n' qt6-speech pipewire whisper.cpp python-openai-whisper wtype > "$TMP/packages"
run_removal
check 'no supported apps: success' test "$RC" -eq 0
check 'shared libraries/runtimes alone: no actions' test ! -s "$TMP/actions"

reset_fixture
printf '%s\n' voxtype-bin hyprwhspr nerd-dictation-git speech-note qt6-speech wtype > "$TMP/packages"
run_removal
check 'all supported native families: success' test "$RC" -eq 0
check 'one explicit, nonrecursive removal transaction' grep -qxF 'pacman -R -- voxtype-bin hyprwhspr nerd-dictation-git speech-note' "$TMP/actions"
check 'shared libraries retained' grep -qxF qt6-speech "$TMP/packages"
check 'input helper retained' grep -qxF wtype "$TMP/packages"
check 'Voxtype service stopped' grep -qxF 'systemctl --user disable --now voxtype.service' "$TMP/actions"
check 'reload follows removal' test "$(tail -n 1 "$TMP/actions")" = 'hyprctl reload'
: > "$TMP/actions"
run_removal
check 'repeat is a no-op' test ! -s "$TMP/actions"

reset_fixture
printf '%s\n' net.mkiol.SpeechNote org.example.Unrelated > "$TMP/user"
printf '%s\n' net.mkiol.SpeechNote net.mkiol.SpeechNote > "$TMP/system"
run_removal
check 'both Flatpak scopes removed' test "$RC" -eq 0
check 'user uninstall has explicit scope' grep -qxF 'flatpak uninstall --user --noninteractive net.mkiol.SpeechNote' "$TMP/actions"
check 'system uninstall has explicit scope' grep -qxF 'flatpak uninstall --system --noninteractive net.mkiol.SpeechNote' "$TMP/actions"
check 'unrelated Flatpak retained' grep -qxF org.example.Unrelated "$TMP/user"
check 'duplicate Flatpak rows only trigger one uninstall per scope' test "$(grep -c '^flatpak uninstall --system ' "$TMP/actions")" -eq 1

reset_fixture
echo voxtype-bin > "$TMP/packages"
CONFIRM=no
run_removal
check 'cancel returns without failure' test "$RC" -eq 0
check 'cancel causes no mutations' test ! -s "$TMP/actions"

reset_fixture
QUERY_FAIL=1
run_removal
check 'pacman inventory failure is not reported as absent' test "$RC" -eq 1
check 'inventory failure has no actions' test ! -s "$TMP/actions"

reset_fixture
echo voxtype-bin > "$TMP/packages"
FP_QUERY_FAIL=1
run_removal
check 'Flatpak inventory failure aborts before native removals' test "$RC" -eq 1
check 'partial inventory has no actions' test ! -s "$TMP/actions"

reset_fixture
echo voxtype-bin > "$TMP/packages"
SERVICE_FAIL=1
run_removal
check 'service failure aborts' test "$RC" -eq 1
check 'service failure preserves package' grep -qxF voxtype-bin "$TMP/packages"
check 'service failure never invokes pacman removal' test "$(grep -c '^pacman ' "$TMP/actions")" -eq 0

reset_fixture
echo voxtype-bin > "$TMP/packages"
SERVICE_STATE=not-found
run_removal
check 'missing service does not prevent uninstall' test "$RC" -eq 0
check 'missing service is not disabled' test "$(grep -c '^systemctl ' "$TMP/actions")" -eq 0

reset_fixture
echo voxtype-bin > "$TMP/packages"
REMOVE_FAIL=1
run_removal
check 'package removal failure reported' test "$RC" -eq 1
check 'failure explains stopped services' grep -q 'stopped services may need restarting' "$TMP/output"

reset_fixture
echo voxtype-bin > "$TMP/packages"
REMOVE_NOOP=1
run_removal
check 'success exit without actual removal detected' test "$RC" -eq 1

reset_fixture
echo net.mkiol.SpeechNote > "$TMP/user"
FP_REMOVE_FAIL=1
run_removal
check 'Flatpak failure reported' test "$RC" -eq 1

reset_fixture
echo voxtype-bin > "$TMP/packages"
RELOAD_FAIL=1
run_removal
check 'reload failure is not silently swallowed' test "$RC" -eq 1

reset_fixture
voxtype() { return 97; }
run_removal
check 'unmanaged executable reported as incomplete' test "$RC" -eq 1
check 'unmanaged executable is never deleted or invoked' test ! -s "$TMP/actions"
check 'unmanaged install guidance printed' grep -q 'original installer' "$TMP/output"
unset -f voxtype

check 'menu includes opt-in Remove/Keep' grep -qF 'voice_dictation|Dictation|Remove|Keep|radio|' "$REPO/a-la-carchy.sh"
check 'selection wired to removal flag' grep -qF '[[ "${TOGGLE_SELECTIONS[voice_dictation]:-0}" == 1 ]] && REMOVE_VOICE_DICTATION=true' "$REPO/a-la-carchy.sh"
check 'execution wired to removal function' grep -qx '    remove_voice_dictation' "$REPO/a-la-carchy.sh"
printf '\n%s passed, %s failed\n' "$PASSED" "$FAILED"
[[ "$FAILED" -eq 0 ]]
