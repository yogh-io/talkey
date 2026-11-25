# ===========================================================================
# seamless
# ===========================================================================
# The optional SeamlessStreaming translate engine. Meta licenses its weights
# CC-BY-NC-4.0, so talkey does not ship them: this step downloads them, after
# asking, from a pinned revision, and checks each file against its checksum.
SEAMLESS_WEIGHTS_URL=https://huggingface.co/facebook/seamless-streaming/resolve/82746da73c2927b962e53ad2f723babf1ae0e0b5

step_seamless() {
  step "Seamless translate engine (optional)"
  local home="${TALKEY_SEAMLESS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/talkey/seamless}"
  local venv sum name
  venv="$home/venv"
  printf '\nThe SeamlessStreaming weights are licensed CC-BY-NC-4.0 by Meta: non-commercial use only.\n'
  printf 'talkey does not ship them. This downloads about 7.9 GB and installs a 5.5 GB Python environment\n'
  printf 'into %s.\n\n' "$home"
  if ! ask_yn "download and install it?" n; then
    info 'nothing installed'
    return 0
  fi
  mkdir -p "$home/models" || return 1
  # fairseq2 0.2.1 has wheels for Python 3.8 to 3.11 only; 3.10 is the tested one.
  if [[ ! -x "$venv/bin/python" ]]; then
    if have python3.10; then
      python3.10 -m venv "$venv" || return 1
    elif have uv; then
      uv venv --seed --python 3.10 "$venv" || return 1
    else
      warn 'Seamless needs Python 3.10: install python3.10 or uv, then run talkey-setup seamless again'
      return 1
    fi
  fi
  info 'installing PyTorch, fairseq2 and seamless_communication'
  "$venv/bin/python" -m pip install -r "$DATA_DIR/config/seamless-requirements.txt" || return 1
  ok "environment ready in $venv"
  while read -r sum name; do
    [[ -n "$name" ]] || continue
    if [[ -f "$home/models/$name" ]] && sha256sum -c --quiet <<< "$sum  $home/models/$name" 2> /dev/null; then
      ok "$name already there"
      continue
    fi
    info "downloading $name"
    curl -fL --retry 3 -C - -o "$home/models/$name.part" "$SEAMLESS_WEIGHTS_URL/$name" || return 1
    if ! sha256sum -c --quiet <<< "$sum  $home/models/$name.part"; then
      rm -f "$home/models/$name.part"
      warn "$name did not match its checksum and was deleted - run talkey-setup seamless again"
      return 1
    fi
    mv "$home/models/$name.part" "$home/models/$name" || return 1
    ok "$name"
  done < "$DATA_DIR/config/seamless-weights.sha256"
  if ! TALKEY_TRANSLATE_ENGINE=seamless "${TALKEY_TRANSLATE_PYTHON:-python3}" "$SCRIPT_DIR/talkey-translate-worker" --check; then
    warn 'the Seamless engine check failed - see the error above'
    return 1
  fi
  ok 'Seamless engine ready'
  info 'to use it, set TALKEY_TRANSLATE_ENGINE=seamless in talkey.env'
  info 'it needs about 5 GB of GPU memory; on a card too small for it next to whisper, also set'
  info 'TALKEY_SEAMLESS_PAUSE_WHISPER=1: dictation then pauses while Seamless translates'
}
