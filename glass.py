"""Glassmorphism design system for the Video Downloader.

Atmosphere: a near-black canvas (never pure black) lit by a few very soft cool glows, with frosted
translucent panels floating above it. One accent colour (a desaturated sky blue). Edges are a 1px
bright-to-dim gradient plus an inner highlight line, like light catching the rim of real glass.
Motion is limited to short eased transitions (hover, selection slide, dial) and a gentle idle pulse.
"""

import ctypes
import math
import random

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation,
                            Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QPalette)
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsOpacityEffect, QLabel, QLineEdit, QPushButton, QSizePolicy,
                               QWidget)

# ----------------------------------------------------------------------------- tokens
BG = "#0a0b0d"        # off-black canvas
TEXT = "#f2f4f7"      # primary text
MUTED = "#9ba2ae"     # secondary text
DIM = "#5e6570"       # tertiary text / disabled
ACCENT = "#7db4f0"    # the single accent: soft sky blue (saturation < 80%)
ERR = "#e88a8a"       # desaturated rose, errors only
FONT_FAMILY = "Segoe UI"

# very soft ambient lights the glass sits on: (x, y, radius, colour, alpha). All one cool-neutral family.
GLOWS = [(0.16, 0.10, 0.60, "#8aa3c4", 40), (0.88, 0.22, 0.50, "#9fb0c8", 30), (0.50, 1.00, 0.70, "#6c84a6", 26)]


def pick_font() -> str:
    global FONT_FAMILY
    fams = set(QFontDatabase.families())
    for name in ("Segoe UI Variable Text", "Segoe UI Variable Display", "Segoe UI", "Arial"):
        if name in fams:
            FONT_FAMILY = name
            break
    return FONT_FAMILY


