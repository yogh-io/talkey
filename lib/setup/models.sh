# ===========================================================================
# model
# ===========================================================================
# Sizes are the real download, not the parameter count. The tradeoff that
# matters: turbo is much better on accented or disfluent speech but wants a GPU,
# and it dropped whisper's translate task, which only the small multilingual
# model still has.
MODELS=(
  "large-v3-turbo|1.6 GB|best accuracy, multilingual. Slow on CPU - wants a GPU"
  "small|466 MB|decent, multilingual, and the only one here that can translate"
  "small.en|466 MB|English only, comfortable on CPU"
  "base.en|142 MB|English only, fastest, noticeably rougher"
)

installed_models() {
  local f
  for f in "$MODEL_DIR"/ggml-*.bin /usr/share/whisper.cpp-model-*/ggml-*.bin; do
    [[ -f "$f" ]] || continue
    printf '%s\n' "$f"
  done
}

# Whether THIS model is where the server will look. installed_models answers
# "is there any model at all", which is the wrong question before starting a
# server that loads one specific file - same probe order as
# bin/talkey-whisper-server, so the two cannot disagree.
model_path() { talkey_model_path "$1" "${TALKEY_MODEL:-}"; }

# whisper-server takes one model file, as a path, fixed when it starts - there is
# no registry to pull from and no way to name a model per request. So the file
# has to exist before talkey starts anything, and getting it is an install: your
# package manager, or a download you chose to run. Not talkey's to do for you.
# This prints the ways in and gets out of the way.
model_howto() {  # model_howto <name>
  local name="$1"
  printf '\n   %sHow to get it - pick whichever suits you:%s\n\n' "$B" "$R"
  if have pacman; then
    printf '     from the AUR:\n'
    printf '       whisper.cpp-model-%s\n\n' "$name"
  fi
  printf '     the official whisper.cpp downloader, from a whisper.cpp checkout:\n'
  printf '       bash models/download-ggml-model.sh %s %s\n\n' "$name" "$MODEL_DIR"
  printf '     or straight from the model repo:\n'
  printf '       curl -fL --create-dirs -o %s/ggml-%s.bin \\\n' "$MODEL_DIR" "$name"
  printf '         https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-%s.bin\n\n' "$name"
  printf '     whisper.cpp: https://github.com/ggml-org/whisper.cpp\n'
  printf '     models:      https://huggingface.co/ggerganov/whisper.cpp\n\n'
}

step_model() {
  step "speech model"
  local have_list
  have_list="$(installed_models)"
  if [[ -n "$have_list" ]]; then
    ok "already there:"
    while IFS= read -r f; do printf '     %s\n' "$f"; done <<<"$have_list"
    ask_yn "pick a different one?" n || return 0
  else
    info "talkey needs one. Nothing transcribes without it."
  fi

  local labels=() m name size desc
  for m in "${MODELS[@]}"; do
    IFS='|' read -r name size desc <<<"$m"
    labels+=("$name  ($size) - $desc")
  done
  labels+=("decide later")

  local pick
  pick="$(choose "which model do you want to use?" "${labels[@]}")" || return 1
  (( pick > ${#MODELS[@]} )) && {
    info "nothing written - set TALKEY_MODEL_NAME when you have one"
    todo "install a speech model, then: talkey-setup model"
    return 0
  }
  IFS='|' read -r name size desc <<<"${MODELS[$((pick-1))]}"

  # Always written, and exported: step_service runs in this same process and
  # checks for this model, and writing it only when it differs from the default
  # meant re-picking the default silently left the previous choice in place.
  config_set TALKEY_MODEL_NAME "$name"
  export TALKEY_MODEL_NAME="$name"

  local p
  if p="$(model_path "$name")"; then
    ok "already there: $p"
    return 0
  fi

  model_howto "$name"
  info "talkey looks in $MODEL_DIR and /usr/share/whisper.cpp-model-*"
  info "somewhere else? set TALKEY_MODEL to the full path of the .bin"

  # Waiting here rather than marching on to start a server with no model to
  # load. That server exits at once, systemd restarts it until it hits the
  # start limit, and the next step spends 60 seconds printing dots at a unit
  # that stopped trying seconds in.
  printf '\n   Run one of those now, in another terminal - this waits.\n'
  while true; do
    read -r -p "   Enter to check again, or 's' to skip: " a || return 1
    if [[ "${a,,}" == s ]]; then
      todo "install the $name model ($size) - the ways in are above, or re-run: talkey-setup model"
      info "skipped - the speech service needs it, so that is left too"
      return 1
    fi
    if p="$(model_path "$name")"; then ok "found $p"; return 0; fi
    warn "not there yet: $MODEL_DIR/ggml-$name.bin"
  done
}
