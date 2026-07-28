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

# Loading a 1.6GB model off cold cache takes far longer than a second, so a
# single immediate check would say "not up" almost every time and send people
# looking for a fault that isn't there. Wait for it, and only complain if it
# really never answers. Host and port come from your config, not from here.
HOST="${TALKEY_WHISPER_HOST:-127.0.0.1}"
PORT="${TALKEY_WHISPER_PORT:-8910}"
CONF="${TALKEY_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/talkey/talkey.env}"
if [[ -f "$CONF" ]]; then
  while IFS='=' read -r k v; do
    k="${k//[[:space:]]/}"; v="${v%%#*}"; v="${v//[[:space:]]/}"
    case "$k" in
      TALKEY_WHISPER_HOST) [[ -n "$v" ]] && HOST="$v" ;;
      TALKEY_WHISPER_PORT) [[ -n "$v" ]] && PORT="$v" ;;
    esac
  done < "$CONF"
fi

echo -n ">> waiting for the whisper server on $HOST:$PORT "
up=0
for _ in $(seq 1 60); do
  if curl -sS --max-time 2 -o /dev/null "http://$HOST:$PORT/" 2>/dev/null; then up=1; break; fi
  echo -n .
  sleep 1
done
echo
if (( up )); then
  echo ">> whisper server responding on $HOST:$PORT"
else
  echo ">> whisper server did not answer within 60s."
  echo "   check: systemctl --user status talkey-whisper"
  echo "          journalctl --user -u talkey-whisper -n 30"
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
and set TALKEY_NOREPEAT_KEYCODES in ~/.config/talkey/talkey.env. They are wanted
by graphical-session.target, which a plain X11 session does not reach on its own,
so start it from ~/.xinitrc for them to come back at the next login:
  systemctl --user import-environment DISPLAY XAUTHORITY
  systemctl --user start graphical-session.target

To undo this checkout install later (it shadows a packaged one):
  systemctl --user disable --now talkey-whisper talkey-hud talkey-norepeat
  rm ~/.config/systemd/user/talkey-{whisper,hud,norepeat}.service
EOF
