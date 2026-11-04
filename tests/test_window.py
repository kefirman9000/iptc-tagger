"""The real window, opened offscreen: adding photos, saving, locations, the checklist, profiles, near-identical
titles, keyboard moves, the Avoid column and closing. Runs where PySide6 is installed (the Windows test run)."""
import os
import time

import pytest

PySide6 = pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox   # noqa: E402

import tagger_core as core                                                  # noqa: E402
from conftest import exiftool_path                                          # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def wait(cond, timeout=60, what="it"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def window(qapp, monkeypatch):
    exe = exiftool_path()
    if not exe:
        pytest.skip("ExifTool isn't installed here")
    core.save_settings({**core.DEFAULT_SETTINGS, "exiftool_path": exe, "confirm_save": False,
                        "last_seen_version": core.APP_VERSION})
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    import iptc_tagger as app
    win = app.MainWindow()
    win.show()
    QApplication.processEvents()
    yield app, win
    win.close()
    QApplication.processEvents()


def add(win, paths):
    win.add_paths(paths)
    wait(lambda: len(win.photos) >= len(paths) and all(p.status != "reading" for p in win.photos), what="photos")


def test_add_save_and_read_back(window, make_photos):
    app, win = window
    files = make_photos(3)
    add(win, [os.path.dirname(files[0])])
    assert win.exiftool is not None and win.table.rowCount() == 3
    for i, ph in enumerate(win.photos):
        ph.title, ph.keywords = f"Red Barn {i} in Snow", ["barn", "snow"]
        win._show(ph)
    win.save_photos(win.photos)
    wait(lambda: all(p.status == "saved" for p in win.photos), what="saving")
    back = win.exiftool.read(files)
    assert {back[f]["title"] for f in files} == {"Red Barn 0 in Snow", "Red Barn 1 in Snow", "Red Barn 2 in Snow"}


def test_location_swap_without_detecting(window, make_photos):
    app, win = window
    add(win, make_photos(2))
    win.editorial.setChecked(True)
    win.location.setText("Tokyo, Japan")
    win._remember_options()
    for ph in win.photos:
        ph.title = win.compose_title(ph, "Temple Gate at Night")
        ph.keywords = ["temple", "tokyo", "japan"]
        win._show(ph)
    assert win.photos[0].title.startswith("Tokyo, Japan")
    win.set_location(win.photos[:1], "Kyoto, Japan")
    a, b = win.photos
    assert a.title.startswith("Kyoto, Japan") and "Tokyo" not in a.title and a.title.endswith("Temple Gate at Night")
    assert "kyoto" in a.keywords and "tokyo" not in a.keywords
    assert b.title.startswith("Tokyo, Japan")                                   # the other one is untouched
    assert win._save_options(a)["location"] == "Kyoto, Japan"


def test_checklist_and_profiles(window, monkeypatch):
    app, win = window
    win.settings["detect_style"] = ["factual", "words"]
    win._show_style()
    assert "Factual" in win.style_label.text() and "Signs" in win.style_label.text()
    assert "standing preferences" not in win.style_text() and "factually" in win.style_text()
    win.context.setPlainText("Grocery shelves in Ohio")
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Grocery products", True)))
    win.save_profile()
    assert win.settings["profile"] == "Grocery products"
    assert win.profile_box.findData("Grocery products") >= 0
    win.context.setPlainText("Travel photos")
    win.settings["detect_style"] = ["mood"]
    win._remember_options()
    win.apply_profile("Grocery products")
    assert win.context.toPlainText() == "Grocery shelves in Ohio" and win.settings["detect_style"] == ["factual", "words"]
    dlg = app.StyleDialog(win, ["factual"])
    assert dlg.chosen() == ["factual"]


def test_near_identical_titles(window, make_photos):
    app, win = window
    add(win, make_photos(3))
    titles = ["Red Barn in a Snowy Field with Wooden Fence", "Red Barn in Snowy Fields with a Wooden Fence",
              "Doritos Cool Ranch Chips on a Store Shelf"]
    for ph, t in zip(win.photos, titles):
        ph.title = t
        win._show(ph)
    win.show_similar()
    shown = [p for p in win.photos if p.path not in win.hidden]
    assert shown == win.photos[:2]
    assert not win.btn_redo_similar.isHidden()
    win.show_all()
    assert not win.hidden


def test_title_style_for_titles_already_there(window, make_photos):
    app, win = window
    add(win, make_photos(1))
    ph = win.photos[0]
    ph.title = "A picture of magazines on a shelf"
    win.settings.update(title_caps="every", direct_titles=True)
    win.offer_title_style()
    assert ph.title == "Magazines On A Shelf"


def test_keyboard_moves_and_buttons(window, make_photos):
    app, win = window
    add(win, make_photos(3))
    win._select_rows([0])
    wait(lambda: win.review.photo is win.photos[0], what="selection")
    win.select_offset(1)
    wait(lambda: win.review.photo is win.photos[1], what="next photo")
    win.toggle_check_selected()
    assert win.photos[1].checked
    win._refresh_buttons()
    assert win.btn_stockup_all.text() == "Send All 3 to StockUp"
    assert win.btn_stockup_checked.text() == "Send 1 Checked to StockUp"
    assert win.SHORTCUTS


def test_avoid_column_on_and_off(window):
    app, win = window
    win.avoid_on.setChecked(False)
    assert win.table.isColumnHidden(app.COL_AVOID)
    win.avoid_on.setChecked(True)
    assert not win.table.isColumnHidden(app.COL_AVOID)


def test_list_comes_back_after_a_crash(window, make_photos):
    app, win = window
    f = make_photos(1)[0]
    win.pending_restore = {f: {"path": f, "title": "Unsaved Title", "keywords": ["k"], "note": "heron",
                               "location": "Kyoto, Japan"}}
    add(win, [f])
    ph = win.photos[0]
    assert (ph.title, ph.note, ph.location) == ("Unsaved Title", "heron", "Kyoto, Japan")
    assert ph.changed


def test_closing_normally_removes_the_crash_copy(window, make_photos):
    app, win = window
    add(win, make_photos(1))
    win._write_session()
    assert core.load_session()
    win.close()
    QApplication.processEvents()
    assert core.load_session() == []
