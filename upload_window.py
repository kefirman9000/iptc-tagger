"""The Upload window: send photos to your stock sites with a progress bar for each, and the Logins window.
The uploading itself is in uploader.py (adapted from StockUp)."""
import os
import threading

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

import tagger_core as core
import theme
import uploader as up

GREY = "#8a8f98"
STATE_WORDS = {"waiting": "Waiting", "connecting": "Connecting…", "uploading": "Uploading", "finishing":
               "Finishing a photo…", "tidying up": "Removing a half-sent photo…", "paused": "Paused",
               "stopped": "Stopped", "error": "Couldn't upload", "finished": "Done",
               "finished with errors": "Done, with problems"}


class LoginsDialog(QDialog):
    """Your login for each agency, in three groups. Passwords go in the computer's keychain."""

    def __init__(self, parent, settings: dict):
        super().__init__(parent)
        self.setWindowTitle("Agencies and logins")
        self.setMinimumWidth(900)
        self.settings = settings
        lay = QVBoxLayout(self)
        where = up.keychain_name()
        info = QLabel("Your FTP login for each agency you upload to. Leave the others empty: only agencies with a "
                      "login show in the Upload window. "
                      + (f"Passwords are kept in {where}. " if where else
                         "Passwords are kept in a settings file only you can read. ")
                      + "Logins you saved in StockUp are used here too, until you save one here.")
        info.setWordWrap(True)
        lay.addWidget(info)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setHorizontalSpacing(8)
        self.rows = {}
        logins = {s["name"]: s for s in up.load_sites()}
        r = 0
        for title, names in up.SITE_GROUPS:
            head = QLabel(f"<b>{title}</b>")
            head.setStyleSheet("padding-top:8px")
            grid.addWidget(head, r, 0, 1, 6)
            r += 1
            if any(up.SITES[n].get("needs_host") for n in names):
                for c, h in enumerate(("Agency", "Username", "Password", "Server", "Port / type")):
                    grid.addWidget(QLabel(f"<span style='color:{GREY}'>{h}</span>"), r, c)
                r += 1
            for name in names:
                s, rules = logins[name], up.SITES[name]
                if rules.get("custom"):
                    lbl = QLineEdit(s["label"] if s["label"] != rules["label"] else "")
                    lbl.setPlaceholderText(rules["label"] + ": its name")
                else:
                    lbl = QLabel(s["label"])
                lbl.setToolTip(rules["login_hint"])
                user = QLineEdit(s["username"])
                user.setPlaceholderText("username")
                user.setToolTip(rules["login_hint"])
                pw = QLineEdit()
                pw.setEchoMode(QLineEdit.Password)
                pw.setPlaceholderText(("saved" + (" in StockUp" if s["source"] == "StockUp" else ""))
                                      if s["password"] else "password")
                host = port = proto = None
                if rules.get("needs_host"):
                    host = QLineEdit(s["host"])
                    host.setPlaceholderText("server, from the agency's upload page")
                    host.setToolTip(rules["login_hint"])
                    port = QLineEdit(str(s["port"]))
                    port.setMaximumWidth(56)
                    from PySide6.QtWidgets import QComboBox
                    proto = QComboBox()
                    for key, text in (("ftp", "FTP"), ("ftps", "FTPS"), ("sftp", "SFTP")):
                        proto.addItem(text, key)
                    proto.setCurrentIndex(max(0, proto.findData(s["protocol"])))
                    proto.currentIndexChanged.connect(
                        lambda *_, pr=proto, po=port: po.setText("22" if pr.currentData() == "sftp" else "21"))
                test = QPushButton("Test")
                test.setToolTip("Try this login now (takes up to 15 seconds)")
                result = QLabel()
                result.setWordWrap(True)
                result.setMinimumWidth(150)
                test.clicked.connect(lambda *_, n=name: self._test(n))
                grid.addWidget(lbl, r, 0)
                grid.addWidget(user, r, 1)
                grid.addWidget(pw, r, 2)
                if host is not None:
                    grid.addWidget(host, r, 3)
                    pp = QHBoxLayout()
                    pp.addWidget(port)
                    pp.addWidget(proto)
                    grid.addLayout(pp, r, 4)
                grid.addWidget(test, r, 5)
                grid.addWidget(result, r, 6)
                self.rows[name] = {"label": lbl, "user": user, "pw": pw, "host": host, "port": port,
                                   "proto": proto, "result": result, "site": s}
                r += 1
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(3, 2)
        grid.setColumnStretch(6, 2)
        from PySide6.QtWidgets import QFrame, QScrollArea
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(scroll, 1)
        self.enc_only = QCheckBox("Encrypted connections only (skip an agency that only offers plain FTP)")
        self.enc_only.setChecked(bool(settings.get("upload_encrypted_only", False)))
        lay.addWidget(self.enc_only)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _site_now(self, name):
        w = self.rows[name]
        site = dict(w["site"], username=w["user"].text().strip())
        if w["pw"].text():
            site["password"] = w["pw"].text()
        if w["host"] is not None:
            site["host"] = w["host"].text().strip()
            try:
                site["port"] = int(w["port"].text())
            except ValueError:
                pass
            site["protocol"] = w["proto"].currentData()
        return site

    def _test(self, name):
        """Try the login in the background (up to 15 seconds) and show the answer when it comes."""
        result = self.rows[name]["result"]
        result.setText("Testing…")
        answer = []
        site, enc = self._site_now(name), self.enc_only.isChecked()
        threading.Thread(target=lambda: answer.append(up.test_login(site, enc)), daemon=True).start()

        def check():
            if not answer:
                QTimer.singleShot(200, check)
                return
            why = answer[0]
            ok, bad = theme.color("ok"), theme.color("bad")
            result.setText(f"<span style='color:{ok}'>Works</span>" if not why else
                           f"<span style='color:{ok}'>Works</span> ({why[6:]})" if why.startswith("works") else
                           f"<span style='color:{bad}'>{why}</span>")
        QTimer.singleShot(200, check)

    def _save(self):
        for name, w in self.rows.items():
            s = w["site"]
            u = w["user"].text().strip()
            extra = {}
            if w["host"] is not None:
                extra = {"host": w["host"].text().strip(), "port": w["port"].text().strip() or None,
                         "protocol": w["proto"].currentData()}
            if isinstance(w["label"], QLineEdit):
                extra["label"] = w["label"].text().strip()
            if not u and s["source"] == "IPTC Tagger":
                up.save_login(name, "", clear=True)
            elif u and (u != s["username"] or w["pw"].text() or s["source"] != "IPTC Tagger" or extra):
                up.save_login(name, u, w["pw"].text() or (s["password"] if s["source"] == "StockUp" else ""),
                              **extra)
        self.settings["upload_encrypted_only"] = self.enc_only.isChecked()
        core.save_settings(self.settings)
        self.accept()


