# talkey runs on any X11 desktop with bash - the AUR package is one way in, not
# the only one. Package names differ per distro and guessing them wrong sends
# people looking for something that does not exist under that name, so name the
# COMMANDS, and only spell out packages where we can actually tell.
declare -A ARCH_PKG=(
  [ffmpeg]=ffmpeg      [paplay]=libpulse       [xdotool]=xdotool
  [xset]=xorg-xset     [curl]=curl             [xev]=xorg-xev
  [jq]=jq              [python3]=python        [inotifywait]=inotify-tools
  [notify-send]=libnotify [flock]=util-linux
)
# Not in any official repo, so `pacman -S` answers "target not found" - which is
# a worse message than no message, because it reads as talkey being wrong about
# its own dependency.
declare -A AUR_PKG=([eww]=eww)
pkg_hint() {  # pkg_hint <command>...
  local c repo=() aur=()
  if ! have pacman; then
    printf 'install whatever your distro calls the packages providing: %s' "$*"
    return
  fi
  for c in "$@"; do
    if [[ -n "${AUR_PKG[$c]:-}" ]]; then aur+=("${AUR_PKG[$c]}"); else repo+=("${ARCH_PKG[$c]:-$c}"); fi
  done
  (( ${#repo[@]} )) && printf 'sudo pacman -S %s' "${repo[*]}"
  (( ${#repo[@]} && ${#aur[@]} )) && printf '\n   '
  (( ${#aur[@]} )) && printf 'from the AUR (not in the official repos): %s' "${aur[*]}"
}

# --- what is still outstanding ---------------------------------------------
# Steps that cannot finish record why here instead of failing quietly, and the
# summary prints the list. "done." used to be unconditional, so a run where the
# server never answered and no keys were bound still ended by telling you to
# hold your dictate key and speak. Any step added later gets a truthful ending
# by calling todo once.
TODO=()
todo() { TODO+=("$1"); }

# ===========================================================================
# deps
# ===========================================================================
step_deps() {
  step "checking what is installed"
  local missing=()
  # eww, inotifywait and jq are the HUD, and the HUD is not a bonus - without it
  # you dictate blind. Same list as the package's depends.
  for c in ffmpeg paplay xdotool xset curl flock eww inotifywait jq xev; do
    have "$c" || missing+=("$c")
  done
  if (( ${#missing[@]} )); then
    warn "missing, and dictation will not work without them: ${missing[*]}"
    die "$(pkg_hint "${missing[@]}")"
  fi
  ok "core tools present"

  local soft=()
  have python3    || soft+=("python (live text in the HUD as you speak)")
  have notify-send|| soft+=("libnotify (desktop notifications)")
  have ollama     || soft+=("ollama (names recordings after what you said)")
  if (( ${#soft[@]} )); then
    info "optional, everything still dictates without them:"
    printf '     %s\n' "${soft[@]}"
  fi
}
