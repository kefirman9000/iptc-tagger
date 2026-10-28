# What's new

## 2026.10.27.23

- **No more stray Frito-Lay:** the AI's instructions had real brand names as examples, and the AI sometimes copied them into titles that had nothing to do with them. The examples are now placeholders (*[Brand], a [Parent Company] Brand*), and the AI is told to name a brand or parent company only when it's really in the photo or your notes, and only when it's sure who owns it.

## 2026.10.27.22

- **One box for your instructions:** *About these photos* and *Title & keyword direction* are now one box in **Instructions for the AI**. Write the facts (place, event, client, people's roles, species…) and how titles and keywords should be written together. The AI uses the facts and follows the directions, and the directions still take priority over the defaults. Anything you had in the two boxes is joined into the new one.

## 2026.10.27.21

- **Keywords stay out of titles:** now and then the AI wrote its keyword list inside the title (*…Maintenance", "Keywords": "Car Care Products", …*). That's now caught: the title is cut back to the real title, and those keywords go into the keywords where they belong.

## 2026.10.27.20

- **Easier to read:** the links on the blue update banner (*Yes, download it*, *Restart now*…) are now dark, bold blue instead of a light blue that blended into the background.

## 2026.10.27.19

- **Simpler, faster saving:** each photo is now saved in one step. The full title and keywords go straight into the IPTC fields, past IPTC's old 64-character limit, so IPTC matches XMP without a second pass. The separate *Copy XMP to IPTC* step is now off. You can still turn it on in **Settings → Saving** if you also want other XMP fields (creator, copyright…) copied to IPTC.

## 2026.10.27.18

- **Retry when nothing comes back:** if the AI returns no title or keywords for a photo, it's tried again automatically, the same number of times as for the time limit (**Settings → AI**).

## 2026.10.27.17

- **No weights or sizes:** titles and keywords no longer include the weight or volume from the packaging (grams, oz, fl oz, ml, liters, pounds…). The brand, product, flavour and parent company are still named.

## 2026.10.27.16

- **Keywords by relevance:** with *Editorial* on, the location and date keywords no longer always come first. The keywords that name what's in the photo lead, the place (*tokyo*, *japan*) comes right after them, and the month and year go at the end. *Before / After the title* now only moves the place and date in the title. Your *Keywords to put first* still come first. **Add to titles now** re-sorts photos you've already done.

## 2026.10.27.15

- **Don't ask again:** the *Save metadata* question now has a **Don't ask me again** box. Tick it to save straight away from then on. Turn the question back on in **Settings → Saving**. Every file is still checked after saving, as before.

## 2026.10.27.14

- **Reads the label first:** before writing a title, the AI takes a quick first look that only copies the text it can read on the product, packaging or sign: brand, product, flavour or variety, size and parent company. The title and keywords then use those exact spellings. Turn it off in **Settings → Titles & keywords → Labels**.
- **Tidier keywords:** keywords nobody searches for (*photo*, *image*, *stock*, *high quality*…) and long phrases are removed, and the keywords that name what's in the title come first, because stock sites weigh the first ten the most. Keywords from your own instructions are always kept.
- **Undo a save:** right-click a photo → **Undo last save** puts its title, keywords, description, headline and location back to what was in the file before. The last 5 saves of each photo can be undone, even after you restart the app. The picture itself is never touched.

## 2026.10.27.13

- **Updates ask first:** when a new version is published, the app asks whether to download and install it. Nothing is downloaded until you click **Yes, download it**. It's then installed when you restart or close the app. To skip the question, tick **Download new versions without asking first** in **Settings → General**.

## 2026.10.27.12

- **More specific titles:** for products, titles name the brand, the exact product and its variety, flavour, scent or size from the packaging, e.g. *Doritos Cool Ranch Flavored Tortilla Chips* rather than *Bag of Chips*. Brands, stores and restaurants get their well-known parent company too (*a Frito-Lay and PepsiCo Brand*, *Owned by Yum! Brands*), and these go into the keywords as well. *Copyright-free* photos still leave all names out.

## 2026.10.27.11

- **Descriptive titles, without filler:** titles describe more of the scene itself (the style, signs, surroundings, people and objects) instead of adding the time of day. The time of day, light or weather is only mentioned when it really stands out, such as a sunset sky or a city at night.

## 2026.10.27.10

- **Automatic updates:** new versions now download by themselves in the background. Click **Restart now** to install, or it installs when you close the app. You can turn this off in **Settings → General**.
- **Fuller titles:** titles now say what the subject is or is known for, the setting and more of the scene, e.g. *McDonald's Restaurant Exterior, Major American Fast Food Chain, with Drive-Thru Lane and Parking Lot*. A title that comes back too short is sent back once to be filled out. Choose **Short**, **Standard** or **Detailed** in **Settings → Titles & keywords**.
- **Redo the selected photo:** *Redo with reasons* now acts only on the photo you clicked. To redo every checked photo, use **Redo Checked…** in the toolbar or the right-click menu.
- **Detect everywhere:** one name for the AI action, *Detect*, in the toolbar, the menus and the review panel.
- **Simpler:** *General photo* is merged into **Automatic**; the CSV *Editorial* column follows the Editorial checkbox; errors and debug details go to one log file.

## 2026.10.27.9

- **Editorial checkbox:** the *Editorial style* button is replaced by a checkbox, **Editorial (include location and date)**, at the start of the location row. Ticked, the location and the date taken are added to every title and to the keywords (e.g. tokyo, japan, october 2026, 2026). Unticked, neither is added anywhere.
- **Before or after:** choose whether they go **before** the AI's title and keywords (*Tokyo, Japan - October 26, 2026: Title*) or **after** them (*Title, Tokyo, Japan - October 26, 2026*).
- **Add to titles now** also adds the location and date keywords to photos you've already done.

## 2026.10.27.8

- **New column order:** in the photo list, **Status** now comes before **Type**, and the **Detect** and **Save** buttons come before **Photo notes**. The text you edit (notes, title, keywords) now sits together on the right.

## 2026.10.27.7

- **More redo reasons:** *Redo with reasons* now has a second column, **What to focus on**: the words and signs in the photo, the location, the mood, the main subject, the action, the colours and light, the people, the concept for commercial buyers, or the season. There are new problems to pick too: too flowery, not factual enough, and keywords too general. Hover over a reason to see exactly what the AI is told.

## 2026.10.27.6

- **More metadata fields:** the title is now also saved as the **Headline**, and your *Location in title* fills the photo's **City, State and Country** fields, which editorial reviewers check. Windows title and tags and the EXIF description are available too.
- **Choose the fields:** pick exactly which fields are written in **Settings → Saving**. The usual ones are ticked.
- **Settings pages:** Settings is organised into pages: AI, Titles & keywords, Saving, Photo types and General.
- **CSV export:** export the photos to a spreadsheet in Shutterstock or Adobe Stock upload format, or with every detail.
- **Redo with reasons:** not happy with a suggestion? Choose what's wrong (too generic, wrong subject, too long…) and the AI tries again with that in mind.
- **What's new:** this window, shown once after each update. Open it again from **Settings → General**.

## 2026.10.27.5

- **Update check:** when a new version is published, the app says so at startup, with a link to download it. You can turn this off in Settings.

## 2026.10.27.4

- **Symbols:** symbols in the AI's titles and keywords are converted to words (& → and, % → percent) or removed, as you choose in Settings.

## 2026.10.27.3

- **Calmer title preview:** no more highlighted separators. Bold marks the AI's title, and blue marks the location and date.
- **Type lists:** scrolling over a photo's Type no longer changes it unless its list is open.

## 2026.10.27.2

- Clearer wording throughout, and new example text.

## 2026.10.27

- First portable release: unzip and run, with nothing to install.
