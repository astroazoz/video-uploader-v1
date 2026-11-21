"""HUD design system: a futuristic game-menu structure with terminal typography.

Atmosphere: near-black canvas (never pure black) with two barely-there lights, slanted tick marks on the
window edges, thin 1px structure lines, rectangular controls with amber corner brackets, wide letter-spaced
headings (Bahnschrift) and monospace (Consolas) for everything technical. One accent: amber.
Motion: short eased hover/selection transitions, a sweeping idle segment, a breathing status dot.
"""

import math
import os
import time
from html import escape
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation,
                            Signal)
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QWidget

import glass as G
from glass import W, col, draw_icon, icon_pixmap_named, mix

BG = "#050506"        # off-black canvas
TEXT = "#e8eaed"
MUTED = "#8b919b"
DIM = "#4b515a"
ACCENT = "#e0953a"    # the single accent: amber (saturation < 80%)
ACCENT_HI = "#f3b867"
ERR = "#d9776c"       # desaturated red, errors only
HEAD = BODY = "Segoe UI"
MONO = "Consolas"
DOWN = QFont.Weight.DemiBold


def pick_fonts() -> None:
    global HEAD, BODY, MONO
    fams = set(QFontDatabase.families())
    HEAD = next((n for n in ("Bahnschrift", "Segoe UI Variable Display", "Segoe UI") if n in fams), "Segoe UI")
    BODY = next((n for n in ("Segoe UI Variable Text", "Segoe UI") if n in fams), "Segoe UI")
    MONO = next((n for n in ("Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", "Lucida Console") if n in fams), "Consolas")


def hfont(pt: float, spacing: float = 2.4, weight=DOWN) -> QFont:
    f = QFont(HEAD)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def mfont(pt: float, spacing: float = 0.0, weight=QFont.Weight.Normal) -> QFont:
    f = QFont(MONO)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def bfont(pt: float, weight=QFont.Weight.Normal) -> QFont:
    f = QFont(BODY)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    return f


# ----------------------------------------------------------------------------- canvas
def paint_backdrop(p: QPainter, widget: QWidget, washes, ticks: bool = True) -> None:
    r = widget.rect()
    w, h = r.width(), r.height()
    p.fillRect(r, QColor(BG))
    for cx, cy, rad, c, a in ((0.0, 0.0, 0.8, "#b87820", 22), (1.0, 0.0, 0.75, "#3f5f80", 24),
                              (0.0, 1.0, 0.65, "#6a4412", 11), (1.0, 1.0, 0.6, "#2a3f55", 12)):
        g = QRadialGradient(w * cx, h * cy, max(w, h) * rad)
        g.setColorAt(0, col(c, a))
        g.setColorAt(1, col(c, 0))
        p.fillRect(r, g)
    if any(pix is not None and op > 0.01 for pix, op in washes):
        for pix, op in washes:
            if pix is not None and op > 0.01:
                p.save()
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.setOpacity(op * 0.10)
                p.drawPixmap(r, pix)
                p.restore()
        vg = QRadialGradient(w * 0.5, h * 0.45, max(w, h) * 0.8)
        vg.setColorAt(0, col(BG, 0))
        vg.setColorAt(1, col(BG, 240))
        p.fillRect(r, vg)
    if ticks:  # slanted tick marks along both window edges, like the reference menu
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(W(46), 1))
        for y in range(54, h - 34, 38):
            p.drawLine(QPointF(5, y), QPointF(17, y - 3))
            p.drawLine(QPointF(w - 5, y), QPointF(w - 17, y - 3))


class HudBackdrop(G.BackdropMixin, QWidget):
    def __init__(self, ticks: bool = True):
        super().__init__()
        self._bd_init()
        self.ticks = ticks

    def paintEvent(self, e):
        p = QPainter(self)
        paint_backdrop(p, self, [(self._wash_old, 1 - self._wash_t), (self._wash, self._wash_t)], self.ticks)
        p.end()


