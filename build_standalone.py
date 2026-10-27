r"""Build IPTC Tagger as a portable program that runs without Python (used by the GitHub release build).

    python build_standalone.py [--exiftool PATH] [--out FOLDER] [--no-start]

Makes, in FOLDER (default: this folder):
  Windows:  IPTC Tagger\IPTC Tagger.exe   and   IPTC Tagger (Windows).zip
  Mac:      IPTC Tagger.app                and   IPTC Tagger (Mac).zip

It never deletes or overwrites anything: if FOLDER already holds a build, it stops. Temporary build files go
to the system's temp folder. On Windows, ExifTool (from --exiftool, or the one this computer uses) is copied
into the program, so it works on its own.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = "IPTC Tagger"
WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"


def say(text: str):
    print(text, flush=True)


def outputs(folder: str = HERE, windows: bool = WINDOWS, mac: bool = MAC) -> tuple[str, str]:
    """(the program, the zip for other computers) as they'll be made in this folder."""
    program = os.path.join(folder, APP + (".app" if mac else ""))
    zip_path = os.path.join(folder, f"{APP} ({'Windows' if windows else 'Mac' if mac else sys.platform}).zip")
    return program, zip_path


def already_built(folder: str = HERE, windows: bool = WINDOWS, mac: bool = MAC) -> list[str]:
    """What a build here would overwrite. The build doesn't start if this isn't empty."""
    return [p for p in outputs(folder, windows, mac) if os.path.exists(p)]


def pyinstaller_args(work: str, folder: str = HERE, windows: bool = WINDOWS, mac: bool = MAC) -> list[str]:
    """PyInstaller settings. Temporary files and the .spec go to `work` (a temp folder); the program goes
    straight into `folder`. All paths are absolute, because PyInstaller reads them relative to the .spec."""
    sep = ";" if windows else ":"
    src = lambda name: os.path.join(HERE, name)
    args = ["--noconfirm", "--windowed", "--name", APP,
            "--distpath", folder, "--workpath", os.path.join(work, "build"), "--specpath", work,
            "--add-data", f"{src('arg_files')}{sep}arg_files",      # xmp2iptc.args (Shutterstock conversion)
            "--add-data", f"{src('icon.png')}{sep}.",               # window icon
            "--add-data", f"{src('CHANGELOG.md')}{sep}.",           # "What's new"
            "--icon", src("icon.ico" if windows else "icon.png"),
            "--exclude-module", "tkinter"]
    if mac:
        args += ["--osx-bundle-identifier", "com.iptctagger.app"]
    return args + [src("iptc_tagger.py")]


def find_exiftool() -> str | None:
    """The ExifTool the app itself would use (your choice in Settings, or the one it finds)."""
    sys.path.insert(0, HERE)
    import tagger_core as core
    custom = ""
    try:
        with open(os.path.join(core.config_dir(), "settings.json"), encoding="utf-8") as f:
            custom = json.load(f).get("exiftool_path", "") or ""
    except (OSError, ValueError):
        pass
    return core.find_exiftool(custom)


def copy_exiftool(exe: str, app_folder: str) -> str:
    """Copy Windows ExifTool into <program>/exiftool/: the .exe (as exiftool.exe) and, for current versions,
    the exiftool_files folder it needs next to it. Returns the copied exe's path."""
    dest = os.path.join(app_folder, "exiftool")
    os.makedirs(dest, exist_ok=True)
    target = os.path.join(dest, "exiftool.exe")
    shutil.copy2(exe, target)
    files = os.path.join(os.path.dirname(exe), "exiftool_files")
    if os.path.isdir(files):
        shutil.copytree(files, os.path.join(dest, "exiftool_files"), dirs_exist_ok=True)
    return target


def zip_folder(folder: str, zip_path: str):
    """Zip a folder (keeping its name as the top folder in the zip)."""
    base = os.path.dirname(folder)
    with zipfile.ZipFile(zip_path, "x", zipfile.ZIP_DEFLATED) as z:      # "x": never overwrite a zip
        for root, _dirs, files in os.walk(folder):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, base))


def option(name: str, default: str = "") -> str:
    """The value after --name on the command line."""
    args = sys.argv[1:]
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


def main() -> int:
    os.chdir(HERE)
    sys.path.insert(0, HERE)
    import tagger_core as core
    folder = os.path.abspath(option("--out", HERE))
    os.makedirs(folder, exist_ok=True)
    program, zip_path = outputs(folder)
    say(f"=== Building {APP} {core.APP_VERSION} as a standalone program ===\n")
    existing = already_built(folder)
    if existing:
        say("This folder already has a build of the program:\n  " + "\n  ".join(existing) + "\n\n"
            "Nothing was changed. Build into an empty folder (--out FOLDER).")
        return 1

    say("1/4  Getting the packaging tool (PyInstaller) and the app's libraries…")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "pyinstaller",
                           "-r", os.path.join(HERE, "requirements.txt")])

    say("2/4  Building the program (this takes a few minutes)…")
    work = tempfile.mkdtemp(prefix="iptc_tagger_build_")      # temporary files: not in your folder
    subprocess.check_call([sys.executable, "-m", "PyInstaller", *pyinstaller_args(work, folder)])
    if not os.path.exists(program):
        say(f"\nThe build didn't produce {program}. Scroll up for PyInstaller's error message.")
        return 1

    say("3/4  Adding ExifTool…")
    if WINDOWS:
        exe = option("--exiftool") or find_exiftool()
        if exe:
            copied = copy_exiftool(exe, program)
            say(f"     Copied {exe}\n     -> {copied}")
        else:
            say("     ExifTool wasn't found, so it isn't included. The program will ask for it the first\n"
                "     time you save (or put exiftool.exe in an 'exiftool' folder next to IPTC Tagger.exe).")
        shutil.copy2(os.path.join(HERE, "README.md"), os.path.join(program, "README.md"))
    else:
        say("     On a Mac the program uses the ExifTool installed on the computer (from exiftool.org\n"
            "     or Homebrew), so nothing is copied in.")

    say("4/4  Making a zip to copy to other computers…")
    if MAC:   # ditto keeps the app bundle's links and permissions intact
        subprocess.check_call(["ditto", "-c", "-k", "--keepParent", program, zip_path])
    else:
        zip_folder(program, zip_path)

    start = os.path.join(program, APP + ".exe") if WINDOWS else program
    say(f"\n=== Done ===\nThe program:  {start}\nTo share it: {zip_path}")
    if (WINDOWS or MAC) and "--no-start" not in sys.argv:
        say(f"\nStarting {APP}…")
        try:
            if WINDOWS:
                subprocess.Popen([start], cwd=program)
            else:
                subprocess.Popen(["open", program])
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        say(f"\nA step failed ({e}). Scroll up for the details. Check your internet connection, then try again.")
        sys.exit(1)
