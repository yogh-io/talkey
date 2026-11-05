# Command recognition is opt-in, after final transcription and before typing.
dispatch_voice_command() {
  COMMAND_HANDLED=0; COMMAND_TEXT="$1"; COMMAND_FEEDBACK=''; COMMAND_ERROR=0
  [[ "${TALKEY_COMMANDS:-0}" == 1 ]] || return 0
  local definitions="${TALKEY_COMMANDS_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/talkey/commands.d}"
  local python="${TALKEY_AUTO_PYTHON:-python3}" spec="$TAKE_DIR/command.json" kind
  if ! printf '%s' "$1" | "$python" "$DATA_DIR/lib/commands.py" resolve "$DATA_DIR" "$definitions" > "$spec"; then
    COMMAND_HANDLED=1; COMMAND_ERROR=1
    COMMAND_FEEDBACK="command configuration error - see $spec"
    notify "$COMMAND_FEEDBACK" 5000
    return 0
  fi
  kind="$(jq -r '.kind' "$spec")"
  if [[ "$kind" == dictation ]]; then
    COMMAND_TEXT="$(jq -r '.text' "$spec")"
    return 0
  fi
  COMMAND_HANDLED=1
  case "$kind" in
    builtin)
      local ACTION; ACTION="$(jq -r '.builtin' "$spec")"
      if [[ "$ACTION" == auto-stop ]]; then
        if auto_stop; then COMMAND_FEEDBACK='Listening off'
        else COMMAND_ERROR=1; COMMAND_FEEDBACK='could not stop listening'; fi
      elif auto_enter_control; then
        if auto_enter_enabled; then COMMAND_FEEDBACK='Enter on'; else COMMAND_FEEDBACK='Enter off'; fi
      else COMMAND_ERROR=1; COMMAND_FEEDBACK='could not change Enter setting'; fi ;;
    exec)
      local name; name="$(jq -r '.id' "$spec")"
      # Never inherit command.lock or this take's cleanup trap into an extension.
      # Scripts can call Talkey's CLI themselves; capture continues meanwhile.
      (
        trap - EXIT
        exec 9>&-
        if "$python" "$DATA_DIR/lib/commands.py" run "$spec" > "$TAKE_DIR/command.result.json" 2>>"$STATE_DIR/log"; then
          notify "command finished: $name"
        else notify "command failed: $name - see $TAKE_DIR/command.result.json" 5000; fi
      ) </dev/null >/dev/null 2>>"$STATE_DIR/log" &
      COMMAND_FEEDBACK="command started: $name"
      notify "$COMMAND_FEEDBACK" ;;
    *) COMMAND_ERROR=1; COMMAND_FEEDBACK='unknown command'; notify "$COMMAND_FEEDBACK" ;;
  esac
}
