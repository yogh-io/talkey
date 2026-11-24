# Key-event state machine. State transitions and desktop output hold command.lock;
# slow inference runs unlocked against a resolved, private take directory.
now_ms() { date +%s%3N; }
log() { printf '%s talkey[%s] %s\n' "$(date +%H:%M:%S)" "$MODE" "$*" >> "$STATE_DIR/log"; }
diag() { printf '%s talkey %s\n' "$(date +%H:%M:%S.%3N)" "$*" >> "$DIAG"; }
build_prompt() {
  [[ -f "$PROMPT_FILE" ]] || return 0
  sed '/^[[:space:]]*#/d' "$PROMPT_FILE" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//'
}
notify() {
  command -v notify-send >/dev/null 2>&1 || return 0
  notify-send -t "${2:-1500}" -h string:x-canonical-private-synchronous:talkey \
    -h string:x-dunst-stack-tag:talkey "$1" >/dev/null 2>&1 || true
}
play_sound() {
  [[ "$SOUNDS" == 1 && -f "$SOUND_DIR/$1.wav" ]] || return 0
  "$PLAYER" "$SOUND_DIR/$1.wav" >/dev/null 2>&1 9>&- &
}

preflight_ok() {
  curl -fsS --max-time 2 -o /dev/null "${WHISPER_URL%/inference}/" 2>/dev/null && return 0
  overlay_state error; overlay_body 'no speech server - run: talkey doctor'
  : > "$ALERT"; state_changed
  notify "talkey: no speech server - run 'talkey doctor'" 5000
  return 1
}

begin_dictation() {
  # Orphaned takes remain individually recoverable; archiving never races a
  # still-running finisher. A new take cannot truncate their PCM or transcript.
  if [[ -s "$RAW" ]] && ! process_alive "$PIDFILE" && ! process_alive "$PENDING"; then
    if [[ "$TAKE_DIR" == "$STATE_DIR" ]]; then
      local saved; saved="$(mktemp "$STATE_DIR/legacy-XXXXXX.raw")"
      mv "$RAW" "$saved"; RAW="$saved"
    fi
    ( trap - EXIT; archive_audio "$(cat "$FINAL_TEXT" 2>/dev/null || true)" && rm -f "$RAW" ) 9>&- &
  fi
  LANG_CODE="${LANG_OVERRIDE:-${TALKEY_LANG:-en}}"
  SAMPLE_RATE="$CONFIGURED_SAMPLE_RATE"; CHANNELS="$CONFIGURED_CHANNELS"
  BYTES_PER_SECOND=$(( SAMPLE_RATE * CHANNELS * 2 ))
  session_new
  prime_display
  preflight_ok || return 1
  printf '%s\n' "$EVENT_MS" > "$PRESSTIME"
  [[ "$SUBMIT" != 1 ]] || : > "$SUBMIT_REQ"
  overlay_footer "$(current_submit_footer)"
  diag 'dictate start'
  record_start
}

# Exactly one finisher owns a take. Its EXIT cleanup only touches that take.
finish_dictation() {
  [[ -f "$PIDFILE" ]] || return 0
  process_save "$$" "$PENDING"
  trap session_cleanup EXIT
  local quiet='' text='' result=0 archived=0
  [[ ! -f "$CANCEL_REQ" ]] || quiet=quiet
  record_stop "$quiet" || return 1
  migrate_finished_legacy
  stat -c%s "$RAW" > "$TAKE_DIR/bytes"
  if [[ ! -f "$CANCEL_REQ" ]]; then
    overlay_state transcribing
    unlock_commands
    if [[ ! -s "$RAW" ]]; then
      result=2
    elif ! wrap_wav "$RAW" "$WAV"; then
      result=1
    elif ! text="$(stt "$WAV")"; then
      result=1
    elif [[ -f "$TAKE_DIR/auto.owner" && -n "${TALKEY_END_PHRASE:-}" ]] &&
         ! text="$(printf '%s' "$text" | "${TALKEY_AUTO_PYTHON:-python3}" "$DATA_DIR/lib/speech.py" strip-cue "$TALKEY_END_PHRASE")"; then
      result=1
    elif [[ -z "$text" ]]; then
      result=2
    fi
    lock_commands
  fi
  submit_settle
  if [[ -f "$CANCEL_REQ" ]]; then
    if keep_cancelled "$RAW" "$MODE" "$(cat "$TRANSCRIPT" 2>/dev/null || true)"; then
      archived=1
      if [[ "$ARCHIVE" == 1 && -s "$RAW" ]]; then notify '✖ cancelled (kept)'; else notify '✖ cancelled'; fi
    else
      notify "cancelled - archive failed; audio kept at $RAW" 5000
    fi
    overlay_state cancelled; overlay_body ' '
    [[ -f "$TAKE_DIR/auto.stopped" ]] || play_sound cancel
  else
    printf '%s\n' "$text" > "$FINAL_TEXT"
    if (( result == 1 )); then
      overlay_state error; overlay_body 'transcription failed - run: talkey doctor'
      notify 'transcription failed - run: talkey doctor'
    elif (( result == 2 )); then
      overlay_state nospeech; overlay_body ' '; notify '(no speech)'
    elif ! session_current; then
      # A newer take owns the screen and keyboard. Keep this result for recovery.
      log "superseded result kept at $FINAL_TEXT"
      notify "earlier transcript ready: $FINAL_TEXT" 5000
    else
      dispatch_voice_command "$text"
      text="$COMMAND_TEXT"
      printf '%s\n' "$text" > "$FINAL_TEXT"
      if (( COMMAND_HANDLED )); then
        if (( COMMAND_ERROR )); then overlay_state error; else overlay_state done; fi
        overlay_body "$COMMAND_FEEDBACK"
      else
        overlay_body "$text"; overlay_metrics
        if capture_target && insert_text "$text"; then
          overlay_state done
          if [[ -f "$SUBMIT_REQ" ]]; then send_return || result=1; fi
          notify '✓ typed' 800
        else
          result=1; overlay_state error
          notify "could not type - transcript kept at $FINAL_TEXT" 5000
        fi
      fi
    fi
    # Release the desktop lock before conversion/titling. New takes use different
    # paths, so they can start while this take is archived.
    unlock_commands
    if archive_audio "$text"; then archived=1; else notify "archive failed; audio kept at $RAW" 5000; fi
  fi
  if (( archived )); then rm -f "$RAW"; fi
  rm -f "$PRESSTIME" "$MEMO_PRESSTIME" "$SUBMIT_REQ" "$CANCEL_REQ"
  diag "finished status=$result"
  (( result != 1 ))
}

