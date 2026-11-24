# ===========================================================================
# keys
# ===========================================================================
# xev opens its own window and reports only what is typed while that window has
# focus, so this never sees keystrokes meant for anything else. xdotool focuses
# it for you.
# Only the keycode goes to stdout - the caller reads it from there. Everything
# a human reads goes to stderr, or it would be captured as the value.
capture_keycode() {  # capture_keycode "role" -> keycode on stdout, empty if skipped
  local role="$1" out wid code="" _
  out="$(mktemp)"; : > "$out"
  xev -event keyboard > "$out" 2>/dev/null &
  local xevpid=$!
  for _ in $(seq 1 30); do
    wid="$(xdotool search --name '^Event Tester$' 2>/dev/null | head -1)"
    [[ -n "$wid" ]] && break
    sleep 0.1
  done
  if [[ -z "$wid" ]]; then
    kill "$xevpid" 2>/dev/null; rm -f "$out"
    warn "xev did not open a window"
    return 1
  fi
  xdotool windowactivate --sync "$wid" 2>/dev/null
  printf '   press the key for %s%s%s (15s, or wait to keep the current one) ' "$B" "$role" "$R" >&2
  for _ in $(seq 1 150); do
    code="$(grep -A3 '^KeyPress' "$out" 2>/dev/null | grep -oP 'keycode\s+\K[0-9]+' | head -1)"
    [[ -n "$code" ]] && break
    sleep 0.1
  done
  kill "$xevpid" 2>/dev/null; wait "$xevpid" 2>/dev/null
  rm -f "$out"
  [[ -n "$code" ]] || { printf 'skipped\n' >&2; return 1; }
  printf 'keycode %s\n' "$code" >&2
  printf '%s\n' "$code"
}

