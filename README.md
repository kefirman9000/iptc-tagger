# IPTC Tagger

Drop JPEG photos or whole folders into the window. A free AI vision model running
on your own computer looks at each photo and suggests a **title** and
**keywords**. You review and edit them in a table, then save them into the files
as IPTC and XMP metadata, which Lightroom, Bridge, Photo Mechanic and stock sites read.

**It's completely free:** no subscription, no account, no API key, no
per-photo cost. The AI runs locally through [Ollama](https://ollama.com), so your
photos never leave your computer and it works offline once set up.

## Your photos are never altered

Only the metadata is written. The picture itself is never re-saved or re-compressed.

- **No re-encoding:** ExifTool rewrites only the metadata section of each JPEG and copies the image data byte for byte.
- **Every save is verified:** the app fingerprints the compressed image data before and after writing, including the XMP-to-IPTC conversion. If they ever differed, it would put the original file back exactly and mark the photo with an error.
- **Nothing else changes:** file names, folders and modified dates stay the same, and no `_original` backup copies are left behind.
- **Other fields are left alone:** the app writes only the title, the keywords and, if you choose, the description. GPS, camera data and the like are never touched. The XMP-to-IPTC conversion (on by default, see below) also updates the IPTC copy of fields like creator, copyright and headline to match their XMP versions. IPTC fields with no XMP version are left as they are.
- **The AI sees a copy:** the AI looks at a small preview made in memory, which is never saved to disk.

## Download

1. Go to the **[Releases](../../releases/latest)** page and download **IPTC Tagger (Windows).zip** (or **IPTC Tagger (Mac).zip**).
2. Unzip it anywhere.
3. Run **IPTC Tagger.exe**.

It's a portable program: nothing to install, and no Python needed. ExifTool comes built in on Windows. On a Mac, open **IPTC Tagger.app**; the first time, right-click it → **Open**, and install ExifTool from [exiftool.org](https://exiftool.org) or with `brew install exiftool`.

To update, download the new zip from Releases. Your settings are kept separately (in `%APPDATA%\IPTC Tagger`, or `~/Library/Application Support/IPTC Tagger` on a Mac), so every version uses them.

## The AI: Ollama (free)

The AI runs on your own computer through Ollama:

1. Download **Ollama** from [ollama.com/download](https://ollama.com/download), install it and open it. It sits quietly in your system tray (Windows) or menu bar (Mac).
2. In IPTC Tagger, open **Settings** and click **Download** next to a model. This is a one-time download:

   | Model | Download | Best for |
   |---|---|---|
   | Gemma 3 4B (default) | 3.3 GB | Most computers, including ones with 8 GB of memory |
   | Qwen3-VL 8B | 6.1 GB | More detailed results; computers with 16 GB of memory or more |

   Any other Ollama model that can see images also shows up in Settings once you've installed it.

## Using it

1. **Add photos:** drag JPEGs or folders onto the window (folders are searched including subfolders), or use **Add Photos… / Add Folder…**. Any titles and keywords already on the photos are shown.
2. **Instructions for the AI:** the panel at the top. Everything in it is remembered between sessions.
   - **About these photos:** facts about the photos. The AI can't know names, places or events from the picture alone. They're given to the AI first, and it's told to build the title and keywords around them. For example: *"Autumn wedding of Sarah and James at Sarah P. Duke Gardens, Durham, North Carolina. The older couple are the bride's parents."*
   - **Title & keyword direction:** how titles and keywords should be written. These directions are the AI's top priority and override the built-in style. For example: *"Titles in the form 'Subject at Location', 10 words max. Always include the keyword durham. Avoid words like beautiful and stunning."*
     - They're placed where the AI gives them the most weight (its system message) and repeated at the end of the request.
     - If your direction asks for different capitalization, e.g. *"Use sentence case for titles"* or *"Capitalize proper nouns in keywords"*, the app stops forcing Title Case or lowercase.
     - Keywords you name in your instructions are never removed by the repetition filter.
   - **Keywords to put first:** keywords such as location, date, client or event that should come at the front of every photo's keywords, before the AI's suggestions. See **Keywords to put first** below.
   - **Text before titles:** text put in front of every suggested title, such as *Editorial:* or a location. See **Text before titles** below.
   - **Location in title** and **Date taken in title:** two rows, in the same order they appear in the title. Every setting is labelled with what it does, and the **Title preview** under them shows a colour-coded example. See **How a title is put together**, **Location in title** and **Date taken in title** below.
   - **Photo type:** the default type for all photos, such as *Company logo with stock chart* or *Product on a store shelf*. See **Photo types** below.
3. **Check the photos to work on:** tick the box at the left of each photo you want to work on. You don't need to keep rows selected; the ticks stay while you click around. See **Checking photos** below.
4. **Photo type and notes for single photos (optional):**
   - **Type:** each row has a **Type** dropdown, set to *Default* unless you change it. You can also set the type in the review panel, or right-click a selection → **Set photo type**.
   - **Photo notes:** to clarify a single image, click its **Photo notes** cell in the list and type (one click is enough), or use the review panel. For example *"the dog's name is Biscuit"*, or just a company, product or app name. The AI uses notes as information to clarify what the photo shows, and builds the title and keywords on whatever they give: a name, place, event, species, breed or product. Where a note differs from **About these photos**, the note wins for that photo. Notes only guide the AI; they're never written into the file.
5. **Identify:** click **Identify**. It works on the checked photos. With nothing checked, it works on the selected photos, or on all photos if none are selected.
   - **Keep existing titles** (on by default): photos that already have a title keep it, and only empty titles are filled in. If your instructions don't seem to change a title, this is usually why; the status bar says how many photos kept their title. Untick it to get new titles.
   - **Double-check against my instructions** (on by default): after the AI's first suggestion, a quick second pass checks it against About these photos, your photo notes and your direction, and fixes anything that doesn't follow them. It runs only when you've written instructions, and adds a few seconds per photo. Rows say *checked against your instructions* when it ran.
   - **Keep existing keywords** (on by default): new keywords are added after the ones already there.
6. **Review and confirm photo by photo:** click a photo in the list and it opens in the **review panel** on the right, with a large preview. There you can edit its title, keywords and photo notes; changes show in the list as you type. The panel's buttons:
   - **Save** writes this photo's title and keywords into the file straight away.
   - **Save & Next** saves this photo (if changed) and moves to the next one, so you can confirm a batch one at a time. Shortcut: Ctrl/⌘+Return.
   - **Get New AI Suggestion** runs the AI on this photo again for a fresh title and keywords. Add or change the photo note first to steer it.
   - **Revert** throws away unsaved changes and goes back to what's in the file.
   - **Previous / Next** step through the list without saving.
7. **Other ways to edit or save one photo:**
   - Double-click a Title or Keywords cell in the list to edit it directly. Keywords are separated by commas.
   - Each row has its own **Detect** button, which runs the AI on just that photo, and a **Save** button next to it, which becomes **Saved ✓** once done.
   - Right-click a photo, or a selection, for **Check / Uncheck**, **Save**, **Get new AI suggestion**, **Revert**, **Copy / Paste**, **Set photo type**, **Open photo** and **Remove from list**.
   - Copy one photo's title and keywords and paste them onto others. See **Copy and paste between photos** below.
   - Right-click a photo for **Copy photo notes**, then right-click others for **Paste photo notes**, which goes into every checked or selected photo. **Clear photo notes** empties them.
   - Double-click a thumbnail to open the photo.
8. **Save everything at once:** click **Save checked** (or press Ctrl/⌘+S) to confirm the edits on the checked photos and write them into the files. With nothing checked, the button reads **Save metadata** and saves the changed photos in the selection, or all changed photos. Either way it asks for a confirmation first.

Every saved row confirms *Saved ✓ (image verified unchanged)*.

## Filtering and searching the list

The bar above the list narrows down which photos are shown.

**Show** picks a group of photos:

| Choice | Shows |
|---|---|
| All photos | everything (the default) |
| Unsaved changes | photos with edits not yet saved into the file |
| No title | photos without a title |
| No keywords | photos without keywords |
| Fewer keywords than the minimum | photos below the keyword minimum set in Settings (30 by default) |
| AI suggestions to review | photos the AI has filled in that you haven't saved yet |
| Saved | photos whose title and keywords are saved in the file |
| Errors | photos where identifying or saving failed |
| Checked / Not checked | by the tick in the check column |
| No date taken | photos without a capture date (they get no date in their title) |
| With photo notes | photos you've written a note for |

**Search** looks in file names, titles and keywords as you type. Every word you type must appear somewhere, in any order and any case. For example, `pier sunset` finds photos with both words. Press Ctrl/⌘+F to jump to the search box. The filter and the search work together.

- The count next to the search says how many photos are shown, e.g. *Showing 5 of 40*. **Show all** clears the filter and the search.
- **The buttons only work on the photos shown.** With nothing checked or selected, **Identify** and **Save** say *shown* instead of *all*, e.g. *Identify shown (5)*. Photos hidden by the filter are never identified, saved or removed, even if they're checked; the count tells you when checked photos are hidden. **Check All** ticks only the photos shown.
- **When the list updates:** the list doesn't change while you're editing a photo. For example, giving a photo a title in the *No title* view doesn't make it vanish while you type. It updates when you change the filter or search, when a batch of Identify or Save finishes, and after bulk changes such as paste, revert and the *now* buttons.
- **Previous / Next** in the review panel skip photos the filter hides.

## Checking photos

The narrow column at the far left of the list has a checkbox for each photo. Checked photos are what the buttons work on, so you can tick the ones you want and then click around, edit and review without losing them.

- **Which photos a button works on:** the checked photos. With nothing checked, it's the selected photos, and with nothing selected, all of them. The buttons tell you which: *Identify checked (3)*, *Save checked (2)*, *Remove checked (3)*.
- **What follows the checks:** Identify, Save, Remove, Add to photos now, Add to titles now and Paste.
- **Save checked** is the "confirm edits" button. It writes the edits on the checked photos into the files. Edits on unchecked photos stay unsaved until you check them, or save them from their row or the review panel.
- **Checking several at once:** select several rows (Shift-click or Ctrl/⌘-click), then tick or untick the box on any one of them. All the selected photos follow, and they stay selected.
- **Check All / Uncheck All** are at the top of the window. Right-click a photo, or a selection, for **Check** and **Uncheck**.
- **Right-clicking a checked photo** gives a menu for all the checked photos, for example **Get new AI suggestions for 3 photos** or **Set photo type** on all of them. Right-clicking an unchecked photo gives a menu for just that photo (or the selection).
- **What still works on the selection:** the review panel always shows the photo you clicked, and the Delete key removes the selected rows.

Nothing is written to a file until you save it. That means **Save** in the review panel, a row's **Save** button, the right-click menu, or **Save metadata**. **Remove** and **Clear List** only take photos off the list; they never delete files.

### How titles and keywords are written

These rules apply to every photo unless your **Title & keyword direction** box says otherwise.

- **Title:** a descriptive, SEO-friendly title in Title Case that briefly explains what the subject is, does or is known for. Small words such as *and*, *of* and *with* stay lowercase, and names with their own capitals (iPhone, NASA) are kept as they are.
- **Keywords:** 30 to 40 unique SEO keywords, all lowercase, with letters and numbers only. The app also cleans what the AI returns:
  - Symbols are removed (*S&P 500* becomes *sp 500*, *#EV* becomes *ev*).
  - Duplicates and singular/plural pairs are removed.
  - No single word may appear in more than 3 keywords, to avoid keyword stuffing.
  - If fewer than 30 usable keywords come back, the app asks the AI once more for extras.

### Photo types

Tell the app what kind of photo it is, and the AI writes titles and keywords for that situation instead of guessing. The type is given to the AI as a fact, so the photo note only needs the name, e.g. `Tesla` with *Company logo with stock chart*. If the note is empty, the AI reads the name from the photo if it's clearly visible.

| Type | The photo is treated as |
|---|---|
| Automatic (from your notes) | decided from your note, using the table under **How your input is read** below (the default) |
| General photo (no assumptions) | an ordinary photo, described exactly as seen |
| **Copyright-free (no brands or names)** | a commercial stock photo described only in generic words. See **Copyright-free photos** below |
| Company logo with stock chart | a company logo on a smartphone screen, held in front of a computer screen showing a stock market or financial performance chart |
| Product on a store shelf | a retail product sitting on a grocery or retail store shelf |
| App in the Play Store | an app on a phone screen in the Google Play Store |
| Store or restaurant front | the outside of a store, restaurant or business, with its sign |
| Editorial: news or public event | a real news story or public event (protest, parade, festival, concert, ceremony), described factually |
| Landscape | natural scenery: terrain, sky, light, season, weather and mood |
| Beach, coast and seascape | beach, coastline, ocean or lake |
| Cityscape and skyline | city skyline, street or urban scene |
| Architecture and buildings | a building or structure: style, materials and features |
| Interior and rooms | an indoor space: home, office, hotel or venue |
| Travel and landmarks | a famous place or tourist destination, named if known |
| Aerial and drone | a view from above |
| Night and astrophotography | city lights, night sky, stars, Milky Way, moon |
| Wildlife | a wild animal or bird, with the species if known |
| Pets and farm animals | a pet or domestic animal, with the breed if known |
| Flowers, plants and gardens | flowers, plants, trees or gardens, with the species if known |
| Food and drink | the dish or drink, ingredients, cuisine and presentation |
| People and lifestyle | people in everyday situations, never named |
| Portrait | a person's expression, age range, clothing and pose, never named |
| Business and office | offices, meetings, teamwork, with business concepts |
| Technology and devices | computers, phones, screens and gadgets |
| Sports and fitness | the sport or activity and the action |
| Cars and transportation | cars, trucks, trains, planes, boats, bikes |
| Industry and construction | manufacturing, construction, machinery, work sites |
| Farming and agriculture | fields, crops, harvest, farm machinery |
| Holidays and celebrations | Christmas, Halloween, birthdays, weddings and so on |
| Abstract, texture and background | colours, patterns and materials, and use as a background or copy space |
| Your own types | whatever you describe in **Settings → Your own photo types** |

To add your own types, open **Settings** and write one per line as `Name: what the photo shows`. For example:

```
Restaurant storefront: the exterior of a restaurant with its sign visible from the street
Crypto coin on keyboard: a physical cryptocurrency coin lying on a laptop keyboard
```

Choose the default type in the Instructions panel. Any photo can override it with its own Type.

### Copyright-free photos

Choose **Copyright-free (no brands or names)** as a photo's type, or as the default type, for commercial stock photos where no copyrighted words or names may appear:

- The AI is told that the title and keywords must contain no brand, company, product, model or app names, no logos or trademarks, no copyrighted characters or titles of films, books, songs or artworks, and no names of people. Anything like that in the photo is described in generic words instead, for example *electric car*, *smartphone*, *soda can* or *plastic building blocks*.
- This applies even to names in your photo notes. The AI uses them to understand the photo but doesn't use them in the title or keywords.
- Plain place names such as cities, countries and natural features are allowed.
- The **Double-check** pass always runs for these photos, to catch any name the first pass let through.
- As a safety net, keywords containing well-known brand, product or character names (Nike, Coca-Cola, iPhone, Lego, Disney and so on) are removed, including any already on the file. Words that are also ordinary words, such as apple or amazon, are left alone.
- If a known brand name is still in the title, the row's status says *check the title, it may name a brand*.
- Your own **Keywords to put first** are always kept as you typed them.

### Keywords to put first

Type keywords in **Keywords to put first**, separated by commas, and they go at the front of every photo's keywords:

- They come before the AI's suggestions.
- They're kept exactly as you typed them, so capitals and dashes in names and dates aren't changed.
- They're added on top of the 30 to 40 suggested keywords.
- The AI is told about them, so it can use them as information (such as the place) but won't repeat them.

These tokens are filled in from each photo's own capture date (from the camera's EXIF data):

| Token | Becomes |
|---|---|
| `{year}` | 2026 |
| `{month}` | October |
| `{date}` | 2026-10-26 |

For example, `Durham, North Carolina, {year}, {month} {year}` gives *Durham, North Carolina, 2026, October 2026*. A photo with no capture date skips the keywords that contain a token.

The keywords are added automatically when you click **Identify**. To add them without running the AI, for example to tag a folder with its location, click **Add to photos now**. It works on the checked photos, or else the selected photos, or else all photos. Nothing is written until you save.

### Text before titles

Type text in **Text before titles** and it's put in front of every title the AI suggests:

- It's kept exactly as you typed it. For example, `Editorial:` turns *Tesla Logo on a Smartphone…* into *Editorial: Tesla Logo on a Smartphone…*.
- `{year}`, `{month}` and `{date}` are filled in from each photo's capture date. `Durham, {year}:` gives *Durham, 2026: …*, or *Durham: …* for a photo with no capture date.
- The AI never sees this text. It writes a complete title for the photo on its own, and the app puts your text in front afterward. When you re-identify a photo, your text and the date are taken off its current title before the AI sees it. It's never added twice.
- Titles kept because **Keep existing titles** is on are left alone.

To add the text to titles that already exist without running the AI, click **Add to titles now**. It works on the checked photos, or else the selected photos, or else all photos with a title, and adds the date taken too if you've switched that on.

### How a title is put together

With everything switched on, a title is built in this order (the editorial format):

| Your text | | Location | between location and date | Date | between date and title | AI's title |
|---|---|---|---|---|---|---|
| Editorial: | *space* | Durham, North Carolina, USA | ` - ` | October 26, 2026 | `: ` | Pier at Sunset |

= *Editorial: Durham, North Carolina, USA - October 26, 2026: Pier at Sunset*

- Each piece is optional. Leave a box empty, or set the date to *Don't add the date*, and that piece and its separator are left out.
- **Between location and date** (on the Location row) is the character between the place and the date: hyphen `-` (the default, as Shutterstock asks), dash `–`, comma, bar `|` or just a space. It's greyed out unless both a location and a date are used.
- **Between date and title** (on the Date row) is the character between the date and the AI's title: colon (editorial), dash, comma, bar, space or parentheses. Its label changes to say what it sits between for your choices, e.g. *Between location and title* when the date is off, or *Between title and location* when the date goes after the title.
- The **Title preview** shows the result with each piece in its own colour and the separators highlighted, so you can see exactly which characters and spaces go where. A key underneath explains the colours. When one photo is selected, the preview uses that photo's own date.
- **Editorial style** sets everything to the format above in one click.

### Location in title

Type where the photos were taken in **Location in title**, e.g. `Durham, North Carolina, USA`. The box sits to the left of the date settings because that's the order in the title: it goes in just before the date taken, the way editorial titles are written:

*Durham, North Carolina, USA - October 26, 2026: Pier at Sunset*

- **Previously used locations:** the box is also a dropdown. Every location you use is remembered (up to 20, most recent first), so you can pick it again instead of retyping. The same place typed with different capitals or spacing is only listed once. Pick **Clear this list…** at the bottom to start over.
- Click **Editorial style** to set that format in one go: hyphen between location and date, the date written *October 26, 2026* just before the AI's title, then a colon.
- It works with **Text before titles**: `Editorial:` gives *Editorial: Durham, North Carolina, USA - October 26, 2026: Pier at Sunset*.
- The location always stays just before the date, wherever you put the date. If the date is off, or a photo has no capture date, the location goes before the title: *Durham, North Carolina, USA: Pier at Sunset*.
- **AI uses it** (the checkbox next to the box, on by default) decides whether the AI is told the location:
  - On: the AI knows where the photo was taken and uses the place in the keywords. It's told not to put the place in its own title, so it isn't there twice.
  - Off: the AI isn't told the location, so the keywords come from the photo and your other notes only. The location is still put into the titles.
  - Either way, a trailing *in Durham, North Carolina* is removed from the AI's title.
- **Add to titles now** adds the location to existing titles too. A title that already has the date gets the location put in front of that date, not a second date.
- The location, like your other text, is never added twice and is taken off before a photo is re-identified.

### Date taken in title

The **Date taken in title** row adds each photo's capture date (from the camera's EXIF data) to its title. Choose three things, and the preview under the row shows the result. When one photo is selected, the preview uses that photo's own date.

**Where:**

| Choice | Example |
|---|---|
| Don't add the date | Editorial: Pier at Sunset |
| At the very start (before your text) | October 26, 2026 – Editorial: Pier at Sunset |
| Just before the AI's title | Editorial: October 26, 2026 – Pier at Sunset |
| After the AI's title | Editorial: Pier at Sunset – October 26, 2026 |

**Custom date:** tick **Custom date** on the Date row and type a date, or click the arrow to pick one from a calendar. Every photo then uses that date instead of the date in its metadata. Use it when the camera's clock was wrong, or for photos with no date.

- It's used for the date in titles and for `{year}`, `{month}` and `{date}` in **Text before titles** and **Keywords to put first**.
- The preview says *with your custom date* while it's on.
- It's remembered between sessions. While it's on, the status bar reminds you when the app starts.
- Changing the custom date, or switching it off, and then clicking **Add to titles now** replaces the old date in existing titles. You never end up with two dates.
- The **No date taken** filter still goes by the photos' own metadata.
- It doesn't change the date stored in the photo files. The app only writes titles and keywords.

**Format:** October 26, 2026 · Oct 26, 2026 · 26 October 2026 · 2026-10-26 · 10/26/2026 · 26/10/2026 · 26.10.2026 · October 2026 · 2026 · Monday, October 26, 2026

**Between date and title:** a colon, e.g. *October 26, 2026: Pier at Sunset* (editorial), a dash (–), a comma, a bar (|), just a space, or parentheses, e.g. *Pier at Sunset (October 26, 2026)*.

- It works with or without **Text before titles**.
- Photos with no capture date get no date and no leftover separators.
- The date is never added twice, and the AI is told not to put a date in its own title.
- It's applied when you click **Identify** (for new titles) and by **Add to titles now** (for existing titles).
- If you need the date in a spot this doesn't cover, put `{year}`, `{month}` or `{date}` inside **Text before titles** instead.

### Copy and paste between photos

- **Copy:** select one photo and press Ctrl/⌘+C, click **Copy** in the review panel, or right-click → **Copy title & keywords**. The clipboard gets the title on the first line and the keywords, separated by commas, on the next, so you can also paste it into any other app.
- **Paste:** check or select one or more photos and press Ctrl/⌘+V, or click **Paste** in the review panel. This replaces their title and keywords. The right-click menu also has:
  - **Paste title only**
  - **Paste keywords only (replace)**
  - **Add pasted keywords**, which keeps the photo's existing keywords and adds the pasted ones after them. Shortcut: Ctrl/⌘+Shift+V.
- **Text from elsewhere** works too: the title on the first line and keywords after it, or lines starting with `Title:` and `Keywords:`. A single line with several commas is treated as keywords only.
- **Photo files:** copy photos or folders in Finder or Explorer, then press Ctrl/⌘+V in the app to add them to the list.

While you're typing in a text box, Ctrl/⌘+C and Ctrl/⌘+V copy and paste text as usual. Pasted titles and keywords aren't written to the files until you save.

### How your input is read

With the photo type set to **Automatic**, your input (the photo note, plus the **About these photos** box) is read like this:

| You type | The photo is treated as |
|---|---|
| Only a company name, e.g. `Tesla` | the company logo on a smartphone screen, held in front of a computer screen showing a stock market or financial performance chart |
| Only a retail product name, e.g. `Honeycrisp apples` | the product sitting on a grocery or retail store shelf |
| A name followed by `app`, e.g. `Spotify app` | the app on a phone screen in the Google Play Store |
| Anything else | accurate information about the photo |
| Nothing | the AI describes what it sees |

If neither your input nor the photo contains any brand, trademark or identifiable name, none are added.

The full title is always saved in XMP. The older IPTC Title field holds only 64 bytes, so a long title is shortened there at a word break, and the review panel tells you when that happens.

### Speed

Photos are processed one at a time on your computer. The first one in a batch
takes longer while the model loads. After that, expect a few seconds per photo
on a recent Mac (Apple silicon) or a PC with a graphics card, and longer on
older machines. The status bar shows the average time per photo.

**Stopping:** while the AI works on a photo, that row's **Detect** button turns into **Stop**. Click it to stop that photo immediately: the request is cut off mid-way, and Ollama stops working on it. **Stop** in the status bar does the same for every photo at once, including those waiting in line.

**Time limit:** if the AI takes longer than 30 seconds on a photo, it's stopped and tried once more automatically. If it goes over again, the photo is skipped with a message, so one stuck photo can't hold up the batch. The first photo of a session gets 90 seconds extra, because Ollama has to load the model first. Change the limit, or what happens after it, in **Settings → Time limit per photo**.

The window keeps responding while the AI works, so you can review, edit and save other photos in the meantime. The photo being worked on shows a live count, e.g. *The AI is looking at this photo… 12 s*, and photos queued behind it say *Waiting in line…*. The first photo of a session also says it's loading the model.

If the whole computer feels slow while the AI runs, the model is using most of its processor. That's normal without a graphics card. A smaller model in **Settings** is faster and lighter.

### Settings

- **Model to use:** any vision model you've downloaded.
- **Time limit per photo:** 30 seconds by default, then *try once more*, *try twice more* or *skip the photo*. Set it to *No limit* to let the AI take as long as it needs. Raise it if your computer is slow and photos keep getting stopped.
- **Keywords per photo:** from 30 to 40 by default. Your existing keywords and your keywords to put first are always kept; suggestions are added up to the maximum.
- **Your own photo types:** one per line, as `Name: what the photo shows`.
- **ExifTool location:** leave blank to find it automatically.
- **Ollama address:** only change this if Ollama runs on another computer.
- **Troubleshooting → Debug mode:** writes a detailed log of every AI request to `debug_log.txt`. See **Debug mode** below.

### Debug mode

If some photos hang, fail or come out wrong, turn on **Debug mode** in **Settings → Troubleshooting**, then detect a few photos, including some that cause trouble. The log records:

- each photo's file size, the size of the image sent to the AI, and your options
- each request to the AI and how long it took, with Ollama's own figures: time spent loading the model, tokens read and written, and why the reply ended
- the start of each reply, and the whole reply when something went wrong
- every error, Stop, time-out and retry

Click **Open log** to see it, or find `debug_log.txt` in the settings folder: `%APPDATA%\IPTC Tagger` on Windows, `~/Library/Application Support/IPTC Tagger` on a Mac. Send that file when reporting a problem. Your photos and their text aren't in it; only file names, sizes, timings and the AI's replies are. It keeps itself under 3 MB, and **Clear log** empties it. Turn Debug mode off again afterwards.

**Replies that run away:** now and then a model asked for JSON gets stuck writing endless blank space and never finishes. Replies are capped at about three times the normal length, so a runaway reply ends in seconds instead of hanging. The photo is then asked again automatically, and the log shows it as *cut off at the length cap*.

## What gets written

| Field | XMP | IPTC |
|---|---|---|
| Title | `dc:title` | `ObjectName` (Title in most apps) |
| Description (optional, on by default) | `dc:description`, set to the title | `Caption-Abstract` |
| Keywords | `dc:subject` | `Keywords` |

IPTC is marked as UTF-8, so accents and non-Latin text are stored correctly.

The IPTC standard limits Title and each keyword to 64 bytes. With the XMP-to-IPTC conversion on, the full text is copied to IPTC anyway, because it uses ExifTool's `-m` option exactly like your batch file. That's what Shutterstock reads. With the conversion off, the app trims IPTC to the limits and XMP keeps the full text.

## Saving for Shutterstock: XMP to IPTC

After every save, the app runs the same ExifTool conversion as your `Convert_Metadata_Exiftool_Shutterstock.bat`, one photo at a time:

```
exiftool -overwrite_original -P -m -charset iptc=utf8 -codedcharacterset=utf8 -tagsFromFile @ -@ xmp2iptc.args PHOTO
```

It runs on every way of saving: a row's **Save** button, **Save** and **Save & Next** in the review panel, the right-click menu, and **Save metadata**. Rows then say *Saved ✓ (image verified unchanged) · IPTC synced*.

**Description:** Shutterstock has no separate title field; it uses the **Description** (caption) as the title. So the app also saves your title as the Description by default, both in XMP and in IPTC Caption-Abstract. This replaces any existing description.

**Where the rules file comes from:** the conversion uses ExifTool's `xmp2iptc.args`, looked for in this order:
1. the file you chose in **Settings**
2. next to ExifTool, as in your batch file (e.g. `C:\Users\Home\Downloads\exiftool-13.59_64\xmp2iptc.args`)
3. ExifTool's own `arg_files` folder
4. the unchanged copy bundled with this app, in `arg_files` (ExifTool is free software by Phil Harvey, [exiftool.org](https://exiftool.org))

The app also finds ExifTool itself in an `exiftool…` folder in your Downloads, so a setup like yours works without changing Settings.

Both options can be switched off in **Settings → When saving**.

## Notes

- If something unexpected goes wrong, the app shows a message and saves the details to `error_log.txt` in its settings folder: `%APPDATA%\IPTC Tagger` on Windows, `~/Library/Application Support/IPTC Tagger` on a Mac. Send that file along when reporting a problem.

- Only JPEG files are handled; other files in a dropped folder are skipped and counted.
- The AI won't identify real people from their appearance. Put names in your instructions or photo notes if you want them used.
- Local models can occasionally misidentify things, so always review before saving. A larger model, or clearer instructions, usually helps.
- If a photo shows an error, select it and click **Identify** again.

## Building it yourself

The [release build](.github/workflows/release.yml) runs on GitHub. It builds the program with PyInstaller on GitHub's Windows and Mac machines, adds the latest ExifTool on Windows, and attaches the zips to a release when a version tag (like `v2026.10.27`) is pushed. To build on your own computer: `pip install -r requirements.txt pyinstaller`, then `python build_standalone.py --out build-output`. To run from the source without building: `python iptc_tagger.py`.
