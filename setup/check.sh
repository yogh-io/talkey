#!/usr/bin/env bash
# Offline verification. Fake devices keep tests away from your desktop and mic.
set -euo pipefail
cd "$(dirname "$0")/.."
mapfile -t shell_files < <(python3 - <<'PY'
from pathlib import Path
for folder in ['bin', 'lib', 'setup', 'integrations/eww']:
    for path in sorted(Path(folder).rglob('*')):
        if path.is_file() and (path.suffix == '.sh' or path.read_bytes().startswith(b'#!/usr/bin/env bash')):
            print(path)
print('packaging/aur/PKGBUILD')
PY
)
for file in "${shell_files[@]}"; do bash -n "$file"; done
printf 'Bash syntax: %s files passed\n' "${#shell_files[@]}"
if command -v shellcheck >/dev/null; then
  shellcheck --severity=error --shell=bash "${shell_files[@]}"
else
  echo 'ShellCheck unavailable; CI runs it.'
fi
python3 -m unittest discover -s tests -v