# The bindings file keeps every keycode in `set $key_* NNN` lines at the top,
# specifically so only those lines ever need rewriting.
# --- what is already bound -------------------------------------------------
# Capturing a key that another binding already owns produces the failure that
# looks most like talkey being broken: the key does something, just not this.
# i3 resolves includes, so the scan has to as well.
step_keys() {
  step "key bindings"
  if ! have xev; then
    warn "xev is not installed, so keys cannot be read from the keyboard"
    info "$(pkg_hint xev) - then re-run: talkey-setup keys"
    info "or type the numbers in below; 'xmodmap -pke' lists them"
  fi

  local wm="" pick
  if pgrep -x i3 >/dev/null 2>&1; then
    wm=i3; ok "i3 is running"
  else
    pick="$(choose "which do you use for key bindings?" \
      "i3" "sxhkd" "xbindkeys" "none of these - just show me the keycodes")" || return 1
    case "$pick" in 1) wm=i3 ;; 2) wm=sxhkd ;; 3) wm=xbindkeys ;; 4) wm=none ;; esac
  fi

  # Seeded from a previous run, never from built-in numbers. The defaults in
  # bindings/i3.conf are a Keychron K10's media keys - right for the machine
  # this was written on and wrong for nearly everyone - so offering to "keep"
  # them would quietly bind four keys the user does not have, and report them
  # as chosen. A role nobody presses stays empty and gets commented out.
  local dest="${XDG_CONFIG_HOME:-$HOME/.config}/i3/talkey.conf"
  local -A code=([dictate]="" [dictate_alt]="" [memo]="" [submit]="")
  local role prev
  # Bindings of yours that a chosen keycode collides with, kept until the
  # bindings file is written - see disable_conflicts for why they cannot just
  # be reported and left alone.
  local CONFLICTS=()

  local i3cfg="" c
  for c in "${XDG_CONFIG_HOME:-$HOME/.config}/i3/config" "$HOME/.i3/config"; do
    [[ -f "$c" ]] && { i3cfg="$c"; break; }
  done
  if [[ -f "$dest" ]]; then
    for role in dictate dictate_alt memo submit; do
      prev="$(sed -nE "s|^set \\\$key_$role ([0-9]+).*|\1|p" "$dest" | head -1)"
      [[ -n "$prev" ]] && code[$role]="$prev"
    done
    [[ -n "${code[dictate]}" ]] && info "found your current keys in $dest - press Enter at a prompt to keep one"
  fi

  # Caps Lock is the one key that is on every keyboard, reachable without
  # moving your hand, and doing nothing anybody needs. Nothing else is
  # universal: media keys vary, Menu is missing on laptops and Macs, Scroll
  # Lock and Pause are absent from anything compact. So it is the only default
  # offered, and only for dictate - submit and memo have no universal home, and
  # inventing one would bind a key the user does have to something else.
  local CAPS=66
  if [[ -z "${code[dictate]}" ]]; then
    printf '\n'
    info "Caps Lock can be your dictate key: hold it to talk, release to type."
    info "It is on every keyboard, and this stops it being Caps Lock."
    local conf
    conf="$(keycode_conflict "$CAPS" "${i3cfg:-/nonexistent}")"
    if [[ -n "$conf" ]]; then
      warn "something already binds Caps Lock:"
      printf '     %s\n' "$conf"
      info "pressing your own key below avoids the clash"
    elif ask_yn "use Caps Lock for dictate?"; then
      code[dictate]="$CAPS"
      ok "dictate is Caps Lock"
      info "it will stop capitalising - 'setxkbmap -option' with no argument undoes that"
    fi
  fi

  local -A prompt=(
    [dictate]="dictate - hold to talk"
    [submit]="submit - send / Enter, double-tap toggles auto Enter"
    [memo]="memo - double-tap restarts a dictation or toggles live translation, hold for a memo"
    [dictate_alt]="dictate in a second language (optional)"
  )
  local got
  for role in dictate submit memo dictate_alt; do
    while true; do
      if have xev; then
        # Announced before xev takes the focus - once the Event Tester window is
        # up, the prompt is behind it and nobody is reading this line.
        printf '\n   %s%s%s\n' "$B" "${prompt[$role]}" "$R"
        [[ -n "${code[$role]}" ]] && info "currently ${code[$role]}"
        got="$(capture_keycode "${prompt[$role]}")" || got=""
      else
        read -r -p "   keycode for ${prompt[$role]} [${code[$role]:-none}]: " got
        [[ "$got" =~ ^[0-9]+$ ]] || got=""
      fi
      if [[ -n "$got" ]]; then
        # One key cannot hold two roles. i3 keeps the FIRST binding it sees for
        # a keycode and drops the rest as duplicates, so the second role would
        # silently do nothing - and pressing the same key twice is easy when a
        # press does not register the first time.
        local other taken=""
        for other in dictate submit memo dictate_alt; do
          [[ "$other" != "$role" && "${code[$other]}" == "$got" ]] && { taken="$other"; break; }
        done
        if [[ -n "$taken" ]]; then
          warn "keycode $got is already your $taken key, and one key cannot do both"
          info "press a different key, or press nothing to leave $role unbound"
          continue
        fi
        # Say so before writing it. A key that already does something else is
        # the fault that looks most like talkey being broken - it reacts, just
        # not the way you meant.
        local conf
        conf="$(keycode_conflict "$got" "${i3cfg:-/nonexistent}")"
        if [[ -n "$conf" ]]; then
          warn "keycode $got is already bound:"
          printf '     %s\n' "$conf"
          ask_yn "use it for $role anyway?" n || continue
          while IFS= read -r c; do [[ -n "$c" ]] && CONFLICTS+=("$c"); done <<<"$conf"
        fi
        code[$role]="$got"; break
      fi
      # Nothing pressed. Only an offer to keep if there is something to keep.
      if [[ -n "${code[$role]}" ]]; then info "kept ${code[$role]}"; break; fi
      if [[ "$role" == dictate_alt ]]; then info "left unbound - you only dictate in one language"; break; fi
      ask_yn "nothing pressed - try $role again?" || { info "$role left unbound"; break; }
    done
  done

  printf '\n'
  if [[ -z "${code[dictate]}" ]]; then
    warn "no dictate key, so there is nothing to write - that is the one key talkey needs"
    todo "bind your keys: talkey-setup keys"
    return 1
  fi
  info "dictate=${code[dictate]:-unbound} submit=${code[submit]:-unbound} memo=${code[memo]:-unbound} alt=${code[dictate_alt]:-unbound}"

  # talkey-norepeat needs the same codes, and a systemd unit has no argument
  # list - so they have to live in the config for the non-i3 paths to work.
  # Only the keys that exist: an empty slot would pass a blank argument.
  local codes=()
  for role in dictate dictate_alt memo submit; do
    [[ -n "${code[$role]}" ]] && codes+=("${code[$role]}")
  done
  config_set TALKEY_NOREPEAT_KEYCODES "${codes[*]}"

  case "$wm" in
    i3)
      if [[ -f "$dest" ]]; then
        ask_yn "$dest exists - overwrite it?" n || { info "left it alone"; return 0; }
        cp -f "$dest" "$dest.bak"; info "kept a copy at $dest.bak"
      fi
      mkdir -p "$(dirname "$dest")"
      write_i3_bindings "$dest" "${code[dictate]}" "${code[dictate_alt]}" \
        "${code[memo]}" "${code[submit]}" || return 1

      # Holding Caps Lock would otherwise toggle the Lock modifier and put
      # everything talkey types in capitals. Written into our own file so it
      # applies for the session and is undone by removing the include.
      #
      # Derived from the keycodes actually written, NOT from whether the offer
      # above was taken. Taking the offer and then pressing a different key used
      # to disable Caps Lock for nothing; and a re-run keeps an existing Caps
      # Lock binding without ever showing the offer, which used to drop this
      # line and let the key start capitalising again.
      local caps_bound=0
      for role in dictate dictate_alt memo submit; do
        [[ "${code[$role]}" == "$CAPS" ]] && caps_bound=1
      done
      if (( caps_bound )); then
        printf '\n# Caps Lock is one of your talkey keys, so stop it latching capitals.\n' >> "$dest"
        printf '# Undo by deleting this line: it only lasts as long as the session.\n' >> "$dest"
        printf 'exec_always --no-startup-id setxkbmap -option caps:none\n' >> "$dest"
        ok "Caps Lock will no longer capitalise"
      fi

      # The bindings you said to override. Asked separately from "use it
      # anyway", because that answer chose a key and this one edits a file of
      # yours - and until it happens, the old binding is the one that fires.
      local blocked=0
      if (( ${#CONFLICTS[@]} )); then
        printf '\n'
        warn "these bindings of yours own the same keys, and they win over talkey's:"
        printf '     %s\n' "${CONFLICTS[@]}"
        if ask_yn "comment them out so your talkey keys work?"; then
          disable_conflicts "$i3cfg" "${CONFLICTS[@]}" || blocked=1
        else
          info "left alone - those keys go on doing what they do now, not talkey"
          blocked=1
        fi
        (( blocked )) && todo "comment out the bindings listed above, then reload i3 - until then those keys keep doing what they did"
      fi

      # i3 does not read the file until it is included, and a file that is never
      # included behaves exactly like a broken install - every key does nothing,
      # with nothing to tell the two apart. So offer, rather than print a line
      # and hope. i3's own parser gets the veto, and a rejected edit is rolled
      # straight back.
      if [[ -n "$i3cfg" ]] && grep -qE "^[[:space:]]*include[[:space:]]+.*talkey\.conf" "$i3cfg"; then
        ok "already included from $i3cfg"
        i3_reload "$blocked"
      elif [[ -z "$i3cfg" ]]; then
        warn "no i3 config found, so nothing can include the bindings"
        todo "add 'include ${dest/#$HOME/\~}' to your i3 config, then reload i3"
      elif ask_yn "add 'include ${dest/#$HOME/\~}' to $i3cfg now?"; then
        cp -f "$i3cfg" "$i3cfg.talkey.bak"
        printf '\n# talkey key bindings\ninclude %s\n' "$dest" >> "$i3cfg"
        if i3 -C -c "$i3cfg" >/dev/null 2>&1; then
          ok "added, and i3 accepts it (kept a copy at $i3cfg.talkey.bak)"
          i3_reload "$blocked"
        else
          cp -f "$i3cfg.talkey.bak" "$i3cfg"
          warn "i3 rejected the result, so your config was put back exactly as it was:"
          i3 -C -c "$i3cfg.talkey.bak" >/dev/null 2>&1 \
            && printf '     the problem is in %s\n' "$dest" \
            || printf '     your config already had an error before talkey touched it\n'
          todo "add 'include ${dest/#$HOME/\~}' to $i3cfg by hand, then reload i3"
        fi
      else
        printf '\n   %sOne line left, in %s:%s\n\n' "$B" "$i3cfg" "$R"
        printf '     include %s\n\n' "${dest/#$HOME/\~}"
        info "the keys do nothing until you add it, then reload i3 (Mod+Shift+R)"
        todo "add 'include ${dest/#$HOME/\~}' to $i3cfg, then reload i3 (Mod+Shift+R)"
      fi
      ;;
    sxhkd|xbindkeys)
      local src="$DATA_DIR/bindings/sxhkdrc"
      [[ "$wm" == xbindkeys ]] && src="$DATA_DIR/bindings/xbindkeysrc"
      printf '\n   %s%s uses its own format - start from:%s\n' "$B" "$wm" "$R"
      printf '     %s\n' "$src"
      info "put the keycodes above into it, then start the helpers:"
      info "  systemctl --user enable --now talkey-hud talkey-norepeat"
      ;;
    none)
      info "your keycodes are above - bind them however you like"
      ;;
  esac
}
