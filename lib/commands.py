"""Resolve configured whole-phrase commands and run explicitly registered scripts."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys


def words(text):
    return tuple(re.findall(r"\w+(?:['’]\w+)*", text.casefold()))


def registry(data, user):
    definitions = {}
    for directory in (data / 'config/commands.d', user):
        for path in sorted(directory.glob('*.json')):
            entry = json.loads(path.read_text())
            if not isinstance(entry, dict):
                raise ValueError(f'{path.name}: expected an object')
            previous = definitions.get(path.stem, {})
            if 'exec' in entry:
                previous.pop('builtin', None)
            elif 'builtin' in entry:
                previous.pop('exec', None)
            definitions[path.stem] = dict(previous, **entry)
    phrases = {}
    for name, entry in definitions.items():
        if not isinstance(entry.get('enabled', True), bool):
            raise ValueError(f'{name}: enabled must be true or false')
        if not entry.get('enabled', True):
            continue
        aliases = entry.get('phrases')
        if not isinstance(aliases, list) or not aliases or not all(isinstance(p, str) for p in aliases):
            raise ValueError(f'{name}: phrases must be a nonempty list of strings')
        builtin, argv = entry.get('builtin'), entry.get('exec')
        if (builtin is None) == (argv is None):
            raise ValueError(f'{name}: specify either builtin or exec')
        if builtin is not None and builtin not in ('enter-on', 'enter-off', 'enter-toggle', 'auto-stop'):
            raise ValueError(f'{name}: unknown builtin')
        if argv is not None and (not isinstance(argv, list) or not argv or
                                 not all(isinstance(a, str) and '\0' not in a for a in argv) or not argv[0]):
            raise ValueError(f'{name}: exec must be an argument list')
        timeout = entry.get('timeout_seconds', 10)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ValueError(f'{name}: timeout_seconds must be between 0 and 120')
        action = dict(entry, id=name, kind='builtin' if builtin else 'exec', timeout_seconds=timeout)
        for phrase in aliases:
            key = words(phrase)
            if len(key) < 2 or (key[0] != 'command' and key != ('stop', 'listening')):
                raise ValueError(f'{name}: phrases must start with command, or be stop listening')
            if key in phrases:
                raise ValueError(f'{name}: duplicate phrase {phrase!r}')
            phrases[key] = action
    return phrases


def resolve(text, data, user):
    key = words(text)
    if key[:2] == ('dictate', 'command') or key == ('dictate', 'stop', 'listening'):
        literal = re.sub(r'^\W*dictate\W+', '', text, count=1, flags=re.IGNORECASE)
        return {'kind': 'dictation', 'text': literal}
    if key[:1] != ('command',) and key != ('stop', 'listening'):
        return {'kind': 'dictation', 'text': text}
    return registry(data, user).get(key, {'kind': 'unknown', 'text': text})


def execute(action):
    argv = action['exec'].copy()
    argv[0] = os.path.expanduser(argv[0])
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = child.communicate(timeout=action['timeout_seconds'])
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        out, err = child.communicate()
        return {'ok': False, 'error': 'command timed out', 'stdout': out[-4000:], 'stderr': err[-4000:]}
    return {'ok': child.returncode == 0, 'exit_code': child.returncode,
            'stdout': out[-4000:], 'stderr': err[-4000:]}


def main():
    try:
        if sys.argv[1] == 'resolve':
            result = resolve(sys.stdin.read().strip(), Path(sys.argv[2]), Path(sys.argv[3]))
        elif sys.argv[1] == 'run':
            result = execute(json.loads(Path(sys.argv[2]).read_text()))
        else:
            raise ValueError('unknown operation')
    except Exception as error:
        print(json.dumps({'kind': 'error', 'ok': False, 'error': str(error)}))
        return 1
    print(json.dumps(result))
    return 0 if result.get('ok', True) else 1


if __name__ == '__main__':
    sys.exit(main())
