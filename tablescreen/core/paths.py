"""
paths.py — Where the project's data lives.

Features should ask for a directory here rather than building their own
relative paths.
"""

from pathlib import Path

# .../tablescreen/tablescreen/core/paths.py -> up three is the repo root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

ASSETS_DIR = PROJECT_ROOT / "assets"
IMAGES_DIR = ASSETS_DIR / "images"
AUDIO_DIR = ASSETS_DIR / "audio"
COMBATANT_IMAGES_DIR = IMAGES_DIR / "combatants"
COMBATANTS_DIR = PROJECT_ROOT / "combatants"
LOGS_DIR = PROJECT_ROOT / "logs"

def ensure_dir(path: Path) -> Path:
    """Create a directory if it does not exist, then return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
