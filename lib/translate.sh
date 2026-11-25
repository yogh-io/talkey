# Live translation into a virtual microphone. Callbacks are fenced by the run directory.
# Other names let a second, private instance run beside yours, for example in tests.
TRANSLATE_SINK="${TALKEY_TRANSLATE_SINK:-talkey_translate}"
TRANSLATE_MIC="${TALKEY_TRANSLATE_MIC:-talkey_translate_mic}"
TRANSLATE_ENGINE="${TALKEY_TRANSLATE_ENGINE:-whisper}"
TRANSLATE_URL="${TALKEY_TRANSLATE_URL:-http://127.0.0.1:11434/api/chat}"
TRANSLATE_LLM="${TALKEY_TRANSLATE_LLM:-translategemma:4b}"
WHISPER_UNIT=talkey-whisper.service
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

# Seamless needs the GPU memory whisper holds. With TALKEY_SEAMLESS_PAUSE_WHISPER=1 it gets it
# until translate stops; only a unit this stopped is started again.
whisper_pause() {
  command -v systemctl > /dev/null && systemctl --user is-active --quiet "$WHISPER_UNIT" || return 0
  : > "$STATE_DIR/whisper.paused"
  systemctl --user stop "$WHISPER_UNIT" || { rm -f "$STATE_DIR/whisper.paused"; return 1; }
}

# Ollama keeps a model on the GPU for minutes after its last use. talkey's own models are unloaded;
# Ollama loads them again when they are next used. An Ollama that is not running holds nothing.
ollama_unload() {
  curl -s --max-time 5 "$OLLAMA_URL" -d "$(jq -nc --arg m "$OLLAMA_MODEL" '{model: $m, keep_alive: 0}')" \
    > /dev/null || true
  curl -s --max-time 5 "$TRANSLATE_URL" -d "$(jq -nc --arg m "$TRANSLATE_LLM" '{model: $m, messages: [], keep_alive: 0}')" \
    > /dev/null || true
}

whisper_resume() {
  [[ -f "$STATE_DIR/whisper.paused" ]] || return 0
  systemctl --user start "$WHISPER_UNIT" && rm -f "$STATE_DIR/whisper.paused"
}

translate_start() {
  gesture_cancel
  translate_active && return 0
  local python="${TALKEY_TRANSLATE_PYTHON:-python3}" source="$AUDIO_SOURCE" reason pause=0
  command -v "$python" > /dev/null || { echo "talkey translate: Python not found: $python" >&2; return 1; }
  [[ "$source" != default ]] || source="$(pactl get-default-source 2> /dev/null || true)"
  if [[ "$source" == "$TRANSLATE_MIC" ]]; then
    # Listening to its own output would translate the translation forever.
    notify 'translate: your input is the talkey English mic - choose your real microphone' 5000
    return 1
  fi
  if ! reason="$(TALKEY_TRANSLATE_ENGINE="$TRANSLATE_ENGINE" "$python" "$SCRIPT_DIR/talkey-translate-worker" --check 2>&1)"; then
    printf '%s\n' "$reason" >&2
    reason="$(tail -n 1 <<< "$reason")"
    notify "translate mode unavailable: ${reason#talkey translate: }" 8000
    return 1
  fi
  case "$TRANSLATE_ENGINE" in
    whisper) preflight_ok || return 1 ;;
    *) [[ "${TALKEY_SEAMLESS_PAUSE_WHISPER:-0}" != 1 ]] || pause=1 ;;
  esac
  # Stopping whisper under a take that is still recorded or transcribed would lose it.
  if (( pause )) && (MODE=dictate; session_load dictate && dictation_active); then
    notify 'translate: dictation is still running - try again when it is done' 5000
    return 1
  fi
  translate_devices || { notify 'translate: could not create the talkey English mic' 5000; return 1; }
  if auto_active; then auto_stop; fi
  if (( pause )); then
    whisper_pause || { notify "translate: could not stop $WHISPER_UNIT to make room for Seamless" 5000; return 1; }
    ollama_unload
  fi
  rm -f "$STATE_DIR/translate.showing" "$STATE_DIR/translate.subtitles" "$STATE_DIR/translate.raw" \
    "$STATE_DIR/translate.scroll" "$STATE_DIR/translate.context"
  TRANSLATE_DIR="$(mktemp -d "$STATE_DIR/translate.XXXXXXXX")"
  local link="$STATE_DIR/translate.current.new-$$"
  ln -s "$TRANSLATE_DIR" "$link"; mv -Tf "$link" "$STATE_DIR/translate.current"
  printf '%s\n' "$TRANSLATE_ENGINE" > "$TRANSLATE_DIR/engine"
  translate_state starting
  TALKEY_STATE_DIR="$STATE_DIR" TALKEY_DATA_DIR="$DATA_DIR" TALKEY_TRANSLATE_RUN="$TRANSLATE_DIR" \
  TALKEY_FFMPEG="$FFMPEG" TALKEY_SOURCE="$AUDIO_SOURCE" TALKEY_WHISPER_URL="$WHISPER_URL" \
  TALKEY_TRANSLATE_SINK="$TRANSLATE_SINK" TALKEY_ARCHIVE_DIR="$ARCHIVE_DIR" \
  TALKEY_TRANSLATE_ENGINE="$TRANSLATE_ENGINE" \
    "$python" "$SCRIPT_DIR/talkey-translate-worker" 9>&- < /dev/null >> "$STATE_DIR/log" 2>&1 &
  process_save "$!" "$TRANSLATE_DIR/worker.pid" || { translate_state error; return 1; }
  camera_follow_translate
  if [[ -f "$STATE_DIR/whisper.paused" ]]; then
    notify 'translate starting - dictation is paused until you stop translating' 5000
  else
    notify 'translate starting - loading the voice' 3000
  fi
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
  if whisper_resume; then
    notify 'translate off'
  else
    notify "translate off - $WHISPER_UNIT did not start again; run: talkey doctor" 5000
  fi
  [[ "$previous" == off ]] || play_sound auto-off
}

translate_command() {
  case "$ACTION" in
    status)
      local enabled=false state=off engine="$TRANSLATE_ENGINE"
      if translate_load; then
        state="$(cat "$TRANSLATE_DIR/state" 2> /dev/null || echo off)"
        if process_alive "$TRANSLATE_DIR/worker.pid"; then enabled=true
        elif [[ "$state" != off ]]; then state=error; fi
        # A run that is not off reports its own engine; otherwise the one that would start.
        [[ "$state" == off || ! -s "$TRANSLATE_DIR/engine" ]] || engine="$(cat "$TRANSLATE_DIR/engine")"
      fi
      case "$state" in off|starting|listening|hearing|error) ;; *) state=error ;; esac
      printf '{"enabled":%s,"state":"%s","engine":"%s","microphone":"%s"}\n' "$enabled" "$state" "$engine" "$TRANSLATE_MIC" ;;
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
      local reason; reason="$(head -n 1 "$TRANSLATE_DIR/error" 2> /dev/null)"
      notify "translate stopped: ${reason:-see the talkey runtime log}" 8000
      # Whisper starts again once the worker, and Seamless with it, has let go of the GPU.
      if [[ -f "$STATE_DIR/whisper.paused" ]]; then
        (
          for ((i = 0; i < 100; i++)); do process_alive "$TRANSLATE_DIR/worker.pid" || break; sleep 0.1; done
          whisper_resume
        ) 9>&- > /dev/null 2>&1 &
      fi ;;
  esac
}
