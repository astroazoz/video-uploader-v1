"""Video Downloader UI: futuristic HUD menu + terminal. All download / account / library logic lives in core.py."""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import glass as G
import hud as H
import updater as UP
from accounts import ACCOUNTS, PLATFORM_CFG
from core import (APP_NAME, CFG_FILE, VERSION, PLATFORM_TAG, DownloadTask, FetchTask, Library, extract_url, ffmpeg_path,
                  fmt_count, fmt_duration, fmt_size, load_json, move_to_recycle_bin, open_folder_of, platform_of,
                  save_json)
from PySide6.QtCore import (QCoreApplication, QEvent, QObject, QPoint, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QKeySequence, QLinearGradient,
                           QPainter, QPen, QShortcut, QTextLayout, QTextOption)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QSizePolicy, QSlider,
                               QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

TEXT, MUTED, DIM, ACCENT, ACCENT_HI, ERR = H.TEXT, H.MUTED, H.DIM, H.ACCENT, H.ACCENT_HI, H.ERR
SEMI = QFont.Weight.DemiBold


# ----------------------------------------------------------------------------- helpers
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


class ElidedLabel(QLabel):
    """Wrapped text capped at N lines with an ellipsis (height stays constant)."""

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


def entry_badge(e: dict) -> str:
    """Short label on a card: length for videos, photo count for photo posts, MP3 for audio."""
    if e.get("kind") == "image":
        n = e.get("images") or 1
        return f"{n} photo{'s' if n != 1 else ''}"
    return fmt_duration(e.get("duration")) or ("MP3" if e.get("kind") == "audio" else "")


def entry_date(e: dict) -> str:
    try:
        return datetime.fromisoformat(e["date"]).strftime("%Y-%m-%d")
    except Exception:
        return ""


def draw_brackets(p: QPainter, r: QRectF, n: float = 11, alpha: int = 215) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(G.col(ACCENT, alpha), 1.6, Qt.SolidLine, Qt.FlatCap))
    p.drawPolyline([QPoint(int(r.left()), int(r.top() + n)), QPoint(int(r.left()), int(r.top())), QPoint(int(r.left() + n), int(r.top()))])
    p.drawPolyline([QPoint(int(r.right() - n), int(r.bottom())), QPoint(int(r.right()), int(r.bottom())), QPoint(int(r.right()), int(r.bottom() - n))])
    p.restore()


def draw_tag(p: QPainter, x: float, y: float, text: str) -> float:
    f = H.mfont(7.6, 1.0, H.DOWN)
    t = text.upper()
    w = QFontMetrics(f).horizontalAdvance(t) + 16
    r = QRectF(x + 0.5, y + 0.5, w - 1, 19)
    p.save()
    p.fillRect(r, QColor(5, 5, 6, 220))
    p.setPen(QPen(G.W(72), 1))
    p.drawRect(r)
    p.setPen(QColor(TEXT))
    p.setFont(f)
    p.drawText(r, Qt.AlignCenter, t)
    p.restore()
    return w


def draw_action(p: QPainter, rect: QRect, hidden: bool, hot: bool) -> None:
    """HIDE / UNHIDE button on a card or row."""
    r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    p.save()
    p.fillRect(r, QColor(5, 5, 6, 228))
    p.setPen(QPen(G.col(ACCENT, 235) if hot else G.W(84), 1))
    p.drawRect(r)
    c = QColor(ACCENT_HI) if hot else QColor(TEXT)
    G.draw_icon(p, "eye" if hidden else "eye-off", QRectF(r.x() + 9, r.center().y() - 7, 14, 14), c, 1.5)
    p.setPen(c)
    p.setFont(H.hfont(7.6, 1.6))
    p.drawText(QRectF(r.x() + 28, r.y(), r.width() - 30, r.height()), Qt.AlignVCenter | Qt.AlignLeft, "UNHIDE" if hidden else "HIDE")
    p.restore()


