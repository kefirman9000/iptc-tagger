"""Light and dark appearance, and the app's icons.

Appearance follows Windows' (or macOS's) light/dark setting unless you pick Light or Dark in Settings. The icons are
Lucide (lucide.dev, ISC licence, see icons/LICENSE-lucide.txt): one outline style, drawn in the text colour of the
current appearance so they suit both."""
import os

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap

import tagger_core as core

ICON_DIR = os.path.join(core.RESOURCE_DIR, "icons")
MODES = [("system", "Follow Windows"), ("light", "Light"), ("dark", "Dark")]

# Colours that can't come from the palette (banners, status), in a light and a dark version
LIGHT = {
    "warn_bg": "#fff4d6", "warn_fg": "#5c4400", "warn_border": "#e9cf7a",
    "info_bg": "#e8f1fd", "info_fg": "#1c3d6e", "info_border": "#b5cff3", "link": "#0b3a82",
    "unsaved_bg": "#fff4e5", "unsaved_fg": "#7a4b00", "unsaved_border": "#f0a640", "unsaved_dot": "#f08c00",
    "saved_bg": "#e6f4ea", "saved_fg": "#1e6b34",
    "ok": "#2e7d32", "bad": "#c62828", "muted": "#6b7280", "grey": "#8a8f98",
    "drop_border": "#9aa3ad", "preview_bg": "#7f7f7f18",
    "primary": "#2f7ddd", "primary_hover": "#3d8af0", "primary_border": "#2569bd", "primary_off": "#9dbbe2",
    "plain_hover": "rgba(0,0,0,18)", "plain_down": "rgba(0,0,0,32)",
    "toast_bg": "#22252a", "toast_fg": "#ffffff",
}
DARK = {
    "warn_bg": "#3a3016", "warn_fg": "#f3dd9b", "warn_border": "#6b5a24",
    "info_bg": "#16273f", "info_fg": "#cfe0f7", "info_border": "#2e4a73", "link": "#8fbaf5",
    "unsaved_bg": "#3a2c16", "unsaved_fg": "#f3cf96", "unsaved_border": "#c98a2e", "unsaved_dot": "#f5a524",
    "saved_bg": "#17321f", "saved_fg": "#9be0a9",
    "ok": "#6fcf7a", "bad": "#ff8a80", "muted": "#9aa0a6", "grey": "#8a8f98",
    "drop_border": "#5a626b", "preview_bg": "#ffffff10",
    "primary": "#2f7ddd", "primary_hover": "#4a92f0", "primary_border": "#1f5fae", "primary_off": "#2c4363",
    "plain_hover": "rgba(255,255,255,20)", "plain_down": "rgba(255,255,255,36)",
    "toast_bg": "#e8eaed", "toast_fg": "#1a1b1e",
}
_state = {"dark": False}


def is_dark() -> bool:
    return _state["dark"]


def color(name: str) -> str:
    return (DARK if _state["dark"] else LIGHT)[name]


def dark_palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.Window: "#202124", QPalette.WindowText: "#e8eaed", QPalette.Base: "#1a1b1e",
        QPalette.AlternateBase: "#25272b", QPalette.Text: "#e8eaed", QPalette.Button: "#2b2d31",
        QPalette.ButtonText: "#e8eaed", QPalette.BrightText: "#ffffff", QPalette.Highlight: "#2f7ddd",
        QPalette.HighlightedText: "#ffffff", QPalette.ToolTipBase: "#2b2d31", QPalette.ToolTipText: "#e8eaed",
        QPalette.PlaceholderText: "#8a8f98", QPalette.Link: "#8fbaf5", QPalette.Light: "#3c3f45",
        QPalette.Midlight: "#33363b", QPalette.Mid: "#3a3d42", QPalette.Dark: "#141518", QPalette.Shadow: "#000000",
    }
    for role, c in roles.items():
        p.setColor(role, QColor(c))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#6b7078"))
    return p


def system_is_dark(app) -> bool:
    hints = app.styleHints()
    if hasattr(hints, "colorScheme"):
        return hints.colorScheme() == Qt.ColorScheme.Dark
    return app.palette().color(QPalette.Window).lightness() < 128


def apply(app, mode: str = "system") -> bool:
    """Light, dark or following the system. Returns True when dark."""
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):                     # Qt 6.8+: the title bar and dialogs follow too
        hints.setColorScheme({"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}.get(
            mode, Qt.ColorScheme.Unknown))
    dark = mode == "dark" or (mode == "system" and system_is_dark(app))
    _state["dark"] = dark
    app.setPalette(dark_palette() if dark else app.style().standardPalette())
    _icon_cache.clear()
    return dark


