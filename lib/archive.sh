# Sourced by bin/talkey. See docs/architecture.md for ownership rules.

ollama_title() {
  [[ "$ARCHIVE_TITLE" == "1" ]] || return 0
  command -v jq >/dev/null 2>&1 || return 0
  local text; text="$(printf '%s' "$1" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  [[ ${#text} -ge 4 ]] || return 0
  local prompt resp raw
  prompt="Give 2 or 3 keywords that summarize the text (main nouns and verbs, skip filler words like the, a, to, is). Reply with ONLY the words, lowercase, space-separated, no punctuation. Text: $text"
  resp="$(curl -fsS --max-time "$OLLAMA_TIMEOUT" "$OLLAMA_URL" \
      -d "$(jq -nc --arg m "$OLLAMA_MODEL" --arg p "$prompt" \
              '{model:$m,prompt:$p,stream:false,options:{temperature:0.1}}')" 2>/dev/null)" || return 0
  raw="$(printf '%s' "$resp" | jq -r '.response // empty' 2>/dev/null)"
  # No Ollama at all is fine and stays quiet - the feature is optional and curl
  # already returned above. But an Ollama that answers without a usable response
  # is a misconfiguration, nearly always TALKEY_OLLAMA_MODEL not being pulled,
  # and silently timestamping every recording forever is how that goes unnoticed.
  if [[ -z "$raw" ]]; then
    log "no title: ollama reachable but gave nothing for model=$OLLAMA_MODEL (${resp:0:100})"
    return 0
  fi
  # Drop any <think> block, lowercase, strip apostrophes, keep first 3 words.
  printf '%s' "$raw" \
    | sed '/<think>/,/<\/think>/d' \
    | tr '[:upper:]' '[:lower:]' | sed "s/'//g" | tr -c 'a-z0-9' ' ' \
    | awk '{n=NF>3?3:NF; out=""; for(i=1;i<=n;i++){ if(i>1) out=out"-"; out=out $i } print out}'
}

# Reserve a unique private filename before converting. Publish only complete WAVs.
# Nonzero means the source must be retained; archive-disabled is intentional success.
save_audio() {
  local src="$1" dir="$2" mode="$3" text="${4:-}" tmp out
  ARCHIVED_FILE=''
  [[ "$ARCHIVE" == 1 && -s "$src" ]] || return 0
  mkdir -p -m 700 "$dir" || return 1
  tmp="$(mktemp "$dir/.${mode}-$(date +%Y%m%d-%H%M%S)-XXXXXX.wav")" || return 1
  if ! wrap_wav "$src" "$tmp"; then rm -f "$tmp"; return 1; fi
  out="${tmp##*/}"; out="$dir/${out#.}"
  if ! mv "$tmp" "$out"; then rm -f "$tmp"; return 1; fi
  if [[ -n "$text" ]]; then printf '%s\n' "$text" > "${out%.wav}.txt" || return 1; fi
  ARCHIVED_FILE="$out"
  log "archived ${out##*/}"
}

archive_audio() {
  local text="${1:-}"
  save_audio "$RAW" "$ARCHIVE_DIR" "$MODE" "$text" || return 1
  [[ -n "$ARCHIVED_FILE" && -n "$text" && "$ARCHIVE_TITLE" == 1 ]] || return 0
  (
    trap - EXIT
    out="$ARCHIVED_FILE"
    title="$(ollama_title "$text")"
    if [[ -n "$title" && -f "$out" ]]; then
      mv "$out" "${out%.wav}-$title.wav" || exit 1
      if [[ -f "${out%.wav}.txt" ]]; then mv "${out%.wav}.txt" "${out%.wav}-$title.txt"; fi
    fi
  ) 9>&- >>"$STATE_DIR/log" 2>&1 &
  return 0
}

keep_cancelled() { save_audio "$1" "$CANCELLED_DIR" "$2" "${3:-}"; }