def section_label(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setObjectName("section")
    lb.setFont(H.hfont(8.2, 2.6))
    return lb


def hairline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet("background: rgba(255,255,255,0.18);")
    return f


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

    def start_reveal(self):
        """Staggered entrance: cards slide in one after another."""
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
        rv = self.gallery.reveal_value(eid) if self.gallery is not None else 1.0
        if rv < 1.0:
            ease = 1 - (1 - rv) ** 3
            p.setOpacity(ease)
            p.translate((1 - ease) * -16, 0)  # slides in from the left


class GridDelegate(_DelegateBase):
    MIN_W = 236
    W = 244  # set by MainWindow so cards fill each row exactly
    cache: dict = {}

    @staticmethod
    def thumb_h(w: int) -> int:
        return max(100, int((w - 10 - 20) * 9 / 16))

    @property
    def H(self) -> int:
        return self.thumb_h(self.W) + 100

    def sizeHint(self, option, index):
        return QSize(self.W, self.H)

    @staticmethod
    def action_rect(item_rect: QRect, hidden: bool) -> QRect:
        r = item_rect.adjusted(5, 5, -5, -5)
        w = 88 if hidden else 70
        return QRect(r.x() + 10 + (r.width() - 20) - w - 8, r.y() + 18, w, 24)

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
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, G.W(12 if (hover or sel) else 9))
        g.setColorAt(1, G.W(4))
        p.fillRect(r, g)
        p.setPen(QPen(G.col(ACCENT, 200) if (hover or sel) else G.W(34), 1.3 if sel else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        if hover or sel:
            draw_brackets(p, r.adjusted(0.5, 0.5, -0.5, -0.5))
        tr = QRect(int(r.x()) + 10, int(r.y()) + 10, int(r.width()) - 20, self.thumb_h(self.W))
        p.drawPixmap(tr.topLeft(), G.framed_pixmap(e.get("thumb"), tr.width(), tr.height(), 1, cache=self.cache))
        p.setPen(QPen(G.W(40), 1))
        p.drawRect(tr.adjusted(0, 0, -1, -1))
        draw_tag(p, tr.x() + 8, tr.y() + 8, PLATFORM_TAG.get(e.get("platform"), "VIDEO"))
        dur = entry_badge(e)
        if dur:
            w = QFontMetrics(H.mfont(7.6, 1.0, H.DOWN)).horizontalAdvance(dur.upper()) + 16
            draw_tag(p, tr.right() - w - 6, tr.bottom() - 26, dur)
        if missing:
            p.fillRect(tr, QColor(5, 5, 6, 215))
            p.setPen(QColor(ERR))
            p.setFont(H.hfont(9, 2.4))
            p.drawText(tr, Qt.AlignCenter, "FILE MISSING")
        elif hover:
            c = QRectF(tr).center()
            box = QRectF(c.x() - 22, c.y() - 22, 44, 44)
            p.fillRect(box, QColor(5, 5, 6, 200))
            p.setPen(QPen(G.col(ACCENT, 230), 1))
            p.drawRect(box.adjusted(0.5, 0.5, -0.5, -0.5))
            G.draw_icon(p, "play", QRectF(c.x() - 9, c.y() - 9, 18, 18), QColor(ACCENT_HI), 1.7)
        draw_action(p, self.action_rect(opt.rect, bool(e.get("hidden"))).translated(0, -2 if hover else 0),
                    bool(e.get("hidden")), e.get("id") == self.hover_id)
        p.setFont(H.bfont(9.6, SEMI))
        p.setPen(QColor(TEXT))
        wrapped_text(p, e.get("title") or e["filename"], QRect(int(r.x()) + 12, tr.bottom() + 12, int(r.width()) - 24, 38), 2)
        p.setFont(H.mfont(8, 0.6))
        p.setPen(QColor(MUTED))
        meta = "  ·  ".join(x for x in (entry_date(e), fmt_size(e.get("size"))) if x).upper()
        p.drawText(QRect(int(r.x()) + 12, int(r.bottom()) - 28, int(r.width()) - 24, 20), Qt.AlignVCenter | Qt.AlignLeft, meta)
        p.restore()


class ListDelegate(_DelegateBase):
    """One bracketed row per item: thumbnail, title + file name, then platform / length / size / date and the button."""
    H = 84
    cache: dict = {}

    def sizeHint(self, option, index):
        return QSize(100, self.H)

    @staticmethod
    def action_rect(item_rect: QRect, hidden: bool) -> QRect:
        r = item_rect.adjusted(3, 3, -3, -3)
        return QRect(r.right() - 16 - 96, r.y() + (r.height() - 28) // 2, 96, 28)

    def paint(self, p: QPainter, opt, index):
        e = index.data(Qt.UserRole)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        missing = not Path(e["path"]).exists()
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self._enter(p, e["id"])
        r = QRectF(opt.rect.adjusted(3, 3, -3, -3))
        p.fillRect(r, G.W(11 if (hover or sel) else 5))
        p.setPen(QPen(G.col(ACCENT, 200) if (hover or sel) else G.W(30), 1.3 if sel else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        if hover or sel:
            p.fillRect(QRectF(r.x(), r.y(), 3, r.height()), G.col(ACCENT))
        tr = QRect(int(r.x()) + 12, int(r.y()) + 9, 104, 58)
        p.drawPixmap(tr.topLeft(), G.framed_pixmap(e.get("thumb"), tr.width(), tr.height(), 1, cache=self.cache))
        p.setPen(QPen(G.W(40), 1))
        p.drawRect(tr.adjusted(0, 0, -1, -1))
        cols = [(98, PLATFORM_TAG.get(e.get("platform"), "VIDEO")), (78, entry_badge(e) or "-"),
                (90, fmt_size(e.get("size")) or "-"), (104, entry_date(e) or "-")]
        x = r.right() - 16 - 104 - 14 - sum(w for w, _ in cols)
        p.setFont(H.mfont(8.6, 0.8))
        for w, text in cols:
            p.setPen(QColor(TEXT if text in PLATFORM_TAG.values() else MUTED))
            p.drawText(QRect(int(x), int(r.y()), w, int(r.height())), Qt.AlignVCenter | Qt.AlignLeft, text.upper())
            x += w
        tx = tr.right() + 18
        tw = max(60, int(r.right() - 16 - 104 - 14 - sum(w for w, _ in cols)) - tx - 12)
        p.setFont(H.bfont(10.2, SEMI))
        p.setPen(QColor(ERR if missing else TEXT))
        title = ("FILE MISSING: " if missing else "") + (e.get("title") or e["filename"])
        p.drawText(QRect(tx, int(r.y()) + 13, tw, 24), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(p.font()).elidedText(title, Qt.ElideRight, tw))
        p.setFont(H.mfont(8.2, 0.3))
        p.setPen(QColor(MUTED))
        p.drawText(QRect(tx, int(r.y()) + 41, tw, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(p.font()).elidedText(e["filename"], Qt.ElideMiddle, tw))
        draw_action(p, self.action_rect(opt.rect, bool(e.get("hidden"))), bool(e.get("hidden")), e.get("id") == self.hover_id)
        p.restore()


# ----------------------------------------------------------------------------- dialogs
class ClickableVideo(QVideoWidget):
    clicked = Signal()

    def mousePressEvent(self, e):
        self.clicked.emit()


def dialog_header(tail: str, title: str) -> QVBoxLayout:
    box = QVBoxLayout()
    box.setSpacing(4)
    crumb = QLabel(f"<span style='color:{MUTED}'>VIDEO DOWNLOADER // </span><span style='color:{ACCENT}'>{tail.upper()}</span>")
    crumb.setFont(H.mfont(8.5, 1.4))
    t = QLabel(title)
    t.setFont(H.hfont(15, 2.4))
    t.setMaximumWidth(760)
    box.addWidget(crumb)
    box.addWidget(t)
    box.addWidget(hairline())
    return box


def info_grid(rows) -> QGridLayout:
    grid = QGridLayout()
    grid.setContentsMargins(20, 14, 20, 14)
    grid.setHorizontalSpacing(22)
    grid.setVerticalSpacing(6)
    for i, (k, v) in enumerate(rows):
        kl = QLabel(k.upper())
        kl.setFont(H.mfont(8.5, 1.2))
        kl.setObjectName("muted")
        kl.setAlignment(Qt.AlignTop)
        vl = QLabel(v)
        vl.setFont(H.mfont(9.2, 0.2))
        vl.setWordWrap(True)
        vl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        vl.setOpenExternalLinks(True)
        grid.addWidget(kl, i, 0)
        grid.addWidget(vl, i, 1)
    grid.setColumnStretch(1, 1)
    return grid


class PlayerDialog(H.HudDialog):
    def __init__(self, win: "MainWindow", entry: dict):
        super().__init__(win)
        self.win, self.entry = win, entry
        self.setWindowTitle(entry.get("title") or "Video")
        self.resize(1000, 800)
        self.setMinimumSize(760, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)
        root.addLayout(dialog_header("Player", (entry.get("title") or entry["filename"]).upper()[:70]))

        vp = H.HudPanel(0.6)
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

        cp = H.HudPanel(0.8, brackets=False)
        cl = QHBoxLayout(cp)
        cl.setContentsMargins(12, 8, 18, 8)
        cl.setSpacing(14)
        self.play_btn = H.HudIconButton("pause", 40, "primary", "Play / pause")
        self.play_btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setFont(H.mfont(9.5, 0.4))
        self.time_lbl.setMinimumWidth(112)
        vol = QSlider(Qt.Horizontal)
        vol.setRange(0, 100)
        vol.setValue(80)
        vol.setFixedWidth(96)
        vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        vl2 = QLabel("VOL")
        vl2.setFont(H.mfont(8.5, 1.4))
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

        ip = H.HudPanel(0.6, brackets=False)
        try:
            date = datetime.fromisoformat(entry["date"]).strftime("%Y-%m-%d  %H:%M")
        except Exception:
            date = "-"
        link = entry.get("url") or ""
        ip.setLayout(info_grid([("platform", (entry.get("platform") or "-").lower()),
                                ("source", f'<a href="{link}" style="color:{ACCENT_HI}">{link}</a>' if link else "-"),
                                ("file", entry["filename"]), ("saved", date),
                                ("info", "  ·  ".join(x for x in (fmt_duration(entry.get("duration")), fmt_size(entry.get("size")),
                                                                      "mp3 audio" if entry.get("kind") == "audio" else "mp4 video") if x))]))
        root.addWidget(ip)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        for text, icon, fn in (("Open folder", "folder", lambda: open_folder_of(entry["path"])),
                               ("Copy link", "copy", lambda: win.copy_link(entry)),
                               ("Default player", "external", self.open_external),
                               ("Unhide" if entry.get("hidden") else "Hide", "eye" if entry.get("hidden") else "eye-off", self.toggle_hide)):
            b = H.HudButton(text, icon, height=40)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        d = H.HudButton("Delete", "trash", "danger", height=40)
        d.clicked.connect(self.delete)
        c = H.HudButton("Close", None, "primary", height=40)
        c.setMinimumWidth(120)
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
        self.err.setText("[err] this file can't be played inside the app" + (f" ({why})" if why else "") +
                         ". use DEFAULT PLAYER, or download it again as MP4 H.264.")
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


class ImageViewer(H.HudDialog):
    """Photo viewer for saved photo posts (one photo, or a folder of several)."""

    def __init__(self, win: "MainWindow", entry: dict):
        super().__init__(win)
        self.win, self.entry, self.idx = win, entry, 0
        files = [f for f in (entry.get("files") or []) if Path(f).exists()]
        if not files:
            p = Path(entry["path"])
            files = sorted(str(x) for x in p.iterdir() if x.suffix.lower().lstrip(".") in ("jpg", "jpeg", "png", "webp")) \
                if p.is_dir() else [str(p)]
        self.files = files
        self.setWindowTitle(entry.get("title") or "Photos")
        self.resize(1000, 820)
        self.setMinimumSize(760, 660)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)
        root.addLayout(dialog_header("Photos", (entry.get("title") or entry["filename"]).upper()[:70]))

        vp = H.HudPanel(0.6)
        vl = QVBoxLayout(vp)
        vl.setContentsMargins(10, 10, 10, 10)
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumSize(420, 300)
        self.view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        vl.addWidget(self.view)
        root.addWidget(vp, 1)

        nav = H.HudPanel(0.8, brackets=False)
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(10, 8, 10, 8)
        self.prev_btn = H.HudIconButton("prev", 40, "glass", "Previous photo (Left arrow)")
        self.next_btn = H.HudIconButton("next", 40, "glass", "Next photo (Right arrow)")
        self.counter = QLabel("")
        self.counter.setAlignment(Qt.AlignCenter)
        self.counter.setFont(H.mfont(10, 1.4, SEMI))
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn.clicked.connect(lambda: self.step(1))
        nl.addWidget(self.prev_btn)
        nl.addWidget(self.counter, 1)
        nl.addWidget(self.next_btn)
        nav.setVisible(len(files) > 1)
        root.addWidget(nav)

        ip = H.HudPanel(0.6, brackets=False)
        try:
            date = datetime.fromisoformat(entry["date"]).strftime("%Y-%m-%d  %H:%M")
        except Exception:
            date = "-"
        link = entry.get("url") or ""
        ip.setLayout(info_grid([("platform", (entry.get("platform") or "-").lower()),
                                ("source", f'<a href="{link}" style="color:{ACCENT_HI}">{link}</a>' if link else "-"),
                                ("saved as", ("folder: " if len(files) > 1 else "") + entry["filename"]), ("saved", date),
                                ("info", "  ·  ".join((entry_badge(entry), fmt_size(entry.get("size")), "original quality")))]))
        root.addWidget(ip)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        for text, icon, fn in (("Open folder", "folder", lambda: open_folder_of(entry["path"])),
                               ("Copy link", "copy", lambda: win.copy_link(entry)),
                               ("Unhide" if entry.get("hidden") else "Hide", "eye" if entry.get("hidden") else "eye-off", self.toggle_hide)):
            b = H.HudButton(text, icon, height=40)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        d = H.HudButton("Delete", "trash", "danger", height=40)
        d.clicked.connect(self.delete)
        c_ = H.HudButton("Close", None, "primary", height=40)
        c_.setMinimumWidth(120)
        c_.clicked.connect(self.accept)
        btns.addWidget(d)
        btns.addWidget(c_)
        root.addLayout(btns)
        self._show()

    def _show(self):
        n = len(self.files)
        self.prev_btn.setEnabled(self.idx > 0)
        self.next_btn.setEnabled(self.idx < n - 1)
        self.counter.setText(f"{self.idx + 1} / {n}")
        if not self.files:
            self.view.setText("No photos found")
            return
        w, h = max(200, self.view.width()), max(150, self.view.height())
        self.view.setPixmap(G.framed_pixmap(self.files[self.idx], w, h, 1))

    def step(self, d: int):
        j = self.idx + d
        if 0 <= j < len(self.files):
            self.idx = j
            self._show()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "view") and self.files:
            self._show()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._show)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Left:
            self.step(-1)
        elif e.key() == Qt.Key_Right:
            self.step(1)
        else:
            super().keyPressEvent(e)

    def toggle_hide(self):
        self.win.toggle_hidden(self.entry)
        self.accept()

    def delete(self):
        if self.win.delete_entry(self.entry, self):
            self.accept()


class AccountsDialog(H.HudDialog):
    """Connected accounts: connect / reconnect / disconnect TikTok, Instagram and X."""
    changed = Signal()
    ORDER = ("TikTok", "Instagram", "X")

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.setWindowTitle("Connected accounts")
        self.resize(800, 800)
        self.setMinimumSize(740, 780)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 24, 30, 22)
        lay.setSpacing(14)
        lay.addLayout(dialog_header("Accounts", "CONNECTED ACCOUNTS"))
        sub = QLabel("connect the accounts you want to use, so posts those accounts are allowed to see "
                     "(for example a private profile that accepted your follow) can be previewed and saved.")
        sub.setObjectName("muted")
        sub.setFont(H.mfont(9, 0.2))
        sub.setWordWrap(True)
        lay.addWidget(sub)

        self.rows: dict[str, dict] = {}
        for plat in self.ORDER:
            f = H.HudPanel(0.8)
            h = QHBoxLayout(f)
            h.setContentsMargins(22, 14, 18, 14)
            h.setSpacing(14)
            name = QLabel(PLATFORM_CFG[plat]["label"].upper())
            name.setFont(H.hfont(11, 3.0))
            name.setFixedWidth(150)
            status = QLabel("")
            status.setFont(H.mfont(9, 0.2))
            status.setWordWrap(True)
            connect = H.HudButton("Connect", "user", "primary", height=42)
            connect.setMinimumWidth(150)
            disconnect = H.HudButton("Disconnect", None, "danger", height=42)
            disconnect.setMinimumWidth(130)
            connect.clicked.connect(lambda _=False, p=plat: self.connect_account(p))
            disconnect.clicked.connect(lambda _=False, p=plat: self.disconnect_account(p))
            h.addWidget(name)
            h.addWidget(status, 1)
            h.addWidget(connect)
            h.addWidget(disconnect)
            lay.addWidget(f)
            self.rows[plat] = {"status": status, "connect": connect, "disconnect": disconnect}

        self.note = QLabel("")
        self.note.setFont(H.mfont(9, 0.2))
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        info = H.HudPanel(0.6, brackets=False)
        il = QVBoxLayout(info)
        il.setContentsMargins(22, 14, 22, 14)
        il.setSpacing(10)
        for head_t, body in (
            ("how sign-in works", "you sign in on the platform's own page, inside this app. your password is never seen "
                                  "or stored here. only the session cookie is kept, encrypted with Windows for your user "
                                  "account on this PC. disconnect deletes it."),
            ("what it can do", "open posts that the connected account is itself allowed to view. use it only for videos "
                               "you own or have the owner's permission to save."),
            ("limits", "the app never bypasses privacy settings. some private or download-restricted posts can't be saved "
                       "even when you can watch them, because the platform doesn't serve a file for them. using any account "
                       "with automated tools can occasionally trigger a security check on that platform. to end a session "
                       "everywhere, also sign out of other devices in the platform's own security settings.")):
            l = QLabel(f"<span style='color:{ACCENT_HI}'>{head_t.upper()}</span><br><span style='color:{MUTED}'>{body}</span>")
            l.setFont(H.mfont(8.6, 0.2))
            l.setWordWrap(True)
            il.addWidget(l)
        lay.addWidget(info)
        lay.addStretch(1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        close = H.HudButton("Close", None, "glass", height=42)
        close.setMinimumWidth(120)
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        lay.addLayout(foot)
        self.refresh()

    def refresh(self):
        for plat, w in self.rows.items():
            info = ACCOUNTS.info(plat)
            st, name, saved = info["state"], info["username"], info["saved"][:10]
            if st == "connected":
                dot, head = ACCENT, "connected" + (f" as @{name}" if name else "")
                sub = f"signed in, saved {saved}" if saved else "signed in"
            elif st == "attention":
                dot, head = ERR, "needs attention" + (f" (@{name})" if name else "")
                sub = "the last request with this session failed. it may have expired: reconnect if private posts stop working."
            elif st == "expired":
                dot, head = ERR, "session expired" + (f" (@{name})" if name else "")
                sub = "sign in again to keep access to private posts."
            elif st == "unreadable":
                dot, head = ERR, "saved session can't be read"
                sub = "it was saved by another Windows user or PC. reconnect."
            else:
                dot, head = DIM, "not connected"
                sub = "public posts work without an account."
            w["status"].setText(f"<span style='color:{dot}'>■</span>&nbsp; <span style='color:{TEXT}'>{head}</span><br>"
                                f"<span style='color:{MUTED}'>{sub}</span>")
            has = st != "none"
            w["connect"].setText("Reconnect" if has else "Connect")
            w["disconnect"].setEnabled(has)

    def connect_account(self, plat: str):
        cfg = PLATFORM_CFG[plat]
        try:
            from login_dialog import LoginDialog
            dlg = LoginDialog(self, plat, cfg)
        except Exception as e:  # noqa: BLE001
            self.note.setText(f"<span style='color:{ERR}'>[err] the built-in browser couldn't start: {e}</span>")
            return
        dlg.exec()
        res = dlg.result
        if res and res["cookies"]:
            try:
                ACCOUNTS.save(plat, res["cookies"], res["username"])
            except Exception as e:  # noqa: BLE001
                self.note.setText(f"<span style='color:{ERR}'>[err] couldn't save the session securely: {e}</span>")
                return
            who = f"@{res['username']}" if res["username"] else "your account"
            self.note.setText(f"<span style='color:{ACCENT_HI}'>[ok] {cfg['label']} connected as {who}.</span>")
        else:
            self.note.setText(f"<span style='color:{MUTED}'>[..] sign-in to {cfg['label']} was cancelled.</span>")
        self.refresh()
        self.changed.emit()

    def disconnect_account(self, plat: str):
        label = PLATFORM_CFG[plat]["label"]
        r = QMessageBox.question(self, "Disconnect", f"Disconnect {label}?\n\nThe saved session is deleted from this PC. "
                                 f"To end it everywhere, also sign out of other devices in {label}'s security settings.")
        if r != QMessageBox.Yes:
            return
        ACCOUNTS.remove(plat)
        self.note.setText(f"<span style='color:{MUTED}'>[..] {label} disconnected. the saved session was deleted.</span>")
        self.refresh()
        self.changed.emit()


# ----------------------------------------------------------------------------- updates
class _UpdSignals(QObject):
    checked = Signal(dict)
    progress = Signal(float, float)
    downloaded = Signal(str)
    failed = Signal(str)


class UpdateCheckTask(QRunnable):
    """Ask GitHub Releases for the latest version (runs in the background, never blocks the window)."""

    def __init__(self):
        super().__init__()
        self.setAutoDelete(False)
        self.s = _UpdSignals()

    def run(self):
        try:
            self.s.checked.emit(UP.check_latest())
        except Exception as e:  # noqa: BLE001
            self.s.failed.emit(str(e) or type(e).__name__)


class UpdateDownloadTask(QRunnable):
    def __init__(self, info: dict):
        super().__init__()
        self.setAutoDelete(False)
        self.info, self.cancelled = info, False
        self.s = _UpdSignals()

    def cancel(self):
        self.cancelled = True

    def run(self):
        try:
            path = UP.download_installer(self.info, progress=lambda d, t: self.s.progress.emit(float(d), float(t)),
                                         cancelled=lambda: self.cancelled)
            self.s.downloaded.emit(str(path))
        except Exception as e:  # noqa: BLE001
            self.s.failed.emit(str(e) or type(e).__name__)


class UpdateDialog(H.HudDialog):
    """A newer version exists: show what's new and let the user decide. Nothing installs without a click."""

    def __init__(self, win: "MainWindow", info: dict):
        super().__init__(win)
        self.win, self.info, self.task, self.busy = win, info, None, False
        self.setWindowTitle("Update available")
        self.resize(720, 560)
        self.setMinimumSize(640, 500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 24, 30, 22)
        lay.setSpacing(14)
        lay.addLayout(dialog_header("Update", f"VERSION {info['version']} IS AVAILABLE"))
        ver = QLabel(f"<span style='color:{MUTED}'>INSTALLED</span> {VERSION}"
                     f"&nbsp;&nbsp;&nbsp;<span style='color:{ACCENT}'>&rarr;</span>&nbsp;&nbsp;&nbsp;"
                     f"<span style='color:{MUTED}'>NEW</span> <span style='color:{ACCENT_HI}'>{info['version']}</span>")
        ver.setFont(H.mfont(10, 1.0))
        lay.addWidget(ver)
        lay.addWidget(section_label("What's new"))
        from PySide6.QtWidgets import QPlainTextEdit
        notes = QPlainTextEdit(UP.plain_notes(info.get("notes", "")) or "No release notes were provided.")
        notes.setReadOnly(True)
        notes.setFont(H.mfont(9.2, 0.2))
        notes.setStyleSheet("QPlainTextEdit { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.18); padding: 8px; }")
        lay.addWidget(notes, 1)
        self.status = QLabel("Your settings, library and connected accounts are kept. The app closes, updates, then reopens.")
        self.status.setObjectName("muted")
        self.status.setFont(H.mfont(8.8, 0.2))
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.strip = H.ProgressStrip()
        self.strip.hide()
        lay.addWidget(self.strip)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.skip_btn = H.HudButton("Skip this version", None, "ghost", height=42)
        self.later_btn = H.HudButton("Later", None, "glass", height=42)
        self.install_btn = H.HudButton("Install update", "download", "primary", height=42)
        self.install_btn.setMinimumWidth(190)
        self.skip_btn.clicked.connect(self._skip)
        self.later_btn.clicked.connect(self.reject)
        self.install_btn.clicked.connect(self._install)
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.later_btn)
        row.addWidget(self.install_btn)
        lay.addLayout(row)

    def _skip(self):
        self.win.cfg["update_skip"] = self.info["version"]
        save_json(CFG_FILE, self.win.cfg)
        self.accept()

    def _install(self):
        self.busy = True
        for b in (self.skip_btn, self.install_btn):
            b.setEnabled(False)
        self.strip.show()
        self.strip.set_progress(0.0)
        self.status.setText("Downloading the update ...")
        self.task = UpdateDownloadTask(self.info)
        self.task.s.progress.connect(self._progress)
        self.task.s.downloaded.connect(self._downloaded)
        self.task.s.failed.connect(self._failed)
        self.win.tasks.add(self.task)
        self.win.pool.start(self.task)

    def _progress(self, done: float, total: float):
        if total > 0:
            self.strip.set_progress(done / total)
            self.status.setText(f"Downloading the update ...  {fmt_size(int(done))} of {fmt_size(int(total))}")

    def _failed(self, msg: str):
        self.busy = False
        self.strip.set_error()
        self.status.setStyleSheet(f"color: {ERR};")
        self.status.setText(f"Couldn't install the update: {msg}. You can try again, or download it from the releases page.")
        self.skip_btn.setEnabled(True)
        self.install_btn.setEnabled(True)
        self.install_btn.setText("Try again")

    def _downloaded(self, path: str):
        self.strip.set_done()
        self.status.setText("Verified. Starting the installer. The app will close now and reopen when the update is done ...")
        if UP.launch_installer(Path(path)):
            QTimer.singleShot(900, QApplication.quit)
        else:
            self._failed("Windows could not start the installer")

    def reject(self):
        if self.task:
            self.task.cancel()
        super().reject()


# ----------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Downloader")
        self.setWindowIcon(H.app_icon())
        self.resize(1340, 900)
        self.setMinimumSize(1200, 800)
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
        self.is_images = False
        self.lib_tab = "library"
        self._last_msg = ""
        self._checking_update = False
        self.grid_delegate = GridDelegate(self)
        self.list_delegate = ListDelegate(self)
        self._build()
        self.grid_delegate.gallery = self.listw
        self.list_delegate.gallery = self.listw
        self.toast = H.HudToast(self.backdrop)
        self._refresh_accounts()
        self._tick_clock()
        self._idle()
        self.refresh_library(animate=True)
        self.clock_timer = QTimer(self, interval=15000)
        self.clock_timer.timeout.connect(self._tick_clock)
        self.clock_timer.start()
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self.go("library"))
        QShortcut(QKeySequence("Ctrl+D"), self, activated=lambda: self.go("download"))
        QShortcut(QKeySequence("Esc"), self, activated=self._escape)
        QShortcut(QKeySequence("Ctrl+U"), self, activated=lambda: self.check_updates(manual=True))
        self.log("info", "session started. paste a link to begin.")
        QTimer.singleShot(6000, self._auto_update_check)

    # ======================================================================= construction
    def _build(self):
        self.backdrop = H.HudBackdrop()
        self.setCentralWidget(self.backdrop)
        outer = QHBoxLayout(self.backdrop)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        shell = QWidget()
        shell.setMaximumWidth(1800)
        shell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer.addWidget(shell, 1000)
        outer.addStretch(1)
        root = QVBoxLayout(shell)
        root.setContentsMargins(30, 20, 30, 14)
        root.setSpacing(14)
        root.addLayout(self._build_top())
        body = QHBoxLayout()
        body.setSpacing(28)
        body.addWidget(self._build_nav())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_download_page())
        self.pages.addWidget(self._build_library_page())
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        root.addWidget(self._build_hints())
        self.url_timer = QTimer(self, singleShot=True)
        self.url_timer.timeout.connect(self.on_url_changed)

    # ---- top strip: clock, library stats, account tiles
    def _build_top(self):
        h = QHBoxLayout()
        h.setSpacing(30)
        self.stat_clock = H.HudStat("clock")
        self.stat_files = H.HudStat("film")
        h.addWidget(self.stat_clock)
        h.addWidget(self.stat_files)
        h.addStretch(1)
        self.acct_chips: dict[str, H.AccountTile] = {}
        for plat in ("TikTok", "Instagram", "X"):
            t = H.AccountTile(plat)
            t.clicked.connect(self.open_accounts)
            self.acct_chips[plat] = t
            h.addWidget(t)
        return h

    # ---- left menu
    def _build_nav(self):
        box = QWidget()
        box.setFixedWidth(220)
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(0)
        self.nav = H.NavMenu([("download", "Download"), ("library", "Library"), ("accounts", "Accounts"), ("folder", "Open folder")])
        self.nav.activated.connect(self._on_nav)
        v.addWidget(self.nav)
        v.addStretch(1)
        try:
            import yt_dlp
            ytv = yt_dlp.version.__version__
        except Exception:
            ytv = "?"
        try:
            import curl_cffi  # noqa: F401
            imp = "on"
        except Exception:
            imp = "off"
        info = QLabel("\n".join((f"VERSION      {VERSION}", f"YT-DLP       {ytv}", f"FFMPEG       {'bundled' if ffmpeg_path() else 'missing'}",
                                 f"BROWSER-ID   {imp}")))
        info.setFont(H.mfont(8, 0.8))
        info.setStyleSheet(f"color: {DIM};")
        v.addWidget(info)
        v.addSpacing(8)
        upd = H.HudButton("Check for updates", None, "ghost", height=30)
        upd.setToolTip("Look for a newer version on GitHub (Ctrl+U). Nothing installs without your confirmation.")
        upd.clicked.connect(lambda: self.check_updates(manual=True))
        v.addWidget(upd, 0, Qt.AlignLeft)
        return box

    def _on_nav(self, key: str):
        if key in ("download", "library"):
            self.go(key)
        elif key == "accounts":
            self.open_accounts()
        elif key == "folder":
            self._open_dir()

    def go(self, key: str):
        self.nav.set_selected(key)
        self.pages.setCurrentIndex(0 if key == "download" else 1)
        if key == "library":
            QTimer.singleShot(0, self._relayout_grid)
            self.listw.start_reveal()

    def _escape(self):
        if self.pages.currentIndex() == 0 and self.url_input.text() and not self.busy:
            self.url_input.clear()

    # ---- updates
    def _auto_update_check(self):
        """Quiet check at startup (installed app only, at most every 6 hours). Failures are silent."""
        import time as _t
        if not (getattr(sys, "frozen", False) or os.environ.get("VD_UPDATE_URL")):
            return
        if self.cfg.get("update_auto") is False or _t.time() - float(self.cfg.get("update_last_check", 0)) < 6 * 3600:
            return
        self.check_updates(manual=False)

    def check_updates(self, manual: bool = False):
        if self._checking_update:
            return
        self._checking_update = True
        if manual:
            self.log("cmd", "check-updates")
        task = UpdateCheckTask()
        task.s.checked.connect(lambda info, m=manual: self._on_update_info(info, m))
        task.s.failed.connect(lambda msg, m=manual: self._on_update_failed(msg, m))
        self.tasks.add(task)
        self.pool.start(task)

    def _on_update_info(self, info: dict, manual: bool):
        import time as _t
        self._checking_update = False
        self.cfg["update_last_check"] = _t.time()
        save_json(CFG_FILE, self.cfg)
        if info.get("newer"):
            if not manual and self.cfg.get("update_skip") == info["version"]:
                return
            if info.get("installer_url") and info.get("sums_url"):
                self.log("ok", f"update available: v{info['version']} (installed v{VERSION})")
                UpdateDialog(self, info).exec()
            elif manual:
                self.log("info", f"v{info['version']} was announced but its installer isn't published yet. try again later.")
        elif manual:
            self.log("ok", f"you are up to date (v{VERSION})")
            self.toast.popup(f"you are up to date. version {VERSION}.")

    def _on_update_failed(self, msg: str, manual: bool):
        self._checking_update = False
        if manual:
            self.log("err", f"update check failed: {msg}")
            self.toast.popup("couldn't check for updates. check your connection.", error=True)

    # ---- headers
    def _header(self, tail: str, title: str, close=None):
        box = QVBoxLayout()
        box.setSpacing(5)
        crumb = QLabel(f"<span style='color:{MUTED}'>VIDEO DOWNLOADER // </span><span style='color:{ACCENT}'>{tail.upper()}</span>")
        crumb.setFont(H.mfont(8.5, 1.5))
        row = QHBoxLayout()
        row.setSpacing(14)
        t = QLabel(title.upper())
        t.setFont(H.hfont(24, 5.0))
        row.addWidget(t)
        row.addWidget(H.DottedRule(), 1)
        if close:
            b = H.HudIconButton("close", 32, "ghost", "Clear (Esc)")
            b.clicked.connect(close)
            row.addWidget(b)
        box.addWidget(crumb)
        box.addLayout(row)
        box.addWidget(hairline())
        return box

    # ---- download page
    def _build_download_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        v.addLayout(self._header("New download", "New download", close=lambda: self.url_input.clear()))
        v.addWidget(self._build_field())
        self.hint = QLabel("")
        self.hint.setObjectName("error")
        self.hint.setFont(H.mfont(9, 0.2))
        self.hint.hide()
        v.addWidget(self.hint)
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(self._build_preview_card(), 3)
        row.addWidget(self._build_options_card(), 2)
        v.addLayout(row)
        v.addWidget(self._build_terminal(), 1)
        return page

    def _build_field(self):
        self.prompt = H.HudField()
        self.prompt.setFixedHeight(58)
        bl = QHBoxLayout(self.prompt)
        bl.setContentsMargins(20, 8, 8, 8)
        bl.setSpacing(12)
        chev = QLabel(">")
        chev.setFont(H.mfont(15, 0, QFont.Weight.Bold))
        chev.setStyleSheet(f"color:{ACCENT};")
        self.url_input = G.PromptEdit()
        self.url_input.setObjectName("bare")
        self.url_input.setFont(H.mfont(11.5, 0.2))
        self.url_input.setPlaceholderText("paste a link from x, tiktok or instagram")
        self.url_input.setFixedHeight(40)
        self.url_input.focusChanged.connect(self.prompt.set_focused)
        self.url_input.textChanged.connect(lambda *_: self.url_timer.start(450))
        self.url_input.returnPressed.connect(self.on_url_changed)
        self.platform_tag = H.Tag("", accent=True)
        self.platform_tag.hide()
        self.paste_btn = H.HudButton("Paste", "paste", height=42)
        self.paste_btn.setMinimumWidth(124)
        self.paste_btn.clicked.connect(self.paste)
        bl.addWidget(chev)
        bl.addWidget(self.url_input, 1)
        bl.addWidget(self.platform_tag)
        bl.addWidget(self.paste_btn)
        return self.prompt

    def _build_preview_card(self):
        card = H.HudPanel()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 20, 22, 20)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.stack.addWidget(self._page_empty())
        self.skeleton = H.HudSkeleton()
        self.stack.addWidget(self.skeleton)
        self.stack.addWidget(self._page_preview())
        self.stack.addWidget(self._page_error())
        return card

    def _page_empty(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 0, 4, 0)
        v.setSpacing(10)
        v.addStretch(1)
        v.addWidget(H.IconBadge("film", 60, ACCENT_HI), 0, Qt.AlignLeft)
        v.addSpacing(6)
        t = QLabel("AWAITING INPUT")
        t.setFont(H.hfont(17, 4.0))
        d = QLabel("the thumbnail, title, length and quality options show up here before anything is downloaded.")
        d.setObjectName("muted")
        d.setFont(H.mfont(9.2, 0.2))
        d.setWordWrap(True)
        d.setMaximumWidth(500)
        v.addWidget(t)
        v.addWidget(d)
        v.addSpacing(8)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for name in ("X (Twitter)", "TikTok", "Instagram Reels"):
            chips.addWidget(H.Tag(name))
        chips.addStretch(1)
        v.addLayout(chips)
        tip = QLabel("> copy a link anywhere. it is picked up when you come back to this window.")
        tip.setObjectName("muted")
        tip.setFont(H.mfont(8.6, 0.2))
        tip.setWordWrap(True)
        v.addSpacing(4)
        v.addWidget(tip)
        v.addStretch(1)
        return w

    def _page_error(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 0, 4, 0)
        v.setSpacing(10)
        v.addStretch(1)
        v.addWidget(H.IconBadge("alert", 60, ERR), 0, Qt.AlignLeft)
        v.addSpacing(6)
        t = QLabel("READ FAILED")
        t.setFont(H.hfont(17, 4.0))
        self.err_lbl = QLabel("")
        self.err_lbl.setObjectName("muted")
        self.err_lbl.setFont(H.mfont(9.2, 0.2))
        self.err_lbl.setWordWrap(True)
        self.err_lbl.setMaximumWidth(560)
        v.addWidget(t)
        v.addWidget(self.err_lbl)
        v.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(10)
        retry = H.HudButton("Try again", None, "glass", height=40)
        retry.setMinimumWidth(130)
        retry.clicked.connect(lambda: self.start_fetch(self.fetched_url) if self.fetched_url else None)
        self.err_accounts = H.HudButton("Open accounts", "user", "primary", height=40)
        self.err_accounts.setMinimumWidth(170)
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
        col.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(22)
        self.thumb = QLabel()
        self.thumb.setFixedSize(242, 138)
        top.addWidget(self.thumb, 0, Qt.AlignTop)
        r = QVBoxLayout()
        r.setSpacing(8)
        meta = QHBoxLayout()
        meta.setSpacing(10)
        self.p_badge = H.Tag("", accent=True)
        self.p_author = QLabel("")
        self.p_author.setObjectName("muted")
        self.p_author.setFont(H.mfont(9, 0.2))
        meta.addWidget(self.p_badge)
        meta.addWidget(self.p_author, 1)
        r.addLayout(meta)
        self.p_title = ElidedLabel(H.bfont(13.5, SEMI), TEXT, 3)
        self.p_desc = ElidedLabel(H.bfont(9.2), MUTED, 2)
        r.addWidget(self.p_title)
        r.addWidget(self.p_desc)
        r.addStretch(1)
        self.q_chips = QHBoxLayout()
        self.q_chips.setSpacing(8)
        r.addLayout(self.q_chips)
        top.addLayout(r, 1)
        col.addLayout(top)
        col.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(0)
        self.cells: dict[str, tuple[QWidget, QLabel, QLabel]] = {}
        for i, (key, label) in enumerate((("length", "Length"), ("views", "Views"), ("likes", "Likes"), ("date", "Posted"))):
            cell = QWidget()
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(16 if i else 0, 0, 10, 0)
            cl.setSpacing(2)
            cap = QLabel(label.upper())
            cap.setObjectName("muted")
            cap.setFont(H.mfont(7.8, 1.4))
            val = QLabel("")
            val.setFont(H.hfont(13, 1.0, QFont.Weight.Medium))
            cl.addWidget(cap)
            cl.addWidget(val)
            if i:
                sep = QFrame()
                sep.setFixedWidth(1)
                sep.setStyleSheet("background: rgba(255,255,255,0.18);")
                row.addWidget(sep)
            row.addWidget(cell, 1)
            self.cells[key] = (cell, val, cap)
        col.addLayout(row)
        return w

    # ---- options card
    def _build_options_card(self):
        card = H.HudPanel()
        card.setMinimumWidth(390)
        v = QVBoxLayout(card)
        v.setContentsMargins(22, 18, 22, 20)
        v.setSpacing(10)
        v.addWidget(section_label("Format"))
        self.fmt = H.RadioGroup(["MP4 H.264", "MP3 audio", "MP4 original"], columns=2, row_h=30)
        self.fmt.setToolTip("MP4 H.264: best quality, converted when needed so it plays on any PC.\n"
                            "MP3 audio: audio only.\nMP4 original: keeps the site's file (TikTok often uses HEVC, "
                            "which Windows may not play without an extension).")
        self.fmt.changed.connect(self._on_format)
        v.addWidget(self.fmt)
        v.addSpacing(4)
        two = QHBoxLayout()
        two.setSpacing(14)
        qb = QVBoxLayout()
        qb.setSpacing(6)
        qb.addWidget(section_label("Quality"))
        self.q_combo = QComboBox()
        self.q_combo.setFixedHeight(38)
        self.q_combo.addItem("best available", None)
        self.q_combo.currentIndexChanged.connect(lambda *_: self._idle_summary())
        qb.addWidget(self.q_combo)
        sb = QVBoxLayout()
        sb.setSpacing(6)
        sb.addWidget(section_label("Save to"))
        self.dir_btn = H.HudButton("", "folder", height=38, align_left=True, caps=False)
        self.dir_btn.clicked.connect(self.choose_dir)
        sb.addWidget(self.dir_btn)
        two.addLayout(qb, 1)
        two.addLayout(sb, 1)
        v.addLayout(two)
        self._update_dir_btn()
        v.addStretch(1)
        act = QHBoxLayout()
        act.setSpacing(10)
        self.cancel_btn = H.HudButton("Cancel", None, "glass", height=46)
        self.cancel_btn.setMinimumWidth(110)
        self.cancel_btn.clicked.connect(lambda: self.dl_task and self.dl_task.cancel())
        self.cancel_btn.hide()
        self.dl_btn = H.HudButton("Download", "download", "primary", height=46)
        self.dl_btn.clicked.connect(self.start_download)
        self.dl_btn.setEnabled(False)
        act.addWidget(self.cancel_btn)
        act.addWidget(self.dl_btn, 1)
        v.addLayout(act)
        return card

    # ---- terminal
    def _build_terminal(self):
        card = H.HudPanel(0.7)
        card.setMinimumHeight(160)
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 12, 20, 14)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(section_label("Terminal"))
        head.addStretch(1)
        self.state_dot = G.Dot()
        self.state_dot.color = QColor(ACCENT)
        self.state_lbl = QLabel("READY")
        self.state_lbl.setFont(H.mfont(8.6, 1.4))
        self.state_lbl.setObjectName("muted")
        head.addWidget(self.state_dot)
        head.addWidget(self.state_lbl)
        v.addLayout(head)
        self.term = H.TermLog()
        v.addWidget(self.term, 1)
        prog = QHBoxLayout()
        prog.setSpacing(14)
        self.pct_lbl = QLabel("--")
        self.pct_lbl.setFont(H.mfont(11.5, 0.5, QFont.Weight.Bold))
        self.pct_lbl.setFixedWidth(58)
        self.strip = H.ProgressStrip()
        self.eta_lbl = QLabel("idle")
        self.eta_lbl.setObjectName("muted")
        self.eta_lbl.setFont(H.mfont(9, 0.3))
        self.eta_lbl.setMinimumWidth(190)
        self.eta_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        prog.addWidget(self.pct_lbl)
        prog.addWidget(self.strip, 1)
        prog.addWidget(self.eta_lbl)
        v.addLayout(prog)
        return card

    def log(self, level: str, text: str):
        self.term.log(level, text)

    def _set_state(self, text: str, busy: bool = False):
        self.state_lbl.setText(text.upper())
        self.state_dot.color = QColor("#f3b867" if busy else ACCENT)
        self.state_dot.update()

    # ---- library page
    def _build_library_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        v.addLayout(self._header("Library", "Library"))
        self.tabs = H.SplitTabs("Library", "Hidden")
        self.tabs.changed.connect(lambda i: self.set_tab("hidden" if i else "library", from_tabs=True))
        v.addWidget(self.tabs)
        tb = QHBoxLayout()
        tb.setSpacing(12)
        sf = H.HudField()
        sf.setFixedSize(280, 40)
        sl = QHBoxLayout(sf)
        sl.setContentsMargins(14, 0, 12, 0)
        sl.setSpacing(8)
        si = QLabel()
        si.setPixmap(G.icon_pixmap_named("search", 18, MUTED))
        self.search = G.PromptEdit()
        self.search.setObjectName("bare")
        self.search.setFont(H.mfont(9.5, 0.2))
        self.search.setPlaceholderText("search the library")
        self.search.focusChanged.connect(sf.set_focused)
        self.search.textChanged.connect(lambda *_: self.refresh_library())
        sl.addWidget(si)
        sl.addWidget(self.search, 1)
        self.pf = H.SegGroup(["All", "X", "TikTok", "Instagram"], height=40)
        self.pf.changed.connect(lambda *_: self.refresh_library())
        self.vf = H.SegGroup([("", "grid"), ("", "list")], height=40, index=0 if self.view_mode == "grid" else 1)
        self.vf.changed.connect(lambda i: self.set_view("grid" if i == 0 else "list"))
        self.lib_count = QLabel("")
        self.lib_count.setObjectName("muted")
        self.lib_count.setFont(H.mfont(8.8, 1.0))
        tb.addWidget(sf)
        tb.addWidget(self.pf)
        tb.addStretch(1)
        tb.addWidget(self.lib_count)
        tb.addWidget(self.vf)
        v.addLayout(tb)

        self.lib_stack = QStackedWidget()
        ew = QWidget()
        ev = QVBoxLayout(ew)
        ev.setContentsMargins(6, 8, 6, 8)
        ev.addStretch(1)
        self.empty_icon = H.IconBadge("film", 64, ACCENT_HI)
        self.empty_title = QLabel("")
        self.empty_title.setFont(H.hfont(15, 3.6))
        self.empty_sub = QLabel("")
        self.empty_sub.setObjectName("muted")
        self.empty_sub.setFont(H.mfont(9.2, 0.2))
        self.empty_sub.setWordWrap(True)
        self.empty_sub.setFixedWidth(560)
        self.empty_sub.setMinimumHeight(52)
        self.empty_title.setAlignment(Qt.AlignHCenter)
        self.empty_sub.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        ev.addWidget(self.empty_icon, 0, Qt.AlignHCenter)
        ev.addSpacing(10)
        ev.addWidget(self.empty_title, 0, Qt.AlignHCenter)
        ev.addWidget(self.empty_sub, 0, Qt.AlignHCenter)
        ev.addStretch(1)
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
        return page

    def _build_hints(self):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 0)

        def key(k, t):
            return f"<span style='color:{ACCENT_HI}'>[{k}]</span>&nbsp;<span style='color:{MUTED}'>{t}</span>"
        lbl = QLabel("&nbsp;&nbsp;&nbsp;&nbsp;".join((key("ENTER", "FETCH"), key("CTRL+V", "PASTE"), key("ESC", "CLEAR"),
                                                     key("CTRL+D", "DOWNLOAD"), key("CTRL+L", "LIBRARY"),
                                                     key("CTRL+U", "UPDATES"))))
        lbl.setFont(H.mfont(8.4, 1.0))
        ver = QLabel(f"VIDEO DOWNLOADER  v{VERSION}")
        ver.setFont(H.mfont(8.2, 1.6))
        ver.setStyleSheet(f"color: {DIM};")
        h.addWidget(lbl)
        h.addStretch(1)
        h.addWidget(ver)
        return row

    def _relayout_grid(self):
        """Grid mode: choose a column count for the current width and stretch the cards to fill the row."""
        if self.view_mode != "grid":
            return
        gap = self.listw.spacing()
        avail = max(300, self.listw.width() - 26)
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
        for plat, tile in self.acct_chips.items():
            info = ACCOUNTS.info(plat)
            st, name = info["state"], info["username"]
            short = (name if len(name) <= 16 else name[:15] + "…") if name else ""
            if st in ("connected", "attention"):
                txt = (("@" + short) if short else "connected") if st == "connected" else "needs attention"
                dot = QColor(ACCENT) if st == "connected" else QColor(ERR)
                tip = (f"{plat}: connected" + (f" as @{name}" if name else "")) if st == "connected" else \
                    f"{plat}: the saved session was rejected. Click to reconnect."
            elif st == "expired":
                txt, dot, tip = "session expired", QColor(ERR), f"{plat}: session expired. Click to reconnect."
            elif st == "unreadable":
                txt, dot, tip = "reconnect", QColor(ERR), f"{plat}: saved session can't be read. Click to reconnect."
            else:
                txt, dot, tip = "not connected", QColor(DIM), f"{plat}: not connected. Click to connect."
            tile.set_info(txt, dot)
            tile.setToolTip(tip)

    def open_accounts(self):
        dlg = AccountsDialog(self)
        dlg.changed.connect(self._refresh_accounts)
        dlg.exec()
        self._refresh_accounts()

    # ======================================================================= events / clipboard
    def _tick_clock(self):
        now = datetime.now()
        self.stat_clock.set_text(now.strftime("%H:%M"), now.strftime("%A"))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.backdrop.update()

    def showEvent(self, e):
        super().showEvent(e)
        H.style_titlebar(self)
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
            self.go("download")
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
            self.log("info", f"save folder: {self.save_dir}")

    # ======================================================================= URL detection + preview
    def on_url_changed(self):
        self.url_timer.stop()
        text = self.url_input.text().strip()
        if self.busy:
            return
        if not text:
            self.platform_tag.hide()
            self.hint.hide()
            self.fetched_url = None
            self.token += 1
            self.summary = None
            self.is_images = False
            self.backdrop.set_wash(None)
            self.stack.setCurrentIndex(0)
            self.dl_btn.setEnabled(False)
            self.fmt.setEnabled(True)
            self.dl_btn.setText("Download")
            self._set_state("ready")
            self._idle()
            return
        url = extract_url(text)
        if not url:
            self.platform_tag.hide()
            self.hint.setText("[err] that isn't a link from x, tiktok or instagram.")
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
        self.is_images = False
        self.fmt.setEnabled(True)
        self.dl_btn.setText("Download")
        self.dl_btn.setEnabled(False)
        self._set_state("reading post", busy=True)
        self.log("cmd", f"probe {url if len(url) < 90 else url[:87] + '...'}")
        self.stack.setCurrentIndex(1)
        task = FetchTask(self.token, url)
        task.s.info.connect(self.on_info)
        task.s.info_err.connect(self.on_info_err)
        self.tasks.add(task)
        self.pool.start(task)

    def _warn_if_session_bad(self, url: str | None):
        """A lookup may have discovered that a saved account session no longer works."""
        self._refresh_accounts()
        plat = platform_of(url) if url else None
        if plat and ACCOUNTS.state(plat) == "attention":
            self.log("warn", f"the saved {PLATFORM_CFG[plat]['label']} session was rejected, so public data was used. "
                             "reconnect it in ACCOUNTS to open private posts.")

    def on_info_err(self, token: int, msg: str):
        if token != self.token:
            return
        self._warn_if_session_bad(self.fetched_url)
        self.err_lbl.setText(msg)
        self.err_accounts.setVisible("ACCOUNTS" in msg)
        self._set_state("read failed")
        self.log("err", msg)
        self.backdrop.set_wash(None)
        self.stack.setCurrentIndex(3)

    def on_info(self, token: int, s: dict):
        if token != self.token:
            return
        self._warn_if_session_bad(self.fetched_url)
        self.summary = s
        self.thumb.setPixmap(G.framed_pixmap(None, 240, 136, 1, data=s.get("thumb")))
        self.thumb.setStyleSheet("border: 1px solid rgba(255,255,255,0.24);")
        self.backdrop.set_wash(G.make_wash(s.get("thumb")))
        self.p_badge.setText(s["platform"] or "")
        self.p_author.setText(("@" + s["uploader"].lstrip("@")) if s["uploader"] else "")
        self.p_title.setText(s["title"])
        self.p_desc.setText(s["description"])
        self.p_desc.setVisible(bool(s["description"]))
        self.is_images = s.get("kind") == "images"
        n_img = len(s.get("images") or [])
        vals = {"length": str(n_img) if self.is_images else fmt_duration(s["duration"]),
                "views": fmt_count(s["views"]) if s["views"] is not None else "",
                "likes": fmt_count(s["likes"]) if s["likes"] is not None else "", "date": s["date"]}
        for key, (cell, val, cap) in self.cells.items():
            val.setText(vals[key])
            cell.setVisible(bool(vals[key]))
            if key == "length":
                cap.setText("PHOTOS" if self.is_images else "LENGTH")
        self.q_combo.blockSignals(True)
        self.q_combo.clear()
        self.q_combo.addItem("best available", None)
        for h, meta in s["heights"]:
            label = f"{min(meta['w'] or h, h)}p"
            if meta["size"]:
                label += f"  ·  {fmt_size(meta['size'])}"
            self.q_combo.addItem(label, h)
        self.q_combo.blockSignals(False)
        while self.q_chips.count():
            it = self.q_chips.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        if self.is_images:
            self.q_combo.blockSignals(True)
            self.q_combo.clear()
            self.q_combo.addItem("original", None)
            self.q_combo.blockSignals(False)
            im0 = s["images"][0]
            if im0.get("w") and im0.get("h"):
                self.q_chips.addWidget(H.Tag(f"{im0['w']} x {im0['h']}"))
            self.q_chips.addWidget(H.Tag("original quality"))
        else:
            for h, meta in s["heights"][:3]:
                self.q_chips.addWidget(H.Tag(f"{min(meta['w'] or h, h)}p"))
        self.q_chips.addStretch(1)
        self.fmt.setIndex(0, animate=False)
        self.fmt.setEnabled(not self.is_images)
        self.q_combo.setEnabled(not self.is_images)
        self.dl_btn.setText(self._dl_label())
        self.dl_btn.setEnabled(True)
        self._set_state("ready")
        # terminal report
        self.log("ok", f"source    {(s['platform'] or '').lower()}" + (f"  @{s['uploader'].lstrip('@')}" if s["uploader"] else ""))
        self.log("ok", f"title     {s['title'][:90]}")
        if self.is_images:
            im0 = s["images"][0]
            dims = f"  ({im0['w']}x{im0['h']})" if im0.get("w") and im0.get("h") else ""
            self.log("ok", f"photos    {n_img}, original quality{dims}")
        else:
            qs = " ".join(f"{min(m['w'] or h, h)}p" for h, m in s["heights"]) or "best available"
            self.log("ok", f"length    {fmt_duration(s['duration']) or '-'}   formats {qs}")
        self.log("info", "ready. press DOWNLOAD to save the photos." if self.is_images else "ready. choose a format and press DOWNLOAD.")
        self._idle_summary()
        self.stack.setCurrentIndex(2)

    # ======================================================================= download
    def _dl_label(self) -> str:
        return "Download photos" if self.is_images else "Download"

    def _on_format(self, i: int):
        self.q_combo.setEnabled(i != 1 and not self.busy)
        self._idle_summary()

    def _idle(self):
        self.strip.set_idle()
        self.pct_lbl.setText("--")
        self.eta_lbl.setText("idle")

    def _idle_summary(self):
        if self.busy or not self.summary:
            return
        self.strip.set_idle()
        self.pct_lbl.setText("--")
        if self.is_images:
            n = len(self.summary.get("images") or [])
            self.eta_lbl.setText(f"{n} photo{'s' if n != 1 else ''} / original")
        elif self.fmt.index() == 1:
            self.eta_lbl.setText("mp3 audio")
        else:
            q = "best" if self.q_combo.currentData() is None else self.q_combo.currentText().split()[0]
            self.eta_lbl.setText(f"{q} / {'h.264' if self.fmt.index() == 0 else 'original codec'}")

    def set_busy(self, busy: bool):
        self.busy = busy
        for w in (self.url_input, self.paste_btn, self.q_combo, self.fmt, self.dir_btn):
            w.setEnabled(not busy)
        if not busy:
            self.fmt.setEnabled(not self.is_images)
            self.q_combo.setEnabled(self.fmt.index() != 1 and not self.is_images)
        self.dl_btn.setEnabled(not busy and self.summary is not None)
        self.cancel_btn.setVisible(busy)
        self.dl_btn.setText("Downloading..." if busy else self._dl_label())
        self._set_state("downloading" if busy else "ready", busy=busy)

    def start_download(self):
        if self.busy or not self.summary:
            return
        self.token += 1
        audio = self.fmt.index() == 1 and not self.is_images
        tag = "" if audio or self.q_combo.currentData() is None else self.q_combo.currentText().split()[0]
        task = DownloadTask(self.token, self.summary, self.save_dir, self.q_combo.currentData(), audio, tag,
                            compat=self.fmt.index() == 0 and not self.is_images)
        task.s.progress.connect(self.on_progress)
        task.s.done.connect(self.on_done)
        task.s.fail.connect(self.on_fail)
        self.dl_task = task
        self.tasks.add(task)
        self.set_busy(True)
        self._last_msg = ""
        if self.is_images:
            self.log("cmd", f"save-photos --out {self.save_dir}")
        else:
            fmt = "mp3" if audio else ("mp4-h264" if self.fmt.index() == 0 else "mp4-original")
            q = "best" if self.q_combo.currentData() is None else self.q_combo.currentText().split()[0]
            self.log("cmd", f"download --format {fmt} --quality {q} --out {self.save_dir}")
        self.strip.set_progress(0.0)
        self.pct_lbl.setText("0%")
        self.eta_lbl.setText("starting")
        self.pool.start(task)

    def on_progress(self, token: int, frac: float, text: str):
        if token != self.token:
            return
        short = " ".join(text.split())
        self.strip.set_progress(frac)
        self.pct_lbl.setText(f"{int(frac * 100)}%")
        self.eta_lbl.setText(short)
        if len(short) > 22 and short != self._last_msg:  # stage messages such as the H.264 conversion
            self._last_msg = short
            self.log("info", short)

    def _finish_task(self):
        if self.dl_task:
            self.tasks.discard(self.dl_task)
        self.dl_task = None
        self.set_busy(False)

    def on_done(self, token: int, entry: dict):
        if token != self.token:
            return
        self._finish_task()
        self.strip.set_done()
        self.pct_lbl.setText("100%")
        self.eta_lbl.setText("saved")
        self.log("ok", f"saved -> {entry['filename']}  ({fmt_size(entry.get('size')) or '-'})")
        self.log("info", "added to the library. press CTRL+L to open it.")
        for old in [x for x in self.lib.items if os.path.normcase(x["path"]) == os.path.normcase(entry["path"])]:
            self.lib.remove(old)  # same file downloaded again -> keep a single library entry
        self.lib.add(entry)
        if self.lib_tab != "library":
            self.set_tab("library")
        self.refresh_library(animate=True)
        self.listw.setCurrentRow(0)
        self.listw.scrollToTop()
        self.toast.popup("download complete. added to your library.")

    def on_fail(self, token: int, msg: str):
        if token != self.token:
            return
        self._finish_task()
        self.strip.set_error()
        self.pct_lbl.setText("ERR")
        self.eta_lbl.setText("failed")
        self.log("err", msg)

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

    def set_tab(self, tab: str, from_tabs: bool = False):
        self.lib_tab = tab
        if not from_tabs:
            self.tabs.setIndex(1 if tab == "hidden" else 0)
        self.refresh_library(animate=True)

    def toggle_hidden(self, e: dict) -> None:
        """Hide a video from the main library (or restore it). Only the library record changes."""
        hide = not e.get("hidden")
        if self.lib.set_hidden(e["id"], hide):
            self.refresh_library()
            self.log("info", f"{'hidden' if hide else 'restored'}: {e.get('filename', '')}")
            self.toast.popup("hidden. find it in the hidden tab. the file is untouched." if hide else "restored to your library.")

    def refresh_library(self, animate: bool = False):
        q = self.search.text().strip().lower()
        plat = self.pf.items[self.pf.index()][0]
        grid = self.view_mode == "grid"
        hidden_tab = self.lib_tab == "hidden"
        n_hidden = sum(1 for x in self.lib.items if x.get("hidden"))
        n_visible = len(self.lib.items) - n_hidden
        self.tabs.set_labels(f"Library  {n_visible:02d}", f"Hidden  {n_hidden:02d}")
        self.nav.set_count("library", n_visible)
        total_size = sum(int(x.get("size") or 0) for x in self.lib.items)
        self.stat_files.set_text(f"{len(self.lib.items)} FILES", fmt_size(total_size) or "0 B")
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
        self.lib_count.setText(f"{shown if shown == total else f'{shown} OF {total}'} ITEM{'S' if shown != 1 else ''}")
        if shown:
            self.lib_stack.setCurrentIndex(1)
            if animate:
                self.listw.start_reveal()
        else:
            self.lib_stack.setCurrentIndex(0)
            if total == 0 and hidden_tab:
                self.empty_icon.icon = "eye-off"
                self.empty_title.setText("NOTHING HIDDEN")
                self.empty_sub.setText("use hide on any video to move it here. hidden videos stay saved on your pc, "
                                       "and unhide puts them back in the library.")
            elif total == 0:
                self.empty_icon.icon = "film"
                self.empty_title.setText("LIBRARY EMPTY")
                self.empty_sub.setText("paste a link on the download page and save a video. it will appear here with its "
                                       "thumbnail, source link and date.")
            else:
                self.empty_icon.icon = "search"
                self.empty_title.setText("NO MATCHES")
                self.empty_sub.setText("nothing fits the current search or platform filter.")
            self.empty_icon.update()

    def open_entry(self, e: dict):
        if not Path(e["path"]).exists():
            r = QMessageBox.question(self, "File missing",
                                     "This file was moved or deleted outside the app.\nRemove it from your library?")
            if r == QMessageBox.Yes:
                self.lib.remove(e)
                self.refresh_library()
            return
        (ImageViewer if e.get("kind") == "image" else PlayerDialog)(self, e).exec()
        self.refresh_library()

    def copy_link(self, e: dict):
        QGuiApplication.clipboard().setText(e.get("url") or "")
        self.last_clip = e.get("url")  # don't auto-paste our own copy back into the URL box
        self.toast.popup("link copied.")

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
        self.log("info", f"removed: {e.get('filename', '')}")
        self.toast.popup("removed from your library.")
        return True

    def lib_menu(self, pos):
        it = self.listw.itemAt(pos)
        if not it:
            return
        e = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("View photos" if e.get("kind") == "image" else "Play", lambda: self.open_entry(e))
        m.addAction("Open folder", lambda: open_folder_of(e["path"]))
        m.addAction("Copy original link", lambda: self.copy_link(e))
        m.addAction("Unhide (restore to library)" if e.get("hidden") else "Hide from library", lambda: self.toggle_hidden(e))
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


def render_test(png: str) -> None:
    """Diagnostic: open the real window, wait a moment, save a screenshot of it and quit."""
    app = QApplication(sys.argv)
    H.apply_theme(app)
    win = MainWindow()
    win.resize(1340, 900)
    win.show()
    end = time.time() + 1.6
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    win.grab().save(png)
    win.close()


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
        H.icon_pixmap(256).toImage().save(sys.argv[2])
        return
    if len(sys.argv) > 2 and sys.argv[1] == "--selfcheck":
        import selfcheck
        sys.exit(selfcheck.main(sys.argv[2]))
    if len(sys.argv) > 2 and sys.argv[1] == "--render-test":
        render_test(sys.argv[2])
        return
    try:
        import ctypes
        # lets the installer notice a running copy (AppMutex) and close it cleanly before updating
        globals()["_APP_MUTEX"] = ctypes.windll.kernel32.CreateMutexW(None, False, "VideoDownloaderAppMutex")
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VideoDownloader.App")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    H.apply_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
