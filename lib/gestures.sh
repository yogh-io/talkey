# Delay a single dictate press until a possible double-tap has been resolved.
gesture_pending() {
  GESTURE_DIR="$(readlink -f "$STATE_DIR/gesture.current" 2>/dev/null)"
  [[ -d "$GESTURE_DIR" && ! -f "$GESTURE_DIR/done" && ! -f "$GESTURE_DIR/cancelled" ]]
}

gesture_cancel() {
  if gesture_pending; then : > "$GESTURE_DIR/cancelled"; fi
  rm -f "$STATE_DIR/gesture.current"
}

gesture_fire() {
  gesture_pending || return 0
  [[ "$GESTURE_DIR" == "${TALKEY_GESTURE_DIR:-}" ]] || return 0
  local dir="$GESTURE_DIR" action take owner pressed released
  : > "$dir/done"
  rm -f "$STATE_DIR/gesture.current"
  action="$(cat "$dir/action")"; take="$(cat "$dir/take")"
  owner="$(cat "$dir/owner")"; pressed="$(cat "$dir/pressed")"
  MODE=dictate
  session_load dictate || true
  case "$action" in
    auto-finish)
      if auto_active && [[ "$AUTO_DIR" == "$owner" && "$TAKE_DIR" == "$take" ]] && dictation_active; then
        auto_finish_or_stop
      fi ;;
    auto-off)
      if auto_active && [[ "$AUTO_DIR" == "$owner" ]]; then auto_stop; fi ;;
    finish)
      if ! auto_active && [[ "$TAKE_DIR" == "$take" ]] && recording_active; then finish_dictation; fi ;;
    start)
      if ! auto_active && ! recording_active; then
        LANG_OVERRIDE="$(cat "$dir/language")"
        begin_dictation
        printf '%s\n' "$pressed" > "$PRESSTIME"
        if [[ -f "$dir/released" ]]; then
          released="$(cat "$dir/released")"
          if (( released - pressed >= HOLD_MS )); then finish_dictation; fi
        fi
      fi ;;
  esac
}

gesture_press() {
  local pressed dir action=start take="$TAKE_DIR" owner='' delay
  if gesture_pending; then
    pressed="$(cat "$GESTURE_DIR/pressed")"
    # Ignore key-repeat until a real release separates the taps.
    [[ -f "$GESTURE_DIR/released" ]] || return 0
    if (( EVENT_MS - pressed <= DOUBLE_TAP_MS )); then
      gesture_cancel
      if auto_active; then auto_stop; else auto_start; fi
      return
    fi
    local TALKEY_GESTURE_DIR="$GESTURE_DIR"
    gesture_fire
  fi
  session_load dictate || true
  take="$TAKE_DIR"
  if auto_active; then
    owner="$AUTO_DIR"; action=auto-off
    if dictation_active && [[ "$(cat "$TAKE_DIR/auto.owner" 2>/dev/null)" == "$AUTO_DIR" ]]; then action=auto-finish; fi
  elif recording_active; then action=finish; fi
  dir="$(mktemp -d "$STATE_DIR/gesture.XXXXXXXX")"
  printf '%s\n' "$action" > "$dir/action"
  printf '%s\n' "$take" > "$dir/take"
  printf '%s\n' "$owner" > "$dir/owner"
  printf '%s\n' "$EVENT_MS" > "$dir/pressed"
  printf '%s\n' "${LANG_OVERRIDE:-${TALKEY_LANG:-en}}" > "$dir/language"
  ln -s "$dir" "$STATE_DIR/gesture.current.new-$$"
  mv -Tf "$STATE_DIR/gesture.current.new-$$" "$STATE_DIR/gesture.current"
  if (( DOUBLE_TAP_MS == 0 )); then
    local TALKEY_GESTURE_DIR="$dir"
    gesture_fire
  else
    delay="$(awk -v ms="$DOUBLE_TAP_MS" 'BEGIN {print ms/1000}')"
    (
      trap - EXIT
      sleep "$delay"
      [[ -d "$dir" && ! -f "$dir/cancelled" && ! -f "$dir/done" ]] || exit 0
      TALKEY_GESTURE_DIR="$dir" "$SCRIPT_DIR/talkey" _gesture fire
    ) 9>&- </dev/null >/dev/null 2>>"$STATE_DIR/log" &
  fi
}

gesture_release() {
  if gesture_pending; then
    printf '%s\n' "$EVENT_MS" > "$GESTURE_DIR/released"
  elif ! auto_active && recording_active; then
    local pressed; pressed="$(cat "$PRESSTIME" 2>/dev/null || echo 0)"
    if (( EVENT_MS - ${pressed:-0} >= HOLD_MS )); then finish_dictation; fi
  fi
}
