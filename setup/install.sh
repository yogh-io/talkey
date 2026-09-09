#!/usr/bin/env bash
# Wire up talkey from a git checkout.
#
# Only the parts that a package cannot do: build whisper.cpp if no packaged
# server exists, and install the user units pointing at THIS checkout. Choosing
# a model, starting the service and binding your keys are the same questions
# either way, so they are talkey-setup's job - a packaged install runs exactly
# the same code.
#
# Idempotent - safe to re-run.
#
# Installing the AUR package instead? You do not need this script. Just run:
#   talkey-setup
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
. "$REPO_DIR/lib/install.sh"

"$REPO_DIR/bin/talkey-setup" deps

# whisper server: a distro package, or built into ~/.local by build-whisper.sh.
have_server=0
for c in "$HOME/.local/opt/whisper.cpp/build/bin/whisper-server" /usr/bin/whisper-server; do
  [[ -x "$c" ]] && { have_server=1; break; }
done
if (( ! have_server )); then
  echo ">> building whisper.cpp (this compiles once)"
  WHISPER_SKIP_MODEL=1 bash "$REPO_DIR/setup/build-whisper.sh"
fi

echo ">> installing systemd --user units"
mkdir -p "$UNIT_DIR"
# The checkout's bin dir goes into the unit. Note this copy in ~/.config
# permanently overrides a packaged unit in /usr/lib - see the uninstall note at
# the end if you later install the package.
for u in talkey-whisper talkey-hud talkey-norepeat; do
  render_unit "$REPO_DIR/systemd/$u.service.in" "$REPO_DIR/bin" > "$UNIT_DIR/$u.service"
done
systemctl --user daemon-reload

cat <<EOF

Put $REPO_DIR/bin on your PATH so the bare 'talkey' commands resolve.

To undo this checkout install later (it shadows a packaged one):
  systemctl --user disable --now talkey-whisper talkey-hud talkey-norepeat
  rm ~/.config/systemd/user/talkey-{whisper,hud,norepeat}.service
EOF

# Everything from here is identical for a packaged install.
exec "$REPO_DIR/bin/talkey-setup"
