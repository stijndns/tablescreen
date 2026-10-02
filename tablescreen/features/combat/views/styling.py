# ── Layout constants ─────────────────────────────────────────────────────────
FONT_FAMILY     = "Consolas"
MIN_PAGE_SIZE   = 6             # combatants per page
ROUND_FONT_SIZE = 22
NAME_FONT_SIZE  = 15
STAT_FONT_SIZE  = 12
MUTED_FONT_SIZE = 11
PADDING         = 24
# Row height base (without conditions) — used in _draw
ROW_HEIGHT_BASE = 64
COND_EXTRA      = 18            # extra px (pre-scale) reserved for conditions line

# ── Colour palette ────────────────────────────────────────────────────────────
PALETTE = {
    "bg":            "#0d1117",
    "surface":       "#161b22",
    "surface2":      "#21262d",
    "border":        "#30363d",
    "text_primary":  "#e6edf3",
    "text_muted":    "#8b949e",
    "text_dim":      "#484f58",
    "gold":          "#d4a843",
    "current_glow":  "#f0c040",
    "pc_accent":     "#4493f8",
    "npc_accent":    "#bc8cff",
    "monster_accent":"#ff7b72",
    "bar_green":     "#3fb950",
    "bar_yellow":    "#d29922",
    "bar_orange":    "#e3652b",
    "bar_red":       "#f85149",
    "bar_dead":      "#30363d",
    "bar_track":     "#21262d",
    "active_bg":     "#1c2128",
    "active_border": "#d4a843",
}

BAR_COLORS = {
    "green":  PALETTE["bar_green"],
    "yellow": PALETTE["bar_yellow"],
    "orange": PALETTE["bar_orange"],
    "red":    PALETTE["bar_red"],
    "dead":   PALETTE["bar_dead"],
}

TYPE_ACCENT = {
    "pc":      PALETTE["pc_accent"],
    "npc":     PALETTE["npc_accent"],
    "monster": PALETTE["monster_accent"],
}

STATE_LABELS = {
    "green":  "Healthy",
    "yellow": "Bloodied",
    "orange": "Wounded",
    "red":    "Near Death",
    "dead":   "Defeated",
}

def scaled_font(base: int, scale: float) -> int:
    return max(9, int(base * scale))