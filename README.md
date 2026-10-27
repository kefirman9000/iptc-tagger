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
- **AI suggestions:** a descriptive, SEO-friendly title in Title Case and 30–40 lowercase keywords per photo.
- **Review and edit** in a table or a side panel with a large preview. Save one photo at a time or many at once.
- **Instructions for the AI:** facts about the batch, title and keyword rules, and a note per photo.
- **Photo types:** landscape, food, architecture, product on a shelf, company logo with a stock chart, editorial event and more, plus your own. A **Copyright-free** type keeps brand and personal names out.
- **Title building:** text before titles, a location and the date taken, in editorial style, e.g. *Tokyo, Japan - October 26, 2026: Shibuya Crossing at Dusk*. A colour-coded preview shows the result, and a custom date can replace the camera's date.
- **Keywords to put first:** e.g. place or client, with `{year}`, `{month}` and `{date}` filled in from each photo.
- **Working with many photos:** check boxes, filters (no title, no keywords, unsaved, errors…), search, and copy and paste of titles, keywords and notes between photos.
- **Reliable:** Stop at any time, a time limit with automatic retry, and an optional debug log.

## What gets written

| Field | Tags |
|---|---|
| Title | `XMP-dc:Title`, `IPTC:ObjectName` |
| Keywords | `XMP-dc:Subject`, `IPTC:Keywords` |
| Description (optional, on by default) | `XMP-dc:Description`, `IPTC:Caption-Abstract` |

After saving, XMP is optionally copied to IPTC with ExifTool's `xmp2iptc.args`, so both match. This is on by default.

**The image itself is never changed.**
- Only the metadata is rewritten; the image data is never re-encoded.
- Every file is checked after saving, and restored if the image data differs.
- File dates are kept, and no backup copies are left behind.

## Settings and logs

Settings are stored in `%APPDATA%\IPTC Tagger` (Windows) or `~/Library/Application Support/IPTC Tagger` (Mac), so they carry over between versions. The optional debug log (**Settings → Troubleshooting**) is saved there as `debug_log.txt`.

## Building from source

```
pip install -r requirements.txt pyinstaller
python build_standalone.py --out build-output
```

To run without building, use `python iptc_tagger.py`. Releases are built automatically by [GitHub Actions](.github/workflows/release.yml) whenever `main` changes; the release is named after `APP_VERSION` in `tagger_core.py`.
