"""The same checks on Windows, Linux and CI; only synthetic test data."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main():
    seed = ROOT / 'seed.json'
    created_seed = not seed.exists()
    if created_seed:
        shutil.copyfile(ROOT / 'seed.example.json', seed)
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    try:
        commands = [
            [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
            [sys.executable, 'tools/check_utf8.py'],
        ]
        tests = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / 'tests').iterdir()
                       if p.name.endswith(('.test.js', '.test.cjs')))
        if tests:
            commands.append(['node', '--test', *tests])
        for command in commands:
            subprocess.run(command, cwd=ROOT, env=env, check=True)
    finally:
        if created_seed:
            seed.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
