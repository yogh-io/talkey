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
for c in ffmpeg paplay xdotool xset curl python3; do
  command -v "$c" >/dev/null 2>&1 || { echo "MISSING: $c"; exit 1; }
done
for c in eww inotifywait; do
  command -v "$c" >/dev/null 2>&1 || echo "note: '$c' missing - live HUD disabled (set TALKEY_STREAM=0, or install it)"
done

# whisper: a distro package, or built into ~/.local by setup/build-whisper.sh.
have_server=0
for c in "$HOME/.local/opt/whisper.cpp/build/bin/whisper-server" /usr/bin/whisper-server; do
  [[ -x "$c" ]] && { have_server=1; break; }
done
have_model=0
for c in "$HOME/.local/share/whisper/ggml-$MODEL_NAME.bin" \
         "/usr/share/whisper.cpp-model-$MODEL_NAME/ggml-$MODEL_NAME.bin"; do
  [[ -f "$c" ]] && { have_model=1; break; }
done
if (( ! have_server || ! have_model )); then
  echo ">> building whisper.cpp + fetching the model (this compiles once)"
  bash "$REPO_DIR/setup/build-whisper.sh"
fi

echo ">> installing systemd --user service"
mkdir -p "$UNIT_DIR"
# The checkout's bin dir goes into the unit. Note this copy in ~/.config
# permanently overrides a packaged unit in /usr/lib - see the uninstall note at
# the end if you later install the package.
for u in talkey-whisper talkey-hud talkey-norepeat; do
  sed "s|@BINDIR@|${REPO_DIR//|/\\|}/bin|g" \
    "$REPO_DIR/systemd/$u.service.in" > "$UNIT_DIR/$u.service"
done
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
  2) copy the bindings for whatever you use and set your keys in the copy:
       i3        cp $REPO_DIR/bindings/i3.conf ~/.config/i3/talkey.conf
       sxhkd     cp $REPO_DIR/bindings/sxhkdrc ~/.config/sxhkd/sxhkdrc
       xbindkeys cp $REPO_DIR/bindings/xbindkeysrc ~/.xbindkeysrc
  3) load them (i3: add `include ~/.config/i3/talkey.conf` and reload with
     Mod+Shift+R; sxhkd: pkill -USR1 -x sxhkd; xbindkeys: killall -HUP xbindkeys)
  4) hold your dictate key, speak, release.

Put $REPO_DIR/bin on your PATH so the bare 'talkey' commands resolve.

Not using i3? Start the helpers as services instead of from your WM:
  systemctl --user enable --now talkey-hud talkey-norepeat
and set TALKEY_NOREPEAT_KEYCODES in ~/.config/talkey/talkey.env.

To undo this checkout install later (it shadows a packaged one):
  systemctl --user disable --now talkey-whisper talkey-hud talkey-norepeat
  rm ~/.config/systemd/user/talkey-{whisper,hud,norepeat}.service
EOF
