# Recorder defaults. User-facing explanations live in config/talkey.env.example.
WHISPER_URL="$(talkey_whisper_url)"
WHISPER_CLI="$(talkey_binary whisper-cli "${TALKEY_WHISPER_CLI:-}" || true)"
TRANSLATE_MODEL="$(talkey_model_path small "${TALKEY_TRANSLATE_MODEL:-}" || true)"
AUDIO_SOURCE="${TALKEY_SOURCE:-default}"
TYPE_DELAY="${TALKEY_TYPE_DELAY:-8}"
TARGET_WINDOW="${TALKEY_TARGET_WINDOW:-1}"
PASTE="${TALKEY_PASTE:-0}"
SUBMIT="${TALKEY_SUBMIT:-0}"
LANG_CODE="${LANG_OVERRIDE:-${TALKEY_LANG:-en}}"
VOCAB_PROMPT="${TALKEY_VOCAB_PROMPT:-1}"
VOCAB_MAX_S="${TALKEY_VOCAB_MAX_S:-28}"
CONF_WARN="${TALKEY_CONF_WARN:-70}"
SIL_WARN="${TALKEY_SIL_WARN:-55}"
QUIET_FLOOR="${TALKEY_QUIET_FLOOR:-300}"
PEAK_LOUD_DB="${TALKEY_PEAK_LOUD_DB:--10}"
PEAK_LOW_DB="${TALKEY_PEAK_LOW_DB:--20}"
SOUNDS="${TALKEY_SOUNDS:-1}"
MEMO_GAIN="${TALKEY_MEMO_GAIN:-2.0}"
STREAM="${TALKEY_STREAM:-1}"
STREAM_INTERVAL="${TALKEY_STREAM_INTERVAL:-0.3}"
STREAM_PROMPT="${TALKEY_STREAM_PROMPT:-}"
HOLD_MS="${TALKEY_HOLD_MS:-450}"
DOUBLE_TAP_MS="${TALKEY_DOUBLE_TAP_MS:-400}"
MAX_REC_S="${TALKEY_MAX_REC_S:-900}"
PROMPT_FILE="${TALKEY_PROMPT_FILE:-}"
if [[ -z "$PROMPT_FILE" ]]; then
  for c in "${XDG_CONFIG_HOME:-$HOME/.config}/talkey/vocabulary.txt" "$DATA_DIR/vocabulary.txt"; do
    if [[ -f "$c" ]]; then PROMPT_FILE="$c"; break; fi
  done
fi
ARCHIVE="${TALKEY_ARCHIVE:-1}"
ARCHIVE_DIR="${TALKEY_ARCHIVE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/talkey/recordings}"
ARCHIVE_TITLE="${TALKEY_ARCHIVE_TITLE:-1}"
CANCELLED_DIR="${TALKEY_CANCELLED_DIR:-$ARCHIVE_DIR/cancelled}"
OLLAMA_URL="${TALKEY_OLLAMA_URL:-http://localhost:11434/api/generate}"
OLLAMA_MODEL="${TALKEY_OLLAMA_MODEL:-qwen2.5:3b}"
OLLAMA_TIMEOUT="${TALKEY_OLLAMA_TIMEOUT:-12}"
FFMPEG="${TALKEY_FFMPEG:-ffmpeg}"
XDOTOOL="${TALKEY_XDOTOOL:-xdotool}"
XCLIP="${TALKEY_XCLIP:-xclip}"
PLAYER="${TALKEY_PLAYER:-paplay}"
SAMPLE_RATE="${TALKEY_SAMPLE_RATE:-16000}"
CHANNELS="${TALKEY_CHANNELS:-1}"


# Reject malformed arithmetic settings before launching a recorder.
for setting in SAMPLE_RATE CHANNELS HOLD_MS DOUBLE_TAP_MS MAX_REC_S VOCAB_MAX_S TYPE_DELAY; do
  value="${!setting}"
  if [[ ! "$value" =~ ^[0-9]+$ || ${#value} -gt 8 ]]; then
    echo "talkey: invalid $setting: $value" >&2; exit 2
  fi
  printf -v "$setting" '%d' "$((10#$value))"
done
if (( SAMPLE_RATE < 1 || CHANNELS < 1 || MAX_REC_S < 1 )); then
  echo 'talkey: sample rate, channels and recording cap must be positive' >&2; exit 2
fi
BYTES_PER_SECOND=$(( SAMPLE_RATE * CHANNELS * 2 ))
unset setting value

CONFIGURED_SAMPLE_RATE="$SAMPLE_RATE"
CONFIGURED_CHANNELS="$CHANNELS"
