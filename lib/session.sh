# Per-take state. Callers hold command.lock whenever they change lifecycle state.
# A single atomic symlink selects the take displayed by the HUD. Background
# transcription retains its resolved directory and never follows that pointer.
lock_commands() { flock -x 9; }
unlock_commands() { flock -u 9; }
# Both the HUD adapter and its feed listen for MODIFY. touch on an existing
# marker only emits ATTRIB, so every transition must actually write the file.
state_changed() { printf 'changed\n' > "$STATE_DIR/session.changed"; }

session_paths() {
  TAKE_DIR="$1"
  RAW="$TAKE_DIR/$MODE.raw"; WAV="$TAKE_DIR/$MODE.wav"
  PIDFILE="$TAKE_DIR/$MODE.pid"
  TRANSCRIPT="$TAKE_DIR/dictate.transcript"
  FINAL_TEXT="$TAKE_DIR/final.txt"
  HEADER_FILE="$TAKE_DIR/dictate.header"; FOOTER_FILE="$TAKE_DIR/dictate.footer"
  METRICS_FILE="$TAKE_DIR/dictate.metrics"; STT_METRICS="$TAKE_DIR/dictate.sttmetrics"
  PUMP_PID="$TAKE_DIR/dictate.pump.pid"; DICTATE_PID="$TAKE_DIR/dictate.pid"
  PRESSTIME="$TAKE_DIR/dictate.presstime"; MEMO_PRESSTIME="$TAKE_DIR/memo.presstime"
  PENDING="$TAKE_DIR/dictate.pending"
  SUBMIT_REQ="$TAKE_DIR/submit.requested"; CANCEL_REQ="$TAKE_DIR/cancel.requested"
  TRANSLATE_REQ="$TAKE_DIR/translate.requested"; TARGET_WIN="$TAKE_DIR/dictate.target_win"
  DIAG="$TAKE_DIR/live.diag"
}

session_load() {
  local dir
  dir="$(readlink -f "$STATE_DIR/$1.current" 2>/dev/null)"
  [[ -n "$dir" && -d "$dir" ]] || return 1
  session_paths "$dir"
  [[ ! -s "$TAKE_DIR/language" ]] || LANG_CODE="$(cat "$TAKE_DIR/language")"
  if [[ -s "$TAKE_DIR/audio.format" ]]; then
    read -r SAMPLE_RATE CHANNELS < "$TAKE_DIR/audio.format"
    BYTES_PER_SECOND=$(( SAMPLE_RATE * CHANNELS * 2 ))
  fi
  return 0
}

session_current() {
  if [[ "$TAKE_DIR" == "$STATE_DIR" && ! -L "$STATE_DIR/$MODE.current" ]]; then return 0; fi
  [[ "$(readlink -f "$STATE_DIR/$MODE.current" 2>/dev/null)" == "$TAKE_DIR" ]]
}

session_new() {
  mkdir -p "$STATE_DIR/takes"
  session_paths "$(mktemp -d "$STATE_DIR/takes/$MODE.XXXXXXXX")"
  printf '%s\n' "$LANG_CODE" > "$TAKE_DIR/language"
  printf '%s %s\n' "$SAMPLE_RATE" "$CHANNELS" > "$TAKE_DIR/audio.format"
  local link="$STATE_DIR/$MODE.current.new-$$" name
  ln -s "$TAKE_DIR" "$link"
  mv -Tf "$link" "$STATE_DIR/$MODE.current"
  if [[ "$MODE" == dictate ]]; then
    # Compatibility paths for the renderer and diagnostic tools. They all pass
    # through the same pointer, so publishing a new take is one atomic operation.
    for name in dictate.raw dictate.pid dictate.pid.start dictate.pending dictate.transcript dictate.header \
                dictate.footer dictate.metrics dictate.presstime dictate.target_win live.diag; do
      link="$STATE_DIR/$name.new-$$"
      ln -s "dictate.current/$name" "$link"
      mv -Tf "$link" "$STATE_DIR/$name"
    done
  fi
  state_changed
}

