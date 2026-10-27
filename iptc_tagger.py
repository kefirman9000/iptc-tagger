#!/usr/bin/env python3
"""
IPTC Tagger: drop JPEG photos or folders in, let a free AI vision model running on
your own computer (through Ollama) suggest a title and keywords for each one,
review and edit them, then save them into the files.

Only metadata is written (IPTC and XMP title + keywords). The picture itself is
never re-encoded: every save is checked, and a file whose image data changed
would be restored automatically.
"""
from __future__ import annotations

import os
import sys
import functools
import threading
import time

from PySide6.QtCore import QDate, QItemSelectionModel, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QStackedWidget,
    QStyle, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

import tagger_core as core

THUMB = 72
# column order in the list: status, type and the Detect/Save buttons sit before the text you edit
COL_CHECK, COL_THUMB, COL_FILE, COL_STATUS, COL_TYPE, COL_ACTION, COL_NOTE, COL_TITLE, COL_KEYWORDS = range(9)
PREVIEW = 520   # long edge of the large preview in the review panel
GREY = "#8a8f98"
STATUS_COLORS = {
    "reading": GREY, "ready": GREY, "identifying": "#2f7ddd", "suggested": "#b8860b",
    "edited": "#b8860b", "saving": "#2f7ddd", "saved": "#2e9d57", "error": "#d64545",
}
OLLAMA_DOWNLOAD = "https://ollama.com/download"


# --------------------------------------------------------------------------- background jobs

class JobSignals(QObject):
    started = Signal(object)            # key: the job has left the queue and is running
    done = Signal(object, object)       # key, result
    failed = Signal(object, str)        # key, message
    progress = Signal(object, object)   # key, (fraction or None, text)


class Job(QRunnable):
    """Runs fn(*args) on a worker thread and reports back on the main thread.
    With progress=True, fn also receives progress= and cancel= keyword arguments."""

    def __init__(self, key, fn, *args, cancel: threading.Event | None = None, progress: bool = False):
        super().__init__()
        self.setAutoDelete(False)
        self.key, self.fn, self.args, self.cancel, self.with_progress = key, fn, args, cancel, progress
        self.signals = JobSignals()

    def run(self):
        if self.cancel is not None and self.cancel.is_set():
            self.signals.failed.emit(self.key, "__cancelled__")
            return
        self.signals.started.emit(self.key)
        kwargs = {}
        if self.with_progress:
            kwargs = {"progress": lambda frac, text: self.signals.progress.emit(self.key, (frac, text)),
                      "cancel": self.cancel}
        try:
            result = self.fn(*self.args, **kwargs)
        except Exception as e:   # every failure is shown to the user
            self.signals.failed.emit(self.key, str(e) or e.__class__.__name__)
        else:
            self.signals.done.emit(self.key, result)


def run_job(owner, job: Job, pool: QThreadPool, on_done, on_failed, on_progress=None, on_started=None):
    """Start a job, keeping it alive until it reports back."""
    owner.jobs.add(job)

    def done(key, res, job=job):
        owner.jobs.discard(job)
        on_done(key, res)

    def failed(key, msg, job=job):
        owner.jobs.discard(job)
        on_failed(key, msg)

    job.signals.done.connect(done)
    job.signals.failed.connect(failed)
    if on_progress:
        job.signals.progress.connect(on_progress)
    if on_started:
        job.signals.started.connect(on_started)
    pool.start(job)


class NoWheelComboBox(QComboBox):
    """A dropdown that ignores the mouse wheel unless its list is open, so scrolling over the photo
    list scrolls the list instead of changing a photo's Type by accident."""

    def __init__(self, *args):
        super().__init__(*args)
        self.setFocusPolicy(Qt.StrongFocus)          # don't grab keyboard focus just because the wheel passed over

    def wheelEvent(self, event):
        if self.view().isVisible():                  # list open: the wheel scrolls through the choices
            super().wheelEvent(event)
        else:
            event.ignore()                           # hand the wheel to the list behind it


def make_image(path: str, size: int) -> QImage:
    """Upright preview, made from a reduced decode so big photos stay fast."""
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im.draft("RGB", (size * 2, size * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size))
        data = im.tobytes("raw", "RGB")
        qimg = QImage(data, im.width, im.height, im.width * 3, QImage.Format_RGB888)
        return qimg.copy()   # own the pixels once the Pillow buffer goes away


def make_thumbnail(path: str) -> QImage:
    return make_image(path, THUMB * 2)


def make_preview(path: str) -> QImage:
    return make_image(path, PREVIEW)


def check_ai(url: str) -> dict:
    """Is Ollama running, and which vision models are installed?"""
    version = core.ollama_version(url)
    models = core.list_vision_models(url) if version is not None else []
    return {"version": version, "models": models}


def same_model(a: str, b: str) -> bool:
    """'gemma3' and 'gemma3:latest' are the same model."""
    norm = lambda n: n if ":" in n else n + ":latest"
    return norm(a.strip()) == norm(b.strip())


# --------------------------------------------------------------------------- model

# "Show" filter for the list: (key, label)
FILTERS = [
    ("all", "All photos"),
    ("unsaved", "Unsaved changes"),
    ("no_title", "No title"),
    ("no_keywords", "No keywords"),
    ("few_keywords", "Fewer keywords than the minimum"),
    ("suggested", "AI suggestions to review"),
    ("saved", "Saved"),
    ("errors", "Errors"),
    ("checked", "Checked"),
    ("unchecked", "Not checked"),
    ("no_date", "No date taken"),
    ("has_note", "With photo notes"),
]


def photo_matches(ph, key: str, search: str = "", keyword_min: int = 0) -> bool:
    """Does this photo pass the Show filter and the search? The search looks in the file name,
    title and keywords; every word typed must appear somewhere (in any order, any case)."""
    ok = {
        "all": True,
        "unsaved": ph.changed,
        "no_title": not ph.title.strip(),
        "no_keywords": not ph.keywords,
        "few_keywords": len(ph.keywords) < keyword_min,
        "suggested": ph.status == "suggested",
        "saved": ph.status == "saved" or (not ph.changed and bool(ph.file_title or ph.file_keywords)),
        "errors": ph.status == "error",
        "checked": ph.checked,
        "unchecked": not ph.checked,
        "no_date": not ph.taken,
        "has_note": bool(ph.note.strip()),
    }.get(key, True)
    if not ok:
        return False
    words = search.casefold().split()
    if not words:
        return True
    haystack = " ".join([os.path.basename(ph.path), ph.title, ", ".join(ph.keywords)]).casefold()
    return all(w in haystack for w in words)


class Photo:
    def __init__(self, path: str):
        self.path = path
        self.note = ""                   # per-photo clarification for the AI (not saved to the file)
        self.title = ""
        self.keywords: list[str] = []
        self.file_title = ""             # what is currently saved in the file
        self.file_keywords: list[str] = []
        self.status = "reading"
        self.message = ""
        self.busy = False
        self.row_button: QPushButton | None = None   # the row's own Save button
        self.detect_button: QPushButton | None = None  # the row's own Detect (run the AI) button
        self.type_key = ""                            # photo type; "" = use the default type
        self.taken = ""                               # capture date YYYY-MM-DD, for {year} {month} {date}
        self.ai_started = 0.0                         # when the AI started on this photo (for the timer)
        self.token: core.CancelToken | None = None    # stop switch for this photo's AI request
        self.retries = 0                              # automatic retries used after going over the time limit
        self.timed_out = False                        # stopped because it went over the time limit
        self.redo: dict | None = None                 # "Redo with reasons": {'feedback', 'rejected'} until it's done
        self.checked = False                          # ticked in the check column: the buttons act on it
        self.title_date = ""                          # the date last put into this title (custom or own)
        self.type_combo: QComboBox | None = None      # the row's Type dropdown

    @property
    def changed(self) -> bool:
        return (core.clean_title(self.title), core.clean_keywords(self.keywords)) != \
               (core.clean_title(self.file_title), core.clean_keywords(self.file_keywords))


# --------------------------------------------------------------------------- settings dialog

