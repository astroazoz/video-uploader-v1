"""Video Downloader UI: frosted-glass dashboard. All download / account / library logic lives in core.py."""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import glass as G
from accounts import ACCOUNTS, PLATFORM_CFG
from core import (APP_NAME, CFG_FILE, DATA_DIR, VERSION, PLATFORM_TAG, DownloadTask, FetchTask, Library, extract_url,
                  fmt_count, fmt_duration, fmt_size, load_json, move_to_recycle_bin, open_folder_of, platform_of,
                  save_json)
from PySide6.QtCore import (QCoreApplication, QEvent, QPoint, QRect, QRectF, QSize, Qt, QThreadPool, QTimer, QUrl,
                            Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen,
                           QTextLayout, QTextOption)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QSizePolicy, QSlider, QStackedWidget, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

TEXT, MUTED, DIM, ACCENT, ERR = G.TEXT, G.MUTED, G.DIM, G.ACCENT, G.ERR
SEMI = QFont.Weight.DemiBold


# ----------------------------------------------------------------------------- small widgets
class Chip(QLabel):
    """Static glass capsule label."""

    def __init__(self, text: str = "", accent: bool = False, pt: float = 8.5):
        super().__init__("")
        self.accent = accent
        self.setFont(G.font(pt, SEMI))
        self.setFixedHeight(28)
        self.setText(text)

    def setText(self, text: str):
        super().setText(text)
        self.setFixedWidth(QFontMetrics(self.font()).horizontalAdvance(text) + 28)

    def paintEvent(self, e):
        p = QPainter(self)
        r = QRectF(self.rect())
        G.paint_glass(p, r, r.height() / 2, 0.65, tint=G.col(ACCENT, 46) if self.accent else None)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor("#dcebfb") if self.accent else QColor(TEXT))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignCenter, self.text())
        p.end()


class ElidedLabel(QLabel):
    """Wrapped text capped at N lines with an ellipsis (never clips mid-line, height stays constant)."""

    def __init__(self, fnt, color: str, max_lines: int):
        super().__init__("")
        self._f, self._c, self._n, self._t = fnt, color, max_lines, ""
        self.setFont(fnt)
        self.setMinimumHeight(QFontMetrics(fnt).lineSpacing() * max_lines + 4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def setText(self, text: str):
        self._t = text
        self.update()

    def text(self) -> str:
        return self._t

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self._f)
        p.setPen(QColor(self._c))
        wrapped_text(p, self._t, QRect(0, 0, self.width(), self.height()), self._n)
        p.end()


class IconBadge(QWidget):
    """Glass disc with a line icon, used in empty states."""

    def __init__(self, icon: str, size: int = 64, color: str = MUTED):
        super().__init__()
        self.icon, self.color = icon, color
        self.setFixedSize(size, size)

    def paintEvent(self, e):
        p = QPainter(self)
        r = QRectF(self.rect())
        G.paint_glass(p, r, r.width() / 2, 0.9)
        G.draw_icon(p, self.icon, r.adjusted(r.width() * 0.28, r.height() * 0.28, -r.width() * 0.28, -r.height() * 0.28),
                    QColor(self.color), 1.8)
        p.end()


def draw_capsule(p: QPainter, x: float, y: float, text: str, pt: float = 8, fg: str = TEXT) -> float:
    f = G.font(pt, SEMI)
    fm = QFontMetrics(f)
    w, h = fm.horizontalAdvance(text) + 18, 22
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
    p.fillPath(path, QColor(10, 11, 13, 150))
    p.setPen(QPen(G.W(54), 1))
    p.drawPath(path)
    p.setPen(QColor(fg))
    p.setFont(f)
    p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)
    p.restore()
    return w


def draw_action(p: QPainter, rect: QRect, hidden: bool, hot: bool) -> None:
    """HIDE / UNHIDE button on a card or row."""
    r = QRectF(rect)
    G.paint_glass(p, r, r.height() / 2, 1.5, 0.55 if hot else 0.1, tint=QColor(10, 11, 13, 120))
    c = QColor(ACCENT) if hot else QColor(TEXT)
    G.draw_icon(p, "eye" if hidden else "eye-off", QRectF(r.x() + 10, r.center().y() - 7.5, 15, 15), c, 1.5)
    p.setPen(c)
    p.setFont(G.font(8.5, SEMI))
    p.drawText(QRectF(r.x() + 28, r.y(), r.width() - 32, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
               "Unhide" if hidden else "Hide")


def wrapped_text(p: QPainter, text: str, rect: QRect, max_lines: int) -> None:
    fm = p.fontMetrics()
    layout = QTextLayout(text, p.font())
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    layout.beginLayout()
    y, n = rect.y(), 0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(rect.width())
        n += 1
        start, length = line.textStart(), line.textLength()
        if n == max_lines:
            p.drawText(QPoint(rect.x(), y + fm.ascent()), fm.elidedText(text[start:], Qt.ElideRight, rect.width()))
            break
        p.drawText(QPoint(rect.x(), y + fm.ascent()), text[start:start + length].rstrip())
        y += fm.lineSpacing()
    layout.endLayout()


def entry_date(e: dict) -> str:
    try:
        return datetime.fromisoformat(e["date"]).strftime("%b %d, %Y")
    except Exception:
        return ""


# ----------------------------------------------------------------------------- gallery
class Gallery(QListWidget):
    resized = Signal()
    hideClicked = Signal(object)  # the entry whose HIDE / UNHIDE button was clicked

    def __init__(self):
        super().__init__()
        self._press_entry = None
        self._reveal: dict = {}
        self._reveal_timer = QTimer(self, interval=16)
        self._reveal_timer.timeout.connect(self._reveal_tick)

    # staggered entrance: cards rise and fade in one after another
    def start_reveal(self):
        now = time.monotonic()
        self._reveal = {self.item(i).data(Qt.UserRole)["id"]: now + i * 0.05 for i in range(self.count())}
        if self._reveal:
            self._reveal_timer.start()

    def reveal_value(self, eid: str) -> float:
        t0 = self._reveal.get(eid)
        if t0 is None:
            return 1.0
        return max(0.0, min(1.0, (time.monotonic() - t0) / 0.4))

    def _reveal_tick(self):
        now = time.monotonic()
        self.viewport().update()
        if all(now - t0 >= 0.42 for t0 in self._reveal.values()):
            self._reveal.clear()
            self._reveal_timer.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    def _action_hit(self, pos):
        it = self.itemAt(pos)
        if not it:
            return None
        entry = it.data(Qt.UserRole)
        rect = self.itemDelegate().action_rect(self.visualRect(self.indexFromItem(it)), bool(entry.get("hidden")))
        return entry if rect.contains(pos) else None

    def mousePressEvent(self, ev):
        self._press_entry = self._action_hit(ev.position().toPoint()) if ev.button() == Qt.LeftButton else None
        if self._press_entry is None:
            super().mousePressEvent(ev)
        else:
            ev.accept()

    def mouseReleaseEvent(self, ev):
        if self._press_entry is not None:
            pressed, self._press_entry = self._press_entry, None
            hit = self._action_hit(ev.position().toPoint())
            if hit and hit["id"] == pressed["id"]:
                self.hideClicked.emit(pressed)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseMoveEvent(self, ev):
        hit = self._action_hit(ev.position().toPoint())
        d = self.itemDelegate()
        new = hit["id"] if hit else None
        if d.hover_id != new:
            d.hover_id = new
            self.viewport().update()
        self.viewport().setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        d = self.itemDelegate()
        if d.hover_id is not None:
            d.hover_id = None
            self.viewport().update()
        super().leaveEvent(ev)


class _DelegateBase(QStyledItemDelegate):
    gallery = None
    hover_id = None

    def _enter(self, p: QPainter, eid: str) -> None:
        """Apply the staggered reveal (fade + rise) for this item."""
        rv = self.gallery.reveal_value(eid) if self.gallery is not None else 1.0
        if rv < 1.0:
            ease = 1 - (1 - rv) ** 3
            p.setOpacity(ease)
            p.translate(0, (1 - ease) * 14)


class GridDelegate(_DelegateBase):
    MIN_W = 236
    W = 244  # updated by MainWindow so cards fill each row exactly
    cache: dict = {}

    @staticmethod
    def thumb_h(w: int) -> int:
        return max(100, int((w - 10 - 20) * 9 / 16))

    @property
    def H(self) -> int:
        return self.thumb_h(self.W) + 104

    def sizeHint(self, option, index):
        return QSize(self.W, self.H)

    @staticmethod
    def action_rect(item_rect: QRect, hidden: bool) -> QRect:
        r = item_rect.adjusted(5, 5, -5, -5)
        w = 84 if hidden else 70
        return QRect(r.x() + 10 + (r.width() - 20) - w - 8, r.y() + 18, w, 26)

    def paint(self, p: QPainter, opt, index):
        e = index.data(Qt.UserRole)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        missing = not Path(e["path"]).exists()
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self._enter(p, e["id"])
        r = QRectF(opt.rect.adjusted(5, 5, -5, -5))
        if hover:
            r = r.translated(0, -2)
        G.paint_glass(p, r, 22, 0.9, 0.7 if (hover or sel) else 0.0)
        if sel:
            edge = QPainterPath()
            edge.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 22, 22)
            p.setPen(QPen(G.col(ACCENT, 190), 1.3))
            p.setBrush(Qt.NoBrush)
            p.drawPath(edge)
        tr = QRect(int(r.x()) + 10, int(r.y()) + 10, int(r.width()) - 20, self.thumb_h(self.W))
        p.drawPixmap(tr.topLeft(), G.framed_pixmap(e.get("thumb"), tr.width(), tr.height(), 15, cache=self.cache))
        draw_capsule(p, tr.x() + 8, tr.y() + 8, PLATFORM_TAG.get(e.get("platform"), "VIDEO"))
        dur = fmt_duration(e.get("duration")) or ("MP3" if e.get("kind") == "audio" else "")
        if dur:
            w = QFontMetrics(G.font(8, SEMI)).horizontalAdvance(dur) + 18
            draw_capsule(p, tr.right() - w - 6, tr.bottom() - 28, dur)
        if missing:
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(tr), 15, 15)
            p.fillPath(clip, QColor(8, 9, 11, 205))
            p.setPen(QColor(ERR))
            p.setFont(G.font(9.5, SEMI))
            p.drawText(tr, Qt.AlignCenter, "File missing")
        elif hover:
            c = QRectF(tr).center()
            disc = QRectF(c.x() - 24, c.y() - 24, 48, 48)
            G.paint_glass(p, disc, 24, 1.8, 0.6, tint=QColor(10, 11, 13, 90))
            G.draw_icon(p, "play", QRectF(c.x() - 10, c.y() - 10, 20, 20), QColor("#ffffff"), 1.8)
        draw_action(p, self.action_rect(opt.rect, bool(e.get("hidden"))).translated(0, -2 if hover else 0),
                    bool(e.get("hidden")), e.get("id") == self.hover_id)
        p.setFont(G.font(10, SEMI))
        p.setPen(QColor(TEXT))
        wrapped_text(p, e.get("title") or e["filename"], QRect(int(r.x()) + 14, tr.bottom() + 12, int(r.width()) - 28, 40), 2)
        p.setFont(G.font(8.5))
        p.setPen(QColor(MUTED))
        meta = "  ·  ".join(x for x in (entry_date(e), fmt_size(e.get("size"))) if x)
        p.drawText(QRect(int(r.x()) + 14, int(r.bottom()) - 30, int(r.width()) - 28, 20), Qt.AlignVCenter | Qt.AlignLeft, meta)
        p.restore()


