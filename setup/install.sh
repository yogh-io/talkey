#!/usr/bin/env bash
# Wire up talkey from a git checkout: check deps, make sure whisper.cpp and a
# model are present, then install + start the STT service as a user unit.
# Idempotent - safe to re-run. Key bindings are set up separately (see README).
#
# Installing the AUR package instead? You don't need this script - it ships the
# unit already. Just enable it:  systemctl --user enable --now talkey-whisper
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
MODEL_NAME="${TALKEY_MODEL_NAME:-large-v3-turbo}"

echo ">> checking runtime deps"
for c in ffmpeg paplay xdotool curl python3; do
  command -v "$c" >/dev/null 2>&1 || { echo "MISSING: $c"; exit 1; }
done
for c in eww inotifywait; do
  command -v "$c" >/dev/null 2>&1 || echo "note: '$c' missing - live HUD disabled (set TALKEY_STREAM=0, or install it)"
done
command -v alacritty >/dev/null 2>&1 || echo "note: 'alacritty' missing - the help overlay (talkey help) won't open"

# whisper: a distro package, or built into ~/.local by setup/build-whisper.sh.
have_server=0
for c in /usr/bin/whisper-server "$HOME/.local/opt/whisper.cpp/build/bin/whisper-server"; do
  [[ -x "$c" ]] && { have_server=1; break; }
done
have_model=0
for c in "/usr/share/whisper.cpp-model-$MODEL_NAME/ggml-$MODEL_NAME.bin" \
         "$HOME/.local/share/whisper/ggml-$MODEL_NAME.bin"; do
  [[ -f "$c" ]] && { have_model=1; break; }
done
if (( ! have_server || ! have_model )); then
  echo ">> building whisper.cpp + fetching the model (this compiles once)"
  bash "$REPO_DIR/setup/build-whisper.sh"
fi

echo ">> installing systemd --user service"
mkdir -p "$UNIT_DIR"
sed "s|@BINDIR@|$REPO_DIR/bin|g" \
  "$REPO_DIR/systemd/talkey-whisper.service.in" > "$UNIT_DIR/talkey-whisper.service"
systemctl --user daemon-reload
systemctl --user enable --now talkey-whisper.service

sleep 1
if curl -sS --max-time 5 -o /dev/null http://127.0.0.1:8910/ 2>/dev/null; then
  echo ">> whisper server responding on :8910"
else
  echo ">> whisper server not up yet - check: systemctl --user status talkey-whisper"
fi

cat <<EOF

Next: bind your keys.
  1) run:  xev | grep --line-buffered -oP 'keycode\s+\d+.*'
     press each key (xev window focused), note the number after "keycode".
  2) copy the bindings and set your keycodes at the top of the copy:
       cp $REPO_DIR/i3/talkey.conf ~/.config/i3/talkey.conf
  3) add to ~/.config/i3/config:
       include ~/.config/i3/talkey.conf
  4) reload i3 (Mod+Shift+R). Hold your dictate key, speak, release.

Note: running from a checkout, put $REPO_DIR/bin on your PATH so the i3
bindings resolve, or replace the bare 'talkey' commands in the copy with
absolute paths.
EOF
