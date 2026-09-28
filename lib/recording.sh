# Audio capture and live-preview worker lifecycle.
wrap_wav() {
  "$FFMPEG" -nostdin -hide_banner -loglevel error -y -f s16le \
    -ar "$SAMPLE_RATE" -ac "$CHANNELS" -i "$1" "$2" 2>>"$STATE_DIR/log"
}

start_pump() {
  [[ "$STREAM" == 1 ]] || return 0
  command -v python3 >/dev/null 2>&1 || { log 'no python3, live text disabled'; return 0; }
  TALKEY_WHISPER_URL="$WHISPER_URL" TALKEY_LANG="$LANG_CODE" \
  TALKEY_STREAM_INTERVAL="$STREAM_INTERVAL" TALKEY_PROMPT="$STREAM_PROMPT" \
  TALKEY_SAMPLE_RATE="$SAMPLE_RATE" TALKEY_CHANNELS="$CHANNELS" TALKEY_DIAG="$DIAG" \
    python3 "$SCRIPT_DIR/talkey-live-pump" "$RAW" "$TRANSCRIPT" "$PIDFILE" \
      9>&- >/dev/null 2>>"$STATE_DIR/log" &
  process_save "$!" "$PUMP_PID" || log 'live preview failed to start'
}
stop_pump() { process_stop "$PUMP_PID"; }

record_start() {
  : > "$RAW"
  "$FFMPEG" -nostdin -hide_banner -loglevel error -f pulse -i "$AUDIO_SOURCE" \
    -ac "$CHANNELS" -ar "$SAMPLE_RATE" -t "$MAX_REC_S" -f s16le - \
    > "$RAW" 2>>"$STATE_DIR/log" 9>&- &
  process_save "$!" "$PIDFILE" || { overlay_state error; notify 'microphone failed to start'; return 1; }
  log "recording -> $RAW"
  play_sound start
  if [[ "$MODE" == dictate ]]; then prime_display; start_pump; else notify '● recording'; fi
  state_changed
}

record_stop() {
  [[ -f "$PIDFILE" ]] || return 1
  if [[ -f "$TAKE_DIR/auto.owner" ]]; then
    # The continuous listener owns capture; this pidfile only gates the preview.
    rm -f "$PIDFILE" "$PIDFILE.start"
  else
    process_stop "$PIDFILE" INT || return 1
  fi
  if [[ "$MODE" == dictate ]]; then stop_pump || return 1; fi
  [[ "${1:-}" == quiet ]] || play_sound stop
  state_changed
}

playback() {
  [[ -s "$RAW" ]] || return 0
  "$FFMPEG" -nostdin -hide_banner -loglevel error -y -f s16le \
    -ar "$SAMPLE_RATE" -ac "$CHANNELS" -i "$RAW" \
    -af "volume=${MEMO_GAIN},alimiter=limit=0.97" "$WAV" 2>>"$STATE_DIR/log" \
    || wrap_wav "$RAW" "$WAV" || return 1
  notify '▶ playing'
  "$PLAYER" "$WAV" 2>>"$STATE_DIR/log"
}
