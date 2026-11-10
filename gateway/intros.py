"""
intros.py — Character intros: an animation that plays while the webapp
loads, for a player whose linked character has one.

Each intro is a folder in the intros folder (``intros/`` by default, kept
out of version control: it's the players' own art), with its clips and an
``intro.json``:

    {
      "characters": ["Xyra", "Xyra El Finnis Feror"],   // matched ignoring case
      "loop": ["xyra_left.mp4", "xyra_right.mp4"],      // while loading, random order
      "closing": ["fight.mp4"],                         // optional: once, in order, after the button
      "finale": "xyra_finale.mp4",                      // last; ends on white or black
      "ends_on": "white",                               // optional: "white" (default) or "black"
      "loading": "Loading your campaign…",              // optional texts and colour
      "button": "Enter the library",
      "after_click": "Xyra raises her hands…",
      "color": "#5fd6a0"
    }

Read once at start-up: a new intro needs a restart of the gateway. Only the
files an intro.json names are ever served.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")         # the folder: also its URL
FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(mp4|webm)")
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
TEXT_MAX = 80

log = logging.getLogger("gateway")


def character_key(name: str) -> str:
    """How character names are compared: case and extra spaces ignored."""
    return " ".join(name.split()).casefold()


@dataclass(frozen=True)
class Intro:
    name: str
    folder: Path
    characters: tuple[str, ...]
    loop: tuple[str, ...]
    finale: str
    texts: dict
    color: Optional[str]
    closing: tuple[str, ...] = ()
    ends_on: str = "white"

    @property
    def files(self) -> set[str]:
        return {*self.loop, *self.closing, self.finale}

    def public(self) -> dict:
        """What a page needs to play it."""
        base = f"/intros/{self.name}/"
        return {"name": self.name, "loop": [base + f for f in self.loop],
                "closing": [base + f for f in self.closing], "finale": base + self.finale,
                "ends_on": self.ends_on, "color": self.color, **self.texts}


class Intros:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.by_name: dict[str, Intro] = {}
        self._by_character: dict[str, Intro] = {}

    def load(self) -> "Intros":
        """Read every intro; a broken one is reported and left out."""
        if not self.folder.is_dir():
            return self
        for folder in sorted(p for p in self.folder.iterdir() if p.is_dir()):
            try:
                intro = self._read(folder)
            except (OSError, ValueError) as exc:
                log.warning("Intro %s left out: %s", folder.name, exc)
                continue
            self.by_name[intro.name] = intro
            for character in intro.characters:
                key = character_key(character)
                if key in self._by_character:
                    log.warning("Intro %s: %r already has the intro %s.", intro.name, character,
                                self._by_character[key].name)
                    continue
                self._by_character[key] = intro
        if self.by_name:
            log.info("Intros: %s.", ", ".join(
                f"{i.name} ({', '.join(i.characters)})" for i in self.by_name.values()))
        return self

    def for_character(self, character: Optional[str]) -> Optional[Intro]:
        return self._by_character.get(character_key(character)) if character else None

    def file(self, name: str, file: str) -> Optional[Path]:
        """The path of a file an intro names, or None."""
        intro = self.by_name.get(name)
        if intro is None or file not in intro.files:
            return None
        return intro.folder / file

    @staticmethod
    def _read(folder: Path) -> Intro:
        if not NAME_RE.fullmatch(folder.name):
            raise ValueError("a folder name is lowercase letters, digits, - or _")
        try:
            data = json.loads((folder / "intro.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"intro.json isn't valid JSON ({exc})") from None
        if not isinstance(data, dict):
            raise ValueError("intro.json must be an object")

        def text_list(key: str) -> tuple[str, ...]:
            value = data.get(key)
            if (not isinstance(value, list) or not value
                    or not all(isinstance(v, str) and v.strip() for v in value)):
                raise ValueError(f"{key!r} must be a list of names")
            return tuple(value)

        def clip(value) -> str:
            if not isinstance(value, str) or not FILE_RE.fullmatch(value):
                raise ValueError(f"{value!r} isn't a clip name (an .mp4 or .webm in the folder)")
            if not (folder / value).is_file():
                raise ValueError(f"{value} is missing")
            return value

        texts = {}
        for key in ("loading", "button", "after_click"):
            value = data.get(key)
            if value is not None:
                if not isinstance(value, str) or not 0 < len(value) <= TEXT_MAX:
                    raise ValueError(f"{key!r} must be a text of at most {TEXT_MAX} characters")
                texts[key] = value
        color = data.get("color")
        if color is not None and (not isinstance(color, str) or not COLOR_RE.fullmatch(color)):
            raise ValueError("'color' must look like #5fd6a0")
        ends_on = data.get("ends_on", "white")
        if ends_on not in ("white", "black"):
            raise ValueError("'ends_on' must be white or black")
        closing = tuple(clip(c) for c in text_list("closing")) if "closing" in data else ()
        return Intro(name=folder.name, folder=folder, characters=text_list("characters"),
                     loop=tuple(clip(c) for c in text_list("loop")),
                     finale=clip(data.get("finale")), texts=texts, color=color,
                     closing=closing, ends_on=ends_on)