class UploadWindow(QDialog):
    """Pick the sites, see what will be sent, then upload with Pause / Resume / Stop."""

    def __init__(self, win, photos, scope: str):
        super().__init__(win)
        self.win = win
        self.photos = list(photos)
        self.paths = [p.path for p in self.photos]
        self.uploader = None
        self.plans = {}
        self.setWindowTitle("Upload to stock sites")
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowCloseButtonHint
                            | Qt.WindowMinMaxButtonsHint | Qt.WindowSystemMenuHint)
        self.setMinimumWidth(760)
        lay = QVBoxLayout(self)
        n = len(self.photos)
        head = QLabel(f"<b>{n} photo{'s' if n != 1 else ''}</b> ({scope}). Only JPEGs are sent, exactly as they are "
                      "saved: the title, description and keywords go inside them. Nothing already on a site is "
                      "overwritten, and nothing is sent twice.")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.unsaved = QLabel()
        self.unsaved.setWordWrap(True)
        self.unsaved.setStyleSheet(f"background:{theme.color('unsaved_bg')}; color:{theme.color('unsaved_fg')}; "
                                   "padding:6px; border-radius:4px")
        self.btn_save = QPushButton("Save Metadata first")
        self.btn_save.clicked.connect(lambda *_: self.win.save_photos([p for p in self.photos if p.changed]))
        warn = QHBoxLayout()
        warn.addWidget(self.unsaved, 1)
        warn.addWidget(self.btn_save)
        self.warn_box = QWidget()
        self.warn_box.setLayout(warn)
        lay.addWidget(self.warn_box)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        for c, h in enumerate(("Site", "Login", "What will happen", "Progress")):
            self.grid.addWidget(QLabel(f"<b>{h}</b>"), 0, c)
        self.rows = {}
        lay.addLayout(self.grid)
        self._fill_sites()

        opts = QHBoxLayout()
        self.again = QCheckBox("Also send photos sent before")
        self.again.setToolTip("Normally a photo already sent to a site (by IPTC Tagger or StockUp) is skipped.")
        self.again.toggled.connect(lambda *_: self._show_plans())
        opts.addWidget(self.again)
        opts.addStretch(1)
        b_logins = QPushButton("Logins…")
        b_logins.clicked.connect(lambda *_: self._logins())
        opts.addWidget(b_logins)
        lay.addLayout(opts)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Notes and problems during the upload show here.")
        self.log.setMaximumHeight(110)
        lay.addWidget(self.log)
        self.next_steps = QLabel()
        self.next_steps.setWordWrap(True)
        self.next_steps.setTextFormat(Qt.RichText)
        self.next_steps.hide()
        lay.addWidget(self.next_steps)

        row = QHBoxLayout()
        self.btn_ss_csv = QPushButton("Make Shutterstock CSV")
        self.btn_ss_csv.clicked.connect(lambda *_: self.win.make_shutterstock_csv(self.photos))
        self.btn_receipt = QPushButton("Open Receipt")
        self.btn_receipt.setToolTip("upload_receipt.csv: every photo sent, where, when, encrypted or not")
        self.btn_receipt.clicked.connect(lambda *_: QDesktopServices.openUrl(QUrl.fromLocalFile(up.receipt_path())))
        for b in (self.btn_ss_csv, self.btn_receipt):
            b.hide()
            row.addWidget(b)
        row.addStretch(1)
        self.btn_start = QPushButton("Start Upload")
        self.btn_start.setDefault(True)
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stop")
        self.btn_close = QPushButton("Close")
        self.btn_start.clicked.connect(lambda *_: self.start())
        self.btn_pause.clicked.connect(lambda *_: self.toggle_pause())
        self.btn_stop.clicked.connect(lambda *_: self.uploader and self.uploader.stop())
        self.btn_close.clicked.connect(self.close)
        for b in (self.btn_start, self.btn_pause, self.btn_stop, self.btn_close):
            row.addWidget(b)
        lay.addLayout(row)

        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._seen_log = 0
        self._work_out_plans()
        self._tick()

    # ---- sites and what will be sent
    def _fill_sites(self):
        for widgets in self.rows.values():
            for w in widgets.values():
                if isinstance(w, QWidget):
                    w.deleteLater()
        self.rows = {}
        chosen = set(self.win.settings.get("upload_sites") or [])
        sites = up.load_sites()
        usable = [s for s in sites if s["username"] and s["password"] and s["host"]]
        others = [s["label"] for s in sites if s not in usable]
        if hasattr(self, "more"):
            self.more.deleteLater()
        self.more = QLabel((f"No agency logins yet. Click <b>Logins…</b> to add yours." if not usable else
                            f"<span style='color:{GREY}'>{len(others)} more agencies (" + ", ".join(others[:4])
                            + ("…" if len(others) > 4 else "") + ") can be added under Logins….</span>")
                           if others else "")
        self.more.setWordWrap(True)
        self.grid.addWidget(self.more, len(usable) + 1, 0, 1, 4)
        for r, s in enumerate(usable, start=1):
            name = s["name"]
            ready = True
            cb = QCheckBox(s["label"])
            cb.setChecked(ready and (name in chosen if chosen else True))
            cb.setEnabled(ready)
            login = QLabel((s["username"] + (" <span style='color:%s'>(from StockUp)</span>" % GREY
                                             if s["source"] == "StockUp" else "")) if ready else
                           f"<span style='color:{GREY}'>No login: add it under Logins…</span>")
            what = QLabel("…" if ready else "")
            what.setWordWrap(True)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(0)
            bar.setTextVisible(True)
            bar.setFormat("")
            bar.setMinimumWidth(200)
            bar.setVisible(ready)                    # no login: nothing to show until one is added
            cb.toggled.connect(lambda *_: self._remember_sites())
            for c, w in enumerate((cb, login, what, bar)):
                self.grid.addWidget(w, r, c)
            self.rows[name] = {"cb": cb, "login": login, "what": what, "bar": bar, "site": s}
        self.grid.setColumnStretch(2, 2)
        self.grid.setColumnStretch(3, 1)

    def _remember_sites(self):
        self.win.settings["upload_sites"] = [n for n, r in self.rows.items() if r["cb"].isChecked()]
        core.save_settings(self.win.settings)
        self._refresh_buttons()

    def _work_out_plans(self):
        """Which photos each site would get (in the background: it reads every file to recognise ones sent
        before)."""
        from_win = self.win
        names = list(self.rows)

        def work(paths=tuple(self.paths)):
            history, theirs = up.load_history(), up.stockup_sent()
            return {n: up.plan(paths, n, history, theirs) for n in names}
        job = from_win.make_job("upload-plan", work)
        from_win.start_job(job, from_win.io_pool, lambda _k, res: self._plans_ready(res),
                           lambda _k, msg: self.log.appendPlainText(f"Couldn't check the photos: {msg}"))

    def _plans_ready(self, plans):
        self.plans = plans
        self._show_plans()

    def _show_plans(self):
        for name, r in self.rows.items():
            p = self.plans.get(name)
            if not p or not (r["site"]["username"] and r["site"]["password"]):
                continue
            send = len(p["send"]) + (len(p["sent"]) if self.again.isChecked() else 0)
            bits = [f"<b>{send}</b> to send"]
            if p["sent"] and not self.again.isChecked():
                bits.append(f"{len(p['sent'])} sent before")
            if p["left_out"]:
                bits.append(f"{len(p['left_out'])} not a JPEG")
            if self.uploader is None:
                r["what"].setText(", ".join(bits))
        self._refresh_buttons()

    def _unsaved(self):
        return [p for p in self.photos if p.changed or p.busy]

    def _refresh_buttons(self):
        running = bool(self.uploader and self.uploader.status()["running"])
        chosen = [n for n, r in self.rows.items() if r["cb"].isChecked()]
        unsaved = self._unsaved()
        self.warn_box.setVisible(bool(unsaved) and not running)
        if unsaved:
            n = len(unsaved)
            self.unsaved.setText(f"{n} photo{'s have' if n != 1 else ' has'} changes that aren't saved into the "
                                 "file yet. Save them first: the sites read the title, description and keywords "
                                 "from inside the photos.")
        self.btn_start.setEnabled(not running and bool(chosen) and bool(self.plans) and not unsaved)
        self.btn_pause.setEnabled(running)
        self.btn_stop.setEnabled(running)
        for r in self.rows.values():
            r["cb"].setEnabled(not running and bool(r["site"]["username"] and r["site"]["password"]))

    # ---- running
    def start(self):
        sites = [r["site"] for r in self.rows.values() if r["cb"].isChecked()]
        if not sites or self._unsaved():
            return
        total = {n: len(self.plans[n]["send"]) + (len(self.plans[n]["sent"]) if self.again.isChecked() else 0)
                 for n in (s["name"] for s in sites) if n in self.plans}
        lines = "\n".join(f"  {up.SITES[n]['label']}: {c} photo{'s' if c != 1 else ''}" for n, c in total.items())
        if not os.environ.get("IPTC_TAGGER_OFFLINE") and QMessageBox.question(
                self, "Start upload", f"Send these now?\n\n{lines}") != QMessageBox.Yes:
            return
        self.uploader = up.Uploader(self.paths, sites,
                                    encrypted_only=bool(self.win.settings.get("upload_encrypted_only", False)),
                                    skip_sent=not self.again.isChecked())
        self.uploader.start()
        self._seen_log = 0
        self.log.clear()
        self.next_steps.hide()
        self._tick()

    def toggle_pause(self):
        if self.uploader:
            st = self.uploader.status()
            self.uploader.pause(not st["paused"])
            self._tick()

    def _tick(self):
        if self.uploader is None:
            self._refresh_buttons()
            return
        st = self.uploader.status()
        lines = []
        for name, p in st["sites"].items():
            r = self.rows.get(name)
            if not r:
                continue
            frac = min(1.0, p["bytes"] / max(1, p["total_bytes"]))
            done_all = p["state"].startswith("finished") and not p["errors"]
            r["bar"].setValue(1000 if done_all else int(frac * 1000))
            enc = {True: " · encrypted", False: " · not encrypted", None: ""}[p["encrypted"]]
            r["bar"].setFormat(f"{p['done']} of {p['total']}{enc}")
            word = STATE_WORDS.get(p["state"], p["state"])
            extra = []
            if p["skipped"]:
                extra.append(f"{p['skipped']} skipped (sent before)")
            if p["current"] and p["state"] in ("uploading", "finishing", "tidying up"):
                extra.append(p["current"])
            r["what"].setText(f"<b>{word}</b>" + (": " + ", ".join(extra) if extra else ""))
            lines += [f"{p['label']}: {m}" for m in p["notes"] + p["errors"]]
        for m in lines[self._seen_log:]:
            self.log.appendPlainText(m)
        self._seen_log = max(self._seen_log, len(lines))
        self.btn_pause.setText("Resume" if st["paused"] else "Pause")
        if not st["running"] and not self.next_steps.isVisible():
            self._finished(st)
        self._refresh_buttons()

    def _finished(self, st):
        sent = [n for n, p in st["sites"].items() if p["done"]]
        if not sent:
            return
        steps = "".join(f"<li><b>{up.SITES[n]['label']}:</b> {up.SITES[n]['after']}</li>" for n in sent)
        self.next_steps.setText("<b>What's left on the sites</b><ul style='margin-top:2px'>" + steps + "</ul>")
        self.next_steps.show()
        self.btn_ss_csv.setVisible("shutterstock" in sent)
        self.btn_receipt.show()
        msg = "Upload finished: " + ", ".join(f"{st['sites'][n]['done']} to {st['sites'][n]['label']}" for n in sent)
        self.win.status.setText(msg + ".")
        problems = any(st["sites"][n]["errors"] for n in st["sites"])
        if hasattr(self.win, "toast"):
            self.win.toast(msg + (" (some problems: see the Upload window)" if problems else ""),
                           "warn" if problems else "ok", 7)

    def _logins(self):
        if LoginsDialog(self, self.win.settings).exec():
            self._fill_sites()
            self._work_out_plans()

    def closeEvent(self, e):
        if self.uploader and self.uploader.status()["running"]:
            if not os.environ.get("IPTC_TAGGER_OFFLINE") and QMessageBox.question(
                    self, "Stop the upload?", "The upload is still running. Stop it and close? (A photo that's "
                    "half sent is removed from the site.)") != QMessageBox.Yes:
                e.ignore()
                return
            self.uploader.stop()
        self.timer.stop()
        super().closeEvent(e)


