# talkey/lib/common.sh - sourced by every talkey command.
#
# Three jobs, in this order:
#   1. find the read-only data root (sounds, vocabulary, HUD config)
#   2. load the user's config file, without letting it override real env vars
#   3. set STATE_DIR
#
# Sourced, never executed. The caller must have set SCRIPT_DIR to the directory
# holding the running script.

# --- 1. data root ----------------------------------------------------------
# Read-only data sits one level above bin/ in a git checkout and under
# /usr/share/talkey when installed from a package. The layout INSIDE it is
# identical either way, so one probe settles which root we are running from; if
# neither holds it we say so and stop rather than run on half a setup.
if [[ -z "${TALKEY_DATA_DIR:-}" ]]; then
  for _d in "$SCRIPT_DIR/.." "$SCRIPT_DIR/../.." /usr/share/talkey; do
    if [[ -f "$_d/vocabulary.txt" ]]; then TALKEY_DATA_DIR="$(cd "$_d" && pwd)"; break; fi
  done
fi
if [[ -z "${TALKEY_DATA_DIR:-}" ]]; then
  echo "talkey: no data dir - looked next to $SCRIPT_DIR and in /usr/share/talkey. Set TALKEY_DATA_DIR." >&2
  return 1 2>/dev/null || exit 1
fi
DATA_DIR="$TALKEY_DATA_DIR"

# --- 2. config file --------------------------------------------------------
# Same format as a systemd EnvironmentFile: TALKEY_KEY=value, one per line, #
# comments, optional surrounding quotes. Parsed rather than sourced - a config
# file should set values, not run code.
#
# Anything already in the environment WINS, so a per-binding override still
# works (i3: `env TALKEY_LANG=nl talkey dictate press`) even when the file names
# the same variable.
TALKEY_CONFIG="${TALKEY_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/talkey/talkey.env}"
if [[ -f "$TALKEY_CONFIG" ]]; then
  while IFS= read -r _line; do
    [[ "$_line" == *=* ]] || continue
    _key="${_line%%=*}"; _val="${_line#*=}"
    _key="${_key//[[:space:]]/}"
    [[ "$_key" =~ ^TALKEY_[A-Z0-9_]+$ ]] || continue
    [[ -n "${!_key+isset}" ]] && continue      # the environment wins
    _val="${_val#"${_val%%[![:space:]]*}"}"    # trim leading space
    # Quoted value: take it verbatim up to the closing quote, so a '#' inside it
    # survives. Unquoted: everything up to the first '#' is the value. Stripping
    # comments before this would silently truncate a label like "# 1 key".
    case "$_val" in
      \"*) _val="${_val#\"}"; _val="${_val%%\"*}" ;;
      \'*) _val="${_val#\'}"; _val="${_val%%\'*}" ;;
      *)   _val="${_val%%#*}"
           _val="${_val%"${_val##*[![:space:]]}"}" ;;   # trim trailing space
    esac
    export "$_key=$_val"
  done < "$TALKEY_CONFIG"
  unset _line _key _val
fi
unset _d

# --- 3. runtime state ------------------------------------------------------
# One directory holds every state file the recorder writes and the HUD reads.
# This directory holds raw microphone audio, live transcripts, the log, and the
# id of the window text gets typed into. XDG_RUNTIME_DIR is already private and
# per-user; /tmp is neither, so a fallback there is created 0700 with the uid in
# the name, and refused outright if someone else owns it.
if [[ -n "${TALKEY_STATE_DIR:-}" ]]; then
  STATE_DIR="$TALKEY_STATE_DIR"
elif [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
  STATE_DIR="$XDG_RUNTIME_DIR/talkey"
else
  STATE_DIR="/tmp/talkey-$(id -u)"
fi
mkdir -p -m 700 "$STATE_DIR" 2>/dev/null || true
if [[ -d "$STATE_DIR" && ! -O "$STATE_DIR" ]]; then
  echo "talkey: $STATE_DIR is not owned by you - refusing to use it" >&2
  return 1 2>/dev/null || exit 1
fi
