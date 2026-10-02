"""
completion.py — Tab-completion helpers and argument tokenising.

TODO: tab_completion still takes a completion_type naming a specific
assets folder. That is feature-specific knowledge sitting in core; it is
kept for now so the move stays mechanical, and should be replaced by passing
the directory in directly once every feature has been ported.
"""

from __future__ import annotations

import glob
import os
import shlex
from pathlib import Path

from .paths import AUDIO_DIR, COMBATANT_IMAGES_DIR, IMAGES_DIR

_BASE_DIRS: dict[str, Path] = {
    "image": IMAGES_DIR,
    "audio": AUDIO_DIR,
    "combatant_image": COMBATANT_IMAGES_DIR,
}


def tab_completion(text: str, allowed_filetypes, current_os: str,
                   completion_type: str) -> list[str]:
    base = _BASE_DIRS.get(completion_type)
    if base is None:
        return []

    pattern = os.path.join(str(base), text + "*")
    matches = glob.glob(pattern)
    results: list[str] = []
    for match in matches:
        # Windows vs POSIX separators.
        rel = os.path.relpath(match, str(base)).replace("\\", "/")
        if os.path.isdir(match):
            # Keep directories so the user can descend into them.
            if current_os == "Linux":
                rel = rel.split("/")[-1] + "/"
            else:
                rel += "/"
            results.append(rel)
        else:
            ext = os.path.splitext(match)[1].lower()
            if ext in allowed_filetypes:
                if current_os == "Linux":
                    rel = rel.split("/")[-1]
                results.append(rel)
    return results


def get_arg_parts(arg: str) -> list[str]:
    """Tokenise, respecting quoted strings."""
    try:
        return shlex.split(arg.strip())
    except ValueError:
        return arg.strip().split()