restart_dictation() {
  local lang="$LANG_CODE" pt
  pt="$(cat "$PRESSTIME")"
  record_stop quiet || return 1
  if keep_cancelled "$RAW" dictate "$(cat "$TRANSCRIPT" 2>/dev/null || true)"; then rm -f "$RAW"; fi
  session_new
  LANG_CODE="$lang"; printf '%s\n' "$lang" > "$TAKE_DIR/language"
  printf '%s\n' "$pt" > "$PRESSTIME"
  [[ "$SUBMIT" != 1 ]] || : > "$SUBMIT_REQ"
  play_sound cancel
  record_start
  notify '↺ restarted' 900
}

square_mid_dictation() {
  local last=0
  [[ ! -s "$MEMO_PRESSTIME" ]] || last="$(cat "$MEMO_PRESSTIME")"
  if (( EVENT_MS - last <= DOUBLE_TAP_MS )); then restart_dictation; return; fi
  printf '%s\n' "$EVENT_MS" > "$MEMO_PRESSTIME"
  overlay_footer "$(current_submit_footer)"
}

cycle_submit() {
  if [[ -f "$SUBMIT_REQ" ]]; then
    rm -f "$SUBMIT_REQ"; : > "$CANCEL_REQ"; notify '✖ will cancel'
  elif [[ -f "$CANCEL_REQ" ]]; then
    rm -f "$CANCEL_REQ"; notify '⏎ will type, no send'
  else : > "$SUBMIT_REQ"; notify '⏎ will send'; fi
  overlay_footer "$(current_submit_footer)"
}

finish_memo() {
  [[ -f "$PIDFILE" ]] || return 0
  trap session_cleanup EXIT
  record_stop || return 1
  migrate_finished_legacy
  local pressed; pressed="$(cat "$PRESSTIME" 2>/dev/null || echo 0)"
  if [[ "$ACTION" == release ]] && (( EVENT_MS - ${pressed:-0} < HOLD_MS )); then
    # A tap is the first half of a double tap, not a memo: keep its audio, skip playback.
    printf '%s\n' "$pressed" > "$STATE_DIR/memo.tap"
    if keep_cancelled "$RAW" memo; then rm -f "$RAW"; fi
    return 0
  fi
  unlock_commands
  # Retain raw audio on either playback or archive failure.
  playback || return 1
  archive_audio && rm -f "$RAW"
}

main() {
  case "$MODE" in
    auto) auto_command ;;
    _auto) auto_event ;;
    translate) translate_command ;;
    _translate) translate_event ;;
    camera) camera_command ;;
    _gesture) gesture_fire ;;
    _submit) submit_fire ;;
    help)
      if [[ -f "$HELP_ON" ]]; then rm -f "$HELP_ON"; else : > "$HELP_ON"; fi ;;
    submit) submit_press ;;
    memo)
      if [[ "$ACTION" == press ]]; then
        MODE=dictate
        if session_load dictate && dictation_active; then
          [[ ! -f "$TAKE_DIR/auto.owner" ]] || return 0
          [[ ! -f "$DICTATE_PID" ]] || square_mid_dictation
          return 0
        fi
        MODE=memo
      fi
      session_load memo || session_paths "$STATE_DIR"
      local tap=0
      if [[ "$ACTION" == press && -s "$STATE_DIR/memo.tap" ]]; then
        tap="$(cat "$STATE_DIR/memo.tap")"; rm -f "$STATE_DIR/memo.tap"
      fi
      case "$ACTION" in
        press|start)
          if (( DOUBLE_TAP_MS && EVENT_MS - tap <= DOUBLE_TAP_MS )); then
            if translate_active; then translate_stop; else translate_start; fi
          elif ! process_alive "$PIDFILE"; then
            SAMPLE_RATE="$CONFIGURED_SAMPLE_RATE"; CHANNELS="$CONFIGURED_CHANNELS"
            session_new; printf '%s\n' "$EVENT_MS" > "$PRESSTIME"; record_start
          fi ;;
        release|stop) finish_memo ;;
      esac ;;
    dictate)
      session_load dictate || true
      case "$ACTION" in
        press) gesture_press ;;
        release) gesture_release ;;
        start) gesture_cancel; if ! auto_active && ! recording_active; then begin_dictation; fi ;;
        stop) gesture_cancel; if auto_active; then auto_finish_or_stop; else finish_dictation; fi ;;
      esac ;;
  esac
}