# ---------- icons -----------------------------------------------------------------------------------------------
_icon_cache = {}


def _pixmap(name: str, colour: str, size: int) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer
    with open(os.path.join(ICON_DIR, name + ".svg"), encoding="utf-8") as fh:
        svg = fh.read().replace("currentColor", colour)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    scale = 2                                                # sharp on high-DPI screens
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, on_primary: bool = False, size: int = 16, colour: str = "") -> QIcon:
    """A Lucide icon in the current text colour (white on a blue button, or a colour of its own), with a faded
    version when disabled."""
    key = (name, on_primary, size, _state["dark"], colour)
    if key in _icon_cache:
        return _icon_cache[key]
    if not os.path.exists(os.path.join(ICON_DIR, name + ".svg")):
        return QIcon()
    normal = colour or ("#ffffff" if on_primary else ("#e8eaed" if _state["dark"] else "#2b2f36"))
    faded = "#ffffffaa" if on_primary else ("#6b7078" if _state["dark"] else "#a3a8b0")
    ic = QIcon()
    ic.addPixmap(_pixmap(name, normal, size), QIcon.Normal)
    ic.addPixmap(_pixmap(name, faded, size), QIcon.Disabled)
    _icon_cache[key] = ic
    return ic


def dot(colour: str, size: int = 10) -> QIcon:
    """A small filled circle (the "unsaved changes" mark in the list)."""
    key = ("dot", colour, size)
    if key not in _icon_cache:
        scale = 2
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(colour))
        p.drawEllipse(QRectF(1, 1, size * scale - 2, size * scale - 2))
        p.end()
        pm.setDevicePixelRatio(scale)
        _icon_cache[key] = QIcon(pm)
    return _icon_cache[key]


def stylesheet() -> str:
    """The main window's styles: solid blue main buttons, plain secondary ones."""
    c = DARK if _state["dark"] else LIGHT
    return f"""
        QPushButton{{padding:6px 12px}}
        QPushButton[primary="true"]{{background:{c['primary']};color:white;border:1px solid {c['primary_border']};
            border-radius:6px;font-weight:600}}
        QPushButton[primary="true"]:hover{{background:{c['primary_hover']}}}
        QPushButton[primary="true"]:disabled{{background:{c['primary_off']};border-color:{c['primary_off']};
            color:rgba(255,255,255,170)}}
        QPushButton[plain="true"]{{background:transparent;border:1px solid transparent;border-radius:6px;
            padding:6px 10px}}
        QPushButton[plain="true"]:hover{{background:{c['plain_hover']};border-color:palette(mid)}}
        QPushButton[plain="true"]:pressed{{background:{c['plain_down']}}}
        QPushButton[plain="true"]:disabled{{color:palette(placeholder-text)}}
        QPushButton[plain="true"]::menu-indicator{{subcontrol-position:right center;right:4px}}
        QTableWidget{{font-size:13px;alternate-background-color:rgba(127,127,127,13)}}
        QGroupBox{{font-weight:600}}
        QTabWidget#instrTabs::pane{{border:1px solid palette(mid);border-radius:6px;top:-1px}}
        QTabWidget#instrTabs QTabBar::tab{{padding:5px 12px}}
        QLabel[pill="unsaved"]{{background:{c['unsaved_bg']};color:{c['unsaved_fg']};border-radius:6px;
            padding:5px 8px;font-weight:600}}
        QLabel[pill="saved"]{{background:{c['saved_bg']};color:{c['saved_fg']};border-radius:6px;padding:5px 8px;
            font-weight:600}}
        QLabel[pill="busy"]{{background:{c['info_bg']};color:{c['info_fg']};border-radius:6px;padding:5px 8px}}
        QLabel[pill="plain"]{{background:{c['plain_hover']};border-radius:6px;padding:5px 8px}}
        QLineEdit[edited="true"],QPlainTextEdit[edited="true"]{{border:1px solid {c['unsaved_border']};
            border-left:4px solid {c['unsaved_border']};border-radius:3px}}
        QPushButton#unsavedPill{{color:{c['unsaved_fg']};background:{c['unsaved_bg']};border:1px solid
            {c['unsaved_border']};border-radius:6px;padding:5px 10px;font-weight:600}}
        QPushButton#unsavedPill:hover{{border-color:{c['unsaved_fg']}}}
        QLabel#allSaved{{color:{c['ok']};padding:0 6px}}
    """
