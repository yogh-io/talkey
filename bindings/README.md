# Key bindings

talkey is not tied to any window manager. It needs X11 - `xdotool` to type and
`xset` to stop keys auto-repeating - but nothing in it knows about i3. All a
binding has to do is run two commands:

```
key pressed   ->  talkey dictate press
key released  ->  talkey dictate release
```

The release half is the only real requirement. Plenty of hotkey tools bind press
only; those cannot do hold-to-talk. Three that can:

| File | For | Binds by |
|---|---|---|
| [`i3.conf`](i3.conf) | i3 | keycode (`bindcode`) |
| [`sxhkdrc`](sxhkdrc) | any X11 WM, via sxhkd | keysym |
| [`xbindkeysrc`](xbindkeysrc) | any X11 WM, via xbindkeys | keycode |

Copy the one you want, set your own keys at the top, and load it. Each file
says how.

## Finding your keys

```bash
xev | grep --line-buffered -oP 'keycode\s+\d+.*'
```

Press a key with the `xev` window focused. The number after `keycode` is what
i3 and xbindkeys want; the name in parentheses is the keysym sxhkd wants. Media
and macro keys often have a keycode but no keysym - bind those by keycode.

The shipped defaults are a Keychron K10's media keys, which is simply what this
was written on. Any keys work. The ones that suit it best are keys you do not
otherwise use, within reach of a resting hand.

## The two background helpers

Neither is required for dictation to work, but both make it behave properly:

- **`talkey-hud`** draws the live HUD and the cheat sheet.
- **`talkey-norepeat`** keeps X from auto-repeating your dictation keys. Without
  it, holding a key fires the binding over and over instead of once.

i3 starts both from `i3.conf`. sxhkd and xbindkeys are only hotkey daemons, so
under those, run them as user services:

```bash
systemctl --user enable --now talkey-hud talkey-norepeat
```

`--now` starts them straight away, but both units are wanted by
`graphical-session.target`, and a plain X11 session does not reach that target on
its own - so they would not come back at your next login. Start it yourself, from
`~/.xinitrc` or your WM autostart, before the WM starts:

```bash
systemctl --user import-environment DISPLAY XAUTHORITY
systemctl --user start graphical-session.target
```

`talkey-norepeat` needs to know which keys, and a service has no argument list,
so put them in `~/.config/talkey/talkey.env`:

```
TALKEY_NOREPEAT_KEYCODES=194 191 193 192
```

## Window rules

The HUD must never take keyboard focus, or your text would be typed into it
instead of your real window. eww already declares the windows as unfocusable
notifications, which most window managers honour, so usually there is nothing
to do.

If yours steals focus anyway, add a rule matching the window **title** -
`Eww - talkeyhud` and `Eww - talkeyhelp`. Match on the title rather than the
class: every eww window shares the class `Eww`, so a class rule would also
catch an eww bar or widgets you run yourself. `i3.conf` shows the shape of it.
