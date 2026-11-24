# ===========================================================================
# translate
# ===========================================================================
# Translate mode speaks in your own voice. Pocket TTS clones it from a short
# recording; the cloning weights are gated, so this step needs a Hugging Face
# login once. Afterwards every session runs offline.
VOICE_TEXT="Hi, thanks for having me. I'll keep this short. Last week we looked at the new
version, and honestly, most of it went better than I expected. There are still a
few things I'd like to change before we ship it, so let's go through them one by
one, and stop me if anything is unclear."

step_translate() {
  step "translate mode"
  local data="${XDG_DATA_HOME:-$HOME/.local/share}/talkey"
  local python="${TALKEY_TRANSLATE_PYTHON:-python3}"
  local voice="${TALKEY_TRANSLATE_VOICE:-$data/translate/voice.safetensors}"
  local sample="$data/translate/voice.wav"
  if ! "$python" -c 'import pocket_tts' 2> /dev/null; then
    printf '\nTranslate mode speaks with Pocket TTS on the CPU. Install it locally:\n'
    printf '  python3 -m venv "%s/translate-venv"\n' "$data"
    printf '  "%s/translate-venv/bin/python" -m pip install -r "%s/config/translate-requirements.txt" \\\n' "$data" "$DATA_DIR"
    printf '    --extra-index-url https://download.pytorch.org/whl/cpu\n'
    printf '\nThen set this in talkey.env and run talkey-setup translate again:\n'
    printf '  TALKEY_TRANSLATE_PYTHON="%s/translate-venv/bin/python"\n' "$data"
    return 1
  fi
  # Without continuation each piece of a translation is spoken as its own sentence.
  if ! "$python" -c 'import inspect, sys; from pocket_tts import TTSModel
sys.exit("forced_latents" not in inspect.signature(TTSModel._generate_audio_stream_short_text).parameters)' 2> /dev/null; then
    printf '\nThis Pocket TTS cannot continue an utterance across pieces. Install the pinned version:\n'
    printf '  "%s" -m pip install --force-reinstall --no-deps "%s"\n' "$python" \
      "$(grep '^pocket-tts' "$DATA_DIR/config/translate-requirements.txt")"
    return 1
  fi
  ok 'Pocket TTS installed'
  local record=0 peak
  if [[ -t 0 ]]; then
    if [[ -f "$voice" ]]; then
      if ask_yn "record a new voice sample?" n; then record=1; fi
    elif [[ ! -f "$sample" ]] || ask_yn "record your voice again?" n; then
      record=1
    fi
  elif [[ ! -f "$voice" && ! -f "$sample" ]]; then
    die "recording your voice is interactive - run it in a terminal"
  fi
  if (( record )); then
    printf '\nRead this aloud in English, in your normal call voice, close to the mic:\n\n%s\n\n' "$VOICE_TEXT"
    read -r -p "   Enter to start recording (25 seconds)... " _ || return 1
    # A recording of your voice is personal: private folder, private file.
    mkdir -p -m 700 "$(dirname "$sample")" || return 1
    (umask 077 && "${TALKEY_FFMPEG:-ffmpeg}" -nostdin -hide_banner -loglevel error -y -f pulse \
      -i "${TALKEY_SOURCE:-default}" -t 25 -ac 1 -ar 24000 "$sample") || return 1
    ok "recorded $sample"
    peak="$("${TALKEY_FFMPEG:-ffmpeg}" -nostdin -hide_banner -i "$sample" -af volumedetect -f null - 2>&1 |
      sed -n 's/.*max_volume: \(-\{0,1\}[0-9]*\).*/\1/p')"
    # The clone copies the recording's level and noise, so a faint take sounds faint.
    if [[ -n "$peak" ]] && (( peak < -20 )); then
      warn "that was quiet (peak ${peak} dB) - move closer or raise the mic level, then record again"
    fi
  fi
  if (( record )) || [[ ! -f "$voice" ]]; then
    info 'cloning your voice (the first run downloads the model weights)'
    if ! "$python" "$SCRIPT_DIR/talkey-translate-worker" --export-voice "$sample" "$voice"; then
      printf '\nThe voice cloning weights are gated. Once, in a browser and a terminal:\n'
      printf '  1) accept the terms at https://huggingface.co/kyutai/pocket-tts\n'
      printf '  2) "%s/hf" auth login\n' "$(dirname "$python")"
      printf 'Then run talkey-setup translate again. Your recording is kept.\n'
      return 1
    fi
    ok "voice saved to $voice"
  fi
  if ! "$python" "$SCRIPT_DIR/talkey-translate-worker" --check; then
    warn 'translate mode check failed - see the error above'
    return 1
  fi
  ok 'translate mode ready: talkey translate toggle'
  info 'in your call app, choose the microphone "talkey English mic"'
}
