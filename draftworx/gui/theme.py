# SPDX-License-Identifier: GPL-3.0-or-later
"""Themes. The colour tokens are OpenSchematix's own (openschematix/gui/theme.py), as OpenMeridian carries them into PySide6,
so the family matches theme for theme. Every colour in this program comes from here."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication

CANVAS = {
    "paper": {"desk": "#e4e7eb", "paper": "#ffffff", "ink": "#14181d", "grid": "#eef1f4", "grid_major": "#dfe3e8", "select": "#e8590c",
              "remove": "#d6336c", "ghost": "#1f6feb", "band": "#1f6feb", "band_cross": "#2f9e44", "hover": "#2f9e44", "snap": "#e8590c", "crosshair": "#8a939d", "guide": "#2f9e44", "sheet": "#9aa3ad"},
    "dark": {"desk": "#15171a", "paper": "#1d2024", "ink": "#e6e8eb", "grid": "#23272c", "grid_major": "#2d3238", "select": "#ff922b",
             "remove": "#ff6b9d", "ghost": "#4c8dff", "band": "#4c8dff", "band_cross": "#51cf66", "hover": "#51cf66", "snap": "#ffa94d", "crosshair": "#5c6670", "guide": "#51cf66", "sheet": "#4a525b"},
    "blueprint": {"desk": "#0a2a4f", "paper": "#0f3a6b", "ink": "#e9f2ff", "grid": "#164477", "grid_major": "#1d5291", "select": "#ffd43b",
                  "remove": "#ffa8c5", "ghost": "#a5d8ff", "band": "#a5d8ff", "band_cross": "#b2f2bb", "hover": "#b2f2bb", "snap": "#ffd43b", "crosshair": "#5f8fc4", "guide": "#b2f2bb", "sheet": "#5f8fc4"},
    "high_contrast": {"desk": "#000000", "paper": "#000000", "ink": "#ffffff", "grid": "#262626", "grid_major": "#404040", "select": "#ffff00",
                      "remove": "#ff00ff", "ghost": "#00ff00", "band": "#ffff00", "band_cross": "#00ff00", "hover": "#00ff00", "snap": "#ffff00", "crosshair": "#ffffff", "guide": "#00ff00", "sheet": "#808080"},
}

THEMES = {
    "modern_light": {"label": "Modern Light", "dark": False, "canvas": "paper",
        "ui": {"bg": "#f3f4f6", "surface": "#ffffff", "surface2": "#f7f8fa", "border": "#dcdfe4", "border_strong": "#c3c8cf",
               "text": "#1b1f24", "muted": "#626b75", "faint": "#8a929b", "accent": "#2563eb", "accent_text": "#ffffff",
               "accent_soft": "#e0eaff", "hover": "#eceef1", "pressed": "#dfe3e8", "input": "#ffffff", "select_bg": "#dbe6ff",
               "select_fg": "#10244d", "tooltip_bg": "#1f2329", "tooltip_fg": "#f5f6f8", "danger": "#d9364f", "warning": "#c27803",
               "success": "#1f8a46", "info": "#2563eb", "focus": "#2563eb"}},
    "modern_dark": {"label": "Modern Dark", "dark": True, "canvas": "dark",
        "ui": {"bg": "#16181c", "surface": "#1e2126", "surface2": "#23272d", "border": "#2e333a", "border_strong": "#3b4149",
               "text": "#e6e8eb", "muted": "#9aa3ad", "faint": "#6c757f", "accent": "#4c8dff", "accent_text": "#ffffff",
               "accent_soft": "#1f3355", "hover": "#2a2f36", "pressed": "#343a42", "input": "#14161a", "select_bg": "#24406e",
               "select_fg": "#ffffff", "tooltip_bg": "#f1f3f5", "tooltip_fg": "#1b1f24", "danger": "#ff6b81", "warning": "#f5b649",
               "success": "#51cf66", "info": "#74a7ff", "focus": "#4c8dff"}},
    "graphite": {"label": "Graphite", "dark": True, "canvas": "dark",
        "ui": {"bg": "#2b2d30", "surface": "#323538", "surface2": "#383b3f", "border": "#45494e", "border_strong": "#55595f",
               "text": "#e3e3e3", "muted": "#a7abb0", "faint": "#7d8186", "accent": "#e8a33d", "accent_text": "#1c1c1c",
               "accent_soft": "#4a3f2c", "hover": "#3d4044", "pressed": "#474a4f", "input": "#26282b", "select_bg": "#5a4a2e",
               "select_fg": "#ffffff", "tooltip_bg": "#f1f1f1", "tooltip_fg": "#1c1c1c", "danger": "#ff7b72", "warning": "#e8a33d",
               "success": "#7ee787", "info": "#79c0ff", "focus": "#e8a33d"}},
    "neutral_gray": {"label": "Neutral Gray", "dark": False, "canvas": "paper",
        "ui": {"bg": "#d9dbde", "surface": "#e8e9eb", "surface2": "#e1e3e5", "border": "#c2c5ca", "border_strong": "#a9adb3",
               "text": "#1d2023", "muted": "#555b62", "faint": "#7b8188", "accent": "#3b6ea8", "accent_text": "#ffffff",
               "accent_soft": "#cfdcec", "hover": "#d4d7da", "pressed": "#c9ccd0", "input": "#f4f5f6", "select_bg": "#c3d5ea",
               "select_fg": "#10233a", "tooltip_bg": "#25282c", "tooltip_fg": "#f2f2f2", "danger": "#c0392b", "warning": "#a46a00",
               "success": "#25803d", "info": "#3b6ea8", "focus": "#3b6ea8"}},
    "blueprint": {"label": "Blueprint", "dark": True, "canvas": "blueprint",
        "ui": {"bg": "#0b2140", "surface": "#0f2a52", "surface2": "#13325f", "border": "#1f4478", "border_strong": "#2a5591",
               "text": "#e6f0ff", "muted": "#9fb9dc", "faint": "#6f8fbb", "accent": "#5ab0ff", "accent_text": "#06203f",
               "accent_soft": "#173f72", "hover": "#16386a", "pressed": "#1d4580", "input": "#0a1d38", "select_bg": "#1f4e8f",
               "select_fg": "#ffffff", "tooltip_bg": "#e6f0ff", "tooltip_fg": "#06203f", "danger": "#ff8a8a", "warning": "#ffd166",
               "success": "#7ee2a8", "info": "#5ab0ff", "focus": "#5ab0ff"}},
    "high_contrast": {"label": "High Contrast", "dark": True, "canvas": "high_contrast",
        "ui": {"bg": "#000000", "surface": "#000000", "surface2": "#0d0d0d", "border": "#ffffff", "border_strong": "#ffffff",
               "text": "#ffffff", "muted": "#ffffff", "faint": "#d0d0d0", "accent": "#ffff00", "accent_text": "#000000",
               "accent_soft": "#333300", "hover": "#1f1f1f", "pressed": "#333333", "input": "#000000", "select_bg": "#ffff00",
               "select_fg": "#000000", "tooltip_bg": "#ffff00", "tooltip_fg": "#000000", "danger": "#ff4040", "warning": "#ffff00",
               "success": "#00ff00", "info": "#00ffff", "focus": "#ffff00"}},
}
DEFAULT_THEME = "modern_light"
UI_FONTS = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Cantarell", "Ubuntu", "Noto Sans", "Open Sans", "DejaVu Sans",
            "Liberation Sans", "Helvetica", "Arial")
MONO_FONTS = ("Cascadia Mono", "Consolas", "JetBrains Mono", "Ubuntu Mono", "Noto Sans Mono", "DejaVu Sans Mono", "Liberation Mono")


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t), int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def translucent(color: str, alpha: float) -> QColor:
    """A theme colour let through to what's underneath: what an area about to be filled is shown in."""
    c = QColor(color)
    c.setAlphaF(alpha)
    return c


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #rrggbb colours."""
    def lum(c):
        v = [int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b_ = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@dataclass
class Theme:
    name: str
    label: str
    dark: bool
    ui: dict
    canvas: dict
    family: str = ""
    mono: str = ""

    def on_paper(self, rgb: int) -> str:
        """A drawing colour as it should show on this canvas: its own, unless it would vanish into the paper.
        Only what's on screen changes. The stored colour is untouched."""
        c = f"#{rgb & 0xffffff:06x}"
        paper = self.canvas["paper"]
        if contrast(c, paper) >= 2.6:
            return c
        ink = self.canvas["ink"]
        for step in range(1, 11):
            m = _mix(c, ink, step / 10)
            if contrast(m, paper) >= 2.6:
                return m
        return ink


def resolve(name: str) -> Theme:
    key = name if name in THEMES else DEFAULT_THEME
    t = THEMES[key]
    th = Theme(key, t["label"], t["dark"], dict(t["ui"]), dict(CANVAS[t["canvas"]]))
    fams = {f.lower(): f for f in QFontDatabase.families()}
    th.family = next((fams[f.lower()] for f in UI_FONTS if f.lower() in fams), QGuiApplication.font().family())
    th.mono = next((fams[f.lower()] for f in MONO_FONTS if f.lower() in fams), th.family)
    return th


def stylesheet(t: Theme) -> str:
    u = t.ui
    return f"""
* {{ font-family: "{t.family}"; font-size: 9.5pt; }}
QWidget {{ color: {u['text']}; }}
QMainWindow, QDialog {{ background: {u['surface']}; }}
QToolTip {{ background: {u['tooltip_bg']}; color: {u['tooltip_fg']}; border: none; padding: 4px 6px; }}
QMenuBar {{ background: {u['surface']}; border-bottom: 1px solid {u['border']}; }}
QMenuBar::item {{ padding: 4px 9px; background: transparent; }}
QMenuBar::item:selected {{ background: {u['hover']}; }}
QMenu {{ background: {u['surface']}; border: 1px solid {u['border_strong']}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {u['select_bg']}; color: {u['select_fg']}; }}
QMenu::item:disabled {{ color: {u['faint']}; }}
QMenu::separator {{ height: 1px; background: {u['border']}; margin: 4px 6px; }}
#rail {{ background: {u['bg']}; border-right: 1px solid {u['border']}; }}
#railRule {{ background: {u['border_strong']}; max-height: 1px; min-height: 1px; margin: 4px 3px; }}
QToolButton#tool {{ background: transparent; border: none; border-radius: 6px; padding: 5px; }}
QToolButton#tool:hover {{ background: {u['hover']}; }}
QToolButton#tool:pressed {{ background: {u['pressed']}; }}
QToolButton#tool:checked {{ background: {u['accent_soft']}; }}
#panel {{ background: {u['surface']}; border-left: 1px solid {u['border']}; }}
#panelTitle {{ font-size: 11pt; font-weight: 700; }}
#sectionTitle {{ color: {u['muted']}; font-size: 8pt; font-weight: 700; letter-spacing: 0.4px; padding-top: 8px; }}
#muted {{ color: {u['muted']}; }}
#small {{ color: {u['muted']}; font-size: 8.5pt; }}
#statusStrip {{ background: {u['surface2']}; border-top: 1px solid {u['border']}; }}
#prompt {{ font-weight: 500; }}
#message {{ color: {u['warning']}; }}
QPushButton {{ background: {u['surface2']}; border: 1px solid {u['border_strong']}; border-radius: 6px; padding: 4px 10px; }}
QPushButton:hover {{ background: {u['hover']}; }}
QPushButton:pressed {{ background: {u['pressed']}; }}
QPushButton:focus {{ border-color: {u['focus']}; }}
QPushButton:disabled {{ color: {u['faint']}; background: {u['surface2']}; border-color: {u['border']}; }}
QPushButton#primary {{ background: {u['accent']}; color: {u['accent_text']}; border: 1px solid {u['accent']}; font-weight: 600; }}
QPushButton#chip {{ background: {u['accent_soft']}; border: none; border-radius: 9px; padding: 2px 9px; font-size: 8.5pt; }}
QPushButton#chip:hover {{ background: {u['hover']}; }}
QToolButton#toggle {{ background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 2px 7px; color: {u['muted']}; font-size: 8.5pt; }}
QToolButton#toggle:hover {{ background: {u['hover']}; }}
QToolButton#toggle:checked {{ background: {u['accent_soft']}; color: {u['text']}; font-weight: 600; }}
QLineEdit, QPlainTextEdit, QComboBox {{ background: {u['input']}; border: 1px solid {u['border_strong']}; border-radius: 5px; padding: 3px 6px; selection-background-color: {u['select_bg']}; selection-color: {u['select_fg']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {u['focus']}; }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{ background: {u['surface']}; border: 1px solid {u['border_strong']}; selection-background-color: {u['select_bg']}; selection-color: {u['select_fg']}; }}
QListWidget {{ background: {u['surface']}; border: none; outline: 0; }}
QListWidget::item {{ padding: 5px 8px; border-radius: 4px; }}
QListWidget::item:hover {{ background: {u['hover']}; }}
QListWidget::item:selected {{ background: {u['select_bg']}; color: {u['select_fg']}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {u['border_strong']}; border-radius: 4px; min-height: 24px; min-width: 24px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {u['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QDialog#palette {{ border: 1px solid {u['border_strong']}; border-radius: 8px; }}
"""
