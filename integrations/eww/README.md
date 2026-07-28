# eww dictation HUD

The live HUD for talkey, built on **eww** (a GTK widget system for X11/i3). It
is display-only: it reads the same state files `bin/talkey` writes and never
feeds back into the recorder.

`talkey` stays renderer-agnostic - it just writes the state files. This adapter
watches the recorder pidfile (`$STATE_DIR/dictate.pid`) via inotify and opens or
closes the HUD on its own, event-driven, no polling.

Why eww over a terminal: a terminal caps the level meter at 8 block-heights and
16 colours. eww gives a rounded translucent panel, proportional type for the
transcript, and a taller multi-row colour meter - and it is a no-focus,
always-on-top window by design, which is the one hard requirement (xdotool must
keep typing into your real window).

## What's here

```
eww.yuck     window + widgets (deflisten hud -> header/transcript/meter/readout)
eww.scss     styling (gradients, zone colours, fonts)
eww-feed     streams the live state as JSON (peak dBFS -> level + coloured trace)
talkey-hud   the renderer adapter: daemon + open/close the HUD per dictation
```

`talkey-hud` is installed onto your PATH; the three files it reads stay
together in this directory (`/usr/share/talkey/integrations/eww` when installed
from a package).

## Prerequisites

- **eww**: `pacman -S eww`, or `yay -S eww-git`.
- `jq`, `inotify-tools`.

## How it runs

The i3 include (`i3/talkey.conf`) does two things for the HUD: floats it and
never focuses it (`[class="(?i)eww"]`), and starts the adapter with
`exec_always` (it is single-instance, so reloads never stack a second watcher).

So once eww is installed and i3 is reloaded, dictation just shows the HUD. No
separate setup.

## Manual control (debugging)

```
talkey-hud          # start the daemon + watch (i3 does this for you)
talkey-hud stop     # close the HUD + kill the eww daemon
```

The HUD centres itself via its own geometry in `eww.yuck` (fixed width, top
anchor - so growing text only ever scrolls down, never reflows sideways). Tune
position/size there (`:x`, `:y`, `:width`) and the meter density via
`TALKEY_HIST_N` / `TALKEY_SPARK_ROWS` (see `eww-feed`).