class ListDelegate(_DelegateBase):
    """One glass row per video: thumbnail, title + file name, then platform / length / size / date and the button."""
    H = 86
    cache: dict = {}

    def sizeHint(self, option, index):
        return QSize(100, self.H)

    @staticmethod
    def action_rect(item_rect: QRect, hidden: bool) -> QRect:
        r = item_rect.adjusted(3, 3, -3, -3)
        return QRect(r.right() - 16 - 92, r.y() + (r.height() - 30) // 2, 92, 30)

    def paint(self, p: QPainter, opt, index):
        e = index.data(Qt.UserRole)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        missing = not Path(e["path"]).exists()
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self._enter(p, e["id"])
        r = QRectF(opt.rect.adjusted(3, 3, -3, -3))
        G.paint_glass(p, r, 20, 0.8, 0.6 if (hover or sel) else 0.0)
        if sel:
            edge = QPainterPath()
            edge.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)
            p.setPen(QPen(G.col(ACCENT, 190), 1.3))
            p.setBrush(Qt.NoBrush)
            p.drawPath(edge)
        tr = QRect(int(r.x()) + 10, int(r.y()) + 9, 104, 58)
        p.drawPixmap(tr.topLeft(), G.framed_pixmap(e.get("thumb"), tr.width(), tr.height(), 12, cache=self.cache))
        cols = [(92, PLATFORM_TAG.get(e.get("platform"), "VIDEO")), (66, fmt_duration(e.get("duration")) or ("mp3" if e.get("kind") == "audio" else "-")),
                (86, fmt_size(e.get("size")) or "-"), (104, entry_date(e) or "-")]
        x = r.right() - 16 - 100 - 14 - sum(w for w, _ in cols)
        p.setFont(G.font(9))
        for w, text in cols:
            p.setPen(QColor(TEXT if text in PLATFORM_TAG.values() else MUTED))
            p.drawText(QRect(int(x), int(r.y()), w, int(r.height())), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += w
        tx = tr.right() + 18
        tw = max(60, int(r.right() - 16 - 100 - 14 - sum(w for w, _ in cols)) - tx - 12)
        p.setFont(G.font(10.5, SEMI))
        p.setPen(QColor(ERR if missing else TEXT))
        title = ("File missing: " if missing else "") + (e.get("title") or e["filename"])
        p.drawText(QRect(tx, int(r.y()) + 14, tw, 24), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(p.font()).elidedText(title, Qt.ElideRight, tw))
        p.setFont(G.font(8.5))
        p.setPen(QColor(MUTED))
        p.drawText(QRect(tx, int(r.y()) + 42, tw, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(p.font()).elidedText(e["filename"], Qt.ElideMiddle, tw))
        draw_action(p, self.action_rect(opt.rect, bool(e.get("hidden"))), bool(e.get("hidden")), e.get("id") == self.hover_id)
        p.restore()


# ----------------------------------------------------------------------------- dialogs
class ClickableVideo(QVideoWidget):
    clicked = Signal()

    def mousePressEvent(self, e):
        self.clicked.emit()


class PlayerDialog(G.GlassDialog):
    def __init__(self, win: "MainWindow", entry: dict):
        super().__init__(win)
        self.win, self.entry = win, entry
        self.setWindowTitle(entry.get("title") or "Video")
        self.resize(1000, 800)
        self.setMinimumSize(760, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 22)
        root.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(12)
        t = QLabel(entry.get("title") or entry["filename"])
        t.setFont(G.font(13, SEMI))
        t.setMaximumWidth(700)
        t.setToolTip(entry["filename"])
        top.addWidget(t, 1)
        top.addWidget(Chip(entry.get("platform") or "Video"))
        root.addLayout(top)

        vp = G.GlassPanel(26, 0.7, grain=False)
        vl = QVBoxLayout(vp)
        vl.setContentsMargins(10, 10, 10, 10)
        self.video = ClickableVideo()
        self.video.setMinimumHeight(340)
        vl.addWidget(self.video)
        root.addWidget(vp, 1)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.video.clicked.connect(self.toggle)

        cp = G.GlassPanel(28, 0.9, grain=False)
        cl = QHBoxLayout(cp)
        cl.setContentsMargins(14, 10, 20, 10)
        cl.setSpacing(14)
        self.play_btn = G.IconButton("pause", 44, "primary", "Play / pause")
        self.play_btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setObjectName("muted")
        self.time_lbl.setMinimumWidth(104)
        vol = QSlider(Qt.Horizontal)
        vol.setRange(0, 100)
        vol.setValue(80)
        vol.setFixedWidth(96)
        vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        vl2 = QLabel("Volume")
        vl2.setObjectName("muted")
        cl.addWidget(self.play_btn)
        cl.addWidget(self.slider, 1)
        cl.addWidget(self.time_lbl)
        cl.addWidget(vl2)
        cl.addWidget(vol)
        root.addWidget(cp)

        self.err = QLabel("")
        self.err.setObjectName("error")
        self.err.setWordWrap(True)
        self.err.hide()
        root.addWidget(self.err)

        ip = G.GlassPanel(24, 0.8, grain=False)
        grid = QGridLayout(ip)
        grid.setContentsMargins(22, 16, 22, 16)
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(8)
        try:
            date = datetime.fromisoformat(entry["date"]).strftime("%B %d, %Y  at  %H:%M")
        except Exception:
            date = "-"
        link = entry.get("url") or ""
        rows = [("Platform", entry.get("platform") or "-"),
                ("Source link", f'<a href="{link}" style="color:{ACCENT}">{link}</a>' if link else "-"),
                ("File name", entry["filename"]), ("Saved", date),
                ("Details", "  ·  ".join(x for x in (fmt_duration(entry.get("duration")), fmt_size(entry.get("size")),
                                                         "MP3 audio" if entry.get("kind") == "audio" else "MP4 video") if x))]
        for i, (k, v) in enumerate(rows):
            kl = QLabel(k)
            kl.setObjectName("muted")
            kl.setAlignment(Qt.AlignTop)
            vl3 = QLabel(v)
            vl3.setWordWrap(True)
            vl3.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
            vl3.setOpenExternalLinks(True)
            grid.addWidget(kl, i, 0)
            grid.addWidget(vl3, i, 1)
        grid.setColumnStretch(1, 1)
        root.addWidget(ip)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        for text, icon, fn in (("Open folder", "folder", lambda: open_folder_of(entry["path"])),
                               ("Copy link", "copy", lambda: win.copy_link(entry)),
                               ("Default player", "external", self.open_external),
                               ("Unhide" if entry.get("hidden") else "Hide", "eye" if entry.get("hidden") else "eye-off", self.toggle_hide)):
            b = G.GlassButton(text, icon, height=42)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        d = G.GlassButton("Delete", "trash", "danger", height=42)
        d.clicked.connect(self.delete)
        c = G.GlassButton("Close", None, "primary", height=42)
        c.setMinimumWidth(110)
        c.clicked.connect(self.accept)
        btns.addWidget(d)
        btns.addWidget(c)
        root.addLayout(btns)

        self.player.positionChanged.connect(self._pos)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.playbackStateChanged.connect(self._state)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.errorOccurred.connect(lambda *_: self._show_err())
        self.player.setSource(QUrl.fromLocalFile(entry["path"]))
        self.player.play()

    def _state(self, s):
        self.play_btn.icon_name = "pause" if s == QMediaPlayer.PlayingState else "play"
        self.play_btn.update()

    def _pos(self, ms):
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.time_lbl.setText(f"{fmt_duration(ms // 1000) or '0:00'} / {fmt_duration(self.player.duration() // 1000) or '0:00'}")

    def _status(self, st):
        if st == QMediaPlayer.EndOfMedia:
            self.player.setPosition(0)
            self.player.pause()

    def _show_err(self):
        why = (self.player.errorString() or "").strip()
        self.err.setText("This file can't be played inside the app" + (f" ({why})" if why else "") +
                         ". Use “Default player”, or download it again as MP4 H.264.")
        self.err.show()

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def open_external(self):
        self.player.pause()
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.entry["path"]))

    def release(self):
        self.player.stop()
        self.player.setSource(QUrl())

    def toggle_hide(self):
        self.release()
        self.win.toggle_hidden(self.entry)
        self.accept()

    def delete(self):
        if self.win.delete_entry(self.entry, self, before=self.release):
            self.accept()

    def done(self, r):
        self.release()
        super().done(r)


