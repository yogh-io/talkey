# --- output ----------------------------------------------------------------
if [[ -t 1 ]]; then
  B=$'\e[1m'; DIM=$'\e[2m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; R=$'\e[0m'
else
  B=""; DIM=""; GREEN=""; YELLOW=""; R=""
fi
step()  { printf '\n%s== %s%s\n' "$B" "$*" "$R"; }
ok()    { printf '   %s+%s %s\n' "$GREEN" "$R" "$*"; }
info()  { printf '   %s-%s %s\n' "$DIM" "$R" "$*"; }
warn()  { printf '   %s!%s %s\n' "$YELLOW" "$R" "$*"; }
die()   { printf '\ntalkey-setup: %s\n' "$*" >&2; exit 1; }

ask_yn() {  # ask_yn "question" [y|n] -> 0 for yes
  local q="$1" def="${2:-y}" a prompt="[Y/n]"
  [[ "$def" == n ]] && prompt="[y/N]"
  while true; do
    # A closed stdin used to re-prompt forever. It means nobody is there to
    # answer, so stop rather than spin.
    read -r -p "   $q $prompt " a || { printf '\n'; die "input ended - completed changes have been kept"; }
    a="${a:-$def}"
    case "${a,,}" in y|yes) return 0 ;; n|no) return 1 ;; esac
  done
}

# choose "question" "label1" "label2" ... -> index (1-based) on stdout
# The menu goes to stderr for the same reason capture_keycode's prompt does: the
# caller reads the answer from stdout, so anything printed there is swallowed.
choose() {
  local q="$1"; shift
  local n=$# i a
  printf '   %s\n' "$q" >&2
  for ((i=1; i<=n; i++)); do printf '     %d) %s\n' "$i" "${!i}" >&2; done
  while true; do
    read -r -p "   choice [1-$n]: " a || { printf '\n' >&2; die "input ended - completed changes have been kept"; }
    [[ "$a" =~ ^[0-9]+$ ]] && (( a >= 1 && a <= n )) && { printf '%s' "$a"; return 0; }
  done
}

have() { command -v "$1" >/dev/null 2>&1; }
