# Continuous listening is opt-in. Every callback is fenced by its run directory.
auto_load() { AUTO_DIR="$(readlink -f "$STATE_DIR/auto.current" 2>/dev/null)"; [[ -d "$AUTO_DIR" ]]; }
auto_active() { auto_load && process_alive "$AUTO_DIR/worker.pid"; }
auto_state() { printf '%s\n' "$1" > "$AUTO_DIR/state"; state_changed; }

# This preference survives listener restarts and logins. Manual
# dictation still uses TALKEY_SUBMIT, and triangle remains a per-phrase override.
auto_enter_path() { printf '%s/talkey/auto-enter' "${XDG_STATE_HOME:-$HOME/.local/state}"; }
auto_enter_enabled() {
  local value="$SUBMIT" path; path="$(auto_enter_path)"
  [[ ! -f "$path" ]] || value="$(cat "$path")"
  [[ "$value" == 1 ]]
}

auto_enter_control() {
  local value=0 path tmp
  case "$ACTION" in
    enter-on) value=1 ;;
    enter-toggle) if ! auto_enter_enabled; then value=1; fi ;;
  esac
  path="$(auto_enter_path)"
  mkdir -p -m 700 "$(dirname "$path")"
  tmp="$(mktemp "$path.XXXXXX")"
  printf '%s\n' "$value" > "$tmp"
  mv -f "$tmp" "$path"
  if auto_active; then
    # Update the next phrase without undoing an explicit cancellation.
    if [[ ! -f "$AUTO_DIR/cancel.requested" ]]; then
      if (( value )); then : > "$AUTO_DIR/submit.requested"
      else rm -f "$AUTO_DIR/submit.requested"; fi
    fi
    MODE=dictate
    if session_load dictate && dictation_active &&
       [[ "$(cat "$TAKE_DIR/auto.owner" 2>/dev/null)" == "$AUTO_DIR" && ! -f "$CANCEL_REQ" ]]; then
      if (( value )); then : > "$SUBMIT_REQ"; else rm -f "$SUBMIT_REQ"; fi
      overlay_footer "$(current_submit_footer)"
    fi
  fi
  state_changed
  if (( value )); then notify 'auto Enter on - send each phrase'
  else notify 'auto Enter off - type each phrase'; fi
}

auto_start() {
  gesture_cancel
  auto_active && return 0
  local python="${TALKEY_AUTO_PYTHON:-python3}"
  command -v "$python" >/dev/null || { echo "talkey auto: Python not found: $python" >&2; return 1; }
  # Validate the model and detector before interrupting a manual recording.
  if ! "$python" "$SCRIPT_DIR/talkey-auto-worker" --check; then
    notify 'auto mode unavailable - run: talkey-setup vad / talkey-setup turn' 5000
    return 1
  fi
  preflight_ok || return 1
  if recording_active; then
    record_stop quiet || return 1
    if keep_cancelled "$RAW" dictate "$(cat "$TRANSCRIPT" 2>/dev/null || true)"; then rm -f "$RAW"; fi
  fi
  AUTO_DIR="$(mktemp -d "$STATE_DIR/auto.XXXXXXXX")"
  local link="$STATE_DIR/auto.current.new-$$"
  ln -s "$AUTO_DIR" "$link"; mv -Tf "$link" "$STATE_DIR/auto.current"
  if auto_enter_enabled; then : > "$AUTO_DIR/submit.requested"; fi
  auto_state starting
  TALKEY_STATE_DIR="$STATE_DIR" TALKEY_DATA_DIR="$DATA_DIR" TALKEY_AUTO_RUN="$AUTO_DIR" TALKEY_LANG="$LANG_CODE" \
  TALKEY_FFMPEG="$FFMPEG" TALKEY_SOURCE="$AUDIO_SOURCE" TALKEY_MAX_REC_S="$MAX_REC_S" \
    "$python" "$SCRIPT_DIR/talkey-auto-worker" 9>&- </dev/null >>"$STATE_DIR/log" 2>&1 &
  process_save "$!" "$AUTO_DIR/worker.pid" || { auto_state error; return 1; }
  play_sound auto-on
  notify 'auto listening on - double-tap dictate to switch off'
}

auto_stop() {
  gesture_cancel
  auto_load || return 0
  local previous; previous="$(cat "$AUTO_DIR/state" 2>/dev/null || echo off)"
  # Invalidate callbacks before stopping capture or a blocked worker.
  auto_state off
  process_stop "$AUTO_DIR/worker.pid" || return 1
  auto_state off
  MODE=dictate
  if session_load dictate && [[ "$(cat "$TAKE_DIR/auto.owner" 2>/dev/null)" == "$AUTO_DIR" ]]; then
    # Stopping auto mode cancels its unfinished take, including in-flight inference.
    : > "$TAKE_DIR/auto.stopped"
    : > "$CANCEL_REQ"
    if [[ -f "$PIDFILE" ]]; then finish_dictation; fi
  fi
  notify 'auto listening off'
  [[ "$previous" == off ]] || play_sound auto-off
}