class AccountsDialog(G.GlassDialog):
    """Connected accounts: connect / reconnect / disconnect TikTok, Instagram and X."""
    changed = Signal()
    ORDER = ("TikTok", "Instagram", "X")

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.setWindowTitle("Connected accounts")
        self.resize(780, 800)
        self.setMinimumSize(720, 780)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(16)

        head = QLabel("Connected accounts")
        head.setFont(G.font(17, SEMI))
        sub = QLabel("Connect the accounts you want to use, so posts those accounts are allowed to see "
                     "(for example a private profile that accepted your follow) can be previewed and saved.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(head)
        lay.addWidget(sub)

        self.rows: dict[str, dict] = {}
        for plat in self.ORDER:
            f = G.GlassPanel(24, 0.9, grain=False)
            h = QHBoxLayout(f)
            h.setContentsMargins(22, 16, 18, 16)
            h.setSpacing(14)
            name = QLabel(PLATFORM_CFG[plat]["label"])
            name.setFont(G.font(11.5, SEMI))
            name.setFixedWidth(128)
            status = QLabel("")
            status.setWordWrap(True)
            connect = G.GlassButton("Connect", "user", "primary", height=42)
            connect.setMinimumWidth(138)
            disconnect = G.GlassButton("Disconnect", None, "danger", height=42)
            disconnect.setMinimumWidth(122)
            connect.clicked.connect(lambda _=False, p=plat: self.connect_account(p))
            disconnect.clicked.connect(lambda _=False, p=plat: self.disconnect_account(p))
            h.addWidget(name)
            h.addWidget(status, 1)
            h.addWidget(connect)
            h.addWidget(disconnect)
            lay.addWidget(f)
            self.rows[plat] = {"status": status, "connect": connect, "disconnect": disconnect}

        self.note = QLabel("")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        info = G.GlassPanel(24, 0.7, grain=False)
        il = QVBoxLayout(info)
        il.setContentsMargins(22, 16, 22, 16)
        il.setSpacing(10)
        for head_t, body in (
            ("How sign-in works", "You sign in on the platform's own page, inside this app. Your password is never seen "
                                  "or stored here. Only the session cookie is kept, encrypted with Windows for your user "
                                  "account on this PC. Disconnect deletes it."),
            ("What it can do", "Open posts that the connected account is itself allowed to view. Use it only for videos "
                               "you own or have the owner's permission to save."),
            ("Limits", "The app never bypasses privacy settings. Some private or download-restricted posts can't be saved "
                       "even when you can watch them, because the platform doesn't serve a file for them. Using any account "
                       "with automated tools can occasionally trigger a security check on that platform. To end a session "
                       "everywhere, also sign out of other devices in the platform's own security settings.")):
            l = QLabel(f"<span style='color:{ACCENT};font-weight:600'>{head_t}</span><br><span style='color:{MUTED}'>{body}</span>")
            l.setWordWrap(True)
            il.addWidget(l)
        lay.addWidget(info)
        lay.addStretch(1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        close = G.GlassButton("Close", None, "glass", height=42)
        close.setMinimumWidth(112)
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        lay.addLayout(foot)
        self.refresh()

    def refresh(self):
        for plat, w in self.rows.items():
            info = ACCOUNTS.info(plat)
            st, name, saved = info["state"], info["username"], info["saved"][:10]
            if st == "connected":
                dot, head = ACCENT, "Connected" + (f" as <b>@{name}</b>" if name else "")
                sub = f"Signed in, saved {saved}" if saved else "Signed in"
            elif st == "attention":
                dot, head = ERR, "Needs attention" + (f" (@{name})" if name else "")
                sub = "The last request with this session failed. It may have expired: reconnect if private posts stop working."
            elif st == "expired":
                dot, head = ERR, "Session expired" + (f" (@{name})" if name else "")
                sub = "Sign in again to keep access to private posts."
            elif st == "unreadable":
                dot, head = ERR, "Saved session can't be read"
                sub = "It was saved by another Windows user or PC. Reconnect."
            else:
                dot, head = DIM, "Not connected"
                sub = "Public posts work without an account."
            w["status"].setText(f"<span style='color:{dot}'>●</span>&nbsp; {head}<br><span style='color:{MUTED}'>{sub}</span>")
            has = st != "none"
            w["connect"].setText("Reconnect" if has else "Connect")
            w["disconnect"].setEnabled(has)

    def connect_account(self, plat: str):
        cfg = PLATFORM_CFG[plat]
        try:
            from login_dialog import LoginDialog
            dlg = LoginDialog(self, plat, cfg)
        except Exception as e:  # noqa: BLE001
            self.note.setText(f"<span style='color:{ERR}'>The built-in browser couldn't start: {e}</span>")
            return
        dlg.exec()
        res = dlg.result
        if res and res["cookies"]:
            try:
                ACCOUNTS.save(plat, res["cookies"], res["username"])
            except Exception as e:  # noqa: BLE001
                self.note.setText(f"<span style='color:{ERR}'>Couldn't save the session securely: {e}</span>")
                return
            who = f"@{res['username']}" if res["username"] else "your account"
            self.note.setText(f"<span style='color:{ACCENT}'>{cfg['label']} connected as {who}.</span>")
        else:
            self.note.setText(f"<span style='color:{MUTED}'>Sign-in to {cfg['label']} was cancelled.</span>")
        self.refresh()
        self.changed.emit()

    def disconnect_account(self, plat: str):
        label = PLATFORM_CFG[plat]["label"]
        r = QMessageBox.question(self, "Disconnect", f"Disconnect {label}?\n\nThe saved session is deleted from this PC. "
                                 f"To end it everywhere, also sign out of other devices in {label}'s security settings.")
        if r != QMessageBox.Yes:
            return
        ACCOUNTS.remove(plat)
        self.note.setText(f"<span style='color:{MUTED}'>{label} disconnected. The saved session was deleted.</span>")
        self.refresh()
        self.changed.emit()


# ----------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Downloader")
        self.setWindowIcon(G.app_icon())
        self.resize(1300, 960)
        self.setMinimumSize(1160, 880)
        self.cfg = load_json(CFG_FILE, {})
        self.save_dir = self.cfg.get("save_dir") or str(Path.home() / "Videos" / "Video Downloader")
        self.view_mode = self.cfg.get("view", "grid")
        self.lib = Library()
        self.pool = QThreadPool.globalInstance()
        self.tasks: set = set()
        self.token = 0
        self.fetched_url = None
        self.summary: dict | None = None
        self.dl_task: DownloadTask | None = None
        self.busy = False
        self.last_clip = None
        self.lib_tab = "library"
        self.grid_delegate = GridDelegate(self)
        self.list_delegate = ListDelegate(self)
        self._build()
        self.grid_delegate.gallery = self.listw
        self.list_delegate.gallery = self.listw
        self.toast = G.Toast(self.backdrop)
        self._refresh_accounts()
        self._set_status_text()
        self.refresh_library(animate=True)

    # ======================================================================= construction
    def _build(self):
        self.backdrop = G.Backdrop()
        self.setCentralWidget(self.backdrop)
        outer = QHBoxLayout(self.backdrop)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        shell = QWidget()
        shell.setMaximumWidth(1760)
        shell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer.addWidget(shell, 1000)
        outer.addStretch(1)

        root = QHBoxLayout(shell)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(20)
        root.addWidget(self._build_rail())

        col = QVBoxLayout()
        col.setSpacing(18)
        root.addLayout(col, 1)
        col.addLayout(self._build_header())
        col.addWidget(self._build_prompt())
        self.hint = QLabel("")
        self.hint.setObjectName("error")
        self.hint.hide()
        col.addWidget(self.hint)
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addWidget(self._build_preview_card(), 2)
        row.addWidget(self._build_download_card(), 1)
        col.addLayout(row)
        col.addWidget(self._build_library(), 1)
        self.url_timer = QTimer(self, singleShot=True)
        self.url_timer.timeout.connect(self.on_url_changed)

    # ---- left rail
    def _build_rail(self):
        rail = G.GlassPanel(34, 0.9)
        rail.setFixedWidth(76)
        v = QVBoxLayout(rail)
        v.setContentsMargins(15, 18, 15, 18)
        v.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(G.icon_pixmap(44))
        logo.setFixedSize(46, 46)
        v.addWidget(logo, 0, Qt.AlignHCenter)
        v.addSpacing(8)
        self.rail_btns = {}
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for key, icon, tip, checkable in (("paste", "link", "Paste a link", False), ("library", "film", "Library", True),
                                          ("hidden", "eye-off", "Hidden videos", True), ("accounts", "user", "Connected accounts", False)):
            b = G.IconButton(icon, 46, "glass", tip)
            b.setCheckable(checkable)
            if checkable:
                grp.addButton(b)
            self.rail_btns[key] = b
            v.addWidget(b, 0, Qt.AlignHCenter)
        self.rail_btns["library"].setChecked(True)
        self.rail_btns["paste"].clicked.connect(lambda: (self.url_input.setFocus(), self.url_input.selectAll()))
        self.rail_btns["library"].clicked.connect(lambda: self.set_tab("library"))
        self.rail_btns["hidden"].clicked.connect(lambda: self.set_tab("hidden"))
        self.rail_btns["accounts"].clicked.connect(self.open_accounts)
        v.addStretch(1)
        folder = G.IconButton("folder", 46, "glass", "Open the downloads folder")
        folder.clicked.connect(self._open_dir)
        v.addWidget(folder, 0, Qt.AlignHCenter)
        return rail

    # ---- header
    def _build_header(self):
        h = QHBoxLayout()
        h.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(3)
        title = QLabel(f"Video Downloader")
        title.setFont(G.font(19, SEMI))
        st = QHBoxLayout()
        st.setSpacing(8)
        self.status_dot = G.Dot()
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("muted")
        self.status_lbl.setFont(G.font(9))
        st.addWidget(self.status_dot)
        st.addWidget(self.status_lbl)
        st.addStretch(1)
        left.addWidget(title)
        left.addLayout(st)
        h.addLayout(left)
        h.addStretch(1)
        self.acct_chips: dict[str, G.GlassButton] = {}
        for plat in ("TikTok", "Instagram", "X"):
            b = G.GlassButton("", None, "glass", height=38, radius=19, dot=QColor(DIM), pt=9)
            b.clicked.connect(self.open_accounts)
            self.acct_chips[plat] = b
            h.addWidget(b)
        return h

    # ---- URL bar
    def _build_prompt(self):
        self.prompt = G.GlassField(radius=32)
        self.prompt.setFixedHeight(68)
        bl = QHBoxLayout(self.prompt)
        bl.setContentsMargins(26, 10, 12, 10)
        bl.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(G.icon_pixmap_named("link", 22, MUTED))
        self.url_input = G.PromptEdit()
        self.url_input.setObjectName("bare")
        self.url_input.setFont(G.font(12))
        self.url_input.setPlaceholderText("Paste a link from X, TikTok or Instagram")
        self.url_input.setFixedHeight(44)
        self.url_input.focusChanged.connect(self.prompt.set_focused)
        self.url_input.textChanged.connect(lambda *_: self.url_timer.start(450))
        self.url_input.returnPressed.connect(self.on_url_changed)
        self.platform_tag = Chip("", accent=True)
        self.platform_tag.hide()
        self.clear_btn = G.IconButton("close", 38, "ghost", "Clear")
        self.clear_btn.clicked.connect(lambda: self.url_input.clear())
        self.clear_btn.hide()
        self.paste_btn = G.GlassButton("Paste", "paste", height=46, radius=23)
        self.paste_btn.setMinimumWidth(116)
        self.paste_btn.clicked.connect(self.paste)
        bl.addWidget(ic)
        bl.addWidget(self.url_input, 1)
        bl.addWidget(self.platform_tag)
        bl.addWidget(self.clear_btn)
        bl.addWidget(self.paste_btn)
        return self.prompt

    # ---- preview card (left of the first row)
    def _build_preview_card(self):
        card = G.GlassPanel(30)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 20, 22, 20)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.stack.addWidget(self._page_empty())
        self.skeleton = G.Skeleton()
        self.stack.addWidget(self.skeleton)
        self.stack.addWidget(self._page_preview())
        self.stack.addWidget(self._page_error())
        return card

    def _page_empty(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 0, 8, 0)
        v.setSpacing(10)
        v.addStretch(1)
        v.addWidget(IconBadge("film", 68, ACCENT), 0, Qt.AlignLeft)
        v.addSpacing(8)
        t = QLabel("Paste a link to see a preview")
        t.setFont(G.font(17, SEMI))
        d = QLabel("The thumbnail, title, length and quality options show up here before anything is downloaded.")
        d.setObjectName("muted")
        d.setWordWrap(True)
        d.setMaximumWidth(520)
        v.addWidget(t)
        v.addWidget(d)
        v.addSpacing(10)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for name in ("X (Twitter)", "TikTok", "Instagram Reels"):
            chips.addWidget(Chip(name))
        chips.addStretch(1)
        v.addLayout(chips)
        tip = QLabel("Tip: copy a link anywhere. It is picked up automatically when you come back to this window.")
        tip.setObjectName("muted")
        tip.setFont(G.font(9))
        tip.setWordWrap(True)
        v.addSpacing(6)
        v.addWidget(tip)
        v.addStretch(1)
        return w

    def _page_error(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 0, 8, 0)
        v.setSpacing(10)
        v.addStretch(1)
        v.addWidget(IconBadge("alert", 68, ERR), 0, Qt.AlignLeft)
        v.addSpacing(8)
        t = QLabel("This link couldn't be read")
        t.setFont(G.font(17, SEMI))
        self.err_lbl = QLabel("")
        self.err_lbl.setObjectName("muted")
        self.err_lbl.setWordWrap(True)
        self.err_lbl.setMaximumWidth(560)
        v.addWidget(t)
        v.addWidget(self.err_lbl)
        v.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(10)
        retry = G.GlassButton("Try again", None, "glass", height=42)
        retry.setMinimumWidth(120)
        retry.clicked.connect(lambda: self.start_fetch(self.fetched_url) if self.fetched_url else None)
        self.err_accounts = G.GlassButton("Open accounts", "user", "primary", height=42)
        self.err_accounts.setMinimumWidth(160)
        self.err_accounts.clicked.connect(self.open_accounts)
        row.addWidget(retry)
        row.addWidget(self.err_accounts)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _page_preview(self):
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(16)
        top = QHBoxLayout()
        top.setSpacing(22)
        self.thumb = QLabel()
        self.thumb.setFixedSize(360, 214)
        top.addWidget(self.thumb, 0, Qt.AlignTop)
        r = QVBoxLayout()
        r.setSpacing(8)
        meta = QHBoxLayout()
        meta.setSpacing(10)
        self.p_badge = Chip("", accent=True)
        self.p_author = QLabel("")
        self.p_author.setObjectName("muted")
        meta.addWidget(self.p_badge)
        meta.addWidget(self.p_author, 1)
        r.addLayout(meta)
        r.addSpacing(4)
        self.p_title = ElidedLabel(G.font(15.5, SEMI), TEXT, 3)
        self.p_desc = ElidedLabel(G.font(10), MUTED, 2)
        r.addWidget(self.p_title)
        r.addWidget(self.p_desc)
        r.addStretch(1)
        self.q_chips = QHBoxLayout()
        self.q_chips.setSpacing(8)
        r.addLayout(self.q_chips)
        top.addLayout(r, 1)
        col.addLayout(top)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tiles: dict[str, tuple[G.GlassPanel, QLabel]] = {}
        for key, label in (("length", "Length"), ("views", "Views"), ("likes", "Likes"), ("date", "Posted")):
            tp = G.GlassPanel(18, 0.55, shadow=False, grain=False)
            tp.setFixedHeight(68)
            tl = QVBoxLayout(tp)
            tl.setContentsMargins(18, 10, 14, 10)
            tl.setSpacing(2)
            lb = QLabel(label)
            lb.setObjectName("muted")
            lb.setFont(G.font(8.5))
            val = QLabel("")
            val.setFont(G.font(14, QFont.Weight.Medium))
            tl.addWidget(lb)
            tl.addWidget(val)
            tiles.addWidget(tp, 1)
            self.tiles[key] = (tp, val)
        col.addLayout(tiles)
        return w

    # ---- download card (right of the first row)
    def _build_download_card(self):
        card = G.GlassPanel(30)
        card.setMinimumWidth(392)
        v = QVBoxLayout(card)
        v.setContentsMargins(22, 18, 22, 20)
        v.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("Download")
        t.setFont(G.font(12, SEMI))
        self.dl_state = QLabel("Waiting for a link")
        self.dl_state.setObjectName("muted")
        self.dl_state.setFont(G.font(9))
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(self.dl_state)
        v.addLayout(head)
        self.dial = G.Dial(150)
        v.addWidget(self.dial, 0, Qt.AlignHCenter)
        self.dl_status = QLabel("")
        self.dl_status.setObjectName("muted")
        self.dl_status.setFont(G.font(9))
        self.dl_status.setWordWrap(True)
        self.dl_status.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.dl_status.setMinimumHeight(30)
        v.addWidget(self.dl_status)
        self.fmt = G.PillGroup(["MP4 H.264", "MP3 audio", "MP4 original"], height=40, equal=True)
        self.fmt.setToolTip("MP4 H.264: best quality, converted when needed so it plays on any PC.\n"
                            "MP3 audio: audio only.\nMP4 original: keeps the site's file (TikTok often uses HEVC, "
                            "which Windows may not play without an extension).")
        self.fmt.changed.connect(self._on_format)
        v.addWidget(self.fmt)
        opts = QHBoxLayout()
        opts.setSpacing(10)
        self.q_combo = QComboBox()
        self.q_combo.setFixedHeight(40)
        self.q_combo.addItem("Best available", None)
        self.q_combo.currentIndexChanged.connect(lambda *_: self._idle_dial())
        self.dir_btn = G.GlassButton("", "folder", height=40)
        self.dir_btn.clicked.connect(self.choose_dir)
        opts.addWidget(self.q_combo, 1)
        opts.addWidget(self.dir_btn, 1)
        v.addLayout(opts)
        self._update_dir_btn()
        act = QHBoxLayout()
        act.setSpacing(10)
        self.cancel_btn = G.GlassButton("Cancel", None, "glass", height=46, radius=16)
        self.cancel_btn.setMinimumWidth(104)
        self.cancel_btn.clicked.connect(lambda: self.dl_task and self.dl_task.cancel())
        self.cancel_btn.hide()
        self.dl_btn = G.GlassButton("Download", "download", "primary", height=46, radius=16, pt=10.5)
        self.dl_btn.clicked.connect(self.start_download)
        self.dl_btn.setEnabled(False)
        act.addWidget(self.cancel_btn)
        act.addWidget(self.dl_btn, 1)
        v.addLayout(act)
        self._idle_dial()
        return card

    # ---- library
    def _build_library(self):
        panel = G.GlassPanel(32)
        v = QVBoxLayout(panel)
        v.setContentsMargins(22, 16, 22, 18)
        v.setSpacing(12)
        tb = QHBoxLayout()
        tb.setSpacing(12)
        self.tab_lib = G.GlassButton("Library", None, "glass", height=42, radius=21)
        self.tab_hid = G.GlassButton("Hidden", None, "glass", height=42, radius=21)
        tgrp = QButtonGroup(self)
        tgrp.setExclusive(True)
        for b in (self.tab_lib, self.tab_hid):
            b.setCheckable(True)
            b.setMinimumWidth(128)
            tgrp.addButton(b)
        self.tab_lib.setChecked(True)
        self.tab_lib.clicked.connect(lambda: self.set_tab("library"))
        self.tab_hid.clicked.connect(lambda: self.set_tab("hidden"))
        self.lib_title = QLabel("")  # not shown: counts live on the tabs
        self.lib_title.hide()
        sf = G.GlassField(radius=21, shadow=False)
        sf.setFixedSize(250, 42)
        sl = QHBoxLayout(sf)
        sl.setContentsMargins(14, 0, 12, 0)
        sl.setSpacing(8)
        si = QLabel()
        si.setPixmap(G.icon_pixmap_named("search", 18, MUTED))
        self.search = G.PromptEdit()
        self.search.setObjectName("bare")
        self.search.setPlaceholderText("Search videos")
        self.search.focusChanged.connect(sf.set_focused)
        self.search.textChanged.connect(lambda *_: self.refresh_library())
        sl.addWidget(si)
        sl.addWidget(self.search, 1)
        self.pf = G.PillGroup(["All", "X", "TikTok", "Instagram"], height=42)
        self.pf.changed.connect(lambda *_: self.refresh_library())
        self.vf = G.PillGroup([("", "grid"), ("", "list")], height=42, index=0 if self.view_mode == "grid" else 1)
        self.vf.changed.connect(lambda i: self.set_view("grid" if i == 0 else "list"))
        tb.addWidget(self.tab_lib)
        tb.addStretch(1)
        tb.addWidget(sf)
        tb.addWidget(self.pf)
        tb.addWidget(self.vf)
        tb.addStretch(1)
        tb.addWidget(self.tab_hid)
        v.addLayout(tb)

        self.lib_stack = QStackedWidget()
        ew = QWidget()
        ev = QVBoxLayout(ew)
        ev.setContentsMargins(6, 8, 6, 8)
        ev.addStretch(1)
        self.empty_icon = IconBadge("film", 64, ACCENT)
        self.empty_title = QLabel("")
        self.empty_title.setFont(G.font(15, SEMI))
        self.empty_sub = QLabel("")
        self.empty_sub.setObjectName("muted")
        self.empty_sub.setWordWrap(True)
        ev.addWidget(self.empty_icon, 0, Qt.AlignHCenter)
        ev.addSpacing(8)
        ev.addWidget(self.empty_title, 0, Qt.AlignHCenter)
        ev.addWidget(self.empty_sub, 0, Qt.AlignHCenter)
        ev.addStretch(1)
        self.empty_title.setAlignment(Qt.AlignHCenter)
        self.empty_sub.setAlignment(Qt.AlignHCenter)
        self.listw = Gallery()
        self.listw.setResizeMode(QListWidget.Adjust)
        self.listw.setMovement(QListWidget.Static)
        self.listw.setFrameShape(QFrame.NoFrame)
        self.listw.setMouseTracking(True)
        self.listw.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.listw.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listw.customContextMenuRequested.connect(self.lib_menu)
        self.listw.itemClicked.connect(lambda it: self.open_entry(it.data(Qt.UserRole)))
        self.listw.resized.connect(self._relayout_grid)
        self.listw.hideClicked.connect(lambda e: QTimer.singleShot(0, lambda: self.toggle_hidden(e)))
        self.lib_stack.addWidget(ew)
        self.lib_stack.addWidget(self.listw)
        v.addWidget(self.lib_stack, 1)
        self._apply_view()
        return panel

    def _relayout_grid(self):
        """Grid mode: choose a column count for the current width and stretch the cards to fill the row."""
        if self.view_mode != "grid":
            return
        gap = self.listw.spacing()
        avail = max(300, self.listw.width() - 26)  # reserve room for the scrollbar so layout never flaps
        n = max(1, avail // (GridDelegate.MIN_W + gap))
        w = avail // n - gap
        d = self.grid_delegate
        if w == d.W and self.listw.count() and self.listw.item(0).sizeHint().width() == w:
            return
        d.W = w
        size = QSize(w, d.H)
        for i in range(self.listw.count()):
            self.listw.item(i).setSizeHint(size)
        self.listw.doItemsLayout()

    # ======================================================================= connected accounts
    def _refresh_accounts(self):
        for plat, b in self.acct_chips.items():
            info = ACCOUNTS.info(plat)
            st, name = info["state"], info["username"]
            short = (name if len(name) <= 14 else name[:13] + "…") if name else ""
            if st in ("connected", "attention"):
                txt = f"{plat}  {'@' + short if short else 'connected'}"
                dot, tip = (QColor(ACCENT), f"{plat}: connected" + (f" as @{name}" if name else "")) if st == "connected" \
                    else (QColor(ERR), f"{plat}: needs attention. Click to manage.")
            elif st == "expired":
                txt, dot, tip = f"{plat}  expired", QColor(ERR), f"{plat}: session expired. Click to reconnect."
            elif st == "unreadable":
                txt, dot, tip = f"{plat}  reconnect", QColor(ERR), f"{plat}: saved session can't be read. Click to reconnect."
            else:
                txt, dot, tip = f"{plat}  not connected", QColor(DIM), f"{plat}: not connected. Click to connect."
            b.setText(txt)
            b.set_dot(dot)
            b.setToolTip(tip)
            b.setMinimumWidth(QFontMetrics(b.font()).horizontalAdvance(txt) + 56)

    def open_accounts(self):
        dlg = AccountsDialog(self)
        dlg.changed.connect(self._refresh_accounts)
        dlg.exec()
        self._refresh_accounts()

    # ======================================================================= events / clipboard
    def _set_status_text(self):
        self.status_lbl.setText("Downloading" if self.busy else "Ready")
        self.status_dot.color = QColor(ACCENT if not self.busy else "#e6c27a")
        self.status_dot.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.backdrop.update()

    def showEvent(self, e):
        super().showEvent(e)
        G.style_titlebar(self)
        QTimer.singleShot(0, self._relayout_grid)
        QTimer.singleShot(400, self.check_clipboard)

    def event(self, e):
        if e.type() == QEvent.WindowActivate:
            QTimer.singleShot(150, self.check_clipboard)
        return super().event(e)

    def check_clipboard(self):
        """If a supported link is on the clipboard and the box is empty, fill it in."""
        if self.busy or self.url_input.text():
            return
        url = extract_url(QGuiApplication.clipboard().text())
        if url and url != self.last_clip:
            self.last_clip = url
            self.url_input.setText(url)
            self.on_url_changed()

    def paste(self):
        txt = QGuiApplication.clipboard().text().strip()
        if txt:
            self.url_input.setText(txt)
            self.on_url_changed()

    def _open_dir(self):
        Path(self.save_dir).mkdir(parents=True, exist_ok=True)
        os.startfile(self.save_dir)  # noqa: S606

    def _update_dir_btn(self):
        self.dir_btn.setText(self.save_dir)
        self.dir_btn.setToolTip(f"Saving to: {self.save_dir}\nClick to change.")

    def choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose where to save videos", self.save_dir)
        if d:
            self.save_dir = os.path.normpath(d)
            self.cfg["save_dir"] = self.save_dir
            save_json(CFG_FILE, self.cfg)
            self._update_dir_btn()

    # ======================================================================= URL detection + preview
    def on_url_changed(self):
        self.url_timer.stop()
        text = self.url_input.text().strip()
        self.clear_btn.setVisible(bool(text))
        if self.busy:
            return
        if not text:
            self.platform_tag.hide()
            self.hint.hide()
            self.fetched_url = None
            self.token += 1
            self.summary = None
            self.backdrop.set_wash(None)
            self.stack.setCurrentIndex(0)
            self.dl_btn.setEnabled(False)
            self.dl_state.setText("Waiting for a link")
            self._idle_dial()
            return
        url = extract_url(text)
        if not url:
            self.platform_tag.hide()
            self.hint.setText("That doesn't look like a link from X, TikTok or Instagram.")
            self.hint.show()
            self.stack.setCurrentIndex(0)
            return
        plat = platform_of(url)
        self.platform_tag.setText(f"{plat} detected")
        self.platform_tag.show()
        self.hint.hide()
        if url != self.fetched_url or self.stack.currentIndex() == 3:
            self.start_fetch(url)

    def start_fetch(self, url: str):
        self.token += 1
        self.fetched_url = url
        self.summary = None
        self.dl_btn.setEnabled(False)
        self.dl_state.setText("Reading the post")
        self.stack.setCurrentIndex(1)
        task = FetchTask(self.token, url)
        task.s.info.connect(self.on_info)
        task.s.info_err.connect(self.on_info_err)
        self.tasks.add(task)
        self.pool.start(task)

    def on_info_err(self, token: int, msg: str):
        if token != self.token:
            return
        self.err_lbl.setText(msg)
        self.err_accounts.setVisible("ACCOUNTS" in msg)
        self.dl_state.setText("Couldn't read the post")
        self.backdrop.set_wash(None)
        self.stack.setCurrentIndex(3)

    def on_info(self, token: int, s: dict):
        if token != self.token:
            return
        self.summary = s
        self.thumb.setPixmap(G.framed_pixmap(None, 360, 214, 20, data=s.get("thumb")))
        self.backdrop.set_wash(G.make_wash(s.get("thumb")))
        self.p_badge.setText(s["platform"] or "")
        self.p_author.setText(("@" + s["uploader"].lstrip("@")) if s["uploader"] else "")
        self.p_title.setText(s["title"])
        desc = s["description"]
        self.p_desc.setText(desc)
        self.p_desc.setVisible(bool(desc))
        vals = {"length": fmt_duration(s["duration"]), "views": fmt_count(s["views"]) if s["views"] is not None else "",
                "likes": fmt_count(s["likes"]) if s["likes"] is not None else "", "date": s["date"]}
        for key, (panel, lab) in self.tiles.items():
            lab.setText(vals[key])
            panel.setVisible(bool(vals[key]))
        self.q_combo.blockSignals(True)
        self.q_combo.clear()
        self.q_combo.addItem("Best available", None)
        for h, meta in s["heights"]:
            label = f"{min(meta['w'] or h, h)}p"
            if meta["size"]:
                label += f"  ·  {fmt_size(meta['size'])}"
            self.q_combo.addItem(label, h)
        self.q_combo.blockSignals(False)
        while self.q_chips.count():
            it = self.q_chips.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for h, meta in s["heights"][:4]:
            self.q_chips.addWidget(Chip(f"{min(meta['w'] or h, h)}p"))
        self.q_chips.addStretch(1)
        self.fmt.setIndex(0, animate=False)
        self.q_combo.setEnabled(True)
        self.dl_status.setStyleSheet("")
        self.dl_status.setText("Choose a format and quality, then press Download.")
        self.dl_btn.setEnabled(True)
        self.dl_state.setText("Ready")
        self._idle_dial()
        self.stack.setCurrentIndex(2)

    # ======================================================================= download
    def _on_format(self, i: int):
        self.q_combo.setEnabled(i != 1 and not self.busy)
        self._idle_dial()

    def _idle_dial(self):
        if self.busy:
            return
        if self.fmt.index() == 1:
            big = "MP3"
        elif self.q_combo.currentData() is None:
            big = "Best"
        else:
            big = self.q_combo.currentText().split()[0]
        self.dial.set_idle(big, "ready" if self.summary else "paste a link")

    def set_busy(self, busy: bool):
        self.busy = busy
        for w in (self.url_input, self.paste_btn, self.q_combo, self.fmt, self.dir_btn):
            w.setEnabled(not busy)
        if not busy:
            self.q_combo.setEnabled(self.fmt.index() != 1)
        self.dl_btn.setEnabled(not busy and self.summary is not None)
        self.cancel_btn.setVisible(busy)
        self.dl_btn.setText("Downloading..." if busy else "Download")
        self.dl_state.setText("Downloading" if busy else "Ready")
        self._set_status_text()

    def start_download(self):
        if self.busy or not self.summary:
            return
        self.token += 1
        audio = self.fmt.index() == 1
        tag = "" if audio or self.q_combo.currentData() is None else self.q_combo.currentText().split()[0]
        task = DownloadTask(self.token, self.summary, self.save_dir, self.q_combo.currentData(), audio, tag,
                            compat=self.fmt.index() == 0)
        task.s.progress.connect(self.on_progress)
        task.s.done.connect(self.on_done)
        task.s.fail.connect(self.on_fail)
        self.dl_task = task
        self.tasks.add(task)
        self.set_busy(True)
        self.dial.set_progress(0.0, "0%", "starting")
        self.dl_status.setStyleSheet("")
        self.dl_status.setText("Saving to " + os.path.basename(self.save_dir.rstrip("\\/")) )
        self.pool.start(task)

    def on_progress(self, token: int, frac: float, text: str):
        if token != self.token:
            return
        short = " ".join(text.split())
        self.dial.set_progress(frac, f"{int(frac * 100)}%", short)
        if len(short) > 20:  # e.g. "converting to h.264 so it plays everywhere"
            self.dl_status.setText(short[:1].upper() + short[1:])

    def _finish_task(self):
        if self.dl_task:
            self.tasks.discard(self.dl_task)
        self.dl_task = None
        self.set_busy(False)

    def on_done(self, token: int, entry: dict):
        if token != self.token:
            return
        self._finish_task()
        self.dial.set_done("saved to library")
        self.dl_status.setStyleSheet(f"color:{ACCENT};")
        self.dl_status.setText(f"Saved: {entry['filename']}")
        self.dl_state.setText("Done")
        for old in [x for x in self.lib.items if os.path.normcase(x["path"]) == os.path.normcase(entry["path"])]:
            self.lib.remove(old)  # same file downloaded again -> keep a single library entry
        self.lib.add(entry)
        if self.lib_tab != "library":
            self.set_tab("library")
        self.refresh_library(animate=True)
        self.listw.setCurrentRow(0)
        self.listw.scrollToTop()
        self.toast.popup("Download complete. Added to your library.")

    def on_fail(self, token: int, msg: str):
        if token != self.token:
            return
        self._finish_task()
        self.dial.set_error("failed")
        self.dl_status.setStyleSheet(f"color:{ERR};")
        self.dl_status.setText(msg)
        self.dl_state.setText("Failed")

    # ======================================================================= library
    def set_view(self, mode: str):
        self.view_mode = mode
        self.cfg["view"] = mode
        save_json(CFG_FILE, self.cfg)
        self.vf.setIndex(0 if mode == "grid" else 1)
        self._apply_view()
        self.refresh_library(animate=True)

    def _apply_view(self):
        grid = self.view_mode == "grid"
        self.listw.setViewMode(QListWidget.IconMode if grid else QListWidget.ListMode)
        self.listw.setFlow(QListWidget.LeftToRight if grid else QListWidget.TopToBottom)
        self.listw.setWrapping(grid)
        self.listw.setUniformItemSizes(True)
        self.listw.setSpacing(8 if grid else 4)
        self.listw.setItemDelegate(self.grid_delegate if grid else self.list_delegate)

    def set_tab(self, tab: str):
        self.lib_tab = tab
        (self.tab_hid if tab == "hidden" else self.tab_lib).setChecked(True)
        self.rail_btns["hidden" if tab == "hidden" else "library"].setChecked(True)
        self.refresh_library(animate=True)

    def toggle_hidden(self, e: dict) -> None:
        """Hide a video from the main library (or restore it). Only the library record changes."""
        hide = not e.get("hidden")
        if self.lib.set_hidden(e["id"], hide):
            self.refresh_library()
            self.toast.popup("Hidden. Find it in the Hidden tab. The file is untouched." if hide
                             else "Restored to your library.")

    def refresh_library(self, animate: bool = False):
        q = self.search.text().strip().lower()
        plat = self.pf.items[self.pf.index()][0]
        grid = self.view_mode == "grid"
        hidden_tab = self.lib_tab == "hidden"
        n_hidden = sum(1 for x in self.lib.items if x.get("hidden"))
        n_visible = len(self.lib.items) - n_hidden
        self.tab_lib.setText(f"Library  {n_visible}")
        self.tab_hid.setText(f"Hidden  {n_hidden}")
        self.listw.clear()
        total = n_hidden if hidden_tab else n_visible
        for e in self.lib.items:
            if bool(e.get("hidden")) != hidden_tab:
                continue
            if plat != "All" and e.get("platform") != plat:
                continue
            hay = f"{e.get('title','')} {e.get('uploader','')} {e.get('filename','')} {e.get('platform','')}".lower()
            if q and q not in hay:
                continue
            it = QListWidgetItem()
            it.setData(Qt.UserRole, e)
            it.setToolTip(f"{e.get('title','')}\n{e['filename']}")
            it.setSizeHint(QSize(self.grid_delegate.W, self.grid_delegate.H) if grid else QSize(100, ListDelegate.H))
            self.listw.addItem(it)
        shown = self.listw.count()
        if grid:
            self._relayout_grid()
        self.lib_title.setText(f"{shown if shown == total else f'{shown} of {total}'} video{'s' if shown != 1 else ''}")
        if shown:
            self.lib_stack.setCurrentIndex(1)
            if animate:
                self.listw.start_reveal()
        else:
            self.lib_stack.setCurrentIndex(0)
            if total == 0 and hidden_tab:
                self.empty_icon.icon = "eye-off"
                self.empty_title.setText("Nothing hidden")
                self.empty_sub.setText("Use Hide on any video to move it here. Hidden videos stay saved on your PC, "
                                       "and Unhide puts them back in the library.")
            elif total == 0:
                self.empty_icon.icon = "film"
                self.empty_title.setText("Your library is empty")
                self.empty_sub.setText("Paste a link above and download a video. It will appear here with its thumbnail, "
                                       "source link and date.")
            else:
                self.empty_icon.icon = "search"
                self.empty_title.setText("No matches")
                self.empty_sub.setText("Nothing fits the current search or platform filter.")
            self.empty_icon.update()

    def open_entry(self, e: dict):
        if not Path(e["path"]).exists():
            r = QMessageBox.question(self, "File missing",
                                     "This file was moved or deleted outside the app.\nRemove it from your library?")
            if r == QMessageBox.Yes:
                self.lib.remove(e)
                self.refresh_library()
            return
        PlayerDialog(self, e).exec()
        self.refresh_library()

    def copy_link(self, e: dict):
        QGuiApplication.clipboard().setText(e.get("url") or "")
        self.last_clip = e.get("url")  # don't auto-paste our own copy back into the URL box
        self.toast.popup("Link copied.")

    def delete_entry(self, e: dict, parent=None, before=None) -> bool:
        box = QMessageBox(parent or self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Delete video")
        box.setText(f"Delete \"{(e.get('title') or e['filename'])[:80]}\"?")
        box.setInformativeText("The file is moved to the Recycle Bin, so you can still restore it from there.")
        b_del = box.addButton("Move to Recycle Bin", QMessageBox.DestructiveRole)
        b_rm = box.addButton("Remove from library only", QMessageBox.ActionRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_del, b_rm):
            return False
        if before:
            before()
        if clicked is b_del and Path(e["path"]).exists():
            if not move_to_recycle_bin(e["path"]):
                QMessageBox.warning(parent or self, "Couldn't delete", "The file is in use or couldn't be removed. "
                                    "Close any player showing it and try again.")
                return False
        self.lib.remove(e)
        self.refresh_library()
        self.toast.popup("Removed from your library.")
        return True

    def lib_menu(self, pos):
        it = self.listw.itemAt(pos)
        if not it:
            return
        e = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("Play", lambda: self.open_entry(e))
        m.addAction("Open folder", lambda: open_folder_of(e["path"]))
        m.addAction("Copy original link", lambda: self.copy_link(e))
        m.addAction("Unhide (restore to library)" if e.get("hidden") else "Hide from library",
                    lambda: self.toggle_hidden(e))
        m.addSeparator()
        m.addAction("Delete...", lambda: self.delete_entry(e))
        m.exec(self.listw.viewport().mapToGlobal(pos))


# ----------------------------------------------------------------------------- diagnostics + entry point
def probe(url: str, outfile: str) -> None:
    """Headless check used for diagnostics: fetch info for a link and write the result to a file."""
    import yt_dlp
    res: dict = {}
    task = FetchTask(1, url)
    task.s.info.connect(lambda _t, s: res.update(ok=True, **{k: v for k, v in s.items() if k not in ("thumb", "heights")},
                                                  has_thumb=bool(s.get("thumb")), qualities=[h for h, _ in s["heights"]]))
    task.s.info_err.connect(lambda _t, m: res.update(ok=False, error=m))
    task.run()
    try:
        import curl_cffi
        res["curl_cffi"] = curl_cffi.__version__
    except Exception as e:  # noqa: BLE001
        res["curl_cffi"] = f"missing ({e})"
    res["yt_dlp"] = yt_dlp.version.__version__
    Path(outfile).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")


def webengine_check(outfile: str) -> None:
    """Diagnostic: confirm the built-in browser (used for sign-in) starts and can run a page."""
    app = QApplication(sys.argv)
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    prof = QWebEngineProfile()
    page = QWebEnginePage(prof)
    view = QWebEngineView()
    view.setPage(page)
    res: dict = {"loaded": None, "title": None}

    def loaded(ok):
        res["loaded"] = ok
        page.runJavaScript("document.title", lambda v: (res.update(title=v), app.quit()))

    page.loadFinished.connect(loaded)
    page.renderProcessTerminated.connect(lambda s, c: (res.update(render_process_exit=int(c)), app.quit()))
    view.load(QUrl("data:text/html,<title>webengine-ok</title>"))
    QTimer.singleShot(25000, app.quit)
    app.exec()
    Path(outfile).write_text(json.dumps(res), encoding="utf-8")


def main() -> None:
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)  # required before QApplication when using WebEngine
    if len(sys.argv) > 2 and sys.argv[1] == "--webengine-check":
        webengine_check(sys.argv[2])
        return
    if len(sys.argv) > 3 and sys.argv[1] == "--probe":
        probe(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) > 2 and sys.argv[1] == "--make-icon":
        QGuiApplication(sys.argv)
        G.icon_pixmap(256).toImage().save(sys.argv[2])
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VideoDownloader.App")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    G.apply_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
