# Render executable paths safely for systemd's quoted ExecStart syntax and sed.
render_unit() {
  local template="$1" bindir="$2"
  bindir="${bindir//\\/\\\\}"; bindir="${bindir//\"/\\\"}"
  bindir="${bindir//%/%%}"; bindir="${bindir//\$/\$\$}"
  bindir="${bindir//\\/\\\\}"; bindir="${bindir//&/\\&}"; bindir="${bindir//|/\\|}"
  sed "s|@BINDIR@|$bindir|g" "$template"
}
