# Live translation into a virtual microphone. Callbacks are fenced by the run directory.
TRANSLATE_SINK=talkey_translate
TRANSLATE_MIC=talkey_translate_mic
translate_load() { TRANSLATE_DIR="$(readlink -f "$STATE_DIR/translate.current" 2>/dev/null)"; [[ -d "$TRANSLATE_DIR" ]]; }
translate_active() { translate_load && process_alive "$TRANSLATE_DIR/worker.pid"; }
translate_state() { printf '%s\n' "$1" > "$TRANSLATE_DIR/state"; state_changed; }

# A null sink carries the voice, and a source on top of it is what apps list as a
# microphone. Both stay after stop, so a call keeps its microphone selected.
translate_devices() {
  local sinks sources
  sinks="$(pactl list short sinks)" && sources="$(pactl list short sources)" || return 1
  if ! awk -v n="$TRANSLATE_SINK" '$2 == n {f=1} END {exit !f}' <<< "$sinks"; then
    pactl load-module module-null-sink "sink_name=$TRANSLATE_SINK" rate=48000 channels=1 \
      "sink_properties=\"device.description='talkey translation output'\"" > /dev/null || return 1
  fi
  if ! awk -v n="$TRANSLATE_MIC" '$2 == n {f=1} END {exit !f}' <<< "$sources"; then
    pactl load-module module-remap-source "master=$TRANSLATE_SINK.monitor" "source_name=$TRANSLATE_MIC" \
      "source_properties=\"device.description='talkey English mic'\"" > /dev/null || return 1
  fi
}

translate_start() {
  gesture_cancel
  translate_active && return 0
  local python="${TALKEY_TRANSLATE_PYTHON:-python3}" source="$AUDIO_SOURCE"
  command -v "$python" > /dev/null || { echo "talkey translate: Python not found: $python" >&2; return 1; }
  [[ "$source" != default ]] || source="$(pactl get-default-source 2> /dev/null || true)"
  if [[ "$source" == "$TRANSLATE_MIC" ]]; then
    # Listening to its own output would translate the translation forever.
    notify 'translate: your input is the talkey English mic - choose your real microphone' 5000
    return 1
  fi
  if ! "$python" "$SCRIPT_DIR/talkey-translate-worker" --check; then
    notify 'translate mode unavailable - run: talkey-setup translate' 5000
    return 1
  fi
  preflight_ok || return 1
  translate_devices || { notify 'translate: could not create the talkey English mic' 5000; return 1; }
  if auto_active; then auto_stop; fi
  rm -f "$STATE_DIR/translate.showing" "$STATE_DIR/translate.subtitles" "$STATE_DIR/translate.raw"
  TRANSLATE_DIR="$(mktemp -d "$STATE_DIR/translate.XXXXXXXX")"
  local link="$STATE_DIR/translate.current.new-$$"
  ln -s "$TRANSLATE_DIR" "$link"; mv -Tf "$link" "$STATE_DIR/translate.current"
  translate_state starting
  TALKEY_STATE_DIR="$STATE_DIR" TALKEY_DATA_DIR="$DATA_DIR" TALKEY_TRANSLATE_RUN="$TRANSLATE_DIR" \
  TALKEY_FFMPEG="$FFMPEG" TALKEY_SOURCE="$AUDIO_SOURCE" TALKEY_WHISPER_URL="$WHISPER_URL" \
  TALKEY_TRANSLATE_SINK="$TRANSLATE_SINK" TALKEY_ARCHIVE_DIR="$ARCHIVE_DIR" \
    "$python" "$SCRIPT_DIR/talkey-translate-worker" 9>&- < /dev/null >> "$STATE_DIR/log" 2>&1 &
  process_save "$!" "$TRANSLATE_DIR/worker.pid" || { translate_state error; return 1; }
  notify 'translate starting - loading the voice' 3000
}

translate_stop() {
  translate_load || return 0
  local previous; previous="$(cat "$TRANSLATE_DIR/state" 2> /dev/null || echo off)"
  # Invalidate callbacks before stopping the worker.
  translate_state off
  process_stop "$TRANSLATE_DIR/worker.pid" || return 1
  # A killed worker cannot remove its marker; the HUD would stay open.
  rm -f "$STATE_DIR/translate.showing" "$STATE_DIR/translate.raw"
  translate_state off
  notify 'translate off'
  [[ "$previous" == off ]] || play_sound auto-off
}

translate_command() {
  case "$ACTION" in
    status)
      local enabled=false state=off
      if translate_load; then
        state="$(cat "$TRANSLATE_DIR/state" 2> /dev/null || echo off)"
        if process_alive "$TRANSLATE_DIR/worker.pid"; then enabled=true
        elif [[ "$state" != off ]]; then state=error; fi
      fi
      case "$state" in off|starting|listening|hearing|error) ;; *) state=error ;; esac
      printf '{"enabled":%s,"state":"%s","microphone":"%s"}\n' "$enabled" "$state" "$TRANSLATE_MIC" ;;
    stop) translate_stop ;;
    toggle) if translate_active; then translate_stop; else translate_start; fi ;;
    start) translate_start ;;
  esac
}

translate_event() {
  translate_active || return 1
  [[ "${TALKEY_TRANSLATE_RUN:-}" == "$TRANSLATE_DIR" && "$(cat "$TRANSLATE_DIR/state")" != off ]] || return 1
  case "$ACTION" in
    ready)
      translate_state listening
      play_sound auto-on
      notify 'translating to English - pick "talkey English mic" in your call' 4000 ;;
    error)
      translate_state error
      notify 'translate stopped after an error - see the talkey runtime log' 5000 ;;
  esac
}
