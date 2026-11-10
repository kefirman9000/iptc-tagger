# IPTC Tagger

A free desktop app that writes AI-generated **titles** and **keywords** into the metadata of JPEG photos (IPTC and XMP), and into the Lightroom sidecars of camera RAW files, ready for Lightroom, Bridge, Photo Mechanic and stock photo sites.

The AI runs locally through [Ollama](https://ollama.com): no account, no subscription, no API key, and photos never leave the computer.

## Download

1. Download **IPTC.Tagger.Windows.zip** from the [latest release](../../releases/latest).
2. Unzip it anywhere.
3. Run **IPTC Tagger.exe**.

No installation and no Python needed. ExifTool is included.

## Requirements

- **[Ollama](https://ollama.com/download)**, free, installed and running.
- **A vision model**, downloaded once from the app's **Settings**:

  | Model | Size | Good for |
  |---|---|---|
  | Gemma 3 4B (default) | 3.3 GB | Most computers (8 GB memory) |
  | Qwen3-VL 8B | 6.1 GB | More detail (16 GB memory or more) |

  Any other Ollama model that can read images also works.

## Features

- **Drag and drop** JPEGs, camera RAW files or whole folders, including subfolders.
- **RAW files:** titles and keywords go into Lightroom's `.xmp` sidecar next to the RAW, so Lightroom carries them into every export. The RAW itself is never written to (it's verified byte for byte), and edits and ratings already in the sidecar are kept. DNG isn't supported, because Lightroom keeps DNG metadata inside the file.
- **Watch Folder:** point it at Lightroom's export folder and new photos are added (and optionally detected) as they arrive.
- **Stock-site rules:** titles and keywords that Shutterstock, Adobe Stock or the other sites you sell on would reject are marked in amber with the reason, using the same rules as StockUp.
- **Detect:** the AI suggests a full, descriptive, SEO-friendly title that starts with the subject (*Magazines on a Shelf for Reading…*, never *A picture of…*), in Title Case or with every word capitalized, short, standard or detailed, as you choose, and 30–40 lowercase keywords per photo.
- **Review and edit** in a table or a side panel with a large preview. Save one photo at a time or many at once.
- **Every Detect checklist and profiles:** tick how the AI should write for every photo (plain and factual, focus on the signs, the main subject…), and save your instructions and choices as named profiles to switch between kinds of shoot.
- **Near-identical titles** are spotted after each batch, with one click to redo all but one of each group.
- **Keyboard review:** Ctrl+↓ / Ctrl+↑ next and previous photo, Ctrl+D Detect, Ctrl+R Redo, Ctrl+T tick, Ctrl+Enter save and next (⌘ on a Mac; F1 lists them all).
- **Instructions for the AI:** four compact tabs (*Instructions*, *Editorial titling*, *Keywords & title text*, *Options*) for facts about the batch and how titles and keywords should be written, plus a **Photo note** per photo (type it on a checked photo and every checked photo gets it) and an optional **Avoid** column (tick *Avoid column*) for what the AI shouldn't say about it. A **Never use** list keeps words like *beautiful* out of every title and keyword.
- **Find and replace** across the titles and keywords of many photos at once.
- **Colour labels, as in Lightroom:** keys 6–9 (and right-click) label photos into groups; the coloured dots above the list show one group, so Check All and every button can work on just it. Labels from Lightroom are read, and saved back into the files.
- **Saved or not, at a glance:** an orange dot on unsaved photos, a green tick on saved ones, *● edited* on each changed box, and **Save all** at the bottom with the count.
- **Photo types:** landscape, food, architecture, product on a shelf, company logo with a stock chart, editorial event and more, plus your own. A **Copyright-free** type keeps brand and personal names out.
- **Editorial:** one checkbox adds the location and the date taken to every title (before or after it) and to the keywords (sorted by relevance), e.g. *Tokyo, Japan - October 26, 2026: Shibuya Crossing at Dusk*. Text before titles, a live preview and a custom date (when the camera's date is wrong) round it off. Give some photos a different location with **Set for Photos…** (or right-click → **Set location…**): their titles and keywords change right away, without detecting again.
- **Clean text:** symbols in the AI's titles and keywords are converted to words (& → and, % → percent) or removed, as you choose. **Stock-site safe** makes the whole title, description and keywords plain for Shutterstock (*Café* → *Cafe*, no ™ or emoji).
- **Fix automatically:** too many numbers, a word used twice, a title that reads like a keyword list, a description that only repeats the title, or text too long for your stock sites: the AI rewords just that, and the rewording is only kept if it's better.
- **Reads the label first:** for products and signs, the AI copies the brand, product and flavour from the packaging (weights and sizes are left out), then uses those exact spellings.
- **Tidy keywords:** filler words like *photo* and *image* are removed, and the keywords that name what's in the title come first.
- **Undo a save:** right-click → **Undo last save** restores the photo's previous metadata (the last 5 saves per photo, even after a restart).
- **Keywords to put first:** e.g. place or client, with `{year}`, `{month}` and `{date}` filled in from each photo.
- **Redo with reasons:** tell the AI what's wrong with a suggestion (too generic, wrong subject, too short…) or what to focus on, and it tries again with that in mind. Works on the selected photo, or on every checked photo with **Redo Checked**.
- **Description:** the AI also writes a one- or two-sentence description (caption) for Alamy, Dreamstime and Shutterstock, which you can edit next to the title.
- **Reword / Double-check:** a quick text-only pass over titles, descriptions and keywords, with clean-up options or your own instructions. The location and date in front of the title are never changed.
- **A CSV for each stock site, with categories:** **Export CSV…** makes one for each site you tick (Shutterstock, Adobe Stock, Dreamstime, Depositphotos, 123RF, Pond5), with each site's categories picked for every photo, or one with every detail for a spreadsheet, named with the date.
- **Upload to your stock sites:** **Upload…** sends the photos by FTP to Shutterstock, Adobe Stock (SFTP), Dreamstime, Alamy, Depositphotos, 123RF, Pond5, Freepik (Magnific), Vecteezy, Yay Images, Colourbox and any other agency with FTP, at once, with a progress bar for each site, Pause / Resume / Stop, and what's left to do on each site afterwards. Encrypted connections first, nothing overwritten, nothing sent twice, a half-sent photo is removed from the site, and a receipt of everything sent. Passwords are kept in your computer's keychain. The uploading comes from [StockUp](https://github.com/kefirman9000/stockup), and logins you saved there are used here too.
- **Send to StockUp:** **Send All to StockUp** or **Send Checked to StockUp** hands the saved photos to [StockUp](https://github.com/kefirman9000/stockup) to check them for each stock site and upload them, optionally right after every save.
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
| Description / Caption (the photo's description, or the title if it has none) | `XMP-dc:Description`, `IPTC:Caption-Abstract` | on |
| Keywords | `XMP-dc:Subject`, `IPTC:Keywords` | on |
| Location (City, State, Country, when *Editorial* is on) | `XMP-photoshop:City/State/Country`, `IPTC:City/Province-State/Country-PrimaryLocationName` | on |
| EXIF description | `EXIF:ImageDescription` | off |
| Windows title and tags | `EXIF:XPTitle`, `EXIF:XPKeywords` | off |

Everything is written in one ExifTool step. The IPTC title and keywords are written in full, past IPTC's official 64-byte limit, so they match XMP. Optionally, every other XMP field (creator, copyright…) can also be copied to IPTC with ExifTool's `xmp2iptc.args`. This is off by default.

**The image itself is never changed.**
- RAW files are never written to at all: only `photo.xmp` next to `photo.CR3` is created or updated, and the RAW is compared byte for byte before and after.
- Only the metadata is rewritten; the image data is never re-encoded.
- Every file is checked after saving, and restored if the image data differs.
- File dates are kept, and no backup copies are left behind.

### RAW files and Lightroom

- **Photos you haven't imported yet:** import them; Lightroom reads the titles and keywords from the sidecars.
- **Photos already in your catalog:** in Lightroom, first select them and use *Metadata → Save Metadata to File*, so the sidecar holds your edits too, then tag them here, then *Metadata → Read Metadata from Files*. Reading a sidecar that has no edits in it would reset those photos' develop settings in Lightroom.

## Settings and logs

Settings are stored in `%APPDATA%\IPTC Tagger` (Windows) or `~/Library/Application Support/IPTC Tagger` (Mac), so they carry over between versions. The log, `debug_log.txt`, the undo history, `undo_history.json`, and the copy of the open list, `session.json` (removed when the app closes normally), are saved there too. It always records errors; turn on **Debug mode** in **Settings → General** to record every AI request as well.

## Building from source

```
pip install -r requirements.txt pyinstaller
python build_standalone.py --out build-output
```

To run the tests: `pip install pytest`, then `python -m pytest` (set `EXIFTOOL` to ExifTool's path to include the saving tests). They run on Windows before every release, and nothing is published unless they all pass.

To run without building, use `python iptc_tagger.py`. Releases are built automatically by [GitHub Actions](.github/workflows/release.yml) whenever `main` changes; the release is named after `APP_VERSION` in `tagger_core.py`.

## Support IPTC Tagger

IPTC Tagger is free. If it saves you keywording time, you can support its development on **[GitHub Sponsors](https://github.com/sponsors/kefirman9000)**. It helps keep new features and fixes coming. Starring the repo and reporting problems help too.

## License and disclaimer

Copyright © 2026 kefirman9000. All rights reserved. See [LICENSE](LICENSE) for the full terms. IPTC Tagger is free to use for any lawful purpose, including your own professional work. Selling it, redistributing the code or publishing modified versions requires written permission. Third-party components keep their own licenses.

- AI-written titles and keywords can be wrong, misleading or include names and brands that agencies reject. Review them before submitting.

IPTC Tagger is provided **as is**, without warranty of any kind. Use it at your own risk; the author is not liable for any damage, data loss or other loss arising from its use.

IPTC Tagger is an independent project and is not affiliated with, endorsed by or sponsored by any of the companies or products named here. Adobe Stock, Lightroom, Bridge and Photoshop are trademarks of Adobe Inc. Shutterstock is a trademark of Shutterstock, Inc. Photo Mechanic is a trademark of Camera Bits, Inc. Ollama and ExifTool belong to their respective owners.

Button icons: [Lucide](https://lucide.dev) (ISC licence, see `icons/LICENSE-lucide.txt`).
