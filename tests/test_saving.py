"""Saving with the real ExifTool: Unicode, the picture never changing, Undo, and the ExifTool kept running."""
import threading

import tagger_core as core


def test_save_and_read_back_unicode(exiftool, make_photos):
    p = make_photos(1)[0]
    core.safe_write(exiftool, p, "Magazines On A Shelf — Café Ñandú 東京", ["magazines", "café", "東京"])
    meta = exiftool.read([p])[p]
    assert meta["title"] == "Magazines On A Shelf — Café Ñandú 東京"
    assert meta["keywords"] == ["magazines", "café", "東京"]


def test_picture_itself_never_changes(exiftool, make_photos):
    p = make_photos(1)[0]
    before = core.jpeg_image_data_hash(p)
    core.safe_write(exiftool, p, "Title", ["a", "b"])
    assert core.jpeg_image_data_hash(p) == before


def test_undo_last_save(exiftool, make_photos):
    p = make_photos(1)[0]
    core.safe_write(exiftool, p, "First Title", ["one"])
    core.remember_for_undo(p, core.snapshot_metadata(exiftool, p))
    core.safe_write(exiftool, p, "Second Title", ["two"])
    assert p in core.undoable([p])
    core.undo_last_save(exiftool, p)
    assert exiftool.read([p])[p]["title"] == "First Title"


def test_errors_still_come_through(exiftool, tmp_path):
    try:
        exiftool._run(["-XMP-dc:Title=x", str(tmp_path / "missing.jpg")])
    except core.ExifToolError as e:
        assert "not found" in str(e).lower()
    else:
        raise AssertionError("no error for a missing file")


def test_several_saves_at_once(exiftool, make_photos):
    files = make_photos(8)
    errors = []

    def save(f, i):
        try:
            core.safe_write(exiftool, f, f"Title {i}", [f"kw{i}"])
        except Exception as e:                                  # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=save, args=(f, i)) for i, f in enumerate(files)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    back = exiftool.read(files)
    assert all(back[f]["title"] == f"Title {i}" for i, f in enumerate(files))


def test_carries_on_if_exiftool_stops(exiftool, make_photos):
    p = make_photos(1)[0]
    exiftool.version()                                          # (starts the one kept running)
    exiftool._proc.kill()
    exiftool._proc.wait()
    core.safe_write(exiftool, p, "After A Restart", ["ok"])
    assert exiftool.read([p])[p]["title"] == "After A Restart"


def test_line_breaks_are_saved_as_spaces(exiftool, make_photos):
    p = make_photos(1)[0]
    core.safe_write(exiftool, p, "Two\nLines", ["x\ny"])
    meta = exiftool.read([p])[p]
    assert meta["title"] == "Two Lines" and meta["keywords"] == ["x y"]
