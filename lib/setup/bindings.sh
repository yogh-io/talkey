# Reading and generating i3 bindings.
i3_config_files() {
  local -A visited=()
  i3_walk_config "$1"
}

i3_walk_config() {
  local main line inc match
  main="$(readlink -f "$1")"
  [[ -f "$main" && -z "${visited[$main]:-}" ]] || return 0
  visited[$main]=1
  printf '%s\n' "$main"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*include[[:space:]]+ ]] || continue
    inc="${line#*include}"; inc="${inc%%#*}"; inc="${inc//\"/}"
    inc="${inc#"${inc%%[![:space:]]*}"}"; inc="${inc%"${inc##*[![:space:]]}"}"
    [[ "$inc" == talkey.conf || "$inc" == */talkey.conf ]] && continue
    [[ "$inc" != '~'* ]] || inc="$HOME${inc#\~}"
    [[ "$inc" == /* ]] || inc="$(dirname "$main")/$inc"
    if [[ -f "$inc" ]]; then i3_walk_config "$inc"
    else
      while IFS= read -r match; do i3_walk_config "$match"; done < <(compgen -G "$inc" | sort)
    fi
  done < "$main"
}

# Prints "file:line: the offending line" for anything already bound to this
# keycode, so the report can name where to go and change it.
keycode_conflict() {  # keycode_conflict <code> <i3cfg>
  local code="$1" cfg="$2" f
  while read -r f; do
    grep -nE "^[[:space:]]*bindcode([[:space:]]+--[a-z-]+)*[[:space:]]+${code}\b" "$f" 2>/dev/null \
      | sed "s|^|${f}:|"
  done < <(i3_config_files "$cfg")
}

# A role with no keycode is commented out rather than left holding a number
# nobody pressed. Every line mentioning the variable has to go, not just the
# `set`: i3 does not substitute an undefined variable, so a surviving
# `bindcode $key_memo` is a config error on reload. \b does not match before
# an underscore, so $key_dictate never catches $key_dictate_alt.
write_i3_bindings() {  # write_i3_bindings <dest> <dictate> <alt> <memo> <submit>
  local dest="$1" src="$DATA_DIR/bindings/i3.conf"
  [[ -f "$src" ]] || { warn "missing $src"; return 1; }
  local -A val=([key_dictate]="$2" [key_dictate_alt]="$3" [key_memo]="$4" [key_submit]="$5")
  local tmp v args=()
  tmp="$(mktemp "$(dirname "$dest")/.talkey-bindings.XXXXXX")"
  for v in key_dictate key_dictate_alt key_memo key_submit; do
    if [[ -n "${val[$v]}" ]]; then
      args+=(-e "s|^set \\\$$v .*|set \$$v ${val[$v]}|")
    else
      args+=(-e "/^[^#].*\\\$$v\\b/s|^|# unbound by talkey-setup: |")
    fi
  done
  sed -E "${args[@]}" "$src" > "$tmp"

  # One line names every key at once - the watcher that keeps auto-repeat off.
  # The rule above would comment it out for mentioning an unbound variable,
  # taking the bound keys' protection with it, so rebuild it from what is left.
  local bound=()
  for v in key_dictate key_dictate_alt key_memo key_submit; do
    [[ -n "${val[$v]}" ]] && bound+=("\$$v")
  done
  if (( ${#bound[@]} )); then
    sed -i -E "s|^(# unbound by talkey-setup: )?exec_always --no-startup-id talkey-norepeat .*|exec_always --no-startup-id talkey-norepeat ${bound[*]}|" "$tmp"
  fi

  mv -f "$tmp" "$dest"
  ok "wrote $dest"
}

# A conflicting binding that stays where it is WINS. i3 keeps the first binding
# it reads for a keycode and drops every later one as a duplicate, and talkey's
# include sits at the end of your config - so the old binding goes on doing what
# it always did while setup reports the keys as live. Saying "already bound" and
# then changing nothing is the same dead end as not checking at all.
#
# Same contract as the include-line edit: a copy first, i3's own parser holds the
# veto, and a rejected result is put back byte for byte. Commenting a line out
# keeps the line count, so several edits to one file stay on their line numbers.
disable_conflicts() {  # disable_conflicts <i3cfg> <file:line:text>...
  local i3cfg="$1"; shift
  local -A touched=()
  local c f l rest
  for c in "$@"; do
    f="${c%%:*}"; rest="${c#*:}"; l="${rest%%:*}"
    [[ -f "$f" && "$l" =~ ^[0-9]+$ ]] || continue
    [[ -n "${touched[$f]:-}" ]] || { cp -f "$f" "$f.talkey.bak" || return 1; touched[$f]=1; }
    sed -i "${l}s|^|# commented out by talkey-setup - this key is talkey's now: |" "$f"
  done
  (( ${#touched[@]} )) || return 1
  # No i3 binary means no second opinion. Leave the edit in place rather than
  # revert something that may well be correct, and say which it was.
  if ! have i3; then
    info "no i3 binary here to check the result - reload i3 and see"
    return 0
  fi
  if i3 -C -c "$i3cfg" >/dev/null 2>&1; then
    for f in "${!touched[@]}"; do info "kept a copy at $f.talkey.bak"; done
    return 0
  fi
  for f in "${!touched[@]}"; do cp -f "$f.talkey.bak" "$f"; done
  warn "i3 rejected the result, so every file was put back exactly as it was"
  return 1
}

# Reloading is the difference between "written" and "live". i3-msg reports
# failures as JSON with "success":false rather than a non-zero exit, so the
# output is what has to be read.
i3_reload() {  # i3_reload [1 if a binding of yours still owns one of these keys]
  local blocked="${1:-0}"
  have i3-msg || { info "reload i3 (Mod+Shift+R) to load the bindings"; return 0; }
  local out
  out="$(i3-msg -t command reload 2>&1)"
  if [[ "$out" == *'"success":true'* ]]; then
    # Reloading makes the FILE live, which is not the same as the keys working.
    # Saying "live now" while a binding of yours still wins is the reload
    # equivalent of reporting success at a key that does nothing.
    if (( blocked )); then
      ok "i3 reloaded - but the bindings listed above still own those keys"
    else
      ok "i3 reloaded - your keys are live now"
    fi
  else
    warn "i3 would not reload: $out"
    todo "reload i3 (Mod+Shift+R) - the keys do nothing until you do"
  fi
}
