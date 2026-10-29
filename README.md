# IPTC Tagger

A free desktop app that writes AI-generated **titles** and **keywords** into the metadata of JPEG photos (IPTC and XMP), ready for Lightroom, Bridge, Photo Mechanic and stock photo sites.

The AI runs locally through [Ollama](https://ollama.com): no account, no subscription, no API key, and photos never leave the computer.

## Download

1. Download **IPTC.Tagger.Windows.zip** from the [latest release](../../releases/latest).
2. Unzip it anywhere.
3. Run **IPTC Tagger.exe**.

No installation and no Python needed. ExifTool is included.

**Mac:** download **IPTC.Tagger.Mac.zip** and open **IPTC Tagger.app** (the first time: right-click → **Open**). Install [ExifTool](https://exiftool.org) separately, or with `brew install exiftool`.

## Requirements

- **[Ollama](https://ollama.com/download)**, free, installed and running.
- **A vision model**, downloaded once from the app's **Settings**:

  | Model | Size | Good for |
  |---|---|---|
  | Gemma 3 4B (default) | 3.3 GB | Most computers (8 GB memory) |
  | Qwen3-VL 8B | 6.1 GB | More detail (16 GB memory or more) |

  Any other Ollama model that can read images also works.

## Features

- **Drag and drop** JPEGs or whole folders, including subfolders.
- **Detect:** the AI suggests a full, descriptive, SEO-friendly title in Title Case (short, standard or detailed, as you choose) and 30–40 lowercase keywords per photo.
- **Review and edit** in a table or a side panel with a large preview. Save one photo at a time or many at once.
- **Instructions for the AI:** one box for facts about the batch and how titles and keywords should be written, plus a note per photo and an optional **Avoid** column (tick *Avoid column*) for what the AI shouldn't say about it. A **Never use** list keeps words like *beautiful* out of every title and keyword.
- **Find and replace** across the titles and keywords of many photos at once.
- **Photo types:** landscape, food, architecture, product on a shelf, company logo with a stock chart, editorial event and more, plus your own. A **Copyright-free** type keeps brand and personal names out.
- **Editorial:** one checkbox adds the location and the date taken to every title (before or after it) and to the keywords (sorted by relevance), e.g. *Tokyo, Japan - October 26, 2026: Shibuya Crossing at Dusk*. Text before titles, a live preview and a custom date (when the camera's date is wrong) round it off.
- **Clean text:** symbols in the AI's titles and keywords are converted to words (& → and, % → percent) or removed, as you choose.
- **Reads the label first:** for products and signs, the AI copies the brand, product and flavour from the packaging (weights and sizes are left out), then uses those exact spellings.
- **Tidy keywords:** filler words like *photo* and *image* are removed, and the keywords that name what's in the title come first.
- **Undo a save:** right-click → **Undo last save** restores the photo's previous metadata (the last 5 saves per photo, even after a restart).
- **Keywords to put first:** e.g. place or client, with `{year}`, `{month}` and `{date}` filled in from each photo.
- **Redo with reasons:** tell the AI what's wrong with a suggestion (too generic, wrong subject, too short…) or what to focus on, and it tries again with that in mind. Works on the selected photo, or on every checked photo with **Redo Checked**.
- **CSV export:** Shutterstock or Adobe Stock upload format, or every detail for a spreadsheet.
- **Working with many photos:** check boxes, filters (no title, no keywords, unsaved, errors…), search, and copy and paste of titles, keywords and notes between photos.
- **Model ready light:** green when the AI model is loaded, red when it isn't. The model loads when the app starts and stays ready while it's open (both optional).
- **Nothing lost:** the list with unsaved titles and keywords is brought back after a crash, each photo keeps its last 5 AI suggestions (right-click → **Earlier suggestions**), and a sound and notification tell you when a long batch is done.
- **Reliable:** Stop at any time, a time limit with automatic retry, and one log file for troubleshooting.
- **What's new:** shown once after each update; see [CHANGELOG.md](CHANGELOG.md).
- **Easy updates:** when a new release is published here, the app asks whether to download it, then installs it when you restart. Click **Check for Updates** at the top of the window any time, or turn off the check at startup in Settings.

## What gets written

The usual fields are written by default. Choose exactly which in **Settings → Saving**:

| Field | Tags | Default |
|---|---|---|
| Title | `XMP-dc:Title`, `IPTC:ObjectName` | on |
| Headline | `XMP-photoshop:Headline`, `IPTC:Headline` | on |
| Description / Caption (the title) | `XMP-dc:Description`, `IPTC:Caption-Abstract` | on |
| Keywords | `XMP-dc:Subject`, `IPTC:Keywords` | on |
| Location (City, State, Country, when *Editorial* is on) | `XMP-photoshop:City/State/Country`, `IPTC:City/Province-State/Country-PrimaryLocationName` | on |
| EXIF description | `EXIF:ImageDescription` | off |
| Windows title and tags | `EXIF:XPTitle`, `EXIF:XPKeywords` | off |

Everything is written in one ExifTool step. The IPTC title and keywords are written in full, past IPTC's official 64-byte limit, so they match XMP. Optionally, every other XMP field (creator, copyright…) can also be copied to IPTC with ExifTool's `xmp2iptc.args`. This is off by default.

**The image itself is never changed.**
- Only the metadata is rewritten; the image data is never re-encoded.
- Every file is checked after saving, and restored if the image data differs.
- File dates are kept, and no backup copies are left behind.

## Settings and logs

Settings are stored in `%APPDATA%\IPTC Tagger` (Windows) or `~/Library/Application Support/IPTC Tagger` (Mac), so they carry over between versions. The log, `debug_log.txt`, the undo history, `undo_history.json`, and the copy of the open list, `session.json` (removed when the app closes normally), are saved there too. It always records errors; turn on **Debug mode** in **Settings → General** to record every AI request as well.

## Building from source

```
pip install -r requirements.txt pyinstaller
python build_standalone.py --out build-output
```

To run without building, use `python iptc_tagger.py`. Releases are built automatically by [GitHub Actions](.github/workflows/release.yml) whenever `main` changes; the release is named after `APP_VERSION` in `tagger_core.py`.
