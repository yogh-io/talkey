#!/usr/bin/env bash
# Build whisper.cpp (CPU) and fetch the STT model.
#
# Artifacts live OUTSIDE the repo (they are machine-specific binaries):
#   binaries -> $WHISPER_OPT        (default ~/.local/opt/whisper.cpp)
#   model    -> $WHISPER_MODEL_DIR  (default ~/.local/share/whisper)
#
# GPU upgrade later: re-run with WHISPER_CUDA=1 (needs /opt/cuda).
set -euo pipefail

OPT_DIR="${WHISPER_OPT:-$HOME/.local/opt/whisper.cpp}"
MODEL_DIR="${WHISPER_MODEL_DIR:-$HOME/.local/share/whisper}"
# large-v3-turbo is the default in bin/talkey-whisper-server: multilingual,
# strong on accented/disfluent speech, ~1.6GB. Needs the GPU build to be quick
# (WHISPER_CUDA=1); on CPU it is slow - override with WHISPER_MODEL=small.en then.
MODEL="${WHISPER_MODEL:-large-v3-turbo}"

mkdir -p "$(dirname "$OPT_DIR")" "$MODEL_DIR"

if [[ ! -d "$OPT_DIR/.git" ]]; then
  echo ">> cloning whisper.cpp"
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$OPT_DIR"
fi

cd "$OPT_DIR"

cmake_flags=(-DCMAKE_BUILD_TYPE=Release)
if [[ "${WHISPER_CUDA:-0}" == "1" ]]; then
  echo ">> configuring with CUDA"
  export PATH="/opt/cuda/bin:$PATH"
  export CUDACXX="${CUDACXX:-/opt/cuda/bin/nvcc}"
  # Ask the card what it is. A wrong architecture builds kernels that will not
  # run on the installed GPU, and hardcoding one only ever suits its author.
  # nvidia-smi reports e.g. "8.6" -> 86; if it cannot be reached, "native" lets
  # CMake (3.24+) detect it at configure time.
  arch="${WHISPER_CUDA_ARCH:-}"
  if [[ -z "$arch" ]]; then
    arch="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null \
            | head -1 | tr -d '. ' )"
    [[ "$arch" =~ ^[0-9]+$ ]] || arch=native
  fi
  echo ">> CUDA architecture: $arch"
  cmake_flags+=(-DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES="$arch")
fi

echo ">> configuring"
cmake -B build "${cmake_flags[@]}"
echo ">> building whisper-server + whisper-cli"
cmake --build build -j --config Release --target whisper-server whisper-cli

if [[ ! -f "$MODEL_DIR/ggml-$MODEL.bin" ]]; then
  echo ">> downloading model: $MODEL"
  bash "$OPT_DIR/models/download-ggml-model.sh" "$MODEL" "$MODEL_DIR"
fi

# Translate model. The Square mid-dictation translate needs a model that keeps
# whisper's translate task - large-v3-turbo dropped it (it re-transcribes in the
# source language). The small MULTILINGUAL model (not small.en) keeps it and is
# cheap (~466MB). Fetch it unless the main model already is small.
TRANSLATE_MODEL="${WHISPER_TRANSLATE_MODEL:-small}"
if [[ "$MODEL" != "$TRANSLATE_MODEL" && ! -f "$MODEL_DIR/ggml-$TRANSLATE_MODEL.bin" ]]; then
  echo ">> downloading translate model: $TRANSLATE_MODEL"
  bash "$OPT_DIR/models/download-ggml-model.sh" "$TRANSLATE_MODEL" "$MODEL_DIR"
fi

echo ">> done"
echo "binaries:"
find "$OPT_DIR/build" -name 'whisper-server' -o -name 'whisper-cli' | sed 's/^/  /'
echo "model:           $MODEL_DIR/ggml-$MODEL.bin"
echo "translate model: $MODEL_DIR/ggml-$TRANSLATE_MODEL.bin"
