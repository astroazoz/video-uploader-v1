"""Shrink the PyInstaller output by deleting Qt files the app provably never loads.

Run by build.py after PyInstaller:   python prune.py dist/VideoDownloader [--dry-run]

What is removed (and why it is safe):
  * Qt libraries / Python modules that no module the app uses can reach. Reachability is computed from the
    real import tables of the DLLs (pefile), starting from the Qt modules the app imports, the Qt plugins
    that are kept, and the WebEngine helper process. Nothing that is reachable is ever touched.
  * Chromium developer-tools resources and debug resource copies, Qt .qm translation files (the app UI is
    English only) and the non-English Chromium locale packs (en-US is kept).
  * The QML runtime folders, QML tooling plugins and other plugin families the app never asks for.
The self-check (VideoDownloader.exe --selfcheck) and the build's smoke test prove that everything still works.
"""

import os
import shutil
import sys
from pathlib import Path

# Qt modules the app imports directly (see ui.py, login_dialog.py, glass.py, selfcheck.py).
ROOT_MODULES = ["QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtMultimedia", "QtMultimediaWidgets", "QtWebEngineCore",
                "QtWebEngineWidgets", "QtWebChannel"]
KEEP_PLUGIN_DIRS = {"platforms", "styles", "imageformats", "tls", "networkinformation", "multimedia", "platforminputcontexts",
                    "generic", "iconengines", "platformthemes"}
KEEP_LOCALES = {"en-US.pak"}


def _imports(path: Path) -> list[str]:
    import pefile
    try:
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                               pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        ents = list(getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])) + list(getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []))
        pe.close()
        return [e.dll.decode().lower() for e in ents]
    except Exception:  # noqa: BLE001
        return []


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def prune(app_dir: Path, dry: bool = False) -> int:
    py = app_dir / "_internal" / "PySide6"
    if not py.is_dir():
        py = app_dir / "PySide6"
    if not py.is_dir():
        print("prune: PySide6 folder not found, nothing to do")
        return 0
    removed = 0

    def drop(p: Path, why: str) -> None:
        nonlocal removed
        if not p.exists():
            return
        n = _size(p)
        removed += n
        if not dry:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        print(f"  - {n / 1048576:7.1f} MB  {p.relative_to(py)}  ({why})")

    # 1. plugin families and QML runtime the app never uses
    plug = py / "plugins"
    if plug.is_dir():
        for d in plug.iterdir():
            if d.is_dir() and d.name not in KEEP_PLUGIN_DIRS:
                drop(d, "unused plugin family")
    drop(py / "qml", "QML runtime")
    for extra in ("lib", "metatypes", "typesystems", "include", "glue", "scripts", "support"):
        drop(py / extra, "development files")

    # 2. translations, devtools, debug copies
    tr = py / "translations"
    if tr.is_dir():
        for f in tr.glob("*.qm"):
            drop(f, "translation")
        loc = tr / "qtwebengine_locales"
        if loc.is_dir():
            for f in loc.glob("*.pak"):
                if f.name not in KEEP_LOCALES:
                    drop(f, "non-English Chromium locale")
    res = py / "resources"
    if res.is_dir():
        for f in res.iterdir():
            if ".debug." in f.name or "devtools" in f.name:
                drop(f, "developer tools / debug copy")

    # 3. binaries unreachable from what the app uses
    files = {f.name.lower(): f for f in py.iterdir() if f.is_file()}
    # every Python module PyInstaller bundled stays: shiboken imports a module's sibling modules at run time
    # (e.g. QtWebEngineWidgets needs QtPrintSupport), which no DLL import table shows
    stack = [f for k, f in files.items() if k.endswith(".pyd")]
    if plug.is_dir():
        stack += [p for p in plug.rglob("*.dll")]
    if (py / "QtWebEngineProcess.exe").exists():
        stack.append(py / "QtWebEngineProcess.exe")
    need: set[str] = set()
    while stack:
        p = stack.pop()
        key = p.name.lower()
        if key in need:
            continue
        need.add(key)
        for d in _imports(p):
            q = files.get(d)
            if q is not None:
                stack.append(q)
    for k, f in sorted(files.items()):
        # only Qt's own libraries/modules are candidates; helper DLLs loaded at run time (the software OpenGL
        # fallback opengl32sw.dll that PCs without a GPU driver need, d3dcompiler, ...) are never touched
        if k.startswith("qt") and k.endswith((".dll", ".pyd")) and k not in need:
            drop(f, "not reachable from the Qt modules the app uses")
    print(f"prune: {'would remove' if dry else 'removed'} {removed / 1048576:.0f} MB")
    return removed


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("usage: python prune.py <dist/VideoDownloader> [--dry-run]")
    prune(Path(args[0]), dry="--dry-run" in sys.argv)