auto_finish_or_stop() {
  # The capture worker owns the audio boundary. Request a finish on this take
  # instead of starting another recorder or cancelling the continuous listener.
  if session_load dictate && dictation_active &&
     [[ "$(cat "$TAKE_DIR/auto.owner" 2>/dev/null)" == "$AUTO_DIR" ]]; then
    if recording_active; then
      : > "$TAKE_DIR/finish.requested"
      notify 'finishing phrase'
    fi
    # Already transcribing: leave the one existing finalizer in charge.
    return 0
  fi
  auto_stop
}

auto_command() {
  case "$ACTION" in
    status)
      local enabled=false state=off submit=type ended=false enter=false
      if auto_enter_enabled; then enter=true; submit=send; fi
      if auto_load; then
        state="$(cat "$AUTO_DIR/state" 2>/dev/null || echo off)"
        if process_alive "$AUTO_DIR/worker.pid"; then enabled=true
        elif [[ "$state" != off ]]; then state=error; fi
        [[ ! -f "$AUTO_DIR/speech.ended" ]] || ended=true
        if [[ "$enabled" == true ]]; then
          MODE=dictate
          if session_load dictate && dictation_active && [[ "$(cat "$TAKE_DIR/auto.owner" 2>/dev/null)" == "$AUTO_DIR" ]]; then
            submit="$(current_submit_footer)"
          else
            submit=type
            [[ ! -f "$AUTO_DIR/submit.requested" ]] || submit=send
            [[ ! -f "$AUTO_DIR/cancel.requested" ]] || submit=cancel
          fi
        fi
      fi
      case "$state" in off|starting|listening|speaking|paused|transcribing|error) ;; *) state=error ;; esac
      printf '{"enabled":%s,"state":"%s","submit":"%s","speech_ended":%s,"enter_enabled":%s}\n' "$enabled" "$state" "$submit" "$ended" "$enter" ;;
    enter-on|enter-off|enter-toggle) auto_enter_control ;;
    stop) auto_stop ;;
    toggle) if auto_active; then auto_stop; else MODE=dictate; session_load dictate || true; auto_start; fi ;;
    start) MODE=dictate; session_load dictate || true; auto_start ;;
  esac
}

auto_event() {
  auto_active || return 1
  [[ "${TALKEY_AUTO_RUN:-}" == "$AUTO_DIR" && "$(cat "$AUTO_DIR/state")" != off ]] || return 1
  MODE=dictate
  case "$ACTION" in
    idle)
      auto_state off
      play_sound auto-off
      notify 'auto listening off - idle timeout' ;;
    begin)
      session_load dictate || true
      dictation_active && return 1
      SAMPLE_RATE=16000; CHANNELS=1; BYTES_PER_SECOND=32000
      session_new
      printf '%s\n' "$AUTO_DIR" > "$TAKE_DIR/auto.owner"
      printf '%s\n' "$EVENT_MS" > "$PRESSTIME"
      : > "$RAW"
      process_save "$(cat "$AUTO_DIR/worker.pid")" "$PIDFILE"
      local flag
      for flag in submit.requested cancel.requested; do
        if [[ -f "$AUTO_DIR/$flag" ]]; then mv "$AUTO_DIR/$flag" "$TAKE_DIR/$flag"; fi
      done
      # Controls are consumed by this phrase; prepare the default for the next.
      if auto_enter_enabled; then : > "$AUTO_DIR/submit.requested"; fi
      rm -f "$AUTO_DIR/speech.ended"
      prime_display; overlay_state speaking; start_pump
      auto_state speaking
      printf '%s\n' "$TAKE_DIR" ;;
    finish)
      session_load dictate || return 1
      [[ "$TAKE_DIR" == "${TALKEY_AUTO_TAKE:-}" && "$(cat "$TAKE_DIR/auto.owner" 2>/dev/null)" == "$AUTO_DIR" ]] || return 1
      : > "$TAKE_DIR/speech.ended"; : > "$AUTO_DIR/speech.ended"
      auto_state transcribing
      finish_dictation ;;
    error)
      auto_state error
      if session_load dictate && [[ "$(cat "$TAKE_DIR/auto.owner" 2>/dev/null)" == "$AUTO_DIR" ]]; then
        record_stop quiet || true
        overlay_state error; overlay_body 'auto mode stopped after an error - see talkey runtime log'
      fi ;;
  esac
}