class HudDialog(G.BackdropMixin, QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._bd_init()

    def paintEvent(self, e):
        p = QPainter(self)
        paint_backdrop(p, self, [(self._wash_old, 1 - self._wash_t), (self._wash, self._wash_t)], False)
        p.end()

    def showEvent(self, e):
        super().showEvent(e)
        style_titlebar(self)


def _colorref(hex_color: str):
    import ctypes
    c = QColor(hex_color)
    return ctypes.c_int((c.blue() << 16) | (c.green() << 8) | c.red())


def style_titlebar(widget: QWidget) -> None:
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        one = ctypes.c_int(1)
        for attr in (20, 19):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(one), 4)
        for attr, c in ((35, BG), (36, TEXT), (34, "#1a1c20")):
            v = _colorref(c)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
    except Exception:
        pass


# ----------------------------------------------------------------------------- panels and small widgets
class HudPanel(QFrame):
    """Thin-bordered translucent panel with amber corner brackets (top-left, bottom-right)."""

    def __init__(self, strength: float = 1.0, brackets: bool = True):
        super().__init__()
        self.strength, self.brackets = strength, brackets

    def paintEvent(self, e):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, W(10 * self.strength))
        g.setColorAt(1, W(3 * self.strength))
        p.fillRect(r, g)
        p.setPen(QPen(W(34), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        if self.brackets:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(col(ACCENT, 215), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            n = 13
            p.drawPolyline([QPointF(r.left(), r.top() + n), r.topLeft(), QPointF(r.left() + n, r.top())])
            p.drawPolyline([QPointF(r.right() - n, r.bottom()), r.bottomRight(), QPointF(r.right(), r.bottom() - n)])
        p.end()


class Tag(QLabel):
    """Small rectangular label, e.g. TIKTOK or 1080P."""

    def __init__(self, text: str = "", accent: bool = False, pt: float = 7.8):
        super().__init__("")
        self.accent = accent
        self.setFont(mfont(pt, 1.1, DOWN))
        self.setFixedHeight(24)
        self.setText(text)

    def setText(self, text: str):
        super().setText(text)
        self.setFixedWidth(QFontMetrics(self.font()).horizontalAdvance(text.upper()) + 22)

    def paintEvent(self, e):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillRect(r, col(ACCENT, 26) if self.accent else W(6))
        p.setPen(QPen(col(ACCENT, 175) if self.accent else W(56), 1))
        p.drawRect(r)
        p.setPen(QColor(ACCENT_HI) if self.accent else QColor(TEXT))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text().upper())
        p.end()


class IconBadge(QWidget):
    """Square bracketed icon used in empty states."""

    def __init__(self, icon: str, size: int = 64, color: str = MUTED):
        super().__init__()
        self.icon, self.color = icon, color
        self.setFixedSize(size, size)

    def paintEvent(self, e):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillRect(r, W(5))
        p.setPen(QPen(W(44), 1))
        p.drawRect(r)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(col(ACCENT, 215), 1.6))
        n = 11
        p.drawPolyline([QPointF(r.left(), r.top() + n), r.topLeft(), QPointF(r.left() + n, r.top())])
        p.drawPolyline([QPointF(r.right() - n, r.bottom()), r.bottomRight(), QPointF(r.right(), r.bottom() - n)])
        draw_icon(p, self.icon, r.adjusted(r.width() * 0.28, r.height() * 0.28, -r.width() * 0.28, -r.height() * 0.28),
                  QColor(self.color), 1.6)
        p.end()


class DottedRule(QWidget):
    """Row of dots that fills the space between a heading and its close button."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QPen(W(70), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        y = self.height() / 2
        x = 4.0
        while x < self.width():
            p.drawPoint(QPointF(x, y))
            x += 12
        p.end()


class HudStat(QWidget):
    """Icon with a large value and a small caption underneath (clock, file count)."""

    def __init__(self, icon: str):
        super().__init__()
        self.icon, self.big, self.small = icon, "", ""
        self.setFixedHeight(46)
        self.setMinimumWidth(130)

    def set_text(self, big: str, small: str):
        self.big, self.small = big, small
        self.update()

    def sizeHint(self):
        w = max(QFontMetrics(hfont(14, 1.4)).horizontalAdvance(self.big), QFontMetrics(mfont(8, 1.0)).horizontalAdvance(self.small.upper()))
        return QSize(w + 56, 46)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_icon(p, self.icon, QRectF(0, 11, 24, 24), QColor(TEXT), 1.5)
        p.setPen(QPen(W(50), 1))
        p.drawLine(QPointF(36, 6), QPointF(36, 40))
        p.setPen(QColor(TEXT))
        p.setFont(hfont(14, 1.4, QFont.Weight.Medium))
        p.drawText(QRectF(48, 0, self.width() - 48, 28), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.big)
        p.setPen(QColor(MUTED))
        p.setFont(mfont(8, 1.0))
        p.drawText(QRectF(48, 24, self.width() - 48, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.small.upper())
        p.end()


class AccountTile(QPushButton):
    """Connected-account status tile for the top bar: dot, platform name, account or state."""

    def __init__(self, name: str):
        super().__init__("")
        self.name, self.state, self.dot = name, "not connected", QColor(DIM)
        self._h = 0.0
        self.setFixedSize(176, 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_h)

    def _set_h(self, v):
        self._h = float(v)
        self.update()

    def _go(self, t):
        self._anim.stop()
        self._anim.setStartValue(self._h)
        self._anim.setEndValue(t)
        self._anim.start()

    def enterEvent(self, e):
        self._go(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._go(0.0)
        super().leaveEvent(e)

    def set_info(self, state: str, dot: QColor):
        self.state, self.dot = state, dot
        self.setText(f"{self.name}  {state}")
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        h = self._h
        p.fillRect(r, W(3 + 9 * h))
        p.setPen(QPen(mix(W(46), col(ACCENT, 230), h), 1))
        p.drawRect(r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.dot)
        p.drawRect(QRectF(0.5, 0.5, 3, r.height()))
        p.setPen(QColor(TEXT))
        p.setFont(hfont(8.8, 2.2))
        p.drawText(QRectF(16, 5, r.width() - 22, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.name.upper())
        p.setPen(QColor(ACCENT_HI) if self.dot == QColor(ACCENT) else (QColor("#f0a79f") if self.dot == QColor(ERR) else QColor(MUTED)))
        p.setFont(mfont(8.2, 0.4))
        p.drawText(QRectF(16, 23, r.width() - 22, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(mfont(8.2, 0.4)).elidedText(self.state, Qt.TextElideMode.ElideRight, int(r.width() - 26)))
        p.end()


# ----------------------------------------------------------------------------- buttons
class HudButton(G.GlassButton):
    """Rectangular thin-border button with letter-spaced caps. kinds: glass, primary, danger, ghost."""

    def __init__(self, text: str = "", icon: str | None = None, kind: str = "glass", height: int = 40,
                 dot: QColor | None = None, align_left: bool = False, caps: bool = True):
        super().__init__(text, icon, kind, height, 1, dot)
        self.align_left, self.caps = align_left, caps
        self.setFont(hfont(8.8, 2.0) if caps else mfont(9.2, 0.2))

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text().upper() if self.caps else self.text()) + 40
        if self.icon_name and self.text():
            w += 26
        elif self.icon_name:
            w = self.height()
        if self.dot is not None:
            w += 16
        return QSize(max(w, self.minimumWidth()), self.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h, ck, down = self._h, self._ck, self.isDown()
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.translate(0, 1 if down else 0)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        kind, border = self.kind, None
        if kind == "ghost":
            fill, text_col = W(13 * h), mix(QColor(MUTED), QColor(TEXT), h)
        elif kind == "primary":
            fill, border = col(ACCENT, 30 + 36 * h), col(ACCENT, 190 + 55 * h)
            text_col = mix(QColor(ACCENT_HI), QColor("#ffe2b8"), h)
        elif kind == "danger":
            fill, border = col(ERR, 18 + 26 * h), col(ERR, 120 + 90 * h)
            text_col = QColor("#f2b2aa")
        else:
            k = max(h, ck)
            fill, border = mix(W(4), col(ACCENT, 34), ck) if ck > h else W(4 + 12 * h), mix(W(72), col(ACCENT, 235), k)
            text_col = mix(QColor(TEXT), QColor("#ffffff"), k)
        p.fillRect(r, fill)
        if border is not None:
            p.setPen(QPen(border, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
        fm = QFontMetrics(self.font())
        iw = 17 if self.icon_name else 0
        dw = 14 if self.dot is not None else 0
        gap = 9 if (self.icon_name and self.text()) else 0
        shown = fm.elidedText(self.text().upper() if self.caps else self.text(), Qt.TextElideMode.ElideMiddle,
                              int(max(20, r.width() - 32 - dw - iw - gap)))
        tw = fm.horizontalAdvance(shown)
        total = dw + iw + gap + tw
        x = r.x() + 16 if self.align_left else r.x() + (r.width() - total) / 2
        cy = r.y() + r.height() / 2
        if self.dot is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.dot)
            p.drawRect(QRectF(x, cy - 3.5, 7, 7))
            x += dw
        if self.icon_name:
            draw_icon(p, self.icon_name, QRectF(x, cy - iw / 2, iw, iw), text_col, 1.5)
            x += iw + gap
        if shown:
            p.setPen(text_col)
            p.setFont(self.font())
            p.drawText(QRectF(x, r.y(), tw + 6, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, shown)
        if ck > 0.01 and kind == "glass":  # checked: amber underline
            p.fillRect(QRectF(r.x(), r.bottom() - 1.5, r.width() * ck, 2), col(ACCENT))
        p.end()


class HudIconButton(HudButton):
    def __init__(self, icon: str, size: int = 40, kind: str = "glass", tip: str = ""):
        super().__init__("", icon, kind, height=size)
        self.setFixedWidth(size)
        if tip:
            self.setToolTip(tip)


# ----------------------------------------------------------------------------- selectors
class SegGroup(G.PillGroup):
    """Rectangular joined segments; the amber highlight slides to the chosen one."""

    def __init__(self, items, height: int = 38, equal: bool = False, index: int = 0):
        super().__init__(items, height, equal, index)
        self.setFont(hfont(8.5, 1.8))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillRect(r, W(4))
        p.setPen(QPen(W(58), 1))
        p.drawRect(r)
        hl = self._rect_of(self._pos)
        p.fillRect(hl, col(ACCENT, 32))
        p.setPen(QPen(col(ACCENT, 175), 1))
        p.drawRect(hl.adjusted(0.5, 0.5, -0.5, -0.5))
        ws = self._widths()
        fm = QFontMetrics(self.font())
        p.setFont(self.font())
        x = 4.0
        for k, ((text, icon), w) in enumerate(zip(self.items, ws)):
            active = abs(self._pos - k) < 0.5
            c = mix(QColor(MUTED), QColor(TEXT), 0.7 if k == self._hover else 0.0)
            if active:
                c = QColor(ACCENT_HI)
            text = text.upper()
            tw = fm.horizontalAdvance(text)
            iw = 16 if icon else 0
            gap = 8 if (icon and text) else 0
            sx = x + (w - (iw + gap + tw)) / 2
            cy = r.height() / 2
            if icon:
                draw_icon(p, icon, QRectF(sx, cy - iw / 2, iw, iw), c, 1.5)
                sx += iw + gap
            if text:
                p.setPen(c)
                p.drawText(QRectF(sx, 0, tw + 6, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            x += w
        p.end()


class RadioGroup(QWidget):
    """Radio options (circle + caps label). Items are text or (text, note)."""
    changed = Signal(int)

    def __init__(self, items, index: int = 0, columns: int = 1, row_h: int = 32):
        super().__init__()
        self.items = [(i, "") if isinstance(i, str) else i for i in items]
        self._index, self._hover, self.columns, self.row_h = index, -1, columns, row_h
        rows = math.ceil(len(self.items) / columns)
        self.setFixedHeight(rows * row_h)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(mfont(9.2, 0.8))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def index(self) -> int:
        return self._index

    def setIndex(self, i: int, animate: bool = True, emit: bool = False):
        i = max(0, min(len(self.items) - 1, i))
        if i != self._index or emit:
            self._index = i
            self.update()
            if emit:
                self.changed.emit(i)

    def _cell(self, i: int) -> QRectF:
        col_i, row_i = i % self.columns, i // self.columns
        cw = self.width() / self.columns
        return QRectF(col_i * cw, row_i * self.row_h, cw, self.row_h)

    def _hit(self, pos) -> int:
        for i in range(len(self.items)):
            if self._cell(i).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        k = self._hit(e.position())
        if k >= 0 and k != self._index:
            self.setIndex(k, emit=True)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.setFont(self.font())
        for i, (text, note) in enumerate(self.items):
            c = self._cell(i)
            active, hov = i == self._index, i == self._hover
            ring = col(ACCENT, 255) if active else (W(190) if hov else W(110))
            cy = c.center().y()
            p.setPen(QPen(ring, 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(c.x() + 10, cy), 7.2, 7.2)
            if active:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col(ACCENT))
                p.drawEllipse(QPointF(c.x() + 10, cy), 3.8, 3.8)
            p.setPen(QColor(TEXT) if (active or hov) else QColor(MUTED))
            p.drawText(QRectF(c.x() + 28, c.y(), c.width() - 30, c.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       text.upper())
        p.end()


class SplitTabs(QWidget):
    """Two tabs on opposite ends of one row; an amber underline glides between them."""
    changed = Signal(int)

    def __init__(self, left: str, right: str, height: int = 46):
        super().__init__()
        self.labels = [left, right]
        self._index, self._hover, self._pos = 0, -1, 0.0
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(hfont(10.5, 3.0))
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._slide)

    def set_labels(self, left: str, right: str):
        self.labels = [left, right]
        self.update()

    def index(self) -> int:
        return self._index

    def _rects(self):
        fm = QFontMetrics(self.font())
        ws = [fm.horizontalAdvance(t.upper()) + 36 for t in self.labels]
        return [QRectF(0, 0, ws[0], self.height()), QRectF(self.width() - ws[1], 0, ws[1], self.height())]

    def setIndex(self, i: int, animate: bool = True):
        if i == self._index:
            return
        self._index = i
        self._anim.stop()
        if animate and self.isVisible():
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(float(i))
            self._anim.start()
        else:
            self._pos = float(i)
            self.update()

    def _slide(self, v):
        self._pos = float(v)
        self.update()

    def mouseMoveEvent(self, e):
        rs = self._rects()
        h = 0 if rs[0].contains(e.position()) else (1 if rs[1].contains(e.position()) else -1)
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        rs = self._rects()
        for k in (0, 1):
            if rs[k].contains(e.position()) and k != self._index:
                self.setIndex(k, True)
                self.changed.emit(k)

    def paintEvent(self, e):
        p = QPainter(self)
        rs = self._rects()
        t = self._pos
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), W(34))
        x0 = rs[0].x() + (rs[1].x() - rs[0].x()) * t
        w0 = rs[0].width() + (rs[1].width() - rs[0].width()) * t
        p.fillRect(QRectF(x0, 0, w0, self.height() - 2), col(ACCENT, 16))
        p.fillRect(QRectF(x0, self.height() - 3, w0, 3), col(ACCENT))
        p.setFont(self.font())
        for k in (0, 1):
            active = abs(t - k) < 0.5
            c = QColor(ACCENT_HI) if active else mix(QColor(MUTED), QColor(TEXT), 0.7 if k == self._hover else 0.0)
            p.setPen(c)
            p.drawText(rs[k], Qt.AlignmentFlag.AlignCenter, self.labels[k].upper())
        p.end()


class NavMenu(QWidget):
    """Left menu: caps labels, hairline separators, selected row with an amber bar that slides."""
    activated = Signal(str)

    def __init__(self, items, row_h: int = 54):
        super().__init__()
        self.items = items  # [(key, label)]
        self.counts: dict[str, int] = {}
        self.row_h = row_h
        self._sel, self._pos, self._hover = 0, 0.0, -1
        self.setFixedHeight(row_h * len(items))
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._slide)

    def _slide(self, v):
        self._pos = float(v)
        self.update()

    def set_selected(self, key: str, animate: bool = True):
        idx = next((i for i, (k, _) in enumerate(self.items) if k == key), self._sel)
        if idx == self._sel:
            return
        self._sel = idx
        self._anim.stop()
        if animate and self.isVisible():
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(float(idx))
            self._anim.start()
        else:
            self._pos = float(idx)
            self.update()

    def set_count(self, key: str, n: int):
        self.counts[key] = n
        self.update()

    def _row_at(self, y: float) -> int:
        i = int(y // self.row_h)
        return i if 0 <= i < len(self.items) else -1

    def mouseMoveEvent(self, e):
        h = self._row_at(e.position().y())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        i = self._row_at(e.position().y())
        if i >= 0:
            self.activated.emit(self.items[i][0])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rh, w = self.row_h, self.width()
        y_hl = self._pos * rh
        hl = QRectF(0, y_hl + 2, w, rh - 8)
        g = QLinearGradient(hl.topLeft(), hl.topRight())
        g.setColorAt(0, W(26))
        g.setColorAt(1, W(8))
        p.fillRect(hl, g)
        p.fillRect(QRectF(0, hl.y(), 4, hl.height()), col(ACCENT))
        for i, (key, label) in enumerate(self.items):
            y = i * rh
            selected = abs(self._pos - i) < 0.5
            if i == self._hover and not selected:
                p.fillRect(QRectF(0, y + 2, w, rh - 8), W(7))
            if not selected:
                p.fillRect(QRectF(0, y + rh - 4, w, 1), W(30))
            p.setFont(hfont(10.5, 3.2))
            p.setPen(QColor("#ffffff") if selected else (QColor(TEXT) if i == self._hover else QColor(MUTED)))
            p.drawText(QRectF(28, y, w - 90, rh - 6), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label.upper())
            n = self.counts.get(key)
            if n is not None:
                p.setFont(mfont(9.5, 0.5))
                p.setPen(QColor(ACCENT_HI) if selected else QColor(DIM))
                p.drawText(QRectF(w - 70, y, 56, rh - 6), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{n:02d}")
        p.end()


class HudField(G.GlassField):
    """Input frame: thin border that turns amber (with a left bar) while focused."""

    def __init__(self):
        super().__init__(radius=1, shadow=False)

    def paintEvent(self, e):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        f = self._focus
        p.fillRect(r, W(4 + 5 * f))
        p.setPen(QPen(mix(W(60), col(ACCENT, 235), f), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        if f > 0.01:
            p.fillRect(QRectF(0.5, 0.5, 3, r.height() * f), col(ACCENT))
        p.end()


class ProgressStrip(QWidget):
    """Segmented terminal-style progress bar; an idle segment sweeps across until real progress arrives."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._shown, self.mode, self._phase = 0.0, "idle", 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set)
        self._timer = QTimer(self, interval=45)
        self._timer.timeout.connect(self._tick)

    def _set(self, v):
        self._shown = float(v)
        self.update()

    def _tick(self):
        self._phase += 0.55
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def _to(self, v):
        self._anim.stop()
        self._anim.setStartValue(self._shown)
        self._anim.setEndValue(v)
        self._anim.start()

    def set_idle(self):
        self.mode = "idle"
        self._to(0.0)

    def set_progress(self, frac: float):
        self.mode = "busy"
        self._to(max(0.0, min(1.0, frac)))

    def set_done(self):
        self.mode = "done"
        self._to(1.0)

    def set_error(self):
        self.mode = "error"
        self._to(self._shown)

    def paintEvent(self, e):
        p = QPainter(self)
        seg, gap = 5, 4
        n = max(8, int((self.width() + gap) / (seg + gap)))
        filled = self._shown * n
        c_fill = col(ERR, 230) if self.mode == "error" else col(ACCENT, 235)
        sweep = int(self._phase) % (n + 6) - 3 if (self.mode == "idle" or (self.mode == "busy" and self._shown < 0.02)) else -99
        y, hgt = (self.height() - 14) / 2, 14
        for i in range(n):
            x = i * (seg + gap)
            f = max(0.0, min(1.0, filled - i))
            if f >= 0.5 or (self.mode == "error" and i < max(1, int(filled))):
                p.fillRect(QRectF(x, y, seg, hgt), c_fill)
            else:
                a = 24
                if sweep > -50:
                    d = abs(i - sweep)
                    a = int(24 + max(0, 60 - 22 * d))
                p.fillRect(QRectF(x, y, seg, hgt), W(a))
        p.end()


class TermLog(QPlainTextEdit):
    """Read-only terminal output: dim timestamp, coloured level tag, monospace message."""

    LEVELS = {"cmd": (">", ACCENT), "ok": ("ok", ACCENT_HI), "info": ("..", MUTED), "err": ("err", ERR), "warn": ("!!", ACCENT)}

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumBlockCount(400)
        self.setFont(mfont(9.2, 0.2))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(2)

    def log(self, level: str, text: str):
        tag, c = self.LEVELS.get(level, self.LEVELS["info"])
        ts = time.strftime("%H:%M:%S")
        body = escape(text)
        tc = TEXT if level == "cmd" else (ERR if level == "err" else MUTED if level == "info" else TEXT)
        label = f"[{tag}]" if level != "cmd" else tag
        self.appendHtml(f"<span style='color:{DIM}'>{ts}</span>&nbsp; <span style='color:{c}'>{label}</span>&nbsp; "
                        f"<span style='color:{tc}'>{body}</span>")
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class HudToast(G.Toast):
    def popup(self, text: str, error: bool = False):
        super().popup(text, error)
        fm = QFontMetrics(mfont(9.5, 0.3))
        self.resize(fm.horizontalAdvance(text) + 70, 44)
        pw = self.parentWidget()
        self.move((pw.width() - self.width()) // 2, pw.height() - self.height() - 52)

    def paintEvent(self, e):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        c = col(ERR if self.err else ACCENT)
        p.fillRect(r, QColor(5, 5, 6, 240))
        p.setPen(QPen(col(ERR if self.err else ACCENT, 190), 1))
        p.drawRect(r)
        p.fillRect(QRectF(0.5, 0.5, 4, r.height()), c)
        draw_icon(p, "alert" if self.err else "check", QRectF(18, r.height() / 2 - 8, 16, 16), c, 1.8)
        p.setPen(QColor(TEXT))
        p.setFont(mfont(9.5, 0.3))
        p.drawText(QRectF(44, 0, r.width() - 52, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)
        p.end()


class HudSkeleton(QWidget):
    """Loading placeholder: outlined blocks shaped like the preview with a scan line sweeping across."""

    def __init__(self):
        super().__init__()
        self.phase = 0.0
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self._step)

    def showEvent(self, e):
        self.timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def _step(self):
        self.phase = (self.phase + 0.012) % 1.3
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        shapes = [QRectF(0, 0, 240, 136)]
        x0 = 264
        for i, frac in enumerate((0.30, 0.74, 0.55, 0.40)):
            shapes.append(QRectF(x0, 6 + i * 32, max(40, (w - x0) * frac), 14))
        cw = (w - 3 * 12) / 4
        for i in range(4):
            shapes.append(QRectF(i * (cw + 12), 156, cw, 50))
        scan = -60 + self.phase * (w + 120)
        grad = QLinearGradient(scan, 0, scan + 60, 0)
        grad.setColorAt(0, col(ACCENT, 0))
        grad.setColorAt(0.5, col(ACCENT, 46))
        grad.setColorAt(1, col(ACCENT, 0))
        for rect in shapes:
            p.fillRect(rect, W(9))
            p.setPen(QPen(W(26), 1))
            p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            p.fillRect(rect, grad)
        p.setPen(QColor(MUTED))
        p.setFont(mfont(9, 0.4))
        p.drawText(QPointF(x0, 6 + 4 * 32 + 16), "reading the post ...")
        p.end()


# ----------------------------------------------------------------------------- app icon
def icon_pixmap(size: int) -> QPixmap:
    """App icon: a dark square with amber corner brackets and a download arrow."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(0.5, 0.5, size - 1, size - 1)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor("#17120a"))
    g.setColorAt(1, QColor("#08090b"))
    p.fillRect(r, g)
    p.setPen(QPen(W(40), max(1.0, size * 0.02)))
    p.drawRect(r)
    n = size * 0.26
    p.setPen(QPen(col(ACCENT), max(1.6, size * 0.055), Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
    p.drawPolyline([QPointF(r.left(), r.top() + n), r.topLeft(), QPointF(r.left() + n, r.top())])
    p.drawPolyline([QPointF(r.right() - n, r.bottom()), r.bottomRight(), QPointF(r.right(), r.bottom() - n)])
    draw_icon(p, "download", QRectF(size * 0.27, size * 0.25, size * 0.46, size * 0.46), QColor(TEXT), max(1.6, size * 0.06))
    p.end()
    return pm


def app_icon():
    from PySide6.QtGui import QIcon
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(icon_pixmap(s))
    return icon


# ----------------------------------------------------------------------------- theme
def _chevron_path() -> str:
    d = Path(os.getenv("APPDATA") or Path.home()) / "VideoDownloader" / "assets"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "chevron.png"
    pm = QPixmap(20, 12)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(MUTED), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPolyline([QPointF(4, 3), QPointF(10, 9), QPointF(16, 3)])
    p.end()
    pm.save(str(f), "PNG")
    return f.as_posix()


def stylesheet() -> str:
    chev = _chevron_path()
    return f"""
* {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#error {{ color: {ERR}; }}
QLabel#section {{ color: {MUTED}; border-left: 2px solid rgba(255,255,255,0.28); padding-left: 10px; }}
QLineEdit {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.24); border-radius: 0px;
            padding: 0 12px; min-height: 36px; selection-background-color: {ACCENT}; selection-color: {BG}; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit#bare {{ background: transparent; border: none; padding: 0 4px; min-height: 0; }}
QPushButton {{ background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.28); border-radius: 0px;
              padding: 0 18px; min-height: 36px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {DIM}; border-color: rgba(255,255,255,0.10); }}
QComboBox {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.28); border-radius: 0px;
            padding: 0 12px; min-height: 36px; font-family: '{MONO}'; }}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: #6f7681; border-color: rgba(255,255,255,0.12); }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: url({chev}); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{ background: #0b0c0e; border: 1px solid rgba(255,255,255,0.28); border-radius: 0px;
            selection-background-color: rgba(224,149,58,0.30); selection-color: #ffffff; outline: 0; padding: 2px; }}
