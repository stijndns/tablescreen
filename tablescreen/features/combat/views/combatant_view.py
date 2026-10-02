from __future__ import annotations

import os
import tkinter as tk
from typing import TYPE_CHECKING

from PIL import ImageTk, Image

from .styling import *
from ..model import Type, Status
from ....core.paths import COMBATANT_IMAGES_DIR

if TYPE_CHECKING:
    from ..model import Combatant

INITIATIVE_W    = 52
class CombatantView(tk.Canvas):
    def __init__(self, parent: tk.Widget, combatant: Combatant, img_cache: dict, scale: float):
        self._scale = scale
        self.row_h = max(30, int((ROW_HEIGHT_BASE + COND_EXTRA) * self._scale))
        super().__init__(parent, bg=PALETTE["bg"], highlightthickness=0, bd=0, height=self.row_h+2) # max border width extra needed
        self.combatant = combatant
        self._image_cache = img_cache

    def update_config(self, scale: float):
        self._scale = scale
        self.row_h = max(30, int((ROW_HEIGHT_BASE + COND_EXTRA) * self._scale))
        if self.config("height") != self.row_h + 2:
            self.configure(height=self.row_h+2)

    # ── helper functions───────────────────────────────────────────────────────
    def is_unrevealed(self) -> bool:
        """Return True if this self.combatant should be rendered via _draw_unrevealed_row."""
        return (self.combatant.pending
                or self.combatant.left_combat
                #or self.combatant.status in (Status.DEAD, Status.INCAPACITATED)
                or (self.combatant.type is Type.MONSTER and not self.combatant.has_acted))

    @property
    def padding(self) -> int:
        return int(self._scale * PADDING)

    @property
    def inner_padding(self) -> int:
        return int(12 * self._scale)

    @property
    def init_col_w(self) -> int:
        return int(INITIATIVE_W * self._scale)

    @property
    def text_x(self):
        return self.init_col_w + self.inner_padding + 6

    @property
    def _name_and_status(self) -> str:
        display_name = self.combatant.name.replace("_", " ")
        match self.combatant.status:
            case Status.DEAD:
                display_name += "  [DEAD]"
            case Status.DYING:
                display_name += "  [DYING]"
            case Status.INCAPACITATED:
                display_name += "  [INCAPACITATED]"
            case _:
                if self.combatant.left_combat:
                    display_name += "  [LEFT COMBAT]"
                if self.combatant.pending:
                    display_name += "  [PENDING]"
        return display_name

    def _create_accent_bar(self, colour: str):
        left = self.init_col_w + 2 #respect the border
        self.create_rectangle(left, 2, left + 4, self.row_h, fill=colour, width=0)

    def get_background_color(self, is_current: bool) -> str:
        return PALETTE["active_bg"] if is_current else PALETTE["bg"]

    def get_border_color(self, is_current: bool) -> str:
        return PALETTE["active_border"] if is_current else PALETTE["border"]

    def _create_background(self, is_current: bool):
        right = self.winfo_width() - (1 if is_current else 2)
        bottom = self.row_h + (1 if is_current else 0)
        self.create_rectangle(self.init_col_w + 1, 1, right, bottom,
            fill=self.get_background_color(is_current),
            outline=self.get_border_color(is_current),
            width=2 if is_current else 1)

    def _create_badge(self, text_colour: str, pos_y: int):
        self.create_text(self.text_x, pos_y,
            text=self.combatant.type.value.upper(),
            fill=text_colour,
            font=(FONT_FAMILY, scaled_font(MUTED_FONT_SIZE, self._scale)),
            anchor="w")

    def _create_initiative(self, is_current: bool, is_dimmed: bool):
        init_color = PALETTE["current_glow"] if is_current else (PALETTE["text_dim"] if is_dimmed else PALETTE["text_primary"])
        hidden = self.combatant.type is Type.MONSTER and not self.combatant.has_acted
        init_text = "?" if hidden else str(self.combatant.initiative)
        self.create_text(self.init_col_w // 2, self.row_h // 2,
            text=init_text, fill=init_color,
            font=(FONT_FAMILY, scaled_font(NAME_FONT_SIZE, self._scale), "bold"),
            anchor="center")

    def _create_name(self, y_pos: int, is_current: bool, is_dimmed: bool):
        name_color = (PALETTE["text_dim"] if is_dimmed and not is_current else
                      PALETTE["current_glow"] if is_current else PALETTE["text_primary"])
        self.create_text(self.text_x, y_pos,
            text=self._name_and_status,
            fill=name_color,
            font=(FONT_FAMILY, scaled_font(NAME_FONT_SIZE, self._scale), "bold"),
            anchor="w")

    # ── Revealed row ──────────────────────────────────────────────────────────
    def draw_row(self, is_current: bool):
        status     = self.combatant.status
        is_dead    = status in (Status.DEAD, Status.INCAPACITATED)
        is_dying   = status == Status.DYING
        is_dimmed  = is_dead or is_dying   # greyed colours but may still show turn indicator
        accent     = TYPE_ACCENT.get(self.combatant.type.value, PALETTE["text_muted"])

        row_h = self.row_h

        x_right    = self.winfo_width() - 2

        inner_pad  = self.inner_padding

        # Initiative column
        self._create_initiative(is_current, is_dimmed)

        # Row background
        self._create_background(is_current)

        # Combatant image (anchored top-right of row box, drawn before text)
        img_filename = self.combatant.image
        if img_filename:
            tk_img = self._prepare_combatant_image(img_filename, row_h)
            if tk_img:
                self.create_image(x_right, 1, image=tk_img, anchor="ne")

        # Accent bar
        self._create_accent_bar(accent if not is_dimmed else PALETTE["bar_dead"])

        # Layout: divide row into 4 vertical zones
        # name_y: name line (shifted up)
        # badge_y: type badge
        # res_y: resources line
        # cond_y: conditions line (bottom)
        quarter = row_h // 4
        name_y  = quarter - int(4 * self._scale)
        badge_y = quarter * 2 - int(2 * self._scale)
        res_y   = quarter * 3 - int(2 * self._scale)
        cond_y  = row_h - int(10 * self._scale)

        # Name
        self._create_name(name_y, is_current, is_dimmed)

        # Type badge
        self._create_badge(accent if not is_dimmed else PALETTE["text_dim"], badge_y)

        # HP / status (aligned to name_y on right side)
        if self.combatant.type is Type.PC:
            hp_str   = f"{self.combatant.hp_current}/{self.combatant.hp_max} HP"
            hp_color = PALETTE["text_primary"] if not is_dimmed else PALETTE["text_dim"]
            self.create_text(x_right - inner_pad, name_y,
                text=hp_str, fill=hp_color,
                font=(FONT_FAMILY, scaled_font(STAT_FONT_SIZE, self._scale), "bold"),
                anchor="e")
        else:
            state_text  = STATE_LABELS.get(self.combatant.hp_bar_state, "")
            fill_color  = BAR_COLORS.get(self.combatant.hp_bar_state, PALETTE["bar_dead"])
            label_color = fill_color if not is_dimmed else PALETTE["text_dim"]
            self.create_text(x_right - inner_pad, name_y,
                text=state_text, fill=label_color,
                font=(FONT_FAMILY, scaled_font(NAME_FONT_SIZE, self._scale), "bold"),
                anchor="e")

        # Resources line: reaction and legendary actions
        res_parts = []
        reaction = self.combatant.resources.get("reaction", None)
        if reaction is not None:
            res_parts.append(f"Reaction {reaction.current}/{reaction.maximum}")
        leg = self.combatant.resources.get("legendary_actions", None)
        if leg is not None:
            res_parts.append(f"Legendary Actions {leg.current}/{leg.maximum}")
        # Note: "legendary_actions" key → display as "Legendary Actions" (underscore → space, title case)
        if res_parts:
            res_color = PALETTE["text_dim"] if is_dimmed else PALETTE["text_muted"]
            self.create_text(self.text_x, res_y,
                text="  ·  ".join(res_parts),
                fill=res_color,
                font=(FONT_FAMILY, scaled_font(MUTED_FONT_SIZE, self._scale)),
                anchor="w")

        # Conditions line (always reserved at bottom of row)
        conditions = self.combatant.conditions
        if conditions:
            self.create_text(self.text_x, cond_y,
                text="  ·  ".join(conditions), fill=PALETTE["gold"],
                font=(FONT_FAMILY, scaled_font(10, self._scale)),
                anchor="w")

    # ── Unrevealed / pending row ──────────────────────────────────────────────
    def draw_unrevealed_row(self):
        row_h = self.row_h

        # Initiative:
        self._create_initiative(False, True)

        # Row background
        self._create_background(False)

        # Greyed accent bar
        self._create_accent_bar(PALETTE["bar_dead"])

        # Name + status tag
        self._create_name(row_h // 2 - int(9 * self._scale), False, True)

        # Type badge
        self._create_badge(PALETTE["text_dim"], self.row_h // 2 + int(8 * self._scale))


    # ── Image loading ─────────────────────────────────────────────────────────
    def _prepare_combatant_image(self, filename: str, row_h: int) -> ImageTk.PhotoImage | None:
        """Load, resize, fade and cache a combatant image. Returns None on failure."""
        cache_key = (filename, row_h)
        if cache_key in self._image_cache:
            return self._image_cache[cache_key]

        path = COMBATANT_IMAGES_DIR / filename
        if not path.exists():
            return None

        try:
            img = Image.open(path).convert("RGBA")
        except Exception:
            return None

        # self._scale to row height, preserving aspect ratio
        orig_w, orig_h = img.size
        new_w = max(1, int(orig_w * row_h / orig_h))
        img = img.resize((new_w, row_h), Image.LANCZOS) # type: ignore

        # Apply horizontal fade: right=opaque (200/255), left=transparent
        # Use a 1-pixel-tall gradient then self._scale up — fast and avoids pixel loops
        r, g, b, a = img.split()
        # Build gradient as raw bytes: left=0, right=200
        gradient_row = bytes(int(200 * x / new_w) for x in range(new_w))
        gradient_data = gradient_row * row_h
        fade = Image.frombytes("L", (new_w, row_h), gradient_data)
        # Multiply existing alpha channel with fade mask using Pillow multiply
        from PIL import ImageChops
        combined = ImageChops.multiply(a, fade)
        img.putalpha(combined)

        tk_img = ImageTk.PhotoImage(img)
        self._image_cache[cache_key] = tk_img
        return tk_img