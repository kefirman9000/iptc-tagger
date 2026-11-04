"""Shared setup for the tests. Every test gets its own empty settings folder, so nothing here ever touches
your real settings, undo history or saved list. Run them with:  python -m pytest"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def own_settings_folder(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    for var in ("APPDATA", "XDG_CONFIG_HOME"):        # Windows / Linux settings folders
        monkeypatch.setenv(var, str(cfg))
    monkeypatch.setenv("IPTC_TAGGER_OFFLINE", "1")    # no update checks or "what's new" from tests
    import tagger_core as core
    if sys.platform == "darwin":                       # the Mac folder is under the home folder
        monkeypatch.setattr(core, "config_dir", lambda: str(cfg / core.APP_NAME))
    return cfg


@pytest.fixture
def make_photos(tmp_path):
    """make_photos(n, folder='shoot') -> list of new JPEG paths (names with accents, like real cameras exports)."""
    from PIL import Image

    def make(n=3, folder="shoot", size=(320, 240)):
        d = tmp_path / folder
        d.mkdir(exist_ok=True)
        out = []
        for i in range(n):
            p = d / f"photo_{i:02d} café.jpg"
            Image.new("RGB", size, (30 + i * 20, 90, 140)).save(p, quality=90)
            out.append(str(p))
        return out
    return make


def exiftool_path():
    import tagger_core as core
    return os.environ.get("EXIFTOOL") or core.find_exiftool("")


@pytest.fixture
def exiftool():
    import tagger_core as core
    exe = exiftool_path()
    if not exe:
        pytest.skip("ExifTool isn't installed here (the Windows test run downloads it)")
    et = core.ExifTool(exe)
    yield et
    et.close()


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FakeAI:
    """Stands in for Ollama: answers every request from `answer(body)`, and remembers what it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data)
        self.asked.append(body)
        out = self.answer(body)
        return _Reply(json.dumps({"message": {"content": json.dumps(out)}, "done": True}).encode())

    def prompts(self):
        return [b["messages"][-1]["content"] for b in self.asked]

    def looks(self):
        """How many requests showed the AI the photo."""
        return sum(1 for b in self.asked if b["messages"][-1].get("images"))


def schema_fields(body):
    return set(((body.get("format") or {}).get("properties") or {}).keys())
