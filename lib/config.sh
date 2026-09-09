# A deliberately small, non-executable, single-line KEY=value format.
# Values may be unquoted or quoted; double quotes allow escaped quotes/backslashes.
# No interpolation, command substitution, or shell evaluation is performed.
config_value() {
  local value="$1" quote char tail index escaped=0
  REPLY=''
  value="${value#"${value%%[![:space:]]*}"}"
  case "$value" in
    \"*|\'*) quote="${value:0:1}" ;;
    *) value="${value%%#*}"; REPLY="${value%"${value##*[![:space:]]}"}"; return 0 ;;
  esac
  for ((index=1; index<${#value}; index++)); do
    char="${value:index:1}"
    if (( escaped )); then REPLY+="$char"; escaped=0
    elif [[ "$quote" == '"' && "$char" == '\' ]]; then escaped=1
    elif [[ "$char" == "$quote" ]]; then
      tail="${value:index+1}"; tail="${tail#"${tail%%[![:space:]]*}"}"
      [[ -z "$tail" || "$tail" == \#* ]] && return 0
      return 1
    else REPLY+="$char"; fi
  done
  return 1
}

config_load() {
  local path="$1" line key value number=0
  local -A loaded=()
  [[ -f "$path" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    number=$((number+1))
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"; value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^TALKEY_[A-Z0-9_]+$ ]] || continue
    # The inherited environment wins. Within the file, the last assignment wins.
    [[ -n "${!key+set}" && -z "${loaded[$key]:-}" ]] && continue
    if ! config_value "$value"; then
      echo "talkey: invalid quoted value in $path:$number" >&2; return 1
    fi
    export "$key=$REPLY"
    loaded[$key]=1
  done < "$path"
}
