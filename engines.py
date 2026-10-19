"""Discover usable local conversion engines without downloading anything."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pypandoc


def find_pandoc() -> str | None:
    """Probe executables: an installed wheel can contain the wrong CPU binary."""
    candidates = [os.environ.get('PYPANDOC_PANDOC')]
    if sys.platform == 'darwin':
        # PyCharm launched from Finder may not inherit Homebrew's PATH.
        candidates += ['/opt/homebrew/bin/pandoc', '/usr/local/bin/pandoc']
    candidates += [str(Path(pypandoc.__file__).parent / 'files' /
                       ('pandoc.exe' if os.name == 'nt' else 'pandoc')),
                   shutil.which('pandoc')]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            result = subprocess.run([candidate, '--version'], capture_output=True, text=True,
                                    timeout=5, shell=False,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            continue
        if result.returncode == 0 and result.stdout.lower().startswith('pandoc '):
            return candidate
    return None
