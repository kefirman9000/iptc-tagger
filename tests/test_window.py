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
    assert win.act_stockup_all.text() == "All 3 photos in the list"
    assert win.act_stockup_checked.text() == "The 1 checked photo"
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


def test_clean_special_characters_and_limits(window, make_photos):
    app, win = window
    add(win, make_photos(2))
    a, b = win.photos
    a.title, a.keywords, a.description = "Fish & Chips in a Café", ["fish & chips", "café", "chips"], ""
    b.title, b.keywords = "Plain Title Here", ["plain"]
    for ph in (a, b):
        win._show(ph)
    assert win.clean_special_characters(win.photos) == 1
    assert a.title == "Fish and Chips in a Cafe" and a.keywords == ["fish and chips", "cafe", "chips"]
    assert b.title == "Plain Title Here"
    win.settings["symbols"] = "safe"
    a.title = "Crêpes™ & Coffee"
    win._make_plain(a)
    assert a.title == "Crepes and Coffee"
    win.settings.update(text_checks=["fit"], stock_sites_from_stockup=False, stock_sites=["adobe"])
    assert win.text_limits(a)["title_max"] == 70                 # no location or date around this title
    win.settings["text_checks"] = []
    assert win.text_limits(a) == {}


def test_make_shutterstock_csv_button(window, make_photos):
    import csv
    app, win = window
    add(win, make_photos(2))
    title = ("Tokyo, Japan - November 4, 2026: Gerber Puffs Cereal Snacks Variety Pack on Grocery "
             "Store Shelf, Featuring Banana, Apple Cinnamon, Sweet Potato and Strawberry Flavors")
    a, b = win.photos
    a.title, a.description, a.keywords = title, "A colorful display of Gerber Puffs on a shelf.", ["gerber", "puffs"]
    b.title, b.keywords = "Second Photo Title Here Today", ["second"]
    for ph in (a, b):
        win._show(ph)
    win._refresh_buttons()
    assert win.btn_ss_csv.text() == "Make Shutterstock CSV (2)"
    path = win.make_shutterstock_csv(ask_categories=lambda n, t: ["Food and drink"])
    rows = {r["Filename"]: r for r in csv.DictReader(open(path, encoding="utf-8"))}
    assert rows[os.path.basename(a.path)]["Description"] == title             # the title, as in the list
    assert rows[os.path.basename(a.path)]["Categories"] == "Food and drink,Objects"   # picked by itself
    again = win.make_shutterstock_csv(ask_categories=lambda n, t: ["Food and drink"])
    assert again != path and os.path.exists(path)                              # an earlier one is never replaced


def test_model_switch_on_the_main_window(window):
    app, win = window
    win._ai_checked(None, {"version": "1", "models": ["llava:7b", "qwen2.5vl:7b"],
                           "all": ["llava:7b", "qwen2.5vl:7b", "llama3.1:8b"]})
    assert [win.model_pick.itemData(i) for i in range(win.model_pick.count())][:2] == ["llava:7b", "qwen2.5vl:7b"]
    assert win.text_pick.itemData(0) == "" and win.text_pick.findData("llama3.1:8b") > 0
    win.model_pick.setCurrentIndex(win.model_pick.findData("qwen2.5vl:7b"))
    win.text_pick.setCurrentIndex(win.text_pick.findData("llama3.1:8b"))
    win._models_picked()
    assert win.settings["model"] == "qwen2.5vl:7b" and win.settings["text_model"] == "llama3.1:8b"
    assert core.load_settings()["text_model"] == "llama3.1:8b"                 # remembered
    assert win.text_model() == "llama3.1:8b"
    win.ai["all"] = ["llava:7b", "qwen2.5vl:7b"]                                # not downloaded any more
    assert win.text_model() == ""                                               # the photo model does it


def test_fits_a_laptop_screen(window, make_photos):
    """1366 × 768 (a common laptop): the window, with a photo selected, mustn't need more."""
    app, win = window
    add(win, make_photos(3))
    win.resize(1366, 740)
    win._select_rows([win.rows[win.photos[0].path]])
    wait(lambda: win.review.photo is win.photos[0], what="select")
    QApplication.processEvents()
    hint = win.minimumSizeHint()
    assert hint.width() <= 1366 and hint.height() <= 740, hint
    h = win.table.horizontalHeader()
    import iptc_tagger as app_mod
    assert [h.logicalIndex(i) for i in range(5)] == [app_mod.COL_CHECK, app_mod.COL_THUMB, app_mod.COL_FILE,
                                                       app_mod.COL_TITLE, app_mod.COL_KEYWORDS]
    win.toggle_instructions()                                   # folded and unfolded both remembered
    assert win.settings["instructions_folded"] == win.instr_summary.isVisibleTo(win)


def test_light_and_dark_with_icons(window, make_photos):
    import theme
    _mod, win = window
    app = QApplication.instance()
    for mode, dark in (("dark", True), ("light", False)):
        theme.apply(app, mode)
        win.apply_theme()
        assert theme.is_dark() is dark
        assert (app.palette().color(app.palette().ColorRole.Window).lightness() < 128) is dark
        assert theme.color("primary") in win.styleSheet()
        assert theme.color("warn_bg") in win.et_banner.styleSheet()
    # every toolbar button has its icon, and the main ones are the solid blue ones
    assert all(not b.icon().isNull() for b, _n, _p in win.themed_icons)
    assert {b.text().split(" (")[0] for b, _n, p in win.themed_icons if p} >= {"Detect", "Save Metadata"}
    assert win.btn_clear.property("plain") is True and win.btn_identify.property("primary") is True
    assert not win.review.b_copy.icon().isNull()


def test_busy_rows_show_a_bar_and_toasts_pop_up(window, make_photos):
    app, win = window
    add(win, make_photos(2))
    ph = win.photos[0]
    ph.busy = True
    win.table.viewport().repaint()                    # the delegate draws the moving bar without trouble
    win._animate_busy()
    assert win.photo_at_row(win.rows[ph.path]) is ph
    ph.busy = False
    win.batch_total, win.batch_label, win.batch_started, win.batch_quiet = 2, "Detecting", time.monotonic(), False
    win._batch_finished()
    assert win.toast_box.isVisible() and "Detect finished: 2 photos" in win.toast_box.text()
