# Sourced by bin/talkey. See docs/architecture.md for ownership rules.

raw_audio_metrics() {
  [[ -s "$RAW" ]] || { printf '0 -99'; return; }
  od -An -td2 -v "$RAW" 2>/dev/null | awk -v floor="$QUIET_FLOOR" -v frame="$(( SAMPLE_RATE * CHANNELS * 32 / 1000 ))" '
    { for(i=1;i<=NF;i++){ v=$i; s+=v*v; n++;
        a=(v<0)?-v:v; if(a>pk)pk=a;
        if(n>=frame){ if(sqrt(s/n)<floor) quiet++; frames++; s=0; n=0 } } }
    END{ sil=(frames>0)?int(100*quiet/frames+0.5):0;
         db=(pk<1)?-99:20*log(pk/32768)/log(10);
         printf "%d %d", sil, int(db<0?db-0.5:db+0.5) }'
}

stt() {
  local wav="$1" text raw alp="" sil peak secs use_prompt=0
  : > "$STT_METRICS"
  # Silence and peak level are acoustic (from the raw PCM), independent of whisper
  # and of jq. One pass yields both; computed for every path.
  read -r sil peak < <(raw_audio_metrics) || true

  if [[ -f "$TRANSLATE_REQ" ]]; then
    # TRANSLATE (Square toggled it): the warm turbo server can't translate, so run
    # whisper-cli with the small model. -l is the SOURCE language (e.g. nl) so it
    # knows what it's hearing; -tr produces English. The CLI text path has no
    # avg_logprob, so confidence shows blank (silence/peak still apply).
    if [[ ! -x "$WHISPER_CLI" || ! -f "$TRANSLATE_MODEL" ]]; then
      log "translate unavailable: whisper-cli=${WHISPER_CLI:-none} model=${TRANSLATE_MODEL:-none}"
      return 1
    fi
    # No -nt here either, for the same reason the server does not get it: it stops
    # whisper seeking by sentence and starts it cutting blind every 30 seconds. It
    # only ever bought us tidier output, and the [00:00:00.000 --> …] prefixes it
    # would have hidden are stripped below anyway, by the same sed that strips
    # [BLANK_AUDIO].
    text="$("$WHISPER_CLI" -m "$TRANSLATE_MODEL" -l "$LANG_CODE" -tr -f "$wav" 2>>"$STATE_DIR/log")" || return 1
    printf '%s %s %s' none "$sil" "$peak" > "$STT_METRICS"
  else
    # TRANSCRIBE: the warm large-v3-turbo server over HTTP.
    secs=$(( $(stat -c%s "$RAW" 2>/dev/null || echo 0) / BYTES_PER_SECOND ))
    # Wait in proportion to what was said. A fixed ceiling is a length limit in
    # disguise: a CPU-only build runs near realtime, so a long dictation blew a
    # 60s deadline and surfaced as "transcription failed" while whisper was still
    # working on perfectly good audio. Allow 5x the clip plus 30s of model warmup.
    local -a args=(-fsS --max-time $(( 30 + secs * 5 ))
                   -F "file=@$wav" -F temperature=0 -F "language=$LANG_CODE")
    # The prompt is a STYLE bias, not a forced dictionary: it fixes jargon spelling
    # (SonarQube, Jira, worktree) but can also subtly drop/alter a word, and the
    # more speech it is carried across the more chances it has to. So bias only
    # SHORT English dictations, where turbo applies it cleanly; long-form and
    # non-English stay raw. See VOCAB_MAX_S.
    [[ "$VOCAB_PROMPT" == "1" && -n "$PROMPT" && "$LANG_CODE" == "en" && "$secs" -lt "$VOCAB_MAX_S" ]] && use_prompt=1
    # Rolling context lets whisper loop one sentence over real speech, but the
    # prompt travels through it too, so only takes without a prompt can drop it.
    if (( use_prompt )); then args+=(-F "prompt=$PROMPT"); else args+=(-F max_context=0); fi
    # verbose_json carries avg_logprob but needs jq to parse. Without jq, fall back
    # to plain text - the transcript still works, just with no confidence number.
    if command -v jq >/dev/null 2>&1; then
      raw="$(curl "${args[@]}" -F response_format=verbose_json "$WHISPER_URL" 2>>"$STATE_DIR/log")" || return 1
      text="$(printf '%s' "$raw" | jq -er 'if (.text | type) == "string" then .text else error("missing transcript") end' 2>/dev/null)" || return 1
      # confidence = mean avg_logprob across segments (no segments -> blank).
      alp="$(printf '%s' "$raw" | jq -r '[.segments[]?.avg_logprob] as $a | if ($a|length)>0 then (($a|add)/($a|length)) else empty end' 2>/dev/null)"
    else
      text="$(curl "${args[@]}" -F response_format=text "$WHISPER_URL" 2>>"$STATE_DIR/log")" || return 1
    fi
    printf '%s %s %s' "${alp:-none}" "$sil" "$peak" > "$STT_METRICS"
  fi
  # Flatten newlines to spaces FIRST (tr): both paths put each segment on its own
  # line, and whisper-cli's translate output leads with one - left in, that
  # leading newline types as a phantom Enter before the text, and every sentence
  # break in a long dictation types as an Enter too. Then strip whisper's non-speech
  # annotations - [BLANK_AUDIO]/[Music] bracket style AND the *sad music* /
  # *coughing* asterisk style large-v3 emits on non-speech (that leaked into the
  # typed text) - and trim/collapse whitespace.
  text="$(printf '%s' "$text" | tr '\n' ' ' | sed -E 's/\[[^]]*\]//g; s/\*[^*]*\*//g; s/^[[:space:]]+//; s/[[:space:]]+$//; s/[[:space:]]+/ /g')"
  # If nothing but (parenthesised) sound annotations remain, it wasn't speech.
  local check; check="$(printf '%s' "$text" | sed -E 's/\([^)]*\)//g' | tr -d '[:space:]')"
  [[ -z "$check" ]] && return 0
  printf '%s' "$text"
}