# PID alone is not an identity: Linux can reuse it after a crash or recording cap.
# Keep the process start time too, and treat zombies as stopped.
process_identity() {
  local stat rest
  [[ "$1" =~ ^[0-9]+$ ]] || return 1
  IFS= read -r stat 2>/dev/null < "/proc/$1/stat" || return 1
  rest="${stat##*) }"
  local -a fields
  read -r -a fields <<< "$rest"
  [[ "${fields[0]:-Z}" != Z && ${#fields[@]} -ge 20 ]] || return 1
  printf '%s' "${fields[19]}"
}

process_save() {
  local pid="$1" file="$2" identity
  identity="$(process_identity "$pid")" || return 1
  printf '%s\n' "$pid" > "$file"
  printf '%s\n' "$identity" > "$file.start"
}

process_alive() {
  local pid identity expected
  [[ -s "$1" && -s "$1.start" ]] || return 1
  pid="$(cat "$1")"; expected="$(cat "$1.start")"
  identity="$(process_identity "$pid")" || return 1
  [[ "$identity" == "$expected" ]]
}

process_stop() {
  local file="$1" signal="${2:-TERM}" pid i
  if process_alive "$file"; then
    pid="$(cat "$file")"
    kill -s "$signal" "$pid" 2>/dev/null || true
    for ((i=0; i<100; i++)); do
      process_alive "$file" || break
      sleep 0.01
    done
    if process_alive "$file"; then kill -KILL "$pid" 2>/dev/null || true; fi
    for ((i=0; i<100; i++)); do
      process_alive "$file" || break
      sleep 0.01
    done
    process_alive "$file" && return 1
  fi
  rm -f "$file" "$file.start"
  return 0
}

# Preserve unfinished data on failure. Only successful archive/output paths
# remove PCM, so disk/full conversion errors do not destroy the last copy.
session_cleanup() {
  rm -f "$PENDING" "$PENDING.start" "$WAV"
  state_changed
}

recording_active() {
  [[ -f "$PIDFILE" ]] || return 1
  process_alive "$PIDFILE" && return 0
  local pt=0
  [[ ! -s "$PRESSTIME" ]] || pt="$(cat "$PRESSTIME")"
  [[ "$pt" =~ ^[0-9]+$ ]] && (( EVENT_MS - pt < (MAX_REC_S + 60) * 1000 ))
}

dictation_active() {
  recording_active && return 0
  process_alive "$PENDING" && return 0
  rm -f "$PENDING" "$PENDING.start"
  return 1
}

# Upgrade compatibility: old releases wrote PID-only files in the root. Adopt
# only processes demonstrably attached to these exact audio paths, never a PID
# based on its number or executable name alone.
adopt_legacy_processes() {
  local mode pid raw arg matched
  local -a args
  for mode in dictate memo; do
    raw="$STATE_DIR/$mode.raw"
    if [[ -f "$STATE_DIR/$mode.pid" && ! -L "$STATE_DIR/$mode.pid" && ! -e "$STATE_DIR/$mode.pid.start" ]]; then
      pid="$(cat "$STATE_DIR/$mode.pid")"
      if [[ "$pid" =~ ^[0-9]+$ && "$(readlink "/proc/$pid/fd/1" 2>/dev/null)" == "$raw" ]]; then
        process_save "$pid" "$STATE_DIR/$mode.pid" || true
      fi
    fi
  done
  if [[ -f "$STATE_DIR/dictate.pump.pid" && ! -L "$STATE_DIR/dictate.pump.pid" && ! -e "$STATE_DIR/dictate.pump.pid.start" ]]; then
    pid="$(cat "$STATE_DIR/dictate.pump.pid")"
    if [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]]; then
      args=(); matched=0
      while IFS= read -r -d '' arg; do args+=("$arg"); done < "/proc/$pid/cmdline"
      for arg in "${args[@]}"; do
        [[ "$arg" != */talkey-live-pump ]] || matched=$((matched+1))
        [[ "$arg" != "$STATE_DIR/dictate.raw" ]] || matched=$((matched+1))
      done
      if (( matched == 2 )); then process_save "$pid" "$STATE_DIR/dictate.pump.pid" || true; fi
    fi
  fi
}

# A pre-refactor take must leave the shared root before its slow final request,
# just like a new-format take. Call only after stopping its recorder and pump.
migrate_finished_legacy() {
  [[ "$TAKE_DIR" == "$STATE_DIR" ]] || return 0
  local saved path
  local -a paths=("$RAW" "$WAV")
  if [[ "$MODE" == dictate ]]; then
    paths+=("$PRESSTIME" "$PENDING" "$PENDING.start" "$TRANSCRIPT" "$FINAL_TEXT"
            "$HEADER_FILE" "$FOOTER_FILE" "$METRICS_FILE" "$STT_METRICS"
            "$MEMO_PRESSTIME" "$SUBMIT_REQ" "$CANCEL_REQ" "$TRANSLATE_REQ" "$TARGET_WIN" "$DIAG")
  fi
  mkdir -p "$STATE_DIR/takes"
  saved="$(mktemp -d "$STATE_DIR/takes/legacy.XXXXXXXX")"
  for path in "${paths[@]}"; do
    if [[ -f "$path" && ! -L "$path" ]]; then mv "$path" "$saved/"; fi
  done
  session_new
  for path in "$saved"/*; do [[ ! -f "$path" ]] || mv "$path" "$TAKE_DIR/"; done
  rmdir "$saved"
}