class SettingsDialog(QDialog):
    def __init__(self, parent, settings: dict, detected_exiftool: str | None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(600)
        self.settings = dict(settings)
        self.jobs: set[Job] = set()
        self.pool = QThreadPool(self)
        self.pull_cancel = threading.Event()
        self.installed: list[str] = []

        # ----- AI model (Ollama)
        ai = QGroupBox("AI recognition: free, runs on this computer with Ollama")
        ai_lay = QVBoxLayout(ai)
        self.ai_state = QLabel("Checking for Ollama…")
        self.ai_state.setWordWrap(True)
        self.ai_state.setOpenExternalLinks(True)
        recheck = QPushButton("Check again")
        recheck.clicked.connect(lambda *_: self.refresh())
        row = QHBoxLayout()
        row.addWidget(self.ai_state, 1)
        row.addWidget(recheck)
        ai_lay.addLayout(row)

        form = QFormLayout()
        self.model = QComboBox()
        form.addRow("Model to use", self.model)
        ai_lay.addLayout(form)

        ai_lay.addWidget(QLabel("Free models you can download with one click:"))
        grid = QGridLayout()
        self.dl_buttons = {}
        for r, (name, label, size, note) in enumerate(core.RECOMMENDED_MODELS):
            grid.addWidget(QLabel(f"<b>{label}</b>"), r, 0)
            hint = QLabel(f"{size} download · {note}")
            hint.setStyleSheet(f"color:{GREY}")
            grid.addWidget(hint, r, 1)
            b = QPushButton("Download")
            b.clicked.connect(lambda _=False, n=name: self.download(n))
            grid.addWidget(b, r, 2)
            self.dl_buttons[name] = b
        grid.setColumnStretch(1, 1)
        ai_lay.addLayout(grid)
        self.pull_bar = QProgressBar()
        self.pull_bar.hide()
        self.pull_text = QLabel("")
        self.pull_text.setStyleSheet(f"color:{GREY}")
        self.pull_text.hide()
        self.pull_stop = QPushButton("Stop download")
        self.pull_stop.clicked.connect(lambda *_: self.pull_cancel.set())
        self.pull_stop.hide()
        prow = QHBoxLayout()
        prow.addWidget(self.pull_bar, 1)
        prow.addWidget(self.pull_stop)
        ai_lay.addLayout(prow)
        ai_lay.addWidget(self.pull_text)

        adv = QFormLayout()
        self.url = QLineEdit(settings.get("ollama_url", core.DEFAULT_OLLAMA_URL))
        self.url.setToolTip("Only change this if Ollama runs on another computer or port")
        self.url.editingFinished.connect(self.refresh)
        adv.addRow("Ollama address", self.url)
        ai_lay.addLayout(adv)

        # ----- the other settings, one form per page
        f_ai, f_text, f_save, f_types, f_general = (QFormLayout() for _ in range(5))
        kmin, kmax = core.keyword_range(settings.get("keyword_min"), settings.get("keyword_max"))
        self.kmin, self.kmax = QSpinBox(), QSpinBox()
        for box, val in ((self.kmin, kmin), (self.kmax, kmax)):
            box.setRange(5, 50)
            box.setValue(val)
        krow = QHBoxLayout()
        krow.addWidget(QLabel("from"))
        krow.addWidget(self.kmin)
        krow.addWidget(QLabel("to"))
        krow.addWidget(self.kmax)
        krow.addStretch(1)
        f_text.addRow("Keywords per photo", krow)
        self.tlimit = QSpinBox()
        self.tlimit.setRange(0, 900)
        self.tlimit.setSingleStep(10)
        self.tlimit.setSuffix(" s")
        self.tlimit.setSpecialValueText("No limit")
        self.tlimit.setValue(int(settings.get("ai_time_limit", 30) or 0))
        self.tlimit.setToolTip("If the AI takes longer than this on one photo, it's stopped.\n"
                               "The first photo of a session gets 90 s extra while Ollama loads the model.")
        self.tretry = QComboBox()
        for n, label in ((1, "then try once more"), (2, "then try twice more"), (0, "then skip the photo")):
            self.tretry.addItem(label, n)
        self.tretry.setCurrentIndex(max(0, self.tretry.findData(int(settings.get("ai_retries", 1)))))
        trow = QHBoxLayout()
        trow.addWidget(self.tlimit)
        trow.addWidget(self.tretry)
        trow.addStretch(1)
        f_ai.addRow("Time limit per photo", trow)
        self.symbols = QComboBox()
        for key, label in core.SYMBOL_MODES:
            self.symbols.addItem(label, key)
        self.symbols.setCurrentIndex(max(0, self.symbols.findData(settings.get("symbols", "convert"))))
        self.symbols.setToolTip("What happens to symbols like & + @ % in the AI's titles and keywords.\n"
                                "Your own text (text before titles, location, keywords to put first) is never changed.")
        f_text.addRow("Symbols", self.symbols)
        self.title_len = QComboBox()
        for key, label, _lo, _hi in core.TITLE_LENGTHS:
            self.title_len.addItem(label, key)
        self.title_len.setCurrentIndex(max(0, self.title_len.findData(settings.get("title_length", "detailed"))))
        self.title_len.setToolTip("How long the AI's titles are. Detailed titles say what the subject is or is known\n"
                                  "for, the setting and details of the scene, e.g. 'McDonald's Restaurant Exterior, Major\n"
                                  "American Fast Food Chain, with Drive-Thru Lane and Parking Lot'. A title that comes back shorter is expanded once.")
        f_text.insertRow(0, "Title length", self.title_len)
        self.et = QLineEdit(settings.get("exiftool_path", ""))
        self.et.setPlaceholderText(detected_exiftool or "not found: install ExifTool or choose it here")
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda *_: self._browse())
        et_row = QHBoxLayout()
        et_row.addWidget(self.et, 1)
        et_row.addWidget(browse)
        f_save.addRow("ExifTool", et_row)
        hint = QLabel("Leave ExifTool blank to find it automatically.")
        hint.setStyleSheet(f"color:{GREY}")
        f_save.addRow("", hint)
        # which metadata fields are written
        chosen = set(core.meta_fields(settings))
        self.field_boxes: dict[str, QCheckBox] = {}
        fields_box = QGroupBox("Write the title and keywords into")
        fgrid = QGridLayout(fields_box)
        groups = []
        for f in core.METADATA_FIELDS:
            if f["group"] not in groups:
                groups.append(f["group"])
        for col, group in enumerate(groups):
            head = QLabel(f"<b>{group}</b>")
            fgrid.addWidget(head, 0, col)
            for r, f in enumerate([f for f in core.METADATA_FIELDS if f["group"] == group], start=1):
                cb = QCheckBox(f["label"])
                cb.setChecked(f["key"] in chosen)
                cb.setToolTip(f["tip"])
                fgrid.addWidget(cb, r, col)
                self.field_boxes[f["key"]] = cb
        reset = QPushButton("Usual fields")
        reset.setToolTip("Tick the fields that are normally expected, untick the rest")
        reset.clicked.connect(lambda *_: [cb.setChecked(k in core.DEFAULT_META_FIELDS)
                                          for k, cb in self.field_boxes.items()])
        fgrid.addWidget(reset, 5, len(groups) - 1)
        fhint = QLabel("The ticked fields are the ones normally expected by Lightroom, Bridge, Photo Mechanic and "
                       "stock sites. Hover over a field to see who reads it.")
        fhint.setStyleSheet(f"color:{GREY}")
        fhint.setWordWrap(True)
        fgrid.addWidget(fhint, 6, 0, 1, len(groups))
        f_save.addRow(fields_box)
        self.custom = QPlainTextEdit(settings.get("custom_types", ""))
        self.custom.setPlaceholderText("One per line, as  Name: what the photo shows\n"
                                       "e.g. Restaurant storefront: the exterior of the restaurant with its sign "
                                       "visible from the street")
        self.custom.setMaximumHeight(90)
        self.custom.setMaximumHeight(16777215)
        f_types.addRow(QLabel("Add your own photo types, one per line, as  <b>Name: what the photo shows</b>. "
                              "They appear in every Type list."))
        f_types.addRow(self.custom)

        self.x2i = QCheckBox("Copy XMP to IPTC after every save (for sites that read IPTC)")
        self.x2i.setChecked(bool(settings.get("xmp2iptc_on_save", True)))
        self.x2i.setToolTip("Runs ExifTool with xmp2iptc.args on each photo as it's saved, so its IPTC fields match the XMP ones:\n"
                            "-overwrite_original -P -m -charset iptc=utf8 -codedcharacterset=utf8 -tagsFromFile @ "
                            "-@ xmp2iptc.args")
        f_save.addRow("When saving", self.x2i)
        self.x2i_path = QLineEdit(settings.get("xmp2iptc_args", ""))
        found = core.find_xmp2iptc_args(detected_exiftool or core.find_exiftool(settings.get("exiftool_path", "")))
        self.x2i_path.setPlaceholderText(found or "not found")
        x2i_browse = QPushButton("Browse…")
        x2i_browse.clicked.connect(lambda *_: self._browse_args())
        x2i_row = QHBoxLayout()
        x2i_row.addWidget(self.x2i_path, 1)
        x2i_row.addWidget(x2i_browse)
        f_save.addRow("xmp2iptc.args", x2i_row)
        x2i_hint = QLabel("Leave blank to use the one next to ExifTool, or the copy that comes with this app.")
        x2i_hint.setStyleSheet(f"color:{GREY}")
        f_save.addRow("", x2i_hint)
        # troubleshooting: debug log
        self.debug = QCheckBox("Debug mode: also record every AI request in the log")
        self.debug.setChecked(bool(settings.get("debug_log")))
        self.debug.setToolTip("Records timings, what Ollama reports for each reply, and every error, stop,\n"
                              "time-out and retry, so a problem can be tracked down. Photos aren't included.")
        open_log = QPushButton("Open log")
        open_log.setToolTip(core.debug_log_path())
        open_log.clicked.connect(lambda *_: self._open_log())
        clear_log = QPushButton("Clear log")
        clear_log.clicked.connect(lambda *_: self._clear_log())
        drow = QHBoxLayout()
        drow.addWidget(self.debug)
        drow.addStretch(1)
        drow.addWidget(open_log)
        drow.addWidget(clear_log)
        f_general.addRow("Troubleshooting", drow)
        dhint = QLabel(f"One log for everything: unexpected errors are always kept in it, and Debug mode adds the "
                       f"details of every AI request. It's saved as {core.debug_log_path()}. "
                       "Send that file when reporting a problem.")
        dhint.setStyleSheet(f"color:{GREY}")
        dhint.setWordWrap(True)
        f_general.addRow("", dhint)
        self.auto_update = QCheckBox("Download and install updates automatically")
        self.auto_update.setChecked(bool(settings.get("auto_update", True)))
        self.auto_update.setToolTip("A new version is downloaded in the background. Restart when it suits you, or it\n"
                                    "installs itself when you close the app. Your settings are kept.")
        self.updates = QCheckBox("Check for a new version when the app starts")
        self.updates.setChecked(bool(settings.get("check_updates", True)))
        self.updates.setToolTip("Asks GitHub whether a newer release exists. Nothing about you or your photos is sent,\n"
                                "and nothing is downloaded or installed: you choose whether to get it.")
        check_now = QPushButton("Check now")
        check_now.clicked.connect(lambda *_: self._check_now())
        self.update_result = QLabel(f"You have {core.APP_NAME} {core.APP_VERSION}.")
        self.update_result.setStyleSheet(f"color:{GREY}")
        self.update_result.setOpenExternalLinks(True)
        urow = QHBoxLayout()
        urow.addWidget(self.updates)
        urow.addStretch(1)
        urow.addWidget(self.update_result)
        urow.addWidget(check_now)
        f_general.addRow("Updates", urow)
        f_general.addRow("", self.auto_update)
        whats_new = QPushButton("What's new…")
        whats_new.setToolTip("What changed in this version and the ones before it")
        whats_new.clicked.connect(lambda *_: show_whats_new(self, "", full=True))
        f_general.addRow("", whats_new)

        # ----- pages
        self.pages = QTabWidget()
        for title, widgets in (("AI", [ai, f_ai]), ("Titles && keywords", [f_text]), ("Saving", [f_save]),
                               ("Photo types", [f_types]), ("General", [f_general])):
            page = QWidget()
            pl = QVBoxLayout(page)
            for w in widgets:
                (pl.addLayout if isinstance(w, QFormLayout) else pl.addWidget)(w)
            pl.addStretch(1)
            self.pages.addTab(page, title)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.pages)
        lay.addWidget(self.buttons)
        self.refresh()

    def _browse_args(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose xmp2iptc.args", "", "ExifTool args (*.args);;All files (*)")
        if path:
            self.x2i_path.setText(path)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose ExifTool")
        if path:
            self.et.setText(path)

    def refresh(self):
        self.ai_state.setText("Checking for Ollama…")
        run_job(self, Job("check", check_ai, self.url.text().strip() or core.DEFAULT_OLLAMA_URL),
                self.pool, self._checked, lambda _k, msg: self._checked(None, {"version": None, "models": []}))

    def _checked(self, _key, info):
        self.installed = list(info["models"])
        wanted = self.model.currentData() or self.settings.get("model") or core.DEFAULT_MODEL
        self.model.clear()
        if info["version"] is None:
            self.ai_state.setText(f"<b>Ollama isn't running.</b> It's free: <a href='{OLLAMA_DOWNLOAD}'>download "
                                  "Ollama</a>, install and open it, then click <i>Check again</i>.")
            self.model.addItem(f"{wanted} (Ollama not running)", wanted)
        elif not self.installed:
            self.ai_state.setText(f"Ollama {info['version']} is running ✓. "
                                  "<b>Download a model below to start.</b>")
            self.model.addItem("No model downloaded yet", wanted)
        else:
            self.ai_state.setText(f"Ollama {info['version']} is running ✓")
            for n in self.installed:
                self.model.addItem(n, n)
            idx = next((i for i, n in enumerate(self.installed) if same_model(n, wanted)), 0)
            self.model.setCurrentIndex(idx)
        running = info["version"] is not None
        downloading = self.pull_bar.isVisible()
        for name, b in self.dl_buttons.items():
            have = any(same_model(name, n) for n in self.installed)
            b.setText("Installed ✓" if have else "Download")
            b.setEnabled(running and not have and not downloading)

    def download(self, name):
        self.pull_cancel.clear()
        for b in self.dl_buttons.values():
            b.setEnabled(False)
        self.buttons.setEnabled(False)   # stay open until the download ends
        self.pull_bar.setRange(0, 0)
        self.pull_bar.show()
        self.pull_stop.show()
        self.pull_text.setText(f"Downloading {name}…")
        self.pull_text.show()
        run_job(self, Job(name, core.pull_model, self.url.text().strip() or core.DEFAULT_OLLAMA_URL, name,
                          progress=True, cancel=self.pull_cancel),
                self.pool, self._pulled, self._pull_failed, self._pull_progress)

    def _pull_progress(self, name, info):
        frac, text = info
        if frac is None:
            self.pull_bar.setRange(0, 0)
        else:
            self.pull_bar.setRange(0, 1000)
            self.pull_bar.setValue(int(frac * 1000))
        self.pull_text.setText(f"Downloading {name}: {text}" + (f" ({frac:.0%})" if frac is not None else ""))

    def _pull_end(self):
        self.pull_bar.hide()
        self.pull_stop.hide()
        self.buttons.setEnabled(True)

    def _pulled(self, name, _res):
        self._pull_end()
        self.pull_text.setText(f"{name} is ready ✓")
        self.settings["model"] = name
        self.model.clear()
        self.model.addItem(name, name)   # select it once the list refreshes
        self.refresh()

    def _pull_failed(self, name, msg):
        self._pull_end()
        self.pull_text.setText(f"Couldn't download {name}: {msg}")
        self.refresh()

    def reject(self):
        if self.pull_bar.isVisible():   # never leave a download running in the background
            return
        super().reject()

    def _check_now(self):
        self.update_result.setText("Checking…")
        win = self.parent()
        if win is None or not hasattr(win, "check_updates"):
            return
        def shown(info):
            if info:
                self.update_result.setText(f"<a href='{info['url']}'>{core.APP_NAME} {info['version']} is available</a>")
            else:
                self.update_result.setText(f"{core.APP_VERSION} is the latest version (or GitHub couldn't be reached).")
        win.settings["skipped_update"] = ""            # checking by hand shows it even if it was skipped
        win.check_updates(on_done=shown)

    def _open_log(self):
        path = core.debug_log_path()
        if not os.path.exists(path):
            QMessageBox.information(self, "No log yet", "There's no debug log yet. Tick Debug mode, click Save, "
                                    "then detect some photos.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _clear_log(self):
        try:
            os.remove(core.debug_log_path())
        except OSError:
            pass
        QMessageBox.information(self, "Log cleared", "The debug log is empty again.")

    def values(self) -> dict:
        s = dict(self.settings)
        s.update(ollama_url=self.url.text().strip() or core.DEFAULT_OLLAMA_URL,
                 model=self.model.currentData() or s.get("model") or core.DEFAULT_MODEL,
                 exiftool_path=self.et.text().strip(),
                 custom_types=self.custom.toPlainText().strip(),
                 xmp2iptc_on_save=self.x2i.isChecked(), xmp2iptc_args=self.x2i_path.text().strip(),
                 meta_fields=[k for k, cb in self.field_boxes.items() if cb.isChecked()],
                 ai_time_limit=self.tlimit.value(), ai_retries=int(self.tretry.currentData() or 0),
                 debug_log=self.debug.isChecked(), symbols=self.symbols.currentData() or "convert",
                 title_length=self.title_len.currentData() or "detailed",
                 check_updates=self.updates.isChecked(), auto_update=self.auto_update.isChecked())
        s["keyword_min"], s["keyword_max"] = core.keyword_range(self.kmin.value(), self.kmax.value())
        return s


# --------------------------------------------------------------------------- review panel

class ReviewPanel(QWidget):
    """Right-hand panel: look at one photo, edit its title and keywords, and save
    or detect just that photo again. Changes appear in the list straight away."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.photo: Photo | None = None
        self._loading = False
        self.setMinimumWidth(330)

        self.empty = QLabel("Select a photo in the list to review and edit it here.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color:{GREY};padding:30px")

        self.body = QWidget()
        b = QVBoxLayout(self.body)
        b.setContentsMargins(0, 0, 0, 0)
        self.preview = QLabel("Loading preview…")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(220)
        self.preview.setStyleSheet("QLabel{background:#7f7f7f18;border-radius:8px;color:#8a8f98}")
        self.name = QLabel()
        self.name.setWordWrap(True)
        self.state = QLabel()
        self.state.setWordWrap(True)

        self.note = QLineEdit()
        self.note.setPlaceholderText("Tell the AI something about this photo (optional)")
        self.title = QLineEdit()
        self.title.setPlaceholderText("Title")
        self.title_info = QLabel()
        self.title_info.setStyleSheet(f"color:{GREY}")
        self.title_info.setWordWrap(True)
        self.keywords = QPlainTextEdit()
        self.keywords.setPlaceholderText("Keywords, separated by commas")
        self.keywords.setTabChangesFocus(True)
        self.kw_info = QLabel()
        self.kw_info.setStyleSheet(f"color:{GREY}")

        def lbl(text):
            l = QLabel(f"<b>{text}</b>")
            return l

        self.b_identify = QPushButton("Detect Again")
        self.b_identify.setToolTip("Run the AI on this photo again for a fresh title and keywords.\n"
                                   "Add or change the photo notes first to steer it.")
        self.b_revert = QPushButton("Revert")
        self.b_revert.setToolTip("Throw away unsaved changes and go back to what's saved in the file")
        self.b_prev = QPushButton("◂ Previous")
        self.b_next = QPushButton("Next ▸")
        self.b_save = QPushButton("Save")
        self.b_save.setToolTip("Save this photo's title and keywords into the file now")
        self.b_save.setProperty("primary", True)
        self.b_save_next = QPushButton("Save && Next ▸")
        self.b_save_next.setToolTip("Save this photo (if changed) and move to the next one (Ctrl/⌘+Return)")
        self.b_save_next.setProperty("primary", True)

        b.addWidget(self.preview, 3)
        b.addWidget(self.name)
        b.addWidget(self.state)
        self.type = NoWheelComboBox()
        self.type.setToolTip("What kind of photo this is. Helps the AI write the right kind of title")
        b.addWidget(lbl("Photo type"))
        b.addWidget(self.type)
        b.addWidget(lbl("Photo notes for the AI"))
        b.addWidget(self.note)
        b.addWidget(lbl("Title"))
        b.addWidget(self.title)
        b.addWidget(self.title_info)
        b.addWidget(lbl("Keywords"))
        b.addWidget(self.keywords, 2)
        b.addWidget(self.kw_info)
        self.b_copy = QPushButton("Copy")
        self.b_copy.setToolTip("Copy this photo's title and keywords (Ctrl/⌘+C), to paste onto other photos")
        self.b_paste = QPushButton("Paste")
        self.b_paste.setToolTip("Paste a copied title and keywords into this photo (Ctrl/⌘+V)")
        self.b_redo = QPushButton("Redo with Reasons…")
        self.b_redo.setToolTip("Tell the AI what's wrong with this suggestion (too generic, wrong subject, too long…)\n"
                               "and it tries again with that in mind")
        r1 = QHBoxLayout()
        r1.addWidget(self.b_identify)
        r1.addWidget(self.b_redo)
        r1.addWidget(self.b_revert)
        r1.addWidget(self.b_copy)
        r1.addWidget(self.b_paste)
        b.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(self.b_save)
        r2.addWidget(self.b_save_next)
        b.addLayout(r2)
        r3 = QHBoxLayout()
        r3.addWidget(self.b_prev)
        r3.addWidget(self.b_next)
        b.addLayout(r3)

        lay = QVBoxLayout(self)
        lay.addWidget(self.empty)
        lay.addWidget(self.body)
        self.body.hide()

        self.note.textEdited.connect(self._edited)
        self.type.currentIndexChanged.connect(self._type_chosen)
        self.title.textEdited.connect(self._edited)
        self.keywords.textChanged.connect(self._edited)
        self.b_identify.clicked.connect(lambda *_: self.photo and win.identify_photos([self.photo]))
        self.b_redo.clicked.connect(lambda *_: self.photo and win.redo_photos([self.photo]))
        self.b_revert.clicked.connect(lambda *_: self.photo and win.revert_photos([self.photo]))
        self.b_save.clicked.connect(lambda *_: self.photo and win.save_photos([self.photo]))
        self.b_save_next.clicked.connect(lambda *_: win.save_and_next())
        self.b_copy.clicked.connect(lambda *_: win.copy_metadata())
        self.b_paste.clicked.connect(lambda *_: win.paste_metadata("both"))
        self.b_prev.clicked.connect(lambda *_: win.select_offset(-1))
        self.b_next.clicked.connect(lambda *_: win.select_offset(1))

    def show_photo(self, ph: Photo | None, selected: int = 1):
        """Load a photo into the panel (or show a hint when 0 or several are selected)."""
        new_photo = ph is not self.photo
        self.photo = ph
        if ph is None:
            self.body.hide()
            self.empty.setText("Select a photo in the list to review and edit it here." if selected == 0 else
                               f"{selected} photos selected. Detect and Save metadata above work on all of "
                               "them. Select a single photo to review it here.")
            self.empty.show()
            return
        self.empty.hide()
        self.body.show()
        self._loading = True
        self.win.fill_type_combo(self.type, ph.type_key)
        self.name.setText(f"<b>{os.path.basename(ph.path)}</b><br><span style='color:{GREY}'>"
                          f"{os.path.dirname(ph.path)}</span>")
        # only replace text that actually differs, so the cursor isn't thrown around
        if self.note.text() != ph.note:
            self.note.setText(ph.note)
        if core.clean_title(self.title.text()) != ph.title or new_photo:
            self.title.setText(ph.title)
        if core.parse_keywords_text(self.keywords.toPlainText()) != ph.keywords or new_photo:
            self.keywords.setPlainText(core.keywords_to_text(ph.keywords))
        self._loading = False
        if new_photo:
            self.win.request_preview(ph.path)
        self.refresh_state()

    def set_preview(self, path: str, pixmap):
        if self.photo is not None and self.photo.path == path:
            self.preview.setPixmap(pixmap)

    def refresh_state(self):
        ph = self.photo
        if ph is None:
            return
        busy = ph.busy
        for w in (self.note, self.title):
            w.setReadOnly(busy)
        self.type.setEnabled(not busy)
        self.keywords.setReadOnly(busy)
        status = self.win.status_text(ph)
        self.state.setText(f"<span style='color:{status[1]}'>{status[0]}</span>")
        n_bytes = len(ph.title.encode("utf-8"))
        info = f"{len(ph.title)} characters"
        if n_bytes > core.IPTC_TITLE_MAX and not self.win.settings.get("xmp2iptc_on_save", True):
            info += f". The older IPTC field keeps only the first {core.IPTC_TITLE_MAX} bytes; XMP keeps it all."
        self.title_info.setText(info)
        self.kw_info.setText(f"{len(ph.keywords)} keyword{'s' if len(ph.keywords) != 1 else ''}")
        can_write = self.win.exiftool is not None
        self.b_save.setEnabled(ph.changed and not busy and can_write)
        self.b_save_next.setEnabled(not busy and (can_write or not ph.changed))
        self.b_identify.setEnabled(not busy)
        self.b_redo.setEnabled(not busy)
        self.b_revert.setEnabled(ph.changed and not busy)
        self.b_paste.setEnabled(not busy)
        row = self.win.rows.get(ph.path, 0)
        self.b_prev.setEnabled(row > 0)
        self.b_next.setEnabled(row < len(self.win.photos) - 1)

    def _type_chosen(self, *_):
        if self._loading or self.photo is None or self.photo.busy:
            return
        self.win.set_photo_type([self.photo], self.type.currentData() or "")

    def _edited(self, *_):
        if self._loading or self.photo is None or self.photo.busy:
            return
        ph = self.photo
        before = (ph.title, list(ph.keywords))
        ph.note = core.single_line(self.note.text())
        ph.title = core.clean_title(self.title.text())
        ph.keywords = core.parse_keywords_text(self.keywords.toPlainText())
        if (ph.title, ph.keywords) != before:
            if ph.status == "saved":
                ph.status = "ready"
            ph.message = ""
        self.win._show(ph, from_editor=True)
        self.win._refresh_buttons()


# --------------------------------------------------------------------------- main window

class DropPage(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setText("<div style='font-size:22px;font-weight:600'>Drop JPEG photos or folders here</div>"
                     f"<div style='margin-top:10px;color:{GREY}'>Folders are searched including subfolders.<br>"
                     "Or use Add Photos / Add Folder above.</div>")
        self.setStyleSheet("QLabel{border:2px dashed #9aa3ad;border-radius:14px;margin:18px;padding:40px}")


def show_whats_new(parent, since: str = "", full: bool = False) -> bool:
    """The 'What's new' window. Returns False if there was nothing to show."""
    text = core.whats_new(since, full=full)
    if not text:
        if full:
            QMessageBox.information(parent, "What's new", "There are no release notes in this copy of the app.")
        return False
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"What's new in {core.APP_NAME} {core.APP_VERSION}")
    dlg.resize(560, 460)
    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setMarkdown(text)
    ok = QDialogButtonBox(QDialogButtonBox.Ok)
    ok.accepted.connect(dlg.accept)
    lay = QVBoxLayout(dlg)
    lay.addWidget(view)
    lay.addWidget(ok)
    dlg.exec()
    return True


class RedoDialog(QDialog):
    """Redo with reasons: tick what's wrong with the current suggestion, and the AI tries again with that
    in mind (it also sees the title being rejected, so it won't just repeat it)."""

    def __init__(self, parent, count: int, current_title: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Redo with reasons")
        lay = QVBoxLayout(self)
        what = (f"<b>Current title:</b> {current_title}" if count == 1 and current_title else
                f"<b>{count} photos</b>" if count > 1 else "")
        if what:
            lab = QLabel(what)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        lay.addWidget(QLabel("Tick all that apply:"))
        self.boxes = {}
        cols = QGridLayout()
        cols.setHorizontalSpacing(24)
        for col, (heading, reasons) in enumerate((("What's wrong", core.REDO_PROBLEMS),
                                                   ("What to focus on", core.REDO_FOCUS))):
            cols.addWidget(QLabel(f"<b>{heading}</b>"), 0, col)
            for r, (key, label, text) in enumerate(reasons, start=1):
                cb = QCheckBox(label)
                cb.setToolTip(text)                 # what the AI is told
                self.boxes[key] = cb
                cols.addWidget(cb, r, col)
        cols.setRowStretch(len(core.REDO_PROBLEMS) + 1, 1)
        lay.addLayout(cols)
        self.other = QLineEdit()
        self.other.setPlaceholderText("Anything else, e.g. it's a heron, not a crane / mention the cherry blossoms")
        lay.addWidget(QLabel("Anything else (optional):"))
        lay.addWidget(self.other)
        tip = QLabel("Tip: a photo note (e.g. the subject's name) helps the AI most when it got something wrong.")
        tip.setStyleSheet(f"color:{GREY}")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Redo")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def feedback(self) -> str:
        return core.redo_feedback([k for k, cb in self.boxes.items() if cb.isChecked()], self.other.text())


def banner_label() -> QLabel:
    b = QLabel()
    b.setWordWrap(True)
    b.setStyleSheet("QLabel{background:#fff4d6;color:#5c4400;border:1px solid #e9cf7a;"
                    "border-radius:8px;padding:8px 10px}")
    b.hide()
    return b


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{core.APP_NAME} {core.APP_VERSION}")
        self.resize(1400, 860)
        self.setAcceptDrops(True)

        self.settings = core.load_settings()
        core.set_debug(self.settings.get("debug_log", False))
        self.photos: list[Photo] = []
        self.rows: dict[str, int] = {}
        self.jobs: set[Job] = set()
        self.ai_pool = QThreadPool(self)
        self.ai_pool.setMaxThreadCount(1)   # a local model works on one photo at a time
        self.io_pool = QThreadPool(self)
        self.io_pool.setMaxThreadCount(4)
        self.cancel = threading.Event()        # stop switch for the most recent group of jobs
        self.cancels: list[threading.Event] = []   # every stop switch still in use
        self.batch_total = self.batch_done = 0
        self.stats = {"photos": 0, "seconds": 0.0, "kept": 0}
        self.ai = {"version": None, "models": []}
        self.previews: dict[str, QPixmap] = {}   # recent large previews for the review panel
        self._filling = False
        self.exiftool: core.ExifTool | None = None
        self._ready = False   # widgets send signals while the window is being built; ignore those

        self._build_ui()
        self._ready = True
        self.ai_timer = QTimer(self)                  # ticks once a second while the AI works
        self.ai_timer.setInterval(1000)
        self.ai_timer.timeout.connect(self._ai_heartbeat)
        self.models_loaded = False                    # first photo of the session also loads the model
        self.refresh_type_combos()
        self._date_options_changed(save=False)
        self._setup_exiftool()
        self._check_ai()
        self._refresh_buttons()
        if core.debugging():
            self.status.setText(f"Debug mode is on: details are written to {core.debug_log_path()}")
        if self.settings.get("check_updates", True) and not os.environ.get("IPTC_TAGGER_OFFLINE"):
            self.check_updates()
        if self.settings.get("last_seen_version") != core.APP_VERSION:
            QTimer.singleShot(400, self._show_whats_new_once)   # once the window is on screen
        if self.custom_date_value():
            self.status.setText("Custom date is on: titles use "
                                + core.format_date(self.custom_date_value(), "long")
                                + " instead of each photo's own date.")

    # ---------- layout
    def _build_ui(self):
        st = self.style()

        def button(text, slot, icon=None, primary=False):
            b = QPushButton(text)
            if icon is not None:
                b.setIcon(st.standardIcon(icon))
            if primary:
                b.setProperty("primary", True)
            b.clicked.connect(lambda *_, f=slot: f())
            return b

        self.btn_add = button("Add Photos…", self.add_photos_dialog, QStyle.SP_FileIcon)
        self.btn_folder = button("Add Folder…", self.add_folder_dialog, QStyle.SP_DirOpenIcon)
        self.btn_identify = button("Detect", self.identify, primary=True)
        self.btn_save = button("Save Metadata", self.save, QStyle.SP_DialogSaveButton, primary=True)
        self.btn_remove = button("Remove", self.remove_checked, QStyle.SP_TrashIcon)
        self.btn_remove.setToolTip("Remove the checked photos (or the selected ones) from this list. "
                                   "Files are not touched.")
        self.btn_clear = button("Clear List", self.clear_list)
        self.btn_check_all = button("Check All", lambda: self.set_checked(self.visible_photos(), True))
        self.btn_check_all.setToolTip("Tick every photo shown in the list")
        self.btn_uncheck_all = button("Uncheck All", lambda: self.set_checked(self.photos, False))
        self.btn_uncheck_all.setToolTip("Clear all ticks; the buttons then work on the selected photos, or all")
        self.btn_redo_checked = button("Redo Checked…", lambda: self.redo_photos(
            [p for p in self.checked_photos() if not p.busy]))
        self.btn_redo_checked.setToolTip("Redo the checked photos with reasons: tell the AI what's wrong and it tries again")
        self.btn_export = button("Export CSV…", self.export_csv)
        self.btn_export.setToolTip("Save the titles and keywords as a spreadsheet: Shutterstock or Adobe Stock upload\n"
                                   "format, or every detail. Uses the checked photos, else the selected, else all shown.")
        self.btn_instr = button("Hide Instructions", self.toggle_instructions)
        self.btn_settings = button("Settings", self.open_settings, QStyle.SP_FileDialogDetailedView)

        top = QHBoxLayout()
        for w in (self.btn_add, self.btn_folder):
            top.addWidget(w)
        top.addSpacing(16)
        for w in (self.btn_identify, self.btn_redo_checked, self.btn_save, self.btn_export):
            top.addWidget(w)
        top.addSpacing(16)
        for w in (self.btn_check_all, self.btn_uncheck_all):
            top.addWidget(w)
        top.addSpacing(16)
        for w in (self.btn_remove, self.btn_clear):
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(self.btn_instr)
        top.addWidget(self.btn_settings)

        # ----- instructions for the AI
        self.instructions = QGroupBox("Instructions for the AI (used every time you click Detect)")
        g = QGridLayout(self.instructions)
        about_lbl = QLabel("<b>About these photos</b><br><span style='color:#8a8f98'>Clarify what the "
                           "photos show: place, event, client, people's roles, species…</span>")
        dir_lbl = QLabel("<b>Title &amp; keyword direction</b><br><span style='color:#8a8f98'>How titles and "
                         "keywords should be written. These directions take priority.</span>")
        for lbl in (about_lbl, dir_lbl):
            lbl.setWordWrap(True)
        self.context = QPlainTextEdit(self.settings.get("context", ""))
        self.context.setPlaceholderText("e.g. Autumn wedding of Sarah and James at Meiji Shrine, "
                                        "Tokyo, Japan. The older couple are the bride's parents.")
        self.direction = QPlainTextEdit(self.settings.get("direction", ""))
        self.direction.setPlaceholderText("e.g. Titles in the form \"Subject, Location\", 6 words max.\n"
                                          "Always include the keywords wedding, Tokyo and Japan.\n"
                                          "Avoid words like beautiful and stunning.")
        for box in (self.context, self.direction):
            box.setMaximumHeight(78)
            box.setTabChangesFocus(True)
        self.keep_titles = QCheckBox("Keep existing titles")
        self.keep_titles.setChecked(bool(self.settings.get("keep_existing_titles", True)))
        self.keep_titles.setToolTip("Photos that already have a title keep it; only empty titles are filled in")
        self.keep_kw = QCheckBox("Keep existing keywords")
        self.keep_kw.setChecked(bool(self.settings.get("keep_existing_keywords", True)))
        self.keep_kw.setToolTip("New keywords are added after the ones already on the photo")
        self.keep_titles.toggled.connect(lambda *_: self._remember_options())
        self.keep_kw.toggled.connect(lambda *_: self._remember_options())
        self.revise = QCheckBox("Double-check against my instructions")
        self.revise.setChecked(bool(self.settings.get("revise", True)))
        self.revise.setToolTip("After the first suggestion, the AI checks it against About these photos, your photo "
                               "notes and your Title & keyword direction, and fixes anything that doesn't follow them.\n"
                               "Only runs when you've written instructions. Adds a few seconds per photo.")
        self.revise.toggled.connect(lambda *_: self._remember_options())
        note = QLabel("Tip: to clarify a single photo, double-click its <b>Photo notes</b> cell in the list.")
        note.setStyleSheet(f"color:{GREY}")
        g.addWidget(about_lbl, 0, 0)
        g.addWidget(self.context, 1, 0)
        g.addWidget(dir_lbl, 0, 1)
        g.addWidget(self.direction, 1, 1)
        self.lead = QLineEdit(self.settings.get("lead_keywords", ""))
        self.lead.setPlaceholderText("e.g. Tokyo, Japan, Shibuya, {year}, {month} {year}")
        self.lead.setToolTip("Put first in every photo's keywords, exactly as typed, before the AI's suggestions.\n"
                             "{year}, {month} and {date} are filled in from each photo's capture date.")
        self.lead.setClearButtonEnabled(True)
        self.lead.editingFinished.connect(self._remember_options)
        self.btn_lead = QPushButton("Add to photos now")
        self.btn_lead.setToolTip("Put these keywords at the front of the selected photos (or all photos) "
                                 "right away, without running the AI")
        self.btn_lead.clicked.connect(lambda *_: self.add_lead_now())
        lead_row = QHBoxLayout()
        lead_row.addWidget(QLabel("<b>Keywords to put first</b>"))
        lead_row.addWidget(self.lead, 1)
        lead_row.addWidget(self.btn_lead)
        g.addLayout(lead_row, 2, 0, 1, 2)
        self.prefix = QLineEdit(self.settings.get("title_prefix", ""))
        self.prefix.setPlaceholderText("e.g. Editorial:   or   Tokyo, Japan, {year}:")
        self.prefix.setToolTip("Put in front of every suggested title, exactly as typed.\n"
                               "{year}, {month} and {date} are filled in from each photo's capture date.")
        self.prefix.setClearButtonEnabled(True)
        self.prefix.editingFinished.connect(self._remember_options)
        self.btn_prefix = QPushButton("Add to titles now")
        self.btn_prefix.setToolTip("Add this text, and the date taken if chosen below, to the selected photos' titles "
                                   "(or all photos' titles) right away, without running the AI")
        self.btn_prefix.clicked.connect(lambda *_: self.add_prefix_now())
        prefix_row = QHBoxLayout()
        prefix_row.addWidget(QLabel("<b>Text before titles</b>"))
        prefix_row.addWidget(self.prefix, 1)
        prefix_row.addWidget(self.btn_prefix)
        g.addLayout(prefix_row, 3, 0, 1, 2)
        self.location = QLineEdit()
        self.location.setPlaceholderText("e.g. Tokyo, Japan")
        self.location.setToolTip("Where the photos were taken. It goes into each title just before the date taken\n"
                                 "(or before the title if the date is off), editorial style:\n"
                                 "Tokyo, Japan - October 26, 2026: Description")
        self.location.setClearButtonEnabled(True)
        # editable dropdown: type a new location, or pick one you've used before
        self.location_box = QComboBox()
        self.location_box.setEditable(True)
        self.location_box.setLineEdit(self.location)     # (Qt empties the text box here)
        self.location_box.setInsertPolicy(QComboBox.NoInsert)
        self.location_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.location_box.setMinimumContentsLength(18)
        self._fill_location_history()
        self.location.setText(self.settings.get("title_location", ""))   # so the saved location goes in after
        self.location.editingFinished.connect(self._location_entered)
        self.location.textChanged.connect(lambda *_: self._date_options_changed(save=False))
        self.location_box.activated.connect(lambda i: self._location_picked(i))
        # Editorial: one switch for the location and date, and where they go
        self.editorial = QCheckBox("Editorial (include location and date)")
        self.editorial.setChecked(bool(self.settings.get("editorial_on")))
        self.editorial.setToolTip("On: the location and the date taken are added to every title and to the keywords,\n"
                                  "e.g. Tokyo, Japan - October 26, 2026: Shibuya Crossing at Dusk.\n"
                                  "Off: neither is added.")
        self.editorial.toggled.connect(lambda *_: self._date_options_changed())
        self.editorial_pos = QComboBox()
        self.editorial_pos.addItem("Before the title and keywords", "before")
        self.editorial_pos.addItem("After the title and keywords", "after")
        self.editorial_pos.setCurrentIndex(max(0, self.editorial_pos.findData(self.settings.get("editorial_pos", "before"))))
        self.editorial_pos.setToolTip("Before: Tokyo, Japan - October 26, 2026: Title, with tokyo, japan, october 2026, 2026 first\n"
                                      "in the keywords.\nAfter: Title – Tokyo, Japan - October 26, 2026, with those keywords last.")
        self.editorial_pos.currentIndexChanged.connect(lambda *_: self._date_options_changed())
        self.loc_ai = QCheckBox("AI uses it")
        self.loc_ai.setChecked(bool(self.settings.get("location_for_ai", True)))
        self.loc_ai.setToolTip("On: the AI is told where the photos were taken and uses the place in the keywords\n"
                               "(it still leaves it out of its own title, since the location is added there for you).\n"
                               "Off: the AI isn't told the location. It's still put into the titles.")
        self.loc_ai.toggled.connect(lambda *_: self._remember_options())
        self.date_pos, self.date_fmt, self.date_sep = QComboBox(), QComboBox(), QComboBox()
        self.loc_sep = QComboBox()
        for key, label in core.DATE_POSITIONS:
            self.date_pos.addItem(label, key)
        for key, _ in core.DATE_FORMATS:
            self.date_fmt.addItem(core.format_date(core.SAMPLE_DATE, key), key)
        for key, label in core.DATE_SEPARATORS:
            self.date_sep.addItem(label, key)
        for key, label in core.LOCATION_SEPARATORS:
            self.loc_sep.addItem(label, key)
        for combo, key in ((self.date_pos, "title_date_pos"), (self.date_fmt, "title_date_format"),
                           (self.date_sep, "title_date_sep"), (self.loc_sep, "title_loc_sep")):
            combo.setCurrentIndex(max(0, combo.findData(self.settings.get(key))))
            combo.currentIndexChanged.connect(lambda *_: self._date_options_changed())
        # (the date's position now follows the Editorial switch; this list isn't shown, it just holds the value)
        self.date_pos.setToolTip("Whether to add the date the photo was taken (from its EXIF data), and where")
        self.date_fmt.setToolTip("How the date is written (the examples use October 26, 2026)")
        self.date_sep.setToolTip("The character between the date (or location) and the AI's title")
        # custom date: for photos whose camera date is wrong (or missing)
        self.custom_date_on = QCheckBox("Custom date:")
        self.custom_date_on.setChecked(bool(self.settings.get("custom_date_on")))
        self.custom_date_on.setToolTip("Use this date for every photo instead of the date in its metadata,\n"
                                       "e.g. when the camera's clock was wrong. Used for the date in titles\n"
                                       "and for {year} {month} {date} in your text and keywords.")
        self.custom_date = QDateEdit()
        self.custom_date.setCalendarPopup(True)
        self.custom_date.setDisplayFormat("MMM d, yyyy")
        saved = QDate.fromString(self.settings.get("custom_date", ""), "yyyy-MM-dd")
        self.custom_date.setDate(saved if saved.isValid() else QDate.currentDate())
        self.custom_date.setToolTip("Type a date or click the arrow to pick one from a calendar")
        self.custom_date.setEnabled(self.custom_date_on.isChecked())
        self.custom_date_on.toggled.connect(lambda *_: self._date_options_changed())
        self.custom_date.dateChanged.connect(lambda *_: self._date_options_changed())
        self.loc_sep.setToolTip("The character between the location and the date, e.g. Tokyo, Japan - October 26, 2026")
        self.title_preview = QLabel()
        self.title_preview.setWordWrap(True)
        self.title_preview.setTextFormat(Qt.RichText)
        self.title_preview_plain = ""                 # the example title as plain text
        self.prefix.textChanged.connect(lambda *_: self._update_title_preview())
        for combo, chars in ((self.date_pos, 16), (self.date_fmt, 10), (self.date_sep, 8), (self.loc_sep, 8),
                             (self.editorial_pos, 14)):
            # keep the rows narrow: long choices show in full when the list is open
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(chars)

        def small(text):
            lab = QLabel(text)
            lab.setStyleSheet(f"color:{GREY}")
            return lab

        # one row per piece, in the order they appear in the title: location, then date
        loc_row = QHBoxLayout()
        loc_row.addWidget(self.editorial)
        loc_row.addWidget(self.editorial_pos, 1)
        loc_row.addSpacing(12)
        self.loc_label = QLabel("<b>Location</b>")
        loc_row.addWidget(self.loc_label)
        loc_row.addWidget(self.location_box, 3)
        loc_row.addWidget(self.loc_ai)
        loc_row.addSpacing(12)
        self.loc_sep_label = small("Between location and date:")
        loc_row.addWidget(self.loc_sep_label)
        loc_row.addWidget(self.loc_sep, 1)
        g.addLayout(loc_row, 4, 0, 1, 2)
        date_row = QHBoxLayout()
        self.date_label = QLabel("<b>Date taken</b>")
        date_row.addWidget(self.date_label)
        date_row.addWidget(small("Format:"))
        date_row.addWidget(self.date_fmt, 1)
        date_row.addSpacing(8)
        date_row.addWidget(self.custom_date_on)
        date_row.addWidget(self.custom_date)
        date_row.addSpacing(8)
        self.date_sep_label = small("Between date and title:")
        date_row.addWidget(self.date_sep_label)
        date_row.addWidget(self.date_sep, 1)
        g.addLayout(date_row, 5, 0, 1, 2)
        g.addWidget(self.title_preview, 6, 0, 1, 2)
        self.default_type = QComboBox()
        self.default_type.setToolTip("Used for every photo whose own Type is set to Default. "
                                     "Change a single photo's type in its row or in the review panel.")
        self.default_type.currentIndexChanged.connect(self._default_type_chosen)
        checks = QHBoxLayout()
        checks.addWidget(QLabel("<b>Photo type</b> (default for all photos):"))
        checks.addWidget(self.default_type)
        checks.addSpacing(12)
        checks.addWidget(self.keep_titles)
        checks.addWidget(self.keep_kw)
        checks.addWidget(self.revise)
        checks.addStretch(1)
        checks.addWidget(note)
        g.addLayout(checks, 7, 0, 1, 2)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)

        self.et_banner = banner_label()
        self.et_banner.setOpenExternalLinks(True)
        self.ai_banner = banner_label()
        self.update_banner = banner_label()           # "a newer version is available"
        self.update_banner.setStyleSheet("QLabel{background:#e8f1fd;color:#1c3d6e;border:1px solid #b5cff3;"
                                         "border-radius:8px;padding:8px 10px}")
        self.update_banner.linkActivated.connect(self._update_link)
        self.update_info: dict | None = None
        self.update_ready = None                      # (new program folder, download folder) once downloaded
        self.update_downloading = self.update_installing = self.restarting_for_update = False
        self.ai_banner.linkActivated.connect(self._banner_link)

        self.table = QTableWidget(0, 9)
        headers = {COL_CHECK: "✓", COL_THUMB: "", COL_FILE: "File", COL_STATUS: "Status", COL_TYPE: "Type",
                   COL_ACTION: "", COL_NOTE: "Photo notes (optional)", COL_TITLE: "Title",
                   COL_KEYWORDS: "Keywords (comma separated)"}
        self.table.setHorizontalHeaderLabels([headers[c] for c in range(len(headers))])
        self.table.setIconSize(QSize(THUMB, THUMB))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(THUMB + 12)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_CHECK, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_CHECK, 34)
        h.setSectionResizeMode(COL_THUMB, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_THUMB, THUMB + 16)
        for col, width in ((COL_FILE, 170), (COL_TYPE, 200), (COL_NOTE, 160), (COL_TITLE, 260), (COL_STATUS, 160)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, width)
        h.setSectionResizeMode(COL_KEYWORDS, QHeaderView.Stretch)
        h.setSectionResizeMode(COL_ACTION, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_ACTION, 180)      # Detect + Save
        self.table.itemChanged.connect(self._cell_edited)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(self._open_photo)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        self.stack = QStackedWidget()
        self.stack.addWidget(DropPage())
        self.stack.addWidget(self.table)

        # filter bar: Show [what] and a search box over file name, title and keywords
        self.hidden: set[str] = set()                # paths of photos the filter hides
        self.filter_box = QComboBox()
        for key, label in FILTERS:
            self.filter_box.addItem(label, key)
        self.filter_box.setToolTip("Show only some of the photos. The buttons then work on the photos shown.")
        self.filter_box.currentIndexChanged.connect(lambda *_: self.apply_filter())
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search file names, titles and keywords  (Ctrl/⌘+F)")
        self.search.setToolTip("Shows photos whose file name, title or keywords contain every word you type")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self.apply_filter())
        self.filter_count = QLabel("")
        self.filter_count.setStyleSheet(f"color:{GREY}")
        self.btn_show_all = QPushButton("Show all")
        self.btn_show_all.setToolTip("Clear the filter and the search")
        self.btn_show_all.clicked.connect(lambda *_: self.show_all())
        self.filter_bar = QWidget()
        fb = QHBoxLayout(self.filter_bar)
        fb.setContentsMargins(0, 0, 0, 4)
        fb.addWidget(QLabel("<b>Show</b>"))
        fb.addWidget(self.filter_box)
        fb.addWidget(self.search, 1)
        fb.addWidget(self.filter_count)
        fb.addWidget(self.btn_show_all)
        self.filter_bar.hide()                       # shown once there are photos
        self.list_side = QWidget()
        ls = QVBoxLayout(self.list_side)
        ls.setContentsMargins(0, 0, 0, 0)
        ls.addWidget(self.filter_bar)
        ls.addWidget(self.stack, 1)

        self.review = ReviewPanel(self)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.list_side)
        self.splitter.addWidget(self.review)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.btn_stop = button("Stop", self.stop)
        self.btn_stop.hide()
        self.status = QLabel("")
        self.status.setStyleSheet("color:#6b7280")
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(self.btn_stop)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(top)
        lay.addWidget(self.instructions)
        lay.addWidget(self.update_banner)
        lay.addWidget(self.ai_banner)
        lay.addWidget(self.et_banner)
        lay.addWidget(self.splitter, 1)
        lay.addLayout(bottom)
        self.setCentralWidget(central)

        for seq, slot in ((QKeySequence(QKeySequence.Save), self.save),
                          (QKeySequence(QKeySequence.Delete), self.remove_selected),
                          (QKeySequence(Qt.Key_Backspace), self.remove_selected),
                          (QKeySequence("Ctrl+Return"), self.save_and_next),
                          (QKeySequence(QKeySequence.Copy), self.copy_metadata),
                          (QKeySequence(QKeySequence.Paste), lambda: self.paste_metadata("both")),
                          (QKeySequence("Ctrl+Shift+V"), lambda: self.paste_metadata("add")),
                          (QKeySequence(QKeySequence.Find), self.focus_search)):
            act = QAction(self)
            act.setShortcut(seq)
            act.triggered.connect(slot)
            self.addAction(act)

        self.setStyleSheet("""
            QPushButton{padding:6px 12px}
            QPushButton[primary="true"]{background:#2f7ddd;color:white;border:1px solid #2569bd;border-radius:6px;font-weight:600}
            QPushButton[primary="true"]:hover{background:#3d8af0}
            QPushButton[primary="true"]:disabled{background:#9dbbe2;border-color:#9dbbe2}
            QTableWidget{font-size:13px;alternate-background-color:rgba(127,127,127,13)}
            QGroupBox{font-weight:600}
        """)

    def toggle_instructions(self):
        show = not self.instructions.isVisible()
        self.instructions.setVisible(show)
        self.btn_instr.setText("Hide Instructions" if show else "Show Instructions")

    # ---------- ExifTool
    def _setup_exiftool(self):
        exe = core.find_exiftool(self.settings.get("exiftool_path", ""))
        self.exiftool = None
        if exe:
            try:
                ver = core.ExifTool(exe).version()
                self.exiftool = core.ExifTool(exe)
                self.status.setText(f"ExifTool {ver} ready.")
            except core.ExifToolError as e:
                self.status.setText(f"ExifTool problem: {e}")
        if self.exiftool:
            self.et_banner.hide()
        else:
            self.et_banner.setText(
                "<b>ExifTool is needed to read and save metadata.</b> Install it from "
                "<a href='https://exiftool.org'>exiftool.org</a> (Mac: <code>brew install exiftool</code>), "
                "then reopen this app, or choose it in Settings.")
            self.et_banner.show()

    # ---------- AI (Ollama)
    def _check_ai(self):
        run_job(self, Job("check", check_ai, self.settings["ollama_url"]), self.io_pool,
                self._ai_checked, lambda *_: self._ai_checked(None, {"version": None, "models": []}))

    def _ai_checked(self, _key, info):
        self.ai = info
        model = self.settings["model"]
        if info["version"] is None:
            self.ai_banner.setText(
                "<b>AI recognition is free and runs on your computer with Ollama.</b> "
                f"<a href='{OLLAMA_DOWNLOAD}'>Download Ollama</a>, install and open it, then open "
                "<a href='settings:'>Settings</a> to download a model with one click.")
            self.ai_banner.show()
        elif not any(same_model(model, n) for n in info["models"]):
            what = "No AI model is downloaded yet." if not info["models"] else f"The model {model} isn't downloaded."
            self.ai_banner.setText(f"<b>{what}</b> Open <a href='settings:'>Settings</a> to download one "
                                   "(free, one click).")
            self.ai_banner.show()
        else:
            self.ai_banner.hide()

    def _show_whats_new_once(self):
        """After an update (or the first start), show what's new, once."""
        since = self.settings.get("last_seen_version", "")
        self.settings["last_seen_version"] = core.APP_VERSION
        core.save_settings(self.settings)
        if not os.environ.get("IPTC_TAGGER_OFFLINE"):
            show_whats_new(self, since)

    # ---------- CSV export
    def is_editorial(self, ph: Photo) -> bool:
        """Editorial when the photo shows brands or a news event, or your text before titles says so."""
        return bool(self.settings.get("editorial_on"))

    def export_csv(self):
        photos, scope = self.targets()
        photos = [p for p in photos if p.title.strip() or p.keywords]
        if not photos:
            self.status.setText("Nothing to export yet: the photos have no titles or keywords.")
            return
        folder = os.path.dirname(photos[0].path)
        filters = [label + " (*.csv)" for _, label in core.CSV_FORMATS]
        path, chosen = QFileDialog.getSaveFileName(self, f"Export {len(photos)} photo{'s' if len(photos) != 1 else ''} "
                                                   "to CSV", os.path.join(folder, "metadata.csv"), ";;".join(filters))
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        fmt = next((k for (k, label), flt in zip(core.CSV_FORMATS, filters) if flt == chosen), "shutterstock")
        labels = dict(self.type_items())
        rows = [{"file": os.path.basename(p.path), "folder": os.path.dirname(p.path), "title": p.title,
                 "keywords": list(p.keywords), "editorial": self.is_editorial(p),
                 "type": labels.get(self.effective_type(p), ""), "note": p.note, "taken": p.taken,
                 "saved": not p.changed} for p in photos]
        try:
            n = core.export_csv(path, rows, fmt)
        except OSError as e:
            QMessageBox.warning(self, "Export CSV", f"The CSV couldn't be saved:\n{e}")
            return
        unsaved = sum(1 for p in photos if p.changed)
        self.status.setText(f"Exported {n} photo{'s' if n != 1 else ''} to {os.path.basename(path)} "
                            f"({dict(core.CSV_FORMATS)[fmt]})."
                            + (f" {unsaved} of them {'has' if unsaved == 1 else 'have'} changes that aren't saved "
                               "into the files yet."
                               if unsaved else ""))

    # ---------- update check
    def check_updates(self, on_done=None):
        """Look for a newer release in the background (never blocks the window)."""
        run_job(self, Job("update", core.check_for_update, core.APP_VERSION), self.io_pool,
                lambda _k, info: self._update_checked(info, on_done),
                lambda _k, _msg: self._update_checked(None, on_done))

    def _update_checked(self, info, on_done=None):
        if on_done is not None:
            on_done(info)
        if not info or info["version"] == self.settings.get("skipped_update"):
            return
        self.update_info = info
        if self.update_ready or self.update_downloading:
            return
        if self.settings.get("auto_update", True) and self._can_install(info):
            self.start_update_download()             # quietly, in the background
            return
        self.update_banner.setText(
            f"<b>{core.APP_NAME} {info['version']} is available</b> (you have {core.APP_VERSION}). &nbsp;"
            + ("<a href='install'>Install it</a>" if self._can_install(info) else "<a href='download'>Download</a>")
            + " &nbsp;·&nbsp; <a href='skip'>Skip this version</a> &nbsp;·&nbsp; <a href='later'>Not now</a>")
        self.update_banner.show()

    def _can_install(self, info) -> bool:
        """The packaged program can replace itself (not when running from the source code, or when its
        folder can't be written to); and the release has a download for this computer."""
        return core.can_self_update() and bool(core.update_asset((info or {}).get("assets", {})))

    def start_update_download(self):
        info = self.update_info or {}
        url = core.update_asset(info.get("assets", {}))
        if not url:
            return
        self.update_downloading = True
        self.update_banner.setText(f"Downloading {core.APP_NAME} {info['version']}…")
        self.update_banner.show()
        run_job(self, Job("update-download", self._fetch_update, url, progress=True), self.io_pool,
                lambda _k, res: self._update_downloaded(res), lambda _k, msg: self._update_download_failed(msg),
                on_progress=lambda _k, p: self.update_banner.setText(
                    f"Downloading {core.APP_NAME} {info['version']}… {p[1]}"
                    + (f" ({p[0] * 100:.0f}%)" if p[0] is not None else "")))

    @staticmethod
    def _fetch_update(url, progress=None, cancel=None):
        import tempfile
        work = tempfile.mkdtemp(prefix="iptc_tagger_update_")
        zip_path = core.download_update(url, work, progress, cancel)
        return core.unpack_update(zip_path, work), work

    def _update_downloaded(self, res):
        self.update_downloading = False
        self.update_ready = res                       # (new program, download folder)
        v = (self.update_info or {}).get("version", "")
        core.debug(f"update: {v} is downloaded and ready: {res[0]}")
        self.update_banner.setText(
            f"<b>{core.APP_NAME} {v} is ready to install.</b> &nbsp;<a href='restart'>Restart now</a>"
            " &nbsp;·&nbsp; <a href='later'>Later</a> (it installs when you close the app)")
        self.update_banner.show()

    def _update_download_failed(self, msg):
        self.update_downloading = False
        core.debug(f"update: download failed: {msg}", always=True)
        info = self.update_info or {}
        self.update_banner.setText(
            f"<b>{core.APP_NAME} {info.get('version', '')} is available</b>, but it couldn't be downloaded ({msg}). "
            "&nbsp;<a href='download'>Download it yourself</a> &nbsp;·&nbsp; <a href='later'>Not now</a>")
        self.update_banner.show()

    def install_update(self, relaunch: bool) -> bool:
        """Hand over to the updater: it waits for this window to close, then replaces the program."""
        if not self.update_ready or self.update_installing:
            return False
        new_root, work = self.update_ready
        try:
            core.start_updater(core.updater_script(new_root, core.program_root(), work, os.getpid(), relaunch),
                               work)
        except OSError as e:
            core.debug(f"update: couldn't start the updater: {e}", always=True)
            QMessageBox.warning(self, "Update", f"The update couldn't be installed:\n{e}")
            return False
        self.update_installing = True
        return True

    def _update_link(self, link):
        info = self.update_info or {}
        if link == "download":
            QDesktopServices.openUrl(QUrl(info.get("url") or core.RELEASES_PAGE))
        elif link == "install":
            self.start_update_download()
            return
        elif link == "restart":
            self.restarting_for_update = True
            self.close()                              # closeEvent asks about unsaved work, then installs
            return
        elif link == "skip" and info:
            self.settings["skipped_update"] = info["version"]
            core.save_settings(self.settings)
        self.update_banner.hide()                     # (a downloaded update stays ready: it installs on close)

    def _banner_link(self, link):
        if link.startswith("settings"):
            self.open_settings()
        else:
            QDesktopServices.openUrl(QUrl(link))

    # ---------- options
    def _remember_options(self):
        if not self._ready:
            return
        if hasattr(self, "location_box"):
            self._remember_location()                # a location in use goes to the top of the dropdown
        self.settings.update(context=self.context.toPlainText().strip(),
                             lead_keywords=self.lead.text().strip(),
                             title_prefix=self.prefix.text().strip(),
                             title_location=" ".join(self.location.text().split()),
                             editorial_on=self.editorial.isChecked(),
                             editorial_pos=self.editorial_pos.currentData() or "before",
                             location_for_ai=self.loc_ai.isChecked(),
                             title_date_pos=self.date_pos.currentData() or "off",
                             title_date_format=self.date_fmt.currentData() or "long",
                             title_date_sep=self.date_sep.currentData() or "dash",
                             title_loc_sep=self.loc_sep.currentData() or "dash",
                             custom_date_on=self.custom_date_on.isChecked(),
                             custom_date=self.custom_date.date().toString("yyyy-MM-dd"),
                             direction=self.direction.toPlainText().strip(),
                             keep_existing_titles=self.keep_titles.isChecked(),
                             revise=self.revise.isChecked(),
                             keep_existing_keywords=self.keep_kw.isChecked())
        core.save_settings(self.settings)

    def open_settings(self):
        dlg = SettingsDialog(self, self.settings, core.find_exiftool(""))
        if dlg.exec():
            self.settings = dlg.values()
            core.set_debug(self.settings.get("debug_log", False))
            self._remember_options()
            self._setup_exiftool()
            self.refresh_type_combos()
        self._check_ai()   # a model may have been downloaded even if Settings was cancelled
        self._refresh_buttons()

    # ---------- adding photos
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.add_paths(paths)

    def add_photos_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add photos", "", "JPEG images (*.jpg *.jpeg *.jpe *.jfif)")
        if files:
            self.add_paths(files)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Add a folder of photos")
        if d:
            self.add_paths([d])

    def add_paths(self, paths):
        self.status.setText("Looking for JPEG photos…")
        run_job(self, Job("collect", core.collect_jpegs, paths), self.io_pool, self._collected,
                lambda _k, msg: self.status.setText(f"Couldn't read those files: {msg}"))

    def _collected(self, _key, result):
        files, skipped = result
        new = [p for p in files if p not in self.rows]
        for path in new:
            photo = Photo(path)
            self.photos.append(photo)
            row = self.table.rowCount()
            self.rows[path] = row
            self._filling = True
            self.table.insertRow(row)
            fixed = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            thumb = QTableWidgetItem()
            thumb.setFlags(fixed)
            name = QTableWidgetItem(self._display_name(path))
            name.setFlags(fixed)
            name.setToolTip(path)
            status = QTableWidgetItem()
            status.setFlags(fixed)
            note = QTableWidgetItem()
            note.setToolTip("Double-click to tell the AI something about this photo")
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Unchecked)
            check.setToolTip("Tick the photos the buttons should work on")
            for col, item in ((COL_CHECK, check), (COL_THUMB, thumb), (COL_FILE, name), (COL_NOTE, note),
                              (COL_TITLE, QTableWidgetItem()), (COL_KEYWORDS, QTableWidgetItem()),
                              (COL_STATUS, status)):
                self.table.setItem(row, col, item)
            detect = QPushButton("Detect")
            detect.setToolTip("Run the AI on this photo now for a title and keywords\n"
                              "(uses your instructions and this photo's notes)")
            detect.clicked.connect(lambda _=False, ph=photo: self._detect_clicked(ph))
            btn = QPushButton("Save")
            btn.setToolTip("Save this photo's title and keywords into the file now")
            btn.clicked.connect(lambda _=False, ph=photo: self.save_photos([ph]))
            # the row buttons never take keyboard focus: a focused button that then gets disabled (Detect while
            # the AI works, Save once saved) makes Qt move focus like a Tab press, which scrolls the list
            # to whichever row is selected
            detect.setFocusPolicy(Qt.NoFocus)
            btn.setFocusPolicy(Qt.NoFocus)
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(2, 0, 2, 0)
            cl.setSpacing(4)
            cl.addWidget(detect)
            cl.addWidget(btn)
            self.table.setCellWidget(row, COL_ACTION, cell)
            photo.row_button, photo.detect_button = btn, detect
            combo = NoWheelComboBox()
            combo.setToolTip("Photo type: helps the AI write the right kind of title")
            self.fill_type_combo(combo, "")
            combo.currentIndexChanged.connect(lambda *_, ph=photo, c=combo: self._row_type_chosen(ph, c))
            self.table.setCellWidget(row, COL_TYPE, combo)
            photo.type_combo = combo
            self._filling = False
            self._show(photo)
            run_job(self, Job(path, make_thumbnail, path), self.io_pool, self._thumb_ready, lambda *_: None)
        if self.photos:
            self.stack.setCurrentIndex(1)
        self.apply_filter()
        bits = [f"Added {len(new)} photo{'s' if len(new) != 1 else ''}"]
        if len(files) - len(new):
            bits.append(f"{len(files) - len(new)} already in the list")
        if skipped:
            bits.append(f"skipped {skipped} non-JPEG file{'s' if skipped != 1 else ''}")
        self.status.setText(", ".join(bits) + ".")
        if new:
            self._read_metadata(new)
        self._refresh_buttons()

    def _display_name(self, path):
        parent = os.path.basename(os.path.dirname(path))
        return f"{os.path.basename(path)}\n{parent}" if parent else os.path.basename(path)

    def _read_metadata(self, paths):
        if not self.exiftool:
            for p in paths:
                ph = self._photo(p)
                if ph:
                    ph.status, ph.message = "ready", "Ready (existing metadata not read)"
                    self._show(ph)
            return
        for i in range(0, len(paths), 50):
            chunk = paths[i:i + 50]
            run_job(self, Job(tuple(chunk), self.exiftool.read, chunk), self.io_pool,
                    self._metadata_read, self._metadata_failed)

    def _metadata_read(self, _key, result: dict):
        for path, meta in result.items():
            ph = self._photo(path)
            if not ph:
                continue
            ph.file_title, ph.file_keywords = meta["title"], list(meta["keywords"])
            ph.title, ph.keywords = ph.file_title, list(ph.file_keywords)
            ph.taken = meta.get("taken", "")
            ph.status, ph.message = "ready", ("Has metadata" if (ph.title or ph.keywords) else "No title or keywords yet")
            self._show(ph)
        self._refresh_buttons()

    def _metadata_failed(self, key, msg):
        for path in key:
            ph = self._photo(path)
            if ph:
                ph.status, ph.message = "error", f"Couldn't read metadata: {msg}"
                self._show(ph)

    def _thumb_ready(self, path, qimg):
        row = self.rows.get(path)
        if row is not None:
            self.table.item(row, COL_THUMB).setIcon(QIcon(QPixmap.fromImage(qimg)))

    # ---------- table helpers
    def _photo(self, path) -> Photo | None:
        row = self.rows.get(path)
        return self.photos[row] if row is not None else None

    def status_text(self, ph: Photo) -> tuple[str, str]:
        if ph.busy or ph.status in ("error", "saved", "reading"):
            return ph.message or ph.status.title(), STATUS_COLORS.get(ph.status, GREY)
        if ph.changed:
            return ph.message or "Edited, not saved", STATUS_COLORS["edited"]
        return ph.message or "Ready", STATUS_COLORS["ready"]

    def _show(self, ph: Photo, from_editor: bool = False):
        row = self.rows.get(ph.path)
        if row is None:
            return
        self._filling = True
        self.table.item(row, COL_NOTE).setText(ph.note)
        self.table.item(row, COL_TITLE).setText(ph.title)
        self.table.item(row, COL_KEYWORDS).setText(core.keywords_to_text(ph.keywords))
        self.table.item(row, COL_KEYWORDS).setToolTip(f"{len(ph.keywords)} keywords")
        status = self.table.item(row, COL_STATUS)
        text, color = self.status_text(ph)
        status.setText(text)
        status.setToolTip(text)
        status.setForeground(QColor(color))
        editable = Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.NoItemFlags if ph.busy else Qt.ItemIsEditable)
        for col in (COL_NOTE, COL_TITLE, COL_KEYWORDS):
            self.table.item(row, col).setFlags(editable)
        if ph.type_combo is not None:
            if ph.busy and ph.type_combo.hasFocus():
                self.table.setFocus()                 # see the note on the row buttons: don't let focus jump
            ph.type_combo.setEnabled(not ph.busy)
        d = ph.detect_button
        if d is not None:
            running = ph.busy and ph.status == "identifying"
            d.setText("Stop" if running else "Detect")       # while the AI works, the same button stops it
            d.setToolTip("Stop the AI on this photo now" if running else
                         "Run the AI on this photo now for a title and keywords\n"
                         "(uses your instructions and this photo's notes)")
            d.setEnabled(running or not ph.busy)
        b = ph.row_button
        if b is not None:
            if ph.busy:
                b.setText("Saving…" if ph.status == "saving" else "Working…")
                b.setEnabled(False)
            elif ph.changed:
                b.setText("Save")
                b.setEnabled(self.exiftool is not None)
            else:
                b.setText("Saved ✓" if ph.status == "saved" else "Save")
                b.setEnabled(False)
        self._filling = False
        if self.review.photo is ph:
            if from_editor:
                self.review.refresh_state()      # don't overwrite what's being typed
            else:
                self.review.show_photo(ph)

    def _cell_edited(self, item: QTableWidgetItem):
        if not self._filling and item.column() == COL_CHECK and 0 <= item.row() < len(self.photos):
            ph, on = self.photos[item.row()], item.checkState() == Qt.Checked
            sel = self._selected_photos()
            if len(sel) > 1 and ph in sel:
                # ticking one box of a multi-row selection ticks (or unticks) every selected photo,
                # and keeps the selection (the click itself would otherwise reduce it to one row)
                rows = sorted(self.rows[p.path] for p in sel)
                self.set_checked(sel, on)
                QTimer.singleShot(0, lambda: self._select_rows(rows))
            else:
                ph.checked = on
                self._refresh_buttons()
            return
        if self._filling or item.column() not in (COL_NOTE, COL_TITLE, COL_KEYWORDS):
            return
        if not 0 <= item.row() < len(self.photos):
            return
        ph = self.photos[item.row()]
        if item.column() == COL_NOTE:
            ph.note = core.single_line(item.text())
            self._show(ph)
            return   # notes are for the AI only; they don't change the file
        if item.column() == COL_TITLE:
            ph.title = core.clean_title(item.text())
        else:
            ph.keywords = core.parse_keywords_text(item.text())
        if ph.status == "saved":
            ph.status = "ready"
        ph.message = ""
        self._show(ph)
        self._refresh_buttons()

    def _selection_changed(self):
        if not self._ready:
            return
        sel = self._selected_photos()
        self.review.show_photo(sel[0] if len(sel) == 1 else None, len(sel))
        self._update_title_preview()
        self._refresh_buttons()

    def request_preview(self, path: str):
        if path in self.previews:
            self.review.set_preview(path, self.previews[path])
            return
        self.review.preview.setText("Loading preview…")
        run_job(self, Job(path, make_preview, path), self.io_pool, self._preview_ready,
                lambda _k, msg: self.review.preview.setText("No preview available"))

    def _preview_ready(self, path, qimg):
        pix = QPixmap.fromImage(qimg)
        self.previews[path] = pix
        while len(self.previews) > 40:                  # keep memory use modest
            self.previews.pop(next(iter(self.previews)))
        self.review.set_preview(path, pix)

    def select_offset(self, step: int):
        """Move the selection to the previous/next photo in the list."""
        cur = self.review.photo
        row = self.rows.get(cur.path, -1) if cur else -1
        new = row + step
        while 0 <= new < len(self.photos) and self.photos[new].path in self.hidden:
            new += 1 if step > 0 else -1                 # skip photos the filter hides
        if 0 <= new < len(self.photos):
            self.table.selectRow(new)
            self.table.scrollToItem(self.table.item(new, COL_FILE))

    def save_and_next(self):
        ph = self.review.photo
        if ph is None or ph.busy:
            return
        if ph.changed:
            self.save_photos([ph])
        self.select_offset(1)

    def revert_photos(self, photos):
        for ph in photos:
            if ph.busy or not ph.changed:
                continue
            ph.title, ph.keywords = ph.file_title, list(ph.file_keywords)
            ph.status, ph.message = "ready", "Reverted to what's saved in the file"
            self._show(ph)
        self.apply_filter()

    def _context_menu(self, pos):
        picked = self._selected_photos()
        if not picked:
            return
        checked = self.checked_photos()
        # right-clicking a checked photo acts on all the checked photos; otherwise on the selection
        sel = checked if checked and any(p.checked for p in picked) else picked
        idle = [p for p in sel if not p.busy]
        n_changed = sum(1 for p in idle if p.changed)
        one = len(sel) == 1
        menu = QMenu(self)
        a = menu.addAction("Check" + ("" if len(picked) == 1 else f" {len(picked)} selected photos"))
        a.setEnabled(any(not p.checked for p in picked))
        a.triggered.connect(lambda *_: self.set_checked(picked, True))
        a = menu.addAction("Uncheck" + ("" if len(picked) == 1 else f" {len(picked)} selected photos"))
        a.setEnabled(any(p.checked for p in picked))
        a.triggered.connect(lambda *_: self.set_checked(picked, False))
        menu.addSeparator()
        a = menu.addAction(f"Save {'this photo' if one else f'{n_changed} changed photo' + ('s' if n_changed != 1 else '')}")
        a.setEnabled(n_changed > 0 and self.exiftool is not None)
        a.triggered.connect(lambda *_: self.save_photos([p for p in idle if p.changed]))
        a = menu.addAction("Detect again" if one else f"Detect {len(idle)} photos")
        a.setEnabled(bool(idle))
        a.triggered.connect(lambda *_: self.identify_photos(idle))
        # Redo works on exactly the photo(s) you clicked or selected, never on all checked ones by surprise
        picked_idle = [p for p in picked if not p.busy]
        a = menu.addAction("Redo with reasons…" if len(picked_idle) <= 1 else f"Redo {len(picked_idle)} selected photos with reasons…")
        a.setEnabled(bool(picked_idle))
        a.triggered.connect(lambda *_: self.redo_photos(picked_idle))
        checked_idle = [p for p in self.checked_photos() if not p.busy]
        a = menu.addAction(f"Redo checked photos with reasons… ({len(checked_idle)})")
        a.setEnabled(bool(checked_idle))
        a.triggered.connect(lambda *_: self.redo_photos(checked_idle))
        a = menu.addAction("Revert unsaved changes")
        a.setEnabled(n_changed > 0)
        a.triggered.connect(lambda *_: self.revert_photos(idle))
        menu.addSeparator()
        a = menu.addAction("Copy title && keywords")
        a.setEnabled(one)
        a.triggered.connect(lambda *_: self.copy_metadata())
        p_title, p_kws = core.parse_pasted_text(QApplication.clipboard().text())
        for label, mode, ok in (("Paste title && keywords", "both", p_title is not None or p_kws is not None),
                                ("Paste title only", "title", p_title is not None),
                                ("Paste keywords only (replace)", "keywords", p_kws is not None),
                                ("Add pasted keywords", "add", p_kws is not None)):
            a = menu.addAction(label)
            a.setEnabled(ok and bool(idle))
            a.triggered.connect(lambda *_, m=mode: self.paste_metadata(m, idle))
        menu.addSeparator()
        a = menu.addAction("Copy photo notes")
        a.setEnabled(one and bool(sel[0].note.strip()))
        a.triggered.connect(lambda *_: self.copy_notes(sel[0]))
        clip_note = core.single_line(QApplication.clipboard().text())
        a = menu.addAction("Paste photo notes" + ("" if one else f" into {len(idle)} photos"))
        a.setEnabled(bool(clip_note) and bool(idle))
        a.triggered.connect(lambda *_: self.paste_notes(idle))
        a = menu.addAction("Clear photo notes")
        a.setEnabled(any(p.note.strip() for p in idle))
        a.triggered.connect(lambda *_: self.paste_notes(idle, ""))
        menu.addSeparator()
        types = menu.addMenu("Set photo type")
        for key, label in [("", self._default_label())] + self.type_items():
            a = types.addAction(label)
            a.setEnabled(bool(idle))
            a.triggered.connect(lambda *_, k=key: self.set_photo_type(idle, k))
        menu.addSeparator()
        if one:
            a = menu.addAction("Open photo")
            a.triggered.connect(lambda *_: QDesktopServices.openUrl(QUrl.fromLocalFile(sel[0].path)))
        a = menu.addAction("Remove from list")
        a.setEnabled(bool(idle))
        a.triggered.connect(lambda *_: self.remove_selected(use_checks=sel is checked))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ---------- keywords to put first
    def add_lead_now(self):
        self._remember_options()
        text = self.settings.get("lead_keywords", "")
        if not core.parse_keywords_text(text):
            self.status.setText("Type the keywords to put first in the box, then click Add to photos now.")
            return
        targets = [p for p in self.targets()[0] if not p.busy]
        added = 0
        for ph in targets:
            lead = core.expand_lead_keywords(text, self.photo_date(ph))
            new = core.merge_keywords(lead, ph.keywords)
            if new != ph.keywords:
                ph.keywords = new
                added += 1
                if ph.status == "saved":
                    ph.status = "ready"
                ph.message = "Keywords added first, not saved yet"
                self._show(ph)
        self.apply_filter()
        self.status.setText(f"Added your leading keywords to {added} photo{'s' if added != 1 else ''}. "
                            "Save to write them into the files." if added else
                            "Those photos already start with these keywords.")

    # ---------- the date each photo uses
    def custom_date_value(self) -> str:
        """Your custom date (YYYY-MM-DD) when Custom date is ticked, else ''."""
        s = self.settings
        d = s.get("custom_date", "") if s.get("custom_date_on") else ""
        return d if core.format_date(d, "iso") else ""

    def photo_date(self, ph: Photo) -> str:
        """The date used for this photo's title and {year} {month} {date}: your custom date if it's on,
        otherwise the date taken from the photo's metadata."""
        return self.custom_date_value() or ph.taken

    # ---------- text before titles
    def bare_title(self, ph: Photo, strip_date: bool = True) -> str:
        """The photo's title without your text before titles, the location or the date: what the AI may see.
        Both the custom date and the metadata date are taken off, so switching between them never leaves
        an old date behind."""
        s = self.settings
        t = ph.title
        for d in dict.fromkeys([self.photo_date(ph), ph.title_date, ph.taken]):
            t = core.strip_title_extras(t, core.expand_title_prefix(s.get("title_prefix", ""), d),
                                        core.format_date(d, s.get("title_date_format", "long")) if strip_date else "",
                                        s.get("title_location", ""))
        return t

    def compose_title(self, ph: Photo, title: str) -> str:
        """The final title: your text before titles + location + date taken (where you chose) + the title."""
        s = self.settings
        d = self.photo_date(ph)
        if s.get("title_date_pos", "off") != "off" and d:
            ph.title_date = d                         # so a later change of date can take this one off again
        return core.compose_title(title, core.expand_title_prefix(s.get("title_prefix", ""), d),
                                  core.format_date(d, s.get("title_date_format", "long")),
                                  s.get("title_date_pos", "off"), s.get("title_date_sep", "dash"),
                                  self.editorial_location(), s.get("title_loc_sep", "dash"))

    # ---------- previously used locations
    CLEAR_LOCATIONS = "Clear this list…"

    def _fill_location_history(self):
        text = self.location.text()
        history = [h for h in self.settings.get("location_history", []) if isinstance(h, str) and h.strip()]
        self.location_box.blockSignals(True)
        self.location_box.clear()
        for h in history:
            self.location_box.addItem(h)
        if history:
            self.location_box.addItem(self.CLEAR_LOCATIONS)
        self.location_box.setCurrentIndex(-1)
        self.location.setText(text)                 # clearing the list mustn't clear what's typed
        self.location_box.blockSignals(False)
        self.location_box.setToolTip("Pick a location you've used before, or type a new one"
                                     if history else "Locations you use are remembered here")

    def _remember_location(self):
        loc = " ".join(self.location.text().split())
        if not loc:
            return
        history = [h for h in self.settings.get("location_history", []) if h.casefold() != loc.casefold()]
        new = [loc] + history[:19]                   # most recent first, up to 20
        if new != self.settings.get("location_history", []):
            self.settings["location_history"] = new
            self._fill_location_history()

    def _location_entered(self):
        self._remember_options()

    def _location_picked(self, index: int):
        if self.location_box.itemText(index) == self.CLEAR_LOCATIONS:
            keep = self.settings.get("title_location", "")
            self.settings["location_history"] = []
            self.location.setText(keep)
            self._fill_location_history()
            self._remember_options()
            self.status.setText("Cleared the list of previous locations.")
            return
        self.location.setText(self.location_box.itemText(index))
        self._location_entered()

    def editorial_location(self) -> str:
        """The location to use: only when Editorial is on."""
        return self.settings.get("title_location", "") if self.settings.get("editorial_on") else ""

    def editorial_keywords(self, ph: Photo) -> list[str]:
        if not self.settings.get("editorial_on"):
            return []
        return core.editorial_keywords(self.editorial_location(), self.photo_date(ph))

    def _date_options_changed(self, save: bool = True):
        if not self._ready:
            return
        ed_on = self.editorial.isChecked()
        before = (self.editorial_pos.currentData() or "before") == "before"
        # the date goes where the Editorial switch says (the old "Where" list follows it)
        self.date_pos.blockSignals(True)
        self.date_pos.setCurrentIndex(max(0, self.date_pos.findData(
            "off" if not ed_on else "before_title" if before else "end")))
        self.date_pos.blockSignals(False)
        pos = self.date_pos.currentData() or "off"
        # keep the settings in memory in step at once (they're written to disk by _remember_options)
        self.settings.update(editorial_on=ed_on, editorial_pos="before" if before else "after", title_date_pos=pos)
        on, has_loc = pos != "off", bool(self.location.text().strip())
        for wdg in (self.editorial_pos, self.loc_label, self.location_box, self.loc_ai, self.date_label,
                    self.date_fmt, self.custom_date_on):
            wdg.setEnabled(ed_on)
        self.custom_date.setEnabled(ed_on and self.custom_date_on.isChecked())
        self.loc_sep.setEnabled(on and has_loc)           # only used when there's both a location and a date
        self.loc_sep_label.setEnabled(on and has_loc)
        self.date_sep.setEnabled(on)
        self.date_sep_label.setEnabled(on)
        # say exactly what this separator sits between, for the current choices
        self.date_sep_label.setText("Between location and title:" if not on else
                                    "After the date:" if pos == "start" else
                                    f"Between title and {'location' if has_loc else 'date'}:" if pos == "end" else
                                    "Between date and title:")
        if save:
            self._remember_options()
        self._update_title_preview()

    def _update_title_preview(self):
        if not self._ready:
            return
        s = dict(self.settings, title_prefix=self.prefix.text(), title_date_pos=self.date_pos.currentData() or "off",
                 title_date_format=self.date_fmt.currentData() or "long", title_date_sep=self.date_sep.currentData() or "dash",
                 title_location=self.location.text().strip() if self.editorial.isChecked() else "",
                 title_loc_sep=self.loc_sep.currentData() or "dash")
        if not s["title_prefix"].strip() and s["title_date_pos"] == "off" and not s["title_location"]:
            self.title_preview_plain = "Suggested Title"
            self.title_preview.setText(f"<span style='color:{GREY}'>Title preview: titles are used as the AI "
                                       "suggests them.</span>")
            return
        ph = self.review.photo
        custom = self.custom_date.date().toString("yyyy-MM-dd") if self.custom_date_on.isChecked() else ""
        taken = custom or (ph.taken if ph is not None and ph.taken else core.SAMPLE_DATE)
        args = ("AI's Suggested Title", core.expand_title_prefix(s["title_prefix"], taken),
                core.format_date(taken, s["title_date_format"]), s["title_date_pos"], s["title_date_sep"],
                s["title_location"], s["title_loc_sep"])
        self.title_preview_plain = core.compose_title(*args)
        whose = ("with your custom date" if custom else
                 f"using the date of {os.path.basename(ph.path)}" if ph is not None and ph.taken else "with a sample date")
        html = "".join(self._preview_piece(kind, text) for kind, text in core.title_pieces(*args))
        # calm and readable: the title on its own line, only two cues (bold = AI's title, blue = location
        # and date), everything else in the normal text colour
        key = (f"<b>bold</b> = the AI's title &nbsp;·&nbsp; "
               f"<span style='color:{self.PREVIEW_BLUE}'>blue</span> = location and date")
        self.title_preview.setText(f"<span style='color:{GREY};font-size:11px'>Title preview, {whose}:</span><br>"
                                   f"<span style='font-size:14px'>{html}</span><br>"
                                   f"<span style='color:{GREY};font-size:11px'>{key}</span>")

    PREVIEW_BLUE = "#2f6db3"
    PREVIEW_STYLE = {"location": f"color:{PREVIEW_BLUE}", "date": f"color:{PREVIEW_BLUE}",
                     "title": "font-weight:600"}            # your text and separators: plain text

    def _preview_piece(self, kind: str, text: str) -> str:
        import html as _html
        style = self.PREVIEW_STYLE.get(kind, "")
        shown = _html.escape(text)
        return f"<span style='{style}'>{shown}</span>" if style else shown

    def add_prefix_now(self):
        self._remember_options()
        text = self.settings.get("title_prefix", "")
        if not text.strip() and not self.settings.get("editorial_on"):
            self.status.setText("Type text to put before titles, or tick Editorial to add the location and date, "
                                "then click Add to titles now.")
            return
        changed = 0
        for ph in [p for p in self.targets()[0] if not p.busy and p.title.strip()]:
            base = ph.title
            if self.editorial_location().strip() or self.custom_date_value():
                # rebuild from the bare title, so a title that already has your text or a date (from before
                # the location or custom date was set) gets them in the right place, never a second date
                on = self.settings.get("title_date_pos", "off") != "off"
                base = self.bare_title(ph, strip_date=on) or ph.title
            new = self.compose_title(ph, base)
            lead = {k.casefold() for k in core.expand_lead_keywords(self.settings.get("lead_keywords", ""),
                                                                     self.photo_date(ph))}
            n_lead = 0                                # your keywords to put first that really are at the front
            for k in ph.keywords:
                if k.casefold() not in lead:
                    break
                n_lead += 1
            new_kw = core.place_editorial_keywords(ph.keywords, self.editorial_keywords(ph),
                                                   self.settings.get("editorial_pos", "before"), after=n_lead)
            if new != ph.title or new_kw != core.clean_keywords(ph.keywords):
                ph.title, ph.keywords = new, new_kw
                changed += 1
                if ph.status == "saved":
                    ph.status = "ready"
                ph.message = "Title text added, not saved yet"
                self._show(ph)
        self.apply_filter()
        self.status.setText(f"Updated {changed} title{'s' if changed != 1 else ''}. "
                            "Save to write them into the files." if changed else
                            "Those titles already include this (photos without a title are skipped).")

    # ---------- copy & paste between photos
    # ---------- photo notes: copy & paste
    def copy_notes(self, ph):
        QApplication.clipboard().setText(ph.note)
        self.status.setText(f"Copied the photo notes of {os.path.basename(ph.path)}. Right-click other photos "
                            "→ Paste photo notes.")

    def paste_notes(self, photos, text: str | None = None):
        """Put the clipboard text (or the given text; '' clears) into these photos' notes."""
        note = core.single_line(QApplication.clipboard().text() if text is None else text)
        targets = [p for p in photos if not p.busy]
        for ph in targets:
            ph.note = note
            self._show(ph)
        n = len(targets)
        self.status.setText((f"Pasted the notes into {n} photo{'s' if n != 1 else ''}." if note else
                             f"Cleared the notes of {n} photo{'s' if n != 1 else ''}.")
                            + " Notes guide the AI; they're not saved into the files.")
        self.apply_filter()

    def copy_metadata(self):
        if isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit)):
            return                                   # copying text inside a box: leave it to the box
        sel = self._selected_photos()
        ph = sel[0] if len(sel) == 1 else None
        if ph is None:
            self.status.setText("Select one photo to copy its title and keywords.")
            return
        QApplication.clipboard().setText(ph.title + "\n" + core.keywords_to_text(ph.keywords))
        self.status.setText(f"Copied the title and {len(ph.keywords)} keywords from {os.path.basename(ph.path)}. "
                            "Select other photos and paste (Ctrl/⌘+V).")

    def paste_metadata(self, mode: str = "both", photos=None):
        """mode: 'both' (title + keywords), 'title', 'keywords' (replace) or 'add' (keep existing)."""
        if isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit)):
            return                                   # pasting text inside a box: leave it to the box
        clip = QApplication.clipboard()
        mime = clip.mimeData()
        files = [u.toLocalFile() for u in (mime.urls() if mime is not None and mime.hasUrls() else [])
                 if u.isLocalFile()]
        if files:                                    # photo files copied in Finder / Explorer
            self.add_paths(files)
            return
        title, keywords = core.parse_pasted_text(clip.text())
        if title is None and keywords is None:
            self.status.setText("Nothing to paste: copy a photo's title and keywords first "
                                "(or text with the title on the first line and keywords after it).")
            return
        targets = [p for p in (photos if photos is not None else self.targets(fallback_all=False)[0]) if not p.busy]
        if not targets:
            self.status.setText("Check or select the photos to paste into first.")
            return
        changed = 0
        for ph in targets:
            before = (ph.title, list(ph.keywords))
            if mode in ("both", "title") and title is not None:
                ph.title = title
            if mode in ("both", "keywords") and keywords is not None:
                ph.keywords = list(keywords)
            if mode == "add" and keywords is not None:
                ph.keywords = core.merge_keywords(ph.keywords, keywords)
            if (ph.title, ph.keywords) != before:
                changed += 1
                if ph.status == "saved":
                    ph.status = "ready"
                ph.message = "Pasted, not saved yet"
                self._show(ph)
        self.apply_filter()
        what = {"both": "title and keywords", "title": "title", "keywords": "keywords", "add": "keywords (added)"}[mode]
        self.status.setText(f"Pasted the {what} into {changed} photo{'s' if changed != 1 else ''}. "
                            "Save to write them into the files." if changed else "Nothing changed.")

    # ---------- photo types
    def type_items(self) -> list[tuple[str, str]]:
        return [(k, label) for k, label, _ in core.photo_types(self.settings)]

    def effective_type(self, ph: Photo) -> str:
        """The photo's own type, or the default; an unknown (deleted) type falls back to Automatic."""
        key = ph.type_key or self.settings.get("default_type", "auto")
        return key if any(k == key for k, _ in self.type_items()) else "auto"

    def _default_label(self) -> str:
        key = self.settings.get("default_type", "auto")
        name = next((label for k, label in self.type_items() if k == key), "Automatic (from your notes)")
        return f"Default: {name}"

    def fill_type_combo(self, combo: QComboBox, current: str, with_default: bool = True):
        """(Re)fill a Type dropdown without firing change signals."""
        combo.blockSignals(True)
        combo.clear()
        if with_default:
            combo.addItem(self._default_label(), "")
        for key, label in self.type_items():
            combo.addItem(label, key)
        idx = combo.findData(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def refresh_type_combos(self):
        self.fill_type_combo(self.default_type, self.settings.get("default_type", "auto"), with_default=False)
        for ph in self.photos:
            if ph.type_key and not any(k == ph.type_key for k, _ in self.type_items()):
                ph.type_key = ""                     # its custom type was deleted: back to the default
            if ph.type_combo is not None:
                self.fill_type_combo(ph.type_combo, ph.type_key)
        if self.review.photo is not None:
            self.review.show_photo(self.review.photo)

    def set_photo_type(self, photos, key: str):
        for ph in photos:
            if ph.busy:
                continue
            ph.type_key = key
            if ph.type_combo is not None:
                self.fill_type_combo(ph.type_combo, key)
        if self.review.photo is not None:
            self.review.show_photo(self.review.photo)
        n = len([p for p in photos if not p.busy])
        name = self._default_label() if not key else next((l for k, l in self.type_items() if k == key), key)
        self.status.setText(f"Photo type for {n} photo{'s' if n != 1 else ''}: {name}. "
                            "Click Detect to write titles for that type.")

    def _row_type_chosen(self, ph: Photo, combo: QComboBox):
        if not ph.busy:
            self.set_photo_type([ph], combo.currentData() or "")

    def _default_type_chosen(self, *_):
        key = self.default_type.currentData() or "auto"
        if key != self.settings.get("default_type"):
            self.settings["default_type"] = key
            core.save_settings(self.settings)
            self.refresh_type_combos()               # "Default: …" labels follow the new choice

    def _selected_photos(self, include_hidden: bool = False) -> list[Photo]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        # selection signals can arrive mid-way through removing rows; ignore rows we don't know
        sel = [self.photos[r] for r in rows if 0 <= r < len(self.photos)]
        return sel if include_hidden else [p for p in sel if p.path not in self.hidden]

    def _cell_clicked(self, row, col):
        """One click on a Photo notes cell opens it for typing (no double-click needed). Ctrl/Shift-clicks
        are left alone, so selecting several rows still works."""
        if col != COL_NOTE or not 0 <= row < len(self.photos) or self.photos[row].busy:
            return
        if QApplication.keyboardModifiers() != Qt.NoModifier:
            return
        if self.table.state() != QAbstractItemView.EditingState:
            self.table.editItem(self.table.item(row, COL_NOTE))

    def _open_photo(self, row, col):
        if col in (COL_THUMB, COL_FILE) and 0 <= row < len(self.photos):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.photos[row].path))

    def _select_rows(self, rows):
        rows = [r for r in rows if 0 <= r < self.table.rowCount()]
        sm = self.table.selectionModel()
        sm.clearSelection()
        for r in rows:
            sm.select(self.table.model().index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def visible_photos(self) -> list[Photo]:
        return [p for p in self.photos if p.path not in self.hidden]

    def checked_photos(self, include_hidden: bool = False) -> list[Photo]:
        """Ticked photos. Photos hidden by the filter don't count unless include_hidden."""
        return [p for p in self.photos if p.checked and (include_hidden or p.path not in self.hidden)]

    def filtering(self) -> bool:
        return (self.filter_box.currentData() or "all") != "all" or bool(self.search.text().strip())

    def targets(self, fallback_all: bool = True) -> tuple[list[Photo], str]:
        """The photos the buttons act on, and how they were chosen: ticked photos first, otherwise the
        selected rows, otherwise (if allowed) every photo shown. Photos hidden by the filter never count."""
        checked = self.checked_photos()
        if checked:
            return checked, "checked"
        sel = self._selected_photos()
        if sel:
            return sel, "selected"
        if not fallback_all:
            return [], ""
        return self.visible_photos(), ("shown" if self.filtering() else "all")

    # ---------- filter and search
    def apply_filter(self):
        if not self._ready:
            return
        key = self.filter_box.currentData() or "all"
        text = self.search.text()
        kmin = core.keyword_range(self.settings.get("keyword_min"), self.settings.get("keyword_max"))[0]
        hidden = {p.path for p in self.photos if not photo_matches(p, key, text, kmin)}
        for p in self.photos:
            self.table.setRowHidden(self.rows[p.path], p.path in hidden)
        newly_hidden = hidden - self.hidden
        self.hidden = hidden
        if newly_hidden:   # a hidden row can't stay selected: the buttons would act on it unseen
            keep = [self.rows[p.path] for p in self._selected_photos(include_hidden=True) if p.path not in hidden]
            if len(keep) != len(self._selected_photos(include_hidden=True)):
                self._select_rows(keep)
        self.filter_bar.setVisible(bool(self.photos))
        self.btn_show_all.setVisible(self.filtering())
        shown = len(self.photos) - len(hidden)
        hidden_checked = sum(1 for p in self.photos if p.checked and p.path in hidden)
        if not self.filtering():
            self.filter_count.setText(f"{len(self.photos)} photo{'s' if len(self.photos) != 1 else ''}")
        else:
            self.filter_count.setText(f"Showing {shown} of {len(self.photos)}"
                                      + (f" · {hidden_checked} checked hidden (not affected)" if hidden_checked else ""))
        self._refresh_buttons()

    def show_all(self):
        self.filter_box.blockSignals(True)
        self.filter_box.setCurrentIndex(0)
        self.filter_box.blockSignals(False)
        self.search.blockSignals(True)
        self.search.setText("")
        self.search.blockSignals(False)
        self.apply_filter()

    def focus_search(self):
        if self.photos:
            self.search.setFocus()
            self.search.selectAll()

    def set_checked(self, photos, on: bool):
        self._filling = True                          # one refresh at the end, not one per row
        for ph in photos:
            ph.checked = on
            row = self.rows.get(ph.path)
            if row is not None:
                self.table.item(row, COL_CHECK).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._filling = False
        self._refresh_buttons()
        n = len(self.checked_photos())
        self.status.setText(f"{n} photo{'s' if n != 1 else ''} checked. The buttons work on the checked photos."
                            if n else "No photos checked. The buttons work on the selected photos, or all of them.")

    def _refresh_buttons(self):
        if not self._ready:
            return
        busy = any(p.busy for p in self.photos)
        targets, scope = self.targets()
        n_id = sum(1 for p in targets if not p.busy)
        n_save = sum(1 for p in targets if p.changed and not p.busy)
        self.btn_identify.setText(f"Detect {scope} ({n_id})" if self.photos else "Detect")
        self.btn_identify.setEnabled(n_id > 0)
        n_redo = sum(1 for p in self.checked_photos() if not p.busy)
        self.btn_redo_checked.setText(f"Redo Checked… ({n_redo})" if n_redo else "Redo Checked…")
        self.btn_redo_checked.setEnabled(n_redo > 0)
        self.btn_save.setText((f"Save checked ({n_save})" if scope == "checked" else f"Save metadata ({n_save})")
                              if self.photos else "Save Metadata")
        self.btn_save.setToolTip("Confirm the edits on the checked photos and save them into the files"
                                 if scope == "checked" else
                                 "Save the changed photos (the selected ones, or all shown) into the files")
        self.btn_save.setEnabled(n_save > 0 and self.exiftool is not None)
        removable, rscope = self.targets(fallback_all=False)
        self.btn_remove.setText(f"Remove {rscope} ({len(removable)})" if removable else "Remove")
        self.btn_remove.setEnabled(bool(removable) and not any(p.busy for p in removable))
        self.btn_clear.setEnabled(bool(self.photos) and not busy)
        visible = self.visible_photos()
        self.btn_check_all.setEnabled(any(not p.checked for p in visible))
        self.btn_uncheck_all.setEnabled(any(p.checked for p in self.photos))

    def _rebuild_rows(self):
        self.rows = {p.path: i for i, p in enumerate(self.photos)}

    def remove_checked(self):
        self.remove_selected(use_checks=True)

    def remove_selected(self, use_checks: bool = False):
        if self.table.state() == QAbstractItemView.EditingState:
            return   # Delete/Backspace while typing in a cell edits text, not the list
        chosen = self.targets(fallback_all=False)[0] if use_checks else self._selected_photos()
        sel = [p for p in chosen if not p.busy]
        if not sel:
            return
        for row in sorted((self.rows[p.path] for p in sel), reverse=True):
            del self.photos[row]
            self.table.removeRow(row)
        self._rebuild_rows()
        if not self.photos:
            self.stack.setCurrentIndex(0)
        self.apply_filter()
        self._selection_changed()

    def clear_list(self):
        if any(p.busy for p in self.photos):
            return
        unsaved = sum(1 for p in self.photos if p.changed)
        if unsaved and QMessageBox.question(self, "Clear list",
                                            f"{unsaved} photo(s) have changes that aren't saved. Clear anyway?") != QMessageBox.Yes:
            return
        self.photos.clear()
        self.rows.clear()
        self.table.setRowCount(0)
        self.stack.setCurrentIndex(0)
        self.apply_filter()
        self._selection_changed()

    # ---------- running batches
    def _begin_batch(self, total, label):
        self.cancel = threading.Event()   # new jobs get their own switch, so an earlier Stop can't cancel them
        if self.batch_done < self.batch_total:      # something is already running: add to it
            self.cancels.append(self.cancel)
            self.batch_total += total
            self.progress.setRange(0, self.batch_total)
            self.progress.setFormat("Working %v of %m")
        else:
            self.cancels = [self.cancel]
            self.batch_total, self.batch_done = total, 0
            self.progress.setRange(0, total)
            self.progress.setValue(0)
            self.progress.setFormat(f"{label} %v of %m")
        self.progress.show()
        self.btn_stop.show()

    def _tick(self):
        self.batch_done += 1
        self.progress.setValue(self.batch_done)
        if self.batch_done >= self.batch_total:
            self.progress.hide()
            self.btn_stop.hide()
            self.apply_filter()
        self._refresh_buttons()

    def stop(self):
        for c in self.cancels:
            c.set()
        self.stop_photos([p for p in self.photos if p.busy and p.status == "identifying"])
        self.status.setText("Stopped.")

    # ---------- identify with the local AI model
    def identify(self):
        self.identify_photos(self.targets()[0])

    def identify_photos(self, photos):
        """Start the AI on these photos. Nothing here waits for Ollama: a quick readiness check runs in
        the background first, so the window keeps responding."""
        targets = [p for p in photos if not p.busy]
        if not targets:
            return
        self._keep_scroll_position()
        self._remember_options()
        for ph in targets:
            ph.busy, ph.status, ph.message = True, "identifying", "Checking that the AI is ready…"
            ph.ai_started, ph.retries, ph.timed_out = 0.0, 0, False
            ph.token = core.CancelToken()
            self._show(ph)
        self.btn_stop.show()
        self.status.setText("Checking that Ollama is ready…")
        self._refresh_buttons()
        run_job(self, Job("ready", core.ai_ready, self.settings["ollama_url"]), self.io_pool,
                lambda _k, info, t=targets: self._start_identify(t, info),
                lambda _k, _msg, t=targets: self._start_identify(t, {"version": None, "names": []}))

    def _keep_scroll_position(self):
        """Put the list back where it was once Qt has finished reacting to the click (backstop for any
        focus change that would scroll to the selected row)."""
        v, h = self.table.verticalScrollBar(), self.table.horizontalScrollBar()
        pos = (v.value(), h.value())
        QTimer.singleShot(0, lambda: (v.setValue(pos[0]), h.setValue(pos[1])))

    def _release(self, photos, message: str):
        for ph in photos:
            ph.busy, ph.status, ph.message, ph.redo = False, "ready", message, None
            self._show(ph)
        self._refresh_buttons()

    def _start_identify(self, targets, info):
        core.debug(f"readiness check: Ollama {info.get('version')}, models {info.get('names')}; "
                   f"using {self.settings.get('model')}, time limit {self.settings.get('ai_time_limit')} s, "
                   f"retries {self.settings.get('ai_retries')}, {len(targets)} photo(s)")
        stopped = [p for p in targets if p.token is not None and p.token.is_set()]
        if stopped:                                                    # stopped while the check ran
            self._release(stopped, "Stopped")
        targets = [p for p in targets if p in self.photos and p not in stopped]   # (or removed from the list)
        if not any(p.busy for p in self.photos):
            self.btn_stop.hide()
        url, model = self.settings["ollama_url"], self.settings["model"]
        if info.get("version") is None:
            self._release(targets, "Ollama isn't running")
            self._ai_checked(None, {"version": None, "models": []})
            QMessageBox.information(self, "Ollama isn't running",
                                    "The free AI recognition runs on your computer with Ollama.\n\n"
                                    "Install Ollama from ollama.com and open it, then click Detect again.")
            return
        if not any(same_model(model, n) for n in info.get("names", [])):
            self._release(targets, "The AI model isn't downloaded yet")
            QMessageBox.information(self, "Download a model",
                                    "Download a free AI model in Settings (one click), then click Detect again.")
            self.open_settings()
            return
        if not targets:
            return
        context, direction = self.settings["context"], self.settings["direction"]
        kmin, kmax = core.keyword_range(self.settings.get("keyword_min"), self.settings.get("keyword_max"))
        self._begin_batch(len(targets), "Detecting")
        self.status.setText(f"Detecting with {model}. The first photo takes longer while the model loads.")
        for ph in targets:
            ph.message = "Waiting in line…"
            self._show(ph)
            self._queue_identify(ph)
        self.ai_timer.start()
        self._refresh_buttons()

    def _queue_identify(self, ph):
        """Put one photo in line for the AI, with its own stop switch."""
        url, model = self.settings["ollama_url"], self.settings["model"]
        context, direction = self.settings["context"], self.settings["direction"]
        kmin, kmax = core.keyword_range(self.settings.get("keyword_min"), self.settings.get("keyword_max"))
        if ph.token is None or ph.token.is_set():
            ph.token = core.CancelToken()
        ph.ai_started = 0.0
        redo = ph.redo or {}
        job = Job(ph.path, functools.partial(core.identify, cancel=ph.token,
                                             symbols=self.settings.get("symbols", "convert"),
                                             feedback=redo.get("feedback", ""), rejected_title=redo.get("rejected", ""),
                                             title_length=self.settings.get("title_length", "detailed"),
                                             expand_short=True),
                  ph.path, url, model, kmin, kmax,
                  context, self.bare_title(ph), list(ph.keywords), direction, ph.note,
                  core.type_description(self.settings, self.effective_type(ph)),
                  core.expand_lead_keywords(self.settings.get("lead_keywords", ""), self.photo_date(ph)),
                  core.expand_title_prefix(self.settings.get("title_prefix", ""), self.photo_date(ph)),
                  self.settings.get("title_date_pos", "off") != "off",
                  bool(self.settings.get("revise", True)),
                  self.editorial_location() if self.settings.get("location_for_ai", True) else "",
                  self.effective_type(ph) == core.COPYRIGHT_FREE, cancel=ph.token)
        run_job(self, job, self.ai_pool, self._identified, self._identify_failed,
                on_started=self._identify_started)

    def redo_photos(self, photos):
        """Ask what's wrong, then run the AI again on these photos with those reasons."""
        photos = [p for p in photos if not p.busy]
        if not photos:
            return
        dlg = RedoDialog(self, len(photos), self.bare_title(photos[0]) if len(photos) == 1 else "")
        if not dlg.exec():
            return
        feedback = dlg.feedback()
        if not feedback:
            feedback = core.redo_feedback(["angle"])     # nothing ticked: at least ask for something different
        for ph in photos:
            ph.redo = {"feedback": feedback, "rejected": self.bare_title(ph)}
        core.debug(f"redo with reasons for {len(photos)} photo(s):\n{feedback}")
        self.identify_photos(photos)

    # ---------- stopping
    def _detect_clicked(self, ph):
        if ph.busy and ph.status == "identifying":
            self.stop_photos([ph])
        else:
            self.identify_photos([ph])

    def stop_photos(self, photos):
        """Stop the AI on these photos right away: the one being worked on is cut off mid-request,
        photos waiting in line are taken out of the line."""
        for ph in photos:
            if ph.busy and ph.status == "identifying" and ph.token is not None:
                core.debug(f"{os.path.basename(ph.path)}: STOP pressed"
                           + (f" after {time.monotonic() - ph.ai_started:.1f} s" if ph.ai_started else " (waiting)"))
                ph.timed_out = False
                ph.message = "Stopping…"
                ph.token.cancel()
                self._show(ph)

    def _identify_started(self, path):
        ph = self._photo(path)
        if ph:
            ph.ai_started = time.monotonic()
            core.debug(f"{os.path.basename(path)}: the AI started on it"
                       + (f" (retry {ph.retries})" if ph.retries else "")
                       + ("" if self.models_loaded else " (first photo of the session: model may need loading)"))
            self._ai_heartbeat()

    MODEL_LOAD_EXTRA = 90   # extra seconds for the first photo of a session, while Ollama loads the model

    def _ai_heartbeat(self):
        """Once a second while the AI works: show how long it has been on the current photo,
        so a slow photo is clearly busy rather than stuck."""
        working = [p for p in self.photos if p.busy and p.status == "identifying"]
        if not working:
            self.ai_timer.stop()
            return
        limit = int(self.settings.get("ai_time_limit", 30) or 0)
        for ph in working:
            if ph.ai_started:
                secs = int(time.monotonic() - ph.ai_started)
                allowed = limit + (0 if self.models_loaded else self.MODEL_LOAD_EXTRA)
                if limit and secs > allowed and not ph.timed_out and ph.token is not None and not ph.token.is_set():
                    ph.timed_out = True                # over the time limit: stop it (and maybe retry)
                    core.debug(f"{os.path.basename(ph.path)}: TIME LIMIT: {secs} s > {allowed} s allowed, stopping it")
                    ph.message = f"Took longer than {allowed} s, stopping it…"
                    self._set_status_cell(ph)
                    ph.token.cancel()
                    continue
                ph.message = (f"The AI is looking at this photo… {secs} s"
                              + ("" if self.models_loaded else " (loading the model first, this one takes longer)"))
                self._set_status_cell(ph)
                waiting = sum(1 for p in working if not p.ai_started)
                self.status.setText(f"Detecting with {self.settings['model']}: {secs} s on {os.path.basename(ph.path)}"
                                    + (f", {waiting} more waiting" if waiting else "")
                                    + ". You can keep working while it runs.")

    def _set_status_cell(self, ph):
        """Update just the Status cell (cheaper than redrawing the whole row every second)."""
        row = self.rows.get(ph.path)
        if row is None:
            return
        text, _color = self.status_text(ph)
        self._filling = True
        self.table.item(row, COL_STATUS).setText(text)
        self._filling = False

    def _identified(self, path, result):
        self.models_loaded = True
        ph = self._photo(path)
        if ph:
            redo, ph.redo = ph.redo, None
            keep_title = self.keep_titles.isChecked() and ph.title.strip() and not redo   # a redo always replaces it
            if not keep_title:
                # the location is added to the title for you, so a trailing "in <location>" from the AI goes
                ph.title = self.compose_title(ph, core.drop_trailing_location(result["title"],
                                                                               self.editorial_location()))
            # order: your "keywords to put first", then existing keywords (if kept), then suggestions
            kmax = core.keyword_range(self.settings.get("keyword_min"), self.settings.get("keyword_max"))[1]
            lead = core.expand_lead_keywords(self.settings.get("lead_keywords", ""), self.photo_date(ph))
            existing = ph.keywords if self.keep_kw.isChecked() else []
            if redo:   # replace the rejected suggestions; keep only the keywords that were in the file before
                existing = list(ph.file_keywords) if self.keep_kw.isChecked() else []
            suggested = result["keywords"]
            if self.effective_type(ph) == core.COPYRIGHT_FREE:
                existing = core.drop_brand_keywords(existing)    # no brand names, even ones already on the file
                suggested = core.drop_brand_keywords(suggested)
            ph.keywords = core.place_editorial_keywords(core.combine_keywords(lead, existing, suggested, kmax),
                                                        self.editorial_keywords(ph),
                                                        self.settings.get("editorial_pos", "before"), after=len(lead))
            ph.busy, ph.status = False, "suggested"
            ph.message = (("Redone with your reasons" if redo else "Suggested")
                          + (", checked against your instructions" if result.get("revised") else "")
                          + ", not saved yet" + (" (kept existing title)" if keep_title else "")
                          + (f". Note: {result['warning']}" if result.get("warning") else ""))
            self._show(ph)
            self.stats["photos"] += 1
            self.stats["seconds"] += float(result.get("seconds", 0))
            self.stats["kept"] += 1 if keep_title else 0
            avg = self.stats["seconds"] / self.stats["photos"]
            kept = self.stats["kept"]
            self.last_summary = (f"Detected {self.stats['photos']} photo{'s' if self.stats['photos'] != 1 else ''} "
                                 f"this session, about {avg:.0f} s each."
                                 + (f" {kept} kept their existing title: untick Keep existing titles to get new ones."
                                    if kept else ""))
            self.status.setText(self.last_summary)
        self._tick()

    def _identify_failed(self, path, msg):
        ph = self._photo(path)
        if ph and ph.timed_out:
            ph.timed_out = False
            limit = int(self.settings.get("ai_time_limit", 30) or 0)
            tries = int(self.settings.get("ai_retries", 1) or 0)
            if ph.retries < tries:                   # try again: often a stuck request just needs a fresh start
                ph.retries += 1
                core.debug(f"{os.path.basename(ph.path)}: retrying ({ph.retries} of {tries})")
                ph.message = f"Took longer than {limit} s, trying again ({ph.retries} of {tries})…"
                self._show(ph)
                self.batch_total += 1                # the retry counts as one more step in the progress bar
                self.progress.setRange(0, self.batch_total)
                self._queue_identify(ph)
                self._tick()
                return
            core.debug(f"{os.path.basename(ph.path)}: GAVE UP after {ph.retries + 1} tries over the time limit")
            ph.busy, ph.redo = False, None
            ph.status = "error"
            ph.message = (f"The AI took longer than {limit} s" + (f" ({ph.retries + 1} tries)" if ph.retries else "")
                          + ". Click Detect to try again, pick a faster model in Settings, or raise the time limit.")
            self._show(ph)
        elif ph:
            if msg != core.STOPPED:
                core.debug(f"{os.path.basename(ph.path)}: FAILED: {msg}")
            ph.busy, ph.redo = False, None
            ph.status, ph.message = ("ready", "Stopped") if msg == core.STOPPED else ("error", msg)
            self._show(ph)
        if not any(p.busy for p in self.photos):
            self.btn_stop.hide()
        if not any(p.busy and p.status == "identifying" for p in self.photos):
            # the last photo of the batch failed: don't leave "Detecting… 12 s" in the status bar
            errors = sum(1 for p in self.photos if p.status == "error")
            self.status.setText((getattr(self, "last_summary", "") + " " if getattr(self, "last_summary", "") else "")
                                + (f"{errors} photo{'s' if errors != 1 else ''} had a problem: see the Status column."
                                   if errors else "Stopped."))
        self._tick()

    # ---------- save into the files
    def save(self):
        if not self.exiftool:
            self._setup_exiftool()
            if not self.exiftool:
                return
        chosen, scope = self.targets()
        targets = [p for p in chosen if p.changed and not p.busy]
        if not targets:
            self.status.setText("Nothing to save: no changed titles or keywords" +
                                {"checked": " in the checked photos.", "selected": " in the selection."}.get(scope, "."))
            return
        n = len(targets)
        msg = (f"Save title and keywords into {n} photo{'s' if n != 1 else ''}?\n\n"
               "Only metadata (IPTC and XMP) is changed. The pictures themselves are not re-saved, and "
               "each file is checked afterwards to confirm the image data is identical."
               + ("\n\nAfter saving, XMP is copied to IPTC with xmp2iptc.args, so both match."
                  if self.settings.get("xmp2iptc_on_save", True) else ""))
        if QMessageBox.question(self, "Save metadata", msg) != QMessageBox.Yes:
            return
        self.save_photos(targets)

    def save_photos(self, photos):
        """Save these photos now (used by row buttons, the review panel and Save metadata)."""
        if not self.exiftool:
            self._setup_exiftool()
            if not self.exiftool:
                QMessageBox.information(self, "ExifTool needed",
                                        "ExifTool is needed to save metadata. Install it from exiftool.org, "
                                        "or choose it in Settings.")
                return
        targets = [p for p in photos if p.changed and not p.busy]
        if not targets:
            return
        n = len(targets)
        self._keep_scroll_position()
        self._begin_batch(n, "Saving")
        if n == 1:
            self.status.setText(f"Saving {os.path.basename(targets[0].path)}…")
        for ph in targets:
            ph.busy, ph.status, ph.message = True, "saving", "Saving…"
            self._show(ph)
            job = Job(ph.path, self._write_one, ph.path, ph.title, list(ph.keywords), self._save_options(),
                      cancel=self.cancel)
            run_job(self, job, self.io_pool, self._saved, self._save_failed)
        self._refresh_buttons()

    def _save_options(self) -> dict:
        """What happens on every save, decided once per batch (read on the main thread)."""
        s = self.settings
        args = None
        if s.get("xmp2iptc_on_save", True):
            args = core.find_xmp2iptc_args(self.exiftool.exe if self.exiftool else None, s.get("xmp2iptc_args", ""))
        return {"fields": core.meta_fields(s), "location": self.editorial_location(), "xmp2iptc_args": args}

    def _write_one(self, path, title, keywords, opts):
        core.safe_write(self.exiftool, path, title, keywords, xmp2iptc_args=opts["xmp2iptc_args"],
                        fields=opts["fields"], location=opts["location"])
        return self.exiftool.read([path])[path]   # confirm what is now in the file

    def _saved(self, path, meta):
        ph = self._photo(path)
        if ph:
            ph.file_title, ph.file_keywords = meta["title"], list(meta["keywords"])
            ph.busy = False
            if ph.changed:   # file doesn't match what we asked for
                ph.status, ph.message = "error", "Saved, but the file reads back differently"
            else:
                synced = " · IPTC synced" if self.settings.get("xmp2iptc_on_save", True) else ""
                ph.status, ph.message = "saved", f"Saved ✓ (image verified unchanged){synced}"
                self.status.setText(f"Saved {os.path.basename(path)}. Image verified unchanged.")
            self._show(ph)
        self._tick()

    def _save_failed(self, path, msg):
        ph = self._photo(path)
        if ph:
            ph.busy = False
            ph.status, ph.message = ("ready", "Stopped") if msg == "__cancelled__" else ("error", f"Not saved: {msg}")
            self._show(ph)
        self._tick()

    # ---------- closing
    def closeEvent(self, e):
        busy = any(p.busy for p in self.photos)
        unsaved = sum(1 for p in self.photos if p.changed and not p.busy)
        if busy:
            if QMessageBox.question(self, "Still working",
                                    "Photos are still being processed. Stop and quit after the current ones finish?") != QMessageBox.Yes:
                e.ignore()
                return
            self.stop()
            self.io_pool.waitForDone(60000)   # never quit in the middle of writing a file
            self.ai_pool.waitForDone(5000)
        elif unsaved and QMessageBox.question(self, "Unsaved changes",
                                              f"{unsaved} photo(s) have titles or keywords that aren't saved. Quit anyway?") != QMessageBox.Yes:
            self.restarting_for_update = False
            e.ignore()
            return
        self._remember_options()
        if self.update_ready:                         # a downloaded update installs as the app closes
            self.install_update(relaunch=self.restarting_for_update)
        e.accept()


def install_error_reporting():
    """Unexpected errors are written to the log (debug_log.txt, next to the settings) and shown in a message,
    instead of disappearing (there's no console when the app is started by double-clicking)."""
    import datetime
    import traceback
    shown = {"busy": False}

    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        core.debug(f"UNEXPECTED ERROR ({core.APP_NAME} {core.APP_VERSION}):\n" + text, always=True)
        log = core.debug_log_path() if os.path.exists(core.debug_log_path()) else ""
        sys.__stderr__ and sys.__stderr__.write(text)
        try:
            if shown["busy"] or QApplication.instance() is None:
                return
        except Exception:
            return
        shown["busy"] = True                     # one message at a time, even if errors repeat
        try:
            box = QMessageBox(QMessageBox.Warning, "Something went wrong",
                              "IPTC Tagger hit an unexpected error. Your photos were not changed by it.\n\n"
                              f"{kind.__name__}: {value}\n\n"
                              + (f"The details were saved to:\n{log}\n\nPlease send that file (or the text "
                                 "under Show Details) so it can be fixed." if log else ""))
            box.setDetailedText(text)
            box.exec()
        except Exception:
            pass                                 # reporting an error must never cause another
        finally:
            shown["busy"] = False

    sys.excepthook = hook


def main():
    install_error_reporting()
    app = QApplication(sys.argv)
    app.setApplicationName(core.APP_NAME)
    app.setStyle("Fusion")
    if os.path.exists(core.APP_ICON):
        app.setWindowIcon(QIcon(core.APP_ICON))
    if os.name == "nt":   # show the app's own icon in the taskbar, not Python's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("IPTCTagger.App")
        except Exception:
            pass
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:   # photos/folders dropped onto the app icon or given on the command line
        win.add_paths(sys.argv[1:])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
