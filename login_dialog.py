"""Embedded sign-in window.

The user signs in on the platform's own website (real login page, real 2-factor prompts). We never see
the password: we only watch for the site to set its session cookie and then keep the cookies for the
platform's domains. The browser profile is off-the-record, so Chromium itself stores nothing on disk.
"""

import re

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class LoginDialog(QDialog):
    """Opens `cfg["login_url"]`; on success `self.result` = {"username": str, "cookies": [dict, ...]}."""

    def __init__(self, parent, platform: str, cfg: dict):
        super().__init__(parent)
        # Imported lazily so the main window starts fast and works even if WebEngine is unavailable.
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.platform, self.cfg = platform, cfg
        self.result: dict | None = None
        self._cookies: dict = {}
        self._done = False
        self._finishing = False
        self._polling = False
        self._tries = 0
        self._name = ""

        self.setWindowTitle(f"Sign in to {cfg.get('label', platform)}")
        self.resize(560, 800)
        self.setMinimumSize(460, 600)

        class _Page(QWebEnginePage):
            def createWindow(self_inner, _type):  # "Continue with ..." popups: open them in the same page
                tmp = QWebEnginePage(self_inner.profile(), self_inner)
                tmp.urlChanged.connect(lambda u: (self_inner.setUrl(u), tmp.deleteLater()))
                return tmp

        # Off-the-record profile: nothing is persisted by Chromium; we keep only what we choose to.
        self.profile = QWebEngineProfile(self)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        ua = self.profile.httpUserAgent()
        self.profile.setHttpUserAgent(re.sub(r"\s*QtWebEngine/[\d.]+", "", ua))
        self.page = _Page(self.profile, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.host_lbl = QLabel("")
        self.status = QLabel("sign in on the page below. the app never sees your password.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        cancel = QPushButton("CANCEL")
        cancel.setObjectName("tbtn")
        cancel.setFixedHeight(36)
        cancel.setMinimumWidth(96)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        top.addWidget(self.host_lbl, 1)
        top.addWidget(cancel)
        lay.addLayout(top)
        lay.addWidget(self.status)
        lay.addWidget(self.view, 1)

        self.page.urlChanged.connect(self._url_changed)
        self.page.renderProcessTerminated.connect(self._render_died)
        self.page.loadFinished.connect(self._load_finished)
        store = self.profile.cookieStore()
        store.cookieAdded.connect(self._cookie_added)
        store.cookieRemoved.connect(self._cookie_removed)
        self.view.load(QUrl(cfg["login_url"]))

    def _render_died(self, _status, code):
        self.status.setText(f"<span style='color:#e0786c'>[err]</span> the built-in browser failed to start (code {code}). "
                            "close this window and try again.")

    def _load_finished(self, ok: bool):
        if not ok and not self._finishing:
            self.status.setText("<span style='color:#e0786c'>[err]</span> the sign-in page didn't load. "
                                "check your internet connection, then close this window and try again.")

    # ---- showing where the user really is (anti-phishing aid)
    def _url_changed(self, url: QUrl):
        host = url.host()
        ok = any(host == d or host.endswith("." + d) for d in self.cfg["domains"])
        colour = "#66d19a" if (ok and url.scheme() == "https") else "#e0a35f"
        self.host_lbl.setText(f"<span style='color:{colour}'>{'secure site' if url.scheme() == 'https' else 'not secure'}"
                              f"</span> <span style='color:#d5dbd7'>{host}</span>")

    # ---- cookie capture
    def _in_scope(self, domain: str) -> bool:
        d = domain.lstrip(".").lower()
        return any(d == s or d.endswith("." + s) for s in self.cfg["domains"])

    def _cookie_added(self, c: QNetworkCookie):
        domain = c.domain()
        if not self._in_scope(domain):
            return
        exp = c.expirationDate()
        rec = {"domain": domain, "path": c.path() or "/", "name": bytes(c.name()).decode("utf-8", "replace"),
               "value": bytes(c.value()).decode("utf-8", "replace"),
               "expires": int(exp.toSecsSinceEpoch()) if (c.expirationDate().isValid() and not c.isSessionCookie()) else 0,
               "secure": c.isSecure(), "httponly": c.isHttpOnly()}
        self._cookies[(rec["domain"], rec["path"], rec["name"])] = rec
        if rec["name"] in self.cfg["session_cookies"] and rec["value"]:
            self._signed_in()

    def _cookie_removed(self, c: QNetworkCookie):
        self._cookies.pop((c.domain(), c.path() or "/", bytes(c.name()).decode("utf-8", "replace")), None)

    # ---- after sign-in: read the account name, then finish
    def _signed_in(self):
        if self._finishing or self._done:
            return
        self._finishing = True
        self.status.setText("signed in. reading your account name...")
        QTimer.singleShot(1500, self._go_home)

    def _go_home(self):
        if self._done:
            return
        self.view.loadFinished.connect(self._after_load)
        self.view.load(QUrl(self.cfg["home_url"]))
        QTimer.singleShot(15000, self._finalize)  # hard cap: connect even if the name can't be read

    def _after_load(self, _ok: bool):
        if self._polling or self._done:
            return
        self._polling = True
        self._poll_name()

    def _poll_name(self):
        if self._done:
            return
        self.page.runJavaScript(self.cfg["username_js"], self._got_name)

    def _got_name(self, value):
        name = value.strip().lstrip("@") if isinstance(value, str) else ""
        if name:
            self._name = name
            self._finalize()
            return
        self._tries += 1
        if self._tries < 6:
            QTimer.singleShot(1200, self._poll_name)
        else:
            self._finalize()

    def _finalize(self):
        if self._done:
            return
        self._done = True
        self.result = {"username": self._name, "cookies": list(self._cookies.values())}
        self.accept()

    def done(self, r):
        self._done = True
        # Tear down in the order Qt requires (page before profile).
        self.page.deleteLater()
        self.view.deleteLater()
        self.profile.deleteLater()
        super().done(r)