def font(pt: float = 10, weight=QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    return f


def W(a: float) -> QColor:
    return QColor(255, 255, 255, max(0, min(255, int(a))))


def col(hexstr: str, a: int = 255) -> QColor:
    c = QColor(hexstr)
    c.setAlpha(max(0, min(255, int(a))))
    return c


def mix(c1: QColor, c2: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(c1.red() + (c2.red() - c1.red()) * t), int(c1.green() + (c2.green() - c1.green()) * t),
                  int(c1.blue() + (c2.blue() - c1.blue()) * t), int(c1.alpha() + (c2.alpha() - c1.alpha()) * t))


# ----------------------------------------------------------------------------- noise (fine frost grain)
def _make_noise() -> QPixmap:
    rnd = random.Random(7)
    img = QImage(96, 96, QImage.Format.Format_ARGB32)
    for y in range(96):
        for x in range(96):
            v = rnd.randint(0, 255)
            img.setPixelColor(x, y, QColor(v, v, v, rnd.randint(0, 11)))
    return QPixmap.fromImage(img)


_NOISE = None


def noise() -> QPixmap:
    global _NOISE
    if _NOISE is None:
        _NOISE = _make_noise()
    return _NOISE


# ----------------------------------------------------------------------------- glass painting
def paint_glass(p: QPainter, rect: QRectF, radius: float, strength: float = 1.0, lift: float = 0.0,
                grain: bool = False, tint: QColor | None = None) -> None:
    """Frosted-glass surface: translucent gradient fill, soft top light, 1px rim, inner refraction line."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0.0, W((32 + 22 * lift) * strength))
    g.setColorAt(1.0, W((14 + 12 * lift) * strength))
    p.fillPath(path, g)
    if tint is not None:
        p.fillPath(path, tint)
    bloom = QRadialGradient(QPointF(rect.left() + rect.width() * 0.2, rect.top()), max(rect.width(), rect.height()) * 0.8)
    bloom.setColorAt(0, W(24 * strength))
    bloom.setColorAt(1, W(0))
    p.fillPath(path, bloom)
    if grain:
        p.save()
        p.setClipPath(path)
        p.setOpacity(0.7)
        p.drawTiledPixmap(rect.toRect(), noise())
        p.restore()
    rim = QPainterPath()
    rim.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
    bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bg.setColorAt(0.0, W(86 + 30 * lift))
    bg.setColorAt(0.45, W(32 + 16 * lift))
    bg.setColorAt(1.0, W(18 + 10 * lift))
    p.setPen(QPen(QBrush(bg), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(rim)
    inner = QPainterPath()
    inner.addRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), max(1, radius - 1.5), max(1, radius - 1.5))
    ig = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.55))
    ig.setColorAt(0, W(34 * strength))
    ig.setColorAt(1, W(0))
    p.setPen(QPen(QBrush(ig), 1))
    p.drawPath(inner)
    p.restore()


def paint_backdrop(p: QPainter, widget: QWidget, panel_rects, washes) -> None:
    """Canvas: off-black, ambient glows, optional blurred video wash, and soft shadows under each panel."""
    r = widget.rect()
    w, h = r.width(), r.height()
    p.fillRect(r, QColor(BG))
    for pix, op in washes:
        if pix is not None and op > 0.01:
            p.save()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.setOpacity(op * 0.2)
            p.drawPixmap(r, pix)  # a tiny pixmap stretched over the window is a very heavy blur
            p.restore()
    if any(pix is not None and op > 0.01 for pix, op in washes):
        vg = QRadialGradient(w * 0.5, h * 0.42, max(w, h) * 0.78)
        vg.setColorAt(0, col(BG, 0))
        vg.setColorAt(1, col(BG, 245))
        p.fillRect(r, vg)
    for cx, cy, rad, c, a in GLOWS:
        g = QRadialGradient(w * cx, h * cy, max(w, h) * rad)
        g.setColorAt(0, col(c, a))
        g.setColorAt(1, col(c, 0))
        p.fillRect(r, g)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    for rf, rad in panel_rects:
        p.save()
        clip = QPainterPath()
        clip.addRect(QRectF(r))
        hole = QPainterPath()
        hole.addRoundedRect(rf, rad, rad)
        p.setClipPath(clip.subtracted(hole))  # shadow only outside the glass so the panel stays clear
        for i in range(9):
            grow = i * 2.4
            a = 11 * (1 - i / 9) ** 1.7
            p.setBrush(QColor(2, 3, 6, int(a)))
            p.drawRoundedRect(rf.adjusted(-grow, -grow + 9, grow, grow + 9), rad + grow, rad + grow)
        p.restore()


def make_wash(data: bytes | None) -> QPixmap | None:
    """Turn a video thumbnail into a tiny, slightly desaturated image used as a blurred ambient wash."""
    if not data:
        return None
    img = QImage.fromData(data)
    if img.isNull():
        return None
    img = img.scaled(28, 16, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            gray = int(0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue())
            img.setPixelColor(x, y, QColor(int(c.red() * 0.55 + gray * 0.45), int(c.green() * 0.55 + gray * 0.45),
                                           int(c.blue() * 0.55 + gray * 0.45)))
    return QPixmap.fromImage(img)


class BackdropMixin:
    """Shared canvas behaviour for the main window and dialogs."""

    def _bd_init(self):
        self._bd_panels: list = []
        self._wash = None
        self._wash_old = None
        self._wash_t = 1.0
        self._wash_anim = QVariantAnimation(self)
        self._wash_anim.setDuration(700)
        self._wash_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._wash_anim.valueChanged.connect(self._wash_step)

    def register_panel(self, panel):
        if panel not in self._bd_panels:
            self._bd_panels.append(panel)

    def unregister_panel(self, panel):
        if panel in self._bd_panels:
            self._bd_panels.remove(panel)

    def set_wash(self, pix):
        self._wash_old = self._wash
        self._wash = pix
        self._wash_anim.stop()
        self._wash_anim.setStartValue(0.0)
        self._wash_anim.setEndValue(1.0)
        self._wash_anim.start()

    def _wash_step(self, v):
        self._wash_t = float(v)
        self.update()

    def _bd_paint(self):
        p = QPainter(self)
        rects = []
        for pn in self._bd_panels:
            try:
                if pn.isVisible():
                    rects.append((QRectF(pn.mapTo(self, QPoint(0, 0)), QRectF(pn.rect()).size()), pn.radius))
            except RuntimeError:
                pass
        paint_backdrop(p, self, rects, [(self._wash_old, 1 - self._wash_t), (self._wash, self._wash_t)])
        p.end()


class Backdrop(BackdropMixin, QWidget):
    def __init__(self):
        super().__init__()
        self._bd_init()

    def paintEvent(self, e):
        self._bd_paint()


class GlassDialog(BackdropMixin, QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._bd_init()

    def paintEvent(self, e):
        self._bd_paint()

    def showEvent(self, e):
        super().showEvent(e)
        style_titlebar(self)


def _find_backdrop(w: QWidget):
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, BackdropMixin):
            return p
        p = p.parentWidget()
    return None


class GlassPanel(QFrame):
    """A frosted-glass container. Children should keep transparent backgrounds."""

    def __init__(self, radius: float = 26, strength: float = 1.0, shadow: bool = True, grain: bool = True):
        super().__init__()
        self.radius, self.strength, self.shadow, self.grain = radius, strength, shadow, grain
        self._bd = None

    def showEvent(self, e):
        super().showEvent(e)
        if self.shadow and self._bd is None:
            self._bd = _find_backdrop(self)
        if self._bd is not None:
            self._bd.register_panel(self)
            self._bd.update()

    def hideEvent(self, e):
        super().hideEvent(e)
        if self._bd is not None:
            self._bd.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._bd is not None:
            self._bd.update()

    def moveEvent(self, e):
        super().moveEvent(e)
        if self._bd is not None:
            self._bd.update()

    def paintEvent(self, e):
        p = QPainter(self)
        paint_glass(p, QRectF(self.rect()), self.radius, self.strength, grain=self.grain)
        p.end()


# ----------------------------------------------------------------------------- icons (drawn, no emoji)
def _poly(path: QPainterPath, pts, close=False):
    path.moveTo(*pts[0])
    for pt in pts[1:]:
        path.lineTo(*pt)
    if close:
        path.closeSubpath()


def _icon_path(name: str) -> QPainterPath:
    p = QPainterPath()
    if name == "download":
        _poly(p, [(12, 4), (12, 15)]); _poly(p, [(7.5, 10.5), (12, 15), (16.5, 10.5)]); _poly(p, [(5, 19.5), (19, 19.5)])
    elif name == "paste":
        p.addRoundedRect(QRectF(6, 5.5, 12, 15), 2.5, 2.5); p.addRoundedRect(QRectF(9, 3.5, 6, 4), 1.5, 1.5)
    elif name == "close":
        _poly(p, [(6, 6), (18, 18)]); _poly(p, [(18, 6), (6, 18)])
    elif name == "grid":
        for x, y in ((4, 4), (13, 4), (4, 13), (13, 13)):
            p.addRoundedRect(QRectF(x, y, 7, 7), 2, 2)
    elif name == "list":
        for y in (7, 12, 17):
            _poly(p, [(4, y), (6, y)]); _poly(p, [(9.5, y), (20, y)])
    elif name == "folder":
        _poly(p, [(3.5, 6.5), (9.5, 6.5), (11.5, 9), (20.5, 9), (20.5, 18.5), (3.5, 18.5)], True)
    elif name == "user":
        p.addEllipse(QRectF(8, 4, 8, 8)); p.moveTo(4.5, 20); p.cubicTo(4.5, 15.5, 8, 14, 12, 14); p.cubicTo(16, 14, 19.5, 15.5, 19.5, 20)
    elif name in ("eye", "eye-off"):
        p.moveTo(2.5, 12); p.cubicTo(5, 7.5, 8.5, 6, 12, 6); p.cubicTo(15.5, 6, 19, 7.5, 21.5, 12)
        p.cubicTo(19, 16.5, 15.5, 18, 12, 18); p.cubicTo(8.5, 18, 5, 16.5, 2.5, 12); p.closeSubpath()
        p.addEllipse(QRectF(9.2, 9.2, 5.6, 5.6))
        if name == "eye-off":
            _poly(p, [(4, 4), (20, 20)])
    elif name == "play":
        _poly(p, [(8, 5.5), (18.5, 12), (8, 18.5)], True)
    elif name == "pause":
        _poly(p, [(9, 6), (9, 18)]); _poly(p, [(15, 6), (15, 18)])
    elif name == "trash":
        _poly(p, [(5, 7), (19, 7)]); _poly(p, [(9, 7), (9, 5), (15, 5), (15, 7)])
        _poly(p, [(6.5, 7), (7.5, 19), (16.5, 19), (17.5, 7)]); _poly(p, [(10.5, 10.5), (10.5, 16)]); _poly(p, [(13.5, 10.5), (13.5, 16)])
    elif name == "copy":
        p.addRoundedRect(QRectF(8.5, 8.5, 11, 11), 2.5, 2.5); p.moveTo(5.5, 15.5); p.lineTo(5.5, 7.5)
        p.quadTo(5.5, 5.5, 7.5, 5.5); p.lineTo(15.5, 5.5)
    elif name == "film":
        p.addRoundedRect(QRectF(3.5, 5, 17, 14), 3.5, 3.5); _poly(p, [(10, 9.5), (15, 12), (10, 14.5)], True)
    elif name == "search":
        p.addEllipse(QRectF(4.5, 4.5, 11, 11)); _poly(p, [(14, 14), (19.5, 19.5)])
    elif name == "check":
        _poly(p, [(5, 12.5), (10, 17), (19, 7.5)])
    elif name == "alert":
        p.addEllipse(QRectF(4, 4, 16, 16)); _poly(p, [(12, 8), (12, 13)]); _poly(p, [(12, 16), (12, 16.2)])
    elif name == "external":
        _poly(p, [(14, 4.5), (19.5, 4.5), (19.5, 10)]); _poly(p, [(19.5, 4.5), (11, 13)])
        _poly(p, [(17.5, 14), (17.5, 19), (5, 19), (5, 6.5), (10, 6.5)])
    elif name == "prev":
        _poly(p, [(14.5, 6), (8.5, 12), (14.5, 18)])
    elif name == "next":
        _poly(p, [(9.5, 6), (15.5, 12), (9.5, 18)])
    elif name == "link":
        p.addRoundedRect(QRectF(3.5, 9, 9.5, 6), 3, 3); p.addRoundedRect(QRectF(11, 9, 9.5, 6), 3, 3)
    return p


def draw_icon(p: QPainter, name: str, rect: QRectF, color: QColor, stroke: float = 1.7) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.x(), rect.y())
    s = rect.width() / 24.0
    p.scale(s, s)
    pen = QPen(color, stroke / s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(_icon_path(name))
    p.restore()


def icon_pixmap_named(name: str, size: int, color: str = TEXT) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw_icon(p, name, QRectF(1, 1, size - 2, size - 2), QColor(color))
    p.end()
    return pm


def icon_pixmap(size: int) -> QPixmap:
    """App icon: a glass tile with a download arrow."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(0, 0, size, size)
    bgp = QPainterPath()
    bgp.addRoundedRect(r, size * 0.26, size * 0.26)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor("#2a3342"))
    g.setColorAt(1, QColor("#10141b"))
    p.fillPath(bgp, g)
    paint_glass(p, r.adjusted(0, 0, 0, 0), size * 0.26, 0.9)
    draw_icon(p, "download", QRectF(size * 0.22, size * 0.2, size * 0.56, size * 0.56), QColor(ACCENT), max(1.4, size * 0.07))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(icon_pixmap(s))
    return icon