class SiteCsvDialog(QDialog):
    """Export CSV… : tick the stock sites, and each gets its own CSV with categories picked for every photo."""

    def __init__(self, parent, count: int, settings: dict, folder: str):
        import site_files
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Export CSV for your stock sites")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{count} photo{'s' if count != 1 else ''}.</b> Each site you tick gets its own CSV, in "
                      "the format that site asks for. Categories are picked for every photo (the AI's choice "
                      "first), so you don't have to. They're shown in the side panel.")
        head.setWordWrap(True)
        lay.addWidget(head)
        chosen = set(settings.get("csv_sites") or ["shutterstock", "adobe"])
        self.boxes = {}
        grid = QGridLayout()
        for r, (key, label, what) in enumerate(site_files.SITE_CSVS):
            cb = QCheckBox(label)
            cb.setChecked(key in chosen)
            cb.toggled.connect(lambda *_: self._update())
            note = QLabel(what)
            note.setStyleSheet(f"color:{GREY}")
            grid.addWidget(cb, r, 0)
            grid.addWidget(note, r, 1)
            self.boxes[key] = cb
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        row = QHBoxLayout()
        row.addWidget(QLabel("Save in:"))
        self.where = QLineEdit(folder)
        row.addWidget(self.where, 1)
        pick = QPushButton("Choose…")
        pick.clicked.connect(lambda *_: self._choose())
        row.addWidget(pick)
        lay.addLayout(row)
        tip = QLabel("Named by site, date and time, like shutterstock_2026-11-10_08-18.csv, so a later export never "
                     "replaces an earlier one. Upload the photos to a site first, then its CSV there.")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{GREY}")
        lay.addWidget(tip)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._update()

    def _choose(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Save the CSVs in", self.where.text())
        if d:
            self.where.setText(d)

    def chosen(self) -> list[str]:
        return [k for k, cb in self.boxes.items() if cb.isChecked()]

    def folder(self) -> str:
        return self.where.text().strip()

    def _update(self):
        n = len(self.chosen())
        b = self.buttons.button(QDialogButtonBox.Ok)
        b.setEnabled(n > 0)
        b.setText(f"Make {n} CSV{'s' if n != 1 else ''}" if n else "Make CSVs")

    def _accept(self):
        self.settings["csv_sites"] = self.chosen()
        core.save_settings(self.settings)
        self.accept()
