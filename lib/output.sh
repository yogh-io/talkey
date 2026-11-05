# Sourced by bin/talkey. See docs/architecture.md for ownership rules.

capture_target() {
  : > "$TARGET_WIN"
  [[ "$TARGET_WINDOW" == "1" ]] && command -v "$XDOTOOL" >/dev/null 2>&1 || return 0
  "$XDOTOOL" getactivewindow 2>/dev/null > "$TARGET_WIN"
}

send_return() {
  local win cur
  win="$(cat "$TARGET_WIN" 2>/dev/null)"
  if [[ -n "$win" ]] && command -v "$XDOTOOL" >/dev/null 2>&1; then
    cur="$("$XDOTOOL" getactivewindow 2>/dev/null)"
    if [[ -z "$cur" || "$cur" != "$win" ]]; then
      log "focus moved ($win -> $cur), skipping Enter"
      return 0
    fi
  fi
  "$XDOTOOL" key --clearmodifiers Return
}

insert_text() {
  # Re-focus the captured target before typing (XTEST goes to the focused
  # window; Chrome/Electron ignore directed synthetic events, so we can't target
  # by window id). This can't stop focus drifting mid-typing if the mouse moves,
  # but send_return guards the critical Enter by skipping it on a focus change.
  local win; win="$(cat "$TARGET_WIN" 2>/dev/null)"
  if [[ -n "$win" ]]; then "$XDOTOOL" windowactivate --sync "$win" 2>/dev/null || return 1; fi
  if [[ "$PASTE" == "1" ]] && command -v "$XCLIP" >/dev/null 2>&1; then
    printf '%s' "$1" | "$XCLIP" -selection clipboard || return 1
    "$XDOTOOL" key --clearmodifiers ctrl+v
  else
    # Long messages type faster, so the whole text lands within TYPE_MAX_MS.
    local delay="$TYPE_DELAY"
    if (( TYPE_MAX_MS && ${#1} * delay > TYPE_MAX_MS )); then delay=$(( TYPE_MAX_MS / ${#1} )); fi
    "$XDOTOOL" type --clearmodifiers --delay "$delay" -- "$1"
  fi
}
