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

# Triangle bindings send presses only; key repeat is disabled by the bindings.
submit_pending() {
  SUBMIT_GESTURE="$(readlink -f "$STATE_DIR/submit.current" 2>/dev/null)"
  [[ -d "$SUBMIT_GESTURE" && ! -f "$SUBMIT_GESTURE/done" && ! -f "$SUBMIT_GESTURE/cancelled" ]]
}

submit_fire() (
  trap - EXIT
  submit_pending || return 0
  [[ "$SUBMIT_GESTURE" == "${TALKEY_SUBMIT_GESTURE:-}" ]] || return 0
  local dir="$SUBMIT_GESTURE" kind take owner
  : > "$dir/done"
  rm -f "$STATE_DIR/submit.current"
  kind="$(cat "$dir/kind")"; take="$(cat "$dir/take")"; owner="$(cat "$dir/owner")"
  MODE=dictate
  session_load dictate || true
  case "$kind" in
    take)
      if [[ "$TAKE_DIR" == "$take" && ! -f "$take/auto.stopped" ]] && dictation_active; then cycle_submit; fi ;;
    auto)
      if auto_active && [[ "$AUTO_DIR" == "$owner" ]]; then
        if dictation_active && [[ "$(cat "$TAKE_DIR/auto.owner" 2>/dev/null)" == "$owner" ]]; then cycle_submit
        else
          SUBMIT_REQ="$AUTO_DIR/submit.requested"; CANCEL_REQ="$AUTO_DIR/cancel.requested"
          cycle_submit
        fi
      fi ;;
    idle)
      if ! auto_active && ! dictation_active; then capture_target && send_return; fi ;;
  esac
)

submit_press() {
  local dir pressed kind=idle take='' owner='' delay
  if submit_pending; then
    pressed="$(cat "$SUBMIT_GESTURE/pressed")"
    if (( EVENT_MS - pressed <= DOUBLE_TAP_MS )); then
      : > "$SUBMIT_GESTURE/cancelled"
      rm -f "$STATE_DIR/submit.current"
      local ACTION=enter-toggle
      auto_enter_control
      return
    fi
    local TALKEY_SUBMIT_GESTURE="$SUBMIT_GESTURE"
    submit_fire
  fi
  MODE=dictate
  session_load dictate || true
  if dictation_active; then kind=take; take="$TAKE_DIR"
  elif auto_active; then kind=auto; owner="$AUTO_DIR"; fi
  dir="$(mktemp -d "$STATE_DIR/submit.XXXXXXXX")"
  printf '%s\n' "$kind" > "$dir/kind"
  printf '%s\n' "$take" > "$dir/take"
  printf '%s\n' "$owner" > "$dir/owner"
  printf '%s\n' "$EVENT_MS" > "$dir/pressed"
  ln -s "$dir" "$STATE_DIR/submit.current.new-$$"
  mv -Tf "$STATE_DIR/submit.current.new-$$" "$STATE_DIR/submit.current"
  if (( DOUBLE_TAP_MS == 0 )); then
    local TALKEY_SUBMIT_GESTURE="$dir"
    submit_fire
  else
    delay="$(awk -v ms="$DOUBLE_TAP_MS" 'BEGIN {print ms/1000}')"
    (
      trap - EXIT
      sleep "$delay"
      [[ -d "$dir" && ! -f "$dir/cancelled" && ! -f "$dir/done" ]] || exit 0
      TALKEY_SUBMIT_GESTURE="$dir" "$SCRIPT_DIR/talkey" _submit fire
    ) 9>&- </dev/null >/dev/null 2>>"$STATE_DIR/log" &
  fi
}

submit_settle() {
  # A phrase can finish faster than the double-tap window. Resolve its pending
  # triangle gesture before output, releasing the lock so a second tap can arrive.
  local take="$TAKE_DIR" dir kind target owner pressed now
  while submit_pending; do
    dir="$SUBMIT_GESTURE"; kind="$(cat "$dir/kind")"
    target="$(cat "$dir/take")"; owner="$(cat "$dir/owner")"
    if [[ "$kind" == take && "$target" == "$take" ]] ||
       [[ "$kind" == auto && "$owner" == "$(cat "$take/auto.owner" 2>/dev/null)" ]]; then
      pressed="$(cat "$dir/pressed")"; now="$(now_ms)"
      if (( now - pressed >= DOUBLE_TAP_MS )); then
        local TALKEY_SUBMIT_GESTURE="$dir"
        submit_fire
      else
        unlock_commands; sleep .01; lock_commands
      fi
    else break; fi
  done
}
