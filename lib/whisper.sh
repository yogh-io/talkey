# Shared discovery for the client, server, setup and doctor.
talkey_whisper_url() {
  printf '%s' "${TALKEY_WHISPER_URL:-http://${TALKEY_WHISPER_HOST:-127.0.0.1}:${TALKEY_WHISPER_PORT:-8910}/inference}"
}
talkey_binary() {
  local name="$1" override="${2:-}" candidate
  if [[ -n "$override" ]]; then printf '%s\n' "$override"; return 0; fi
  for candidate in "${WHISPER_OPT:-$HOME/.local/opt/whisper.cpp}/build/bin/$name" "/usr/bin/$name"; do
    if [[ -x "$candidate" ]]; then printf '%s\n' "$candidate"; return 0; fi
  done
  command -v "$name"
}
talkey_model_path() {
  local name="$1" override="${2:-}" candidate
  if [[ -n "$override" ]]; then
    [[ -f "$override" ]] || return 1
    printf '%s\n' "$override"; return 0
  fi
  for candidate in "${WHISPER_MODEL_DIR:-$HOME/.local/share/whisper}/ggml-$name.bin" \
                   "/usr/share/whisper.cpp-model-$name/ggml-$name.bin"; do
    if [[ -f "$candidate" ]]; then printf '%s\n' "$candidate"; return 0; fi
  done
  return 1
}
