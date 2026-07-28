#!/usr/bin/env bash
# Generate the short UI tones used by bin/talkey. Committed to the repo so the
# setup stays self-contained; re-run to regenerate.
set -euo pipefail

OUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/sounds"
mkdir -p "$OUT"

# tone NAME FREQ DURATION [VOL]  -> mono wav with click-free fades.
# VOL is the target peak (0..1). ffmpeg's `sine` source here outputs ~-18 dB
# (about 0.125 full scale), so we apply an 8x makeup to make VOL mean the
# actual peak amplitude - otherwise even volume=1.0 comes out faint.
tone() {
  local name="$1" freq="$2" dur="$3" vol="${4:-0.8}"
  local fout gain
  fout="$(awk "BEGIN{print $dur-0.03}")"
  gain="$(awk "BEGIN{print $vol*8}")"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "sine=frequency=$freq:duration=$dur" \
    -af "afade=t=in:d=0.008,afade=t=out:st=$fout:d=0.03,volume=$gain" \
    -ac 1 -ar 48000 "$OUT/$name.wav"
}

# start/stop are the ones you act on, so they're loudest; submit/cancel a touch under.
tone start  987 0.12 0.78   # bright  -> listening
tone stop   587 0.12 0.78   # lower   -> captured
tone submit 1319 0.09 0.64  # crisp   -> Enter sent
tone cancel 300 0.16 0.64   # low     -> discarded

echo "generated: $OUT"
ls -1 "$OUT"