# ----------------------------------------------------------------------------- buttons
class GlassButton(QPushButton):
    """Glass button with an eased hover glow, tactile press, optional icon and status dot.

    kinds: glass (default), primary (accent-tinted glass), danger, ghost.
    """

    def __init__(self, text: str = "", icon: str | None = None, kind: str = "glass", height: int = 40,
                 radius: float = 14, dot: QColor | None = None, pt: float = 9.5):
        super().__init__(text)
        self.icon_name, self.kind, self.radius, self.dot = icon, kind, radius, dot
        self._h = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(height)
        self.setFont(font(pt, QFont.Weight.DemiBold))
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_h)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._ck = 0.0
        self._ck_anim = QVariantAnimation(self)
        self._ck_anim.setDuration(240)
        self._ck_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._ck_anim.valueChanged.connect(self._set_ck)
        self.toggled.connect(self._on_toggled)

    def _set_ck(self, v):
        self._ck = float(v)
        self.update()

    def _on_toggled(self, on: bool):
        self._ck_anim.stop()
        self._ck_anim.setStartValue(self._ck)
        self._ck_anim.setEndValue(1.0 if on else 0.0)
        self._ck_anim.start()

    def setChecked(self, on: bool):  # keep programmatic changes smooth too
        super().setChecked(on)
        if not self.isVisible():
            self._ck = 1.0 if on else 0.0

    def _set_h(self, v):
        self._h = float(v)
        self.update()

    def _go(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._h)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._go(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._go(0.0)
        super().leaveEvent(e)

    def set_dot(self, color: QColor | None):
        self.dot = color
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text()) + 36
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
        h, down, enabled = self._h, self.isDown(), self.isEnabled()
        if not enabled:
            p.setOpacity(0.42)
        p.translate(0, 1 if down else 0)
        r = QRectF(self.rect()).adjusted(0, 0, 0, -1 if down else 0)
        kind = self.kind
        ck = self._ck
        text_col = QColor(TEXT)
        if kind == "ghost":
            if h > 0.01:
                path = QPainterPath()
                path.addRoundedRect(r, self.radius, self.radius)
                p.fillPath(path, W(12 * h))
            text_col = mix(QColor(MUTED), QColor(TEXT), h)
        elif kind == "primary":
            paint_glass(p, r, self.radius, 0.9, h, tint=col(ACCENT, 62 + 40 * h))
            edge = QPainterPath()
            edge.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)
            p.setPen(QPen(col(ACCENT, 140 + 60 * h), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(edge)
            text_col = QColor("#ffffff")
        elif kind == "danger":
            paint_glass(p, r, self.radius, 0.8, h, tint=col(ERR, 30 + 34 * h))
            edge = QPainterPath()
            edge.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)
            p.setPen(QPen(col(ERR, 90 + 60 * h), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(edge)
            text_col = QColor("#ffd9d9")
        else:
            paint_glass(p, r, self.radius, 0.8, min(1.0, h * 0.8 + ck * 0.5), tint=col(ACCENT, 58 * ck) if ck > 0.01 else None)
            if ck > 0.01:
                edge = QPainterPath()
                edge.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)
                p.setPen(QPen(col(ACCENT, int(150 * ck)), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(edge)
            text_col = mix(QColor("#dfe3ea"), QColor("#ffffff"), max(h, ck))
        # content: [dot] [icon] text, centred; long text is elided in the middle
        fm = QFontMetrics(self.font())
        iw = 17 if self.icon_name else 0
        dw = 14 if self.dot is not None else 0
        gap = 8 if (self.icon_name and self.text()) else 0
        shown = fm.elidedText(self.text(), Qt.TextElideMode.ElideMiddle, int(max(20, r.width() - 28 - dw - iw - gap)))
        tw = fm.horizontalAdvance(shown)
        x = r.x() + (r.width() - (dw + iw + gap + tw)) / 2
        cy = r.y() + r.height() / 2
        if self.dot is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.dot)
            p.drawEllipse(QPointF(x + 4, cy), 3.6, 3.6)
            x += dw
        if self.icon_name:
            draw_icon(p, self.icon_name, QRectF(x, cy - iw / 2, iw, iw), text_col, 1.6)
            x += iw + gap
        if shown:
            p.setPen(text_col)
            p.setFont(self.font())
            p.drawText(QRectF(x, r.y(), tw + 4, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, shown)
        p.end()


class IconButton(GlassButton):
    def __init__(self, icon: str, size: int = 42, kind: str = "glass", tip: str = ""):
        super().__init__("", icon, kind, height=size, radius=size / 2)
        self.setFixedWidth(size)
        if tip:
            self.setToolTip(tip)


# ----------------------------------------------------------------------------- pill selectors with a sliding highlight
class PillGroup(QWidget):
    """Capsule of options. The highlight slides to the chosen option (eased), hover brightens text."""
    changed = Signal(int)

    def __init__(self, items, height: int = 40, equal: bool = False, index: int = 0):
        super().__init__()
        self.items = [(i, None) if isinstance(i, str) else i for i in items]  # (text, icon)
        self.equal, self._index, self._hover = equal, index, -1
        self._pos = float(index)
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._slide)
        self.setFont(font(9.5, QFont.Weight.DemiBold))
        self.setSizePolicy(QSizePolicy.Policy.Expanding if equal else QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _widths(self):
        fm = QFontMetrics(self.font())
        n = max(1, len(self.items))
        if self.equal:
            return [(self.width() - 8) / n] * len(self.items)
        ws = []
        for text, icon in self.items:
            if icon and not text:
                ws.append(self.height() + 6)
            else:
                ws.append(fm.horizontalAdvance(text) + 30 + (22 if icon else 0))
        return ws

    def sizeHint(self):
        if self.equal:
            return QSize(260, self.height())
        return QSize(int(sum(self._widths())) + 8, self.height())

    def minimumSizeHint(self):
        return QSize(180, self.height()) if self.equal else self.sizeHint()

    def _rect_of(self, pos: float) -> QRectF:
        pad, ws = 4.0, self._widths()
        n = len(ws)
        xs = [pad + sum(ws[:k]) for k in range(n)]
        lo = max(0, min(n - 1, int(math.floor(pos))))
        hi = min(n - 1, lo + 1)
        t = pos - lo
        return QRectF(xs[lo] + (xs[hi] - xs[lo]) * t, pad, ws[lo] + (ws[hi] - ws[lo]) * t, self.height() - 2 * pad)

    def index(self) -> int:
        return self._index

    def setIndex(self, i: int, animate: bool = True, emit: bool = False):
        i = max(0, min(len(self.items) - 1, i))
        if i == self._index and not emit:
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
        if emit:
            self.changed.emit(i)

    def _slide(self, v):
        self._pos = float(v)
        self.update()

    def _hit(self, x: float) -> int:
        acc = 4.0
        for k, w in enumerate(self._widths()):
            if acc <= x < acc + w:
                return k
            acc += w
        return -1

    def mouseMoveEvent(self, e):
        h = self._hit(e.position().x())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        k = self._hit(e.position().x())
        if k >= 0 and k != self._index:
            self.setIndex(k, True)
            self.changed.emit(k)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        r = QRectF(self.rect())
        paint_glass(p, r, r.height() / 2, 0.55)
        hl = self._rect_of(self._pos)
        paint_glass(p, hl, hl.height() / 2, 1.1, 0.5, tint=col(ACCENT, 46))
        ws = self._widths()
        fm = QFontMetrics(self.font())
        p.setFont(self.font())
        x = 4.0
        for k, ((text, icon), w) in enumerate(zip(self.items, ws)):
            active = (abs(self._pos - k) < 0.5)
            c = mix(QColor(MUTED), QColor(TEXT), 1.0 if active else (0.55 if k == self._hover else 0.0))
            tw = fm.horizontalAdvance(text)
            iw = 16 if icon else 0
            gap = 7 if (icon and text) else 0
            sx = x + (w - (iw + gap + tw)) / 2
            cy = r.height() / 2
            if icon:
                draw_icon(p, icon, QRectF(sx, cy - iw / 2, iw, iw), c, 1.6)
                sx += iw + gap
            if text:
                p.setPen(c)
                p.drawText(QRectF(sx, 0, tw + 4, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            x += w
        p.end()


# ----------------------------------------------------------------------------- download dial
class Dial(QWidget):
    """Circular progress dial: big light numeral in the middle, a knob riding the arc."""

    def __init__(self, size: int = 164):
        super().__init__()
        self.setFixedSize(size, size)
        self._shown = 0.0
        self.big, self.sub, self.mode = "--", "", "idle"
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(340)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_shown)
        self._timer = QTimer(self, interval=50)
        self._timer.timeout.connect(self._tick)

    def _set_shown(self, v):
        self._shown = float(v)
        self.update()

    def _tick(self):
        self._phase = (self._phase + 2.2) % 360
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def _to(self, v: float):
        self._anim.stop()
        self._anim.setStartValue(self._shown)
        self._anim.setEndValue(v)
        self._anim.start()

    def set_idle(self, big: str, sub: str):
        self.mode, self.big, self.sub = "idle", big, sub
        self._to(0.0)

    def set_progress(self, frac: float, big: str, sub: str):
        self.mode, self.big, self.sub = "busy", big, sub
        self._to(frac)

    def set_done(self, sub: str):
        self.mode, self.big, self.sub = "done", "", sub
        self._to(1.0)

    def set_error(self, sub: str):
        self.mode, self.big, self.sub = "error", "", sub
        self._to(0.0)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        c = QPointF(w / 2, w / 2)
        R = w / 2 - 14
        # glass disc
        disc = QRadialGradient(c, R)
        disc.setColorAt(0, W(18))
        disc.setColorAt(1, W(5))
        p.setPen(QPen(W(26), 1))
        p.setBrush(disc)
        p.drawEllipse(c, R - 15, R - 15)
        arc = QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R)
        # track
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(W(30), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawEllipse(c, R, R)
        busy_zero = self.mode in ("idle",) or (self.mode == "busy" and self._shown < 0.02)
        if busy_zero:  # gentle idle sweep so the dial feels alive
            pulse = 0.5 + 0.5 * math.sin(math.radians(self._phase * 2))
            p.setPen(QPen(W(26 + 26 * pulse), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc, int((90 - self._phase) * 16), int(-60 * 16))
        err = self.mode == "error"
        if self._shown > 0.003:
            p.setPen(QPen(col(ACCENT, 235), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc, 90 * 16, int(-self._shown * 360 * 16))
        # knob riding the arc (white bead with an accent rim)
        th = math.radians(90 - self._shown * 360)
        kx, ky = c.x() + R * math.cos(th), c.y() - R * math.sin(th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(2, 3, 6, 70))
        p.drawEllipse(QPointF(kx, ky + 2), 8.5, 8.5)
        p.setBrush(QColor("#f4f7fb"))
        p.setPen(QPen(col(ERR if err else ACCENT, 255), 2.4))
        p.drawEllipse(QPointF(kx, ky), 6.6, 6.6)
        # centre
        if self.mode in ("done", "error"):
            draw_icon(p, "check" if self.mode == "done" else "alert", QRectF(c.x() - 17, c.y() - 30, 34, 34),
                      col(ACCENT if self.mode == "done" else ERR), 2.0)
            p.setPen(QColor(MUTED))
            p.setFont(font(8.5))
            p.drawText(QRectF(0, c.y() + 8, w, 22), Qt.AlignmentFlag.AlignCenter, self.sub)
        else:
            p.setPen(QColor(TEXT))
            p.setFont(font(26, QFont.Weight.Light))
            p.drawText(QRectF(0, c.y() - 34, w, 52), Qt.AlignmentFlag.AlignCenter, self.big)
            p.setPen(QColor(MUTED))
            p.setFont(font(8.5))
            p.drawText(QRectF(14, c.y() + 14, w - 28, 22), Qt.AlignmentFlag.AlignCenter,
                       QFontMetrics(font(8.5)).elidedText(self.sub, Qt.TextElideMode.ElideRight, w - 28))
        p.end()


# ----------------------------------------------------------------------------- inputs, loaders, toast
class PromptEdit(QLineEdit):
    focusChanged = Signal(bool)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focusChanged.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focusChanged.emit(False)


class GlassField(GlassPanel):
    """Capsule that hosts a line edit; its rim brightens to the accent while focused."""

    def __init__(self, radius: float = 22, shadow: bool = True):
        super().__init__(radius, 0.8, shadow, grain=False)
        self._focus = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._f)

    def _f(self, v):
        self._focus = float(v)
        self.update()

    def set_focused(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._focus)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, e):
        p = QPainter(self)
        paint_glass(p, QRectF(self.rect()), self.radius, self.strength, self._focus * 0.4)
        if self._focus > 0.01:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            edge = QPainterPath()
            edge.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)
            p.setPen(QPen(col(ACCENT, int(170 * self._focus)), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(edge)
        p.end()


class Skeleton(QWidget):
    """Loading placeholder shaped like the preview, with a soft shimmer sweeping across glass blocks."""

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
        self.phase = (self.phase + 0.011) % 1.4
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        shapes = [(QRectF(20, 20, 360, 230), 18)]
        x0 = 404
        for i, frac in enumerate((0.34, 0.72, 0.52, 0.40)):
            shapes.append((QRectF(x0, 28 + i * 30, max(40, (w - x0 - 28) * frac), 16), 8))
        for i in range(4):
            shapes.append((QRectF(20 + i * ((w - 40 - 36) / 4 + 12), 268, (w - 40 - 36) / 4, 58), 14))
        band = -240 + self.phase * (w + 480)
        grad = QLinearGradient(band, 0, band + 240, 0)
        grad.setColorAt(0, W(0))
        grad.setColorAt(0.5, W(30))
        grad.setColorAt(1, W(0))
        for rect, rad in shapes:
            path = QPainterPath()
            path.addRoundedRect(rect, rad, rad)
            p.fillPath(path, W(16))
            p.save()
            p.setClipPath(path)
            p.fillRect(rect, grad)
            p.restore()
        p.setPen(QColor(MUTED))
        p.setFont(font(9))
        p.drawText(QPointF(x0, 28 + 4 * 30 + 24), "reading the post...")
        p.end()


class Toast(QWidget):
    """Glass pill notification that fades in and out above the content."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text, self.err = "", False
        self.eff = QGraphicsOpacityEffect(self)
        self.eff.setOpacity(0)
        self.setGraphicsEffect(self.eff)
        self.anim = QVariantAnimation(self)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(lambda v: self.eff.setOpacity(float(v)))
        self.anim.finished.connect(lambda: self.hide() if self.eff.opacity() < 0.05 else None)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(lambda: self._fade(0.0, 380))
        self.hide()

    def popup(self, text: str, error: bool = False):
        self.text, self.err = text, error
        fm = QFontMetrics(font(10, QFont.Weight.DemiBold))
        self.resize(fm.horizontalAdvance(text) + 78, 48)
        pw = self.parentWidget()
        self.move((pw.width() - self.width()) // 2, pw.height() - self.height() - 28)
        self.raise_()
        self.show()
        self.update()
        self._fade(1.0, 200)
        self.timer.start(2600)

    def _fade(self, end: float, ms: int):
        self.anim.stop()
        self.anim.setDuration(ms)
        self.anim.setStartValue(self.eff.opacity())
        self.anim.setEndValue(end)
        self.anim.start()

    def paintEvent(self, e):
        p = QPainter(self)
        r = QRectF(self.rect())
        paint_glass(p, r, r.height() / 2, 1.2, 0.2, tint=QColor(10, 11, 13, 225))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = col(ERR if self.err else ACCENT)
        draw_icon(p, "alert" if self.err else "check", QRectF(18, r.height() / 2 - 9, 18, 18), c, 1.9)
        p.setPen(QColor(TEXT))
        p.setFont(font(10, QFont.Weight.DemiBold))
        p.drawText(QRectF(46, 0, r.width() - 56, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)
        p.end()


class Dot(QLabel):
    """Status dot that breathes while active (perpetual micro-interaction)."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(14, 14)
        self._t = 0.0
        self.color = QColor(ACCENT)
        self._timer = QTimer(self, interval=60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._t = (self._t + 0.07) % (2 * math.pi)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = 0.5 + 0.5 * math.sin(self._t)
        halo = QColor(self.color)
        halo.setAlpha(int(30 + 55 * k))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(7, 7), 4.2 + 2.6 * k, 4.2 + 2.6 * k)
        p.setBrush(self.color)
        p.drawEllipse(QPointF(7, 7), 3.6, 3.6)
        p.end()


# ----------------------------------------------------------------------------- misc helpers
def framed_pixmap(path, w: int, h: int, radius: int = 16, data: bytes | None = None,
                  cache: dict | None = None) -> QPixmap:
    """Thumbnail letterboxed on a dark glass backing, with rounded corners."""
    from pathlib import Path
    key = (path, w, h, radius)
    if cache is not None and not data and key in cache:
        return cache[key]
    src = QPixmap()
    if data:
        src.loadFromData(data)
    elif path and Path(path).exists():
        src.load(path)
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, w, h, radius, radius)
    p.setClipPath(clip)
    p.fillRect(0, 0, w, h, QColor(6, 7, 9))
    if not src.isNull():
        s = src.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((w - s.width()) // 2, (h - s.height()) // 2, s)
    else:
        draw_icon(p, "film", QRectF(w / 2 - 14, h / 2 - 14, 28, 28), W(60), 1.6)
    p.end()
    if cache is not None and not data:
        cache[key] = out
    return out


def _colorref(hex_color: str) -> ctypes.c_int:
    c = QColor(hex_color)
    return ctypes.c_int((c.blue() << 16) | (c.green() << 8) | c.red())


def style_titlebar(widget: QWidget) -> None:
    """Dark title bar; on Windows 11 also tinted to match the canvas."""
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        one = ctypes.c_int(1)
        for attr in (20, 19):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(one), 4)
        for attr, c in ((35, BG), (36, TEXT), (34, "#1a1d22")):
            v = _colorref(c)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
    except Exception:
        pass


def stylesheet() -> str:
    ff = f"'{FONT_FAMILY}'"
    return f"""
* {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#error {{ color: {ERR}; }}
QLineEdit {{ background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.16); border-radius: 14px;
            padding: 0 14px; min-height: 38px; selection-background-color: {ACCENT}; selection-color: #0a0b0d; }}
QLineEdit:focus {{ border-color: {ACCENT}; background: rgba(255,255,255,0.10); }}
QLineEdit#bare {{ background: transparent; border: none; padding: 0 4px; min-height: 0; }}
QPushButton {{ background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.20); border-radius: 14px;
              padding: 0 18px; min-height: 38px; font-weight: 600; }}
QPushButton:hover {{ background: rgba(255,255,255,0.17); border-color: rgba(255,255,255,0.34); }}
QPushButton:pressed {{ background: rgba(255,255,255,0.07); }}
QPushButton:disabled {{ color: {DIM}; background: rgba(255,255,255,0.04); border-color: rgba(255,255,255,0.08); }}
QComboBox {{ background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.18); border-radius: 14px;
            padding: 0 14px; min-height: 38px; }}
QComboBox:hover {{ background: rgba(255,255,255,0.13); border-color: rgba(255,255,255,0.32); }}
QComboBox:disabled {{ color: #7d8490; background: rgba(255,255,255,0.05); border-color: rgba(255,255,255,0.10); }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox QAbstractItemView {{ background: #14171c; border: 1px solid rgba(255,255,255,0.18); border-radius: 10px;
            selection-background-color: rgba(125,180,240,0.35); selection-color: #ffffff; outline: 0; padding: 4px; }}
QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,0.16); border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #f4f7fb; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QListWidget {{ background: transparent; outline: 0; border: none; }}
QListWidget::item {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.22); border-radius: 3px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.36); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu {{ background: #14171c; border: 1px solid rgba(255,255,255,0.18); border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 8px 22px; border-radius: 8px; }}
QMenu::item:selected {{ background: rgba(125,180,240,0.30); }}
QMenu::separator {{ height: 1px; background: rgba(255,255,255,0.12); margin: 5px 8px; }}
QMessageBox {{ background: #101317; }}
QToolTip {{ background: #14171c; color: {TEXT}; border: 1px solid rgba(255,255,255,0.18); padding: 6px 8px; }}
"""


def apply_theme(app) -> None:
    pick_font()
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Base, "#101317"),
                    (QPalette.ColorRole.AlternateBase, "#101317"), (QPalette.ColorRole.Text, TEXT),
                    (QPalette.ColorRole.Button, "#1a1d22"), (QPalette.ColorRole.ButtonText, TEXT),
                    (QPalette.ColorRole.Highlight, ACCENT), (QPalette.ColorRole.HighlightedText, "#0a0b0d"),
                    (QPalette.ColorRole.ToolTipBase, "#14171c"), (QPalette.ColorRole.ToolTipText, TEXT),
                    (QPalette.ColorRole.PlaceholderText, DIM)):
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(10)
    app.setFont(f)
    app.setStyleSheet(stylesheet())
