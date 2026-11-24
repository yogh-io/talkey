# eww dictation HUD

The live HUD for talkey, built on **eww** (a GTK widget system for X11/i3). It
reads the same state files `bin/talkey` writes and never feeds back into the
recorder. Live translation has a window of its own, which you can scroll back.

`talkey` stays renderer-agnostic - it just writes the state files. This adapter
watches `$STATE_DIR/session.changed` via inotify and reads the current take
through compatibility paths such as `dictate.pid` and `dictate.transcript`.
Each take owns its state; older workers cannot overwrite the current display.

Why eww over a terminal: a terminal caps the level meter at 8 block-heights and
16 colours. eww gives a rounded translucent panel, proportional type for the
transcript, and a taller multi-row colour meter - and it is a no-focus,
always-on-top window by design, which is the one hard requirement (xdotool must
keep typing into your real window).

## What's here

```
eww.yuck     the windows: the dictation HUD, the translate window, the key cheat sheet
eww.scss     styling (gradients, zone colours, fonts)
eww-feed     streams the live state as JSON (peak dBFS -> level + coloured trace);
             `eww-feed translate` feeds the translate window, and
             `eww-feed scroll` is what its mouse wheel runs
talkey-hud   the renderer adapter: daemon + open/close the windows
```

The `bin/talkey-hud` launcher works from a checkout and from an installed package.
The files it reads stay
together in this directory (`/usr/share/talkey/integrations/eww` when installed
from a package).

## Prerequisites

- **eww**: from the AUR (`yay -S eww`). It is not in any official repo.
- `jq`, `inotify-tools`.

## How it runs

The i3 include (`bindings/i3.conf`, installed as
`/usr/share/talkey/bindings/i3.conf`, which you copy to `~/.config/i3/talkey.conf`)
does two things for the HUD: floats it and never focuses it
(`[title="^Eww - talkey(hud|help)$"]` - matched by title, not class, because every
eww window shares the class `Eww`), and starts the adapter with `exec_always`
(it is single-instance, so reloads never stack a second watcher).

So once eww is installed and i3 is reloaded, dictation just shows the HUD. No
separate setup.

The translate window (`talkeytranslate`) needs no rule: it is `wm-ignore`, so no
window manager manages it, and it cannot take the focus even when you scroll it.
It opens while translate mode hears, translates or speaks a phrase, in the same
place as the HUD; a dictation replaces it until the dictation is done.

## Manual control (debugging)

```
talkey-hud          # start the daemon + watch (i3 does this for you)
talkey-hud stop     # close the HUD + kill the eww daemon
```

The HUD centres itself via the geometry in `eww.yuck` (fixed width, top anchor -
so growing text only ever scrolls down, never reflows sideways). Do not edit that
file to move it: when talkey came from a package it is root-owned and replaced on
upgrade. Set these instead, in `~/.config/talkey/talkey.env`:

```
TALKEY_HUD_MONITOR   which display, e.g. DP-1   (unset = your primary)
TALKEY_HUD_POS       "<x>x<y>"
TALKEY_HUD_SIZE      "<w>x<h>"
TALKEY_HUD_ANCHOR    e.g. "top center"
TALKEY_EWW_COLS      transcript wrap width
TALKEY_EWW_LINES     transcript rows kept visible
TALKEY_HIST_N        level-trace history columns
TALKEY_SVG_W/_H      level-trace image size
```

`talkey-hud` passes the first four to `eww open`, which overrides the geometry.
The translate window takes the monitor, position and anchor too, but keeps its
own size.

Meter rendering lives in `lib/hud.sh`. It samples only while the recorder is alive,
retains the final trace and duration, and reports when live preview reaches its cap.
