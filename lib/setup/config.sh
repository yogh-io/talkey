# Update one setting atomically, without interpolating values into sed code.
config_set() {
  local key="$1" value="$2" line tmp found=0
  [[ "$key" =~ ^TALKEY_[A-Z0-9_]+$ && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || return 1
  mkdir -p "$CONFIG_DIR" || return 1
  if [[ ! -f "$TALKEY_CONFIG" ]]; then
    if [[ -f "$DATA_DIR/config/talkey.env.example" ]]; then
      cp "$DATA_DIR/config/talkey.env.example" "$TALKEY_CONFIG" || return 1
    else : > "$TALKEY_CONFIG" || return 1; fi
  fi
  value="${value//\\/\\\\}"; value="${value//\"/\\\"}"
  tmp="$(mktemp "$CONFIG_DIR/.talkey.env.XXXXXX")" || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[#[:space:]]*${key}= ]]; then
      if (( ! found )); then printf '%s="%s"\n' "$key" "$value"; found=1; fi
    else printf '%s\n' "$line"; fi
  done < "$TALKEY_CONFIG" > "$tmp"
  if (( ! found )); then printf '%s="%s"\n' "$key" "$value" >> "$tmp"; fi
  chmod 600 "$tmp"
  mv -f "$tmp" "$TALKEY_CONFIG" || { rm -f "$tmp"; return 1; }
  ok "$key=$2"
}