QSlider::groove:horizontal {{ height: 2px; background: rgba(255,255,255,0.20); }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 10px; height: 14px; margin: -6px 0; border-radius: 0px; }}
QListWidget {{ background: transparent; outline: 0; border: none; }}
QListWidget::item {{ background: transparent; }}
QPlainTextEdit {{ background: transparent; border: none; color: {TEXT}; selection-background-color: {ACCENT}; selection-color: {BG}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.22); min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu {{ background: #0b0c0e; border: 1px solid rgba(255,255,255,0.28); padding: 4px; }}
QMenu::item {{ padding: 8px 22px; }}
QMenu::item:selected {{ background: rgba(224,149,58,0.30); }}
QMenu::separator {{ height: 1px; background: rgba(255,255,255,0.14); margin: 5px 8px; }}
QMessageBox {{ background: #0a0b0d; }}
QToolTip {{ background: #0b0c0e; color: {TEXT}; border: 1px solid {ACCENT}; padding: 5px 8px; }}
"""


def apply_theme(app) -> None:
    pick_fonts()
    G.pick_font()
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Base, "#0b0c0e"),
                    (QPalette.ColorRole.AlternateBase, "#0b0c0e"), (QPalette.ColorRole.Text, TEXT),
                    (QPalette.ColorRole.Button, "#15171a"), (QPalette.ColorRole.ButtonText, TEXT),
                    (QPalette.ColorRole.Highlight, ACCENT), (QPalette.ColorRole.HighlightedText, BG),
                    (QPalette.ColorRole.ToolTipBase, "#0b0c0e"), (QPalette.ColorRole.ToolTipText, TEXT),
                    (QPalette.ColorRole.PlaceholderText, DIM)):
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    app.setFont(bfont(10))
    app.setStyleSheet(stylesheet())
