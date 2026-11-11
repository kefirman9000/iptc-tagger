# What's new

## 2026.11.11.5

- **Dreamstime's fixes go into its CSV only:** the Dreamstime CSV now gets the tidied text (long titles cut back to their main phrase, capitals in normal case, descriptions that stop half-way ended cleanly, keyword repeats left out), while your photos keep the full text that Shutterstock and the other sites read. Check Before Upload no longer lists those for Dreamstime, since the CSV takes care of them; the must-fix ones (one-word title, a description that only repeats the title, keyword count) are still listed. To fix the photos' own text instead, untick **Settings → Stock sites → Tidy the text for Dreamstime in its CSV only**.

## 2026.11.11.4

- **Stricter checks for Dreamstime, with fixes:** when you sell on Dreamstime, **Check Before Upload** (and opening the Upload window) now also checks what Dreamstime refuses more readily than Shutterstock:
  - **Long titles** (over about 150 characters) read as keyword stuffing. **Fix** cuts the title back to its main phrase at a comma or before "for…" / "with…", keeping the editorial place and date in front.
  - **Words in capitals** ("THE EYE OPENER"): Fix writes them in normal case (acronyms such as USA and BBQ stay).
  - **Descriptions that stop half-way** ("…gift options, including prints, calendars."): Fix ends them at the last whole phrase.
  - **The same keyword twice** in another spelling ("hungry man" / "hungryman", "hershey's" / "hersheys"): Fix keeps the first.
  - Must fix: a one-word title, a description that only repeats the title, fewer than 7 or more than 50 keywords, a title over 250 characters.
  
  The button is now **Fix These**. Fixes aren't saved until you save, and the text before them is kept under Earlier suggestions.
- **Descriptions are no longer cut in the middle of a list** when they're shortened to fit 200 characters.

## 2026.11.11.3

- **AI markers are spotted:** when photos come in, the app looks for anything in the metadata that tells a stock site AI made or edited the photo: Lightroom AI masks (Select Subject, Sky or People), Lightroom AI Denoise / Super Resolution, an AI *Digital Source Type*, Content Credentials (C2PA), and software such as Firefly or Generative Fill. A photo with one says so in its status, **Show → AI markers in the file** lists them, **Check Before Upload** warns, and **Start upload** names them before sending.
- **Lightroom's develop settings come out of JPEGs at save:** Lightroom exports its whole edit into the JPEG, AI masks included, and stock sites can read that as "made with AI". Saving a JPEG now takes those develop settings out; the picture, title, keywords and camera details stay, and the picture is still checked unchanged. A JPEG carrying a Lightroom AI mask shows as unsaved until you save it. RAW files and their .xmp sidecars are never touched, so your Lightroom edit is safe. Undo can't put the develop settings back. Turn it off in **Settings → Saving**.

## 2026.11.11.2

- **The Dreamstime CSV is sent by itself:** after an upload to Dreamstime, the app makes the Dreamstime CSV straight away and sends it to your Dreamstime FTP folder as soon as Dreamstime has taken the photos in. It knows because Dreamstime moves each photo out of the FTP folder once it's in the Unfinished section, and the app looks every 2 minutes. If they're still waiting after an hour, it's sent anyway. It carries on with the Upload window closed; keep IPTC Tagger open until it's sent (closing asks first). **Send Dreamstime CSV Now** still sends it straight away. Turn it off in **Settings → Stock sites**.
- **Fix:** sending the Dreamstime CSV now follows the *Only encrypted connections* choice of the Upload window.

## 2026.11.11.1

- **Fix: the Dreamstime CSV gets to Dreamstime:** Dreamstime only reads its CSV from your Dreamstime **FTP folder**, never from its website, and only fills in photos that are already in its *Unfinished* section. The app made the CSV but had no way to put it there and didn't say so. Now:
  - After **Export CSV…** makes a Dreamstime CSV, click **Send to Dreamstime** to put it in your Dreamstime FTP folder, using the Dreamstime login you upload with.
  - After uploading photos to Dreamstime, the Upload window has a **Send Dreamstime CSV** button that makes the CSV for those photos and sends it.
  - Send it once the photos show up in Dreamstime's *Unfinished* section (it can take a while): sending too early does nothing. It fills in the titles, descriptions, keywords, three categories and editorial.

## 2026.11.10.5

- **Brands spelled your way, everywhere:** a new **Settings → Brands** page lists each brand as *right spelling = wrong spellings | other ways buyers search for it*, e.g. `BJ's = BJs, Bjs | bjs` or `Coca-Cola = Coca Cola | coca cola, coke`.
  - The wrong spellings are fixed in titles, descriptions and keywords, in any case, with or without the apostrophe, hyphen or accent: *Kelloggs* → *Kellogg's*, *Haagen Dazs* → *Häagen-Dazs*, *Wal-Mart* → *Walmart*.
  - The search spellings and abbreviations (*bjs*, *heb*, *711*, *coke*) are added to the keywords right after the brand, while there's room.
  - It starts with about 45 common store and grocery brands. Add your own, and **Add the Built-in Brands** brings back any you removed.
  - It runs after every Detect, Reword and Fix common problems.
- **The first ten keywords are the ones that matter:** stock sites (Adobe Stock in particular) weigh them the most. After every Detect, Reword and Fix common problems, the keywords naming the title's subject and brand go first, specific before general. General words (*food*, *shelf*, *retail*, *aisle*…) go after them. Your keywords to put first stay first, the place stays right after the subject, and the month and year stay last. You can turn it off in Settings → Titles & keywords.
- **Look-alike photos:** the app compares the pictures themselves (not just the titles) and finds the same shot taken twice. *Show → Look-alike photos* lists them, and Check Before Upload warns you, because Shutterstock and Adobe reject near-duplicates.
- **Check Before Upload:** a new **Check** button at the bottom, which also runs by itself when you click **Upload…**, lists everything a site would reject or that would hurt sales, worst first:
  - **To fix:** no title or keywords, not saved, title length and keyword counts per site, a picture too small (Shutterstock and Adobe Stock 4 MP, Dreamstime 3 MP, Alamy 17 MB uncompressed, about 6 MP) or too large (Adobe Stock 100 MP, 45 MB; Shutterstock 50 MB), and an editorial caption without Shutterstock's *City, Country - Month Day, Year:* format.
  - **To check:** model and property releases, look-alikes, brand spelling and keyword order, plus a reminder about Adobe's Illustrative Editorial.
  - **Buttons:** **Fix Spelling & Keyword Order**, **Save**, **Check Just These** (ticks only the photos listed) and **Upload Anyway**. Double-click a line to go to that photo.
- **One step for a colour group:** right-click a colour dot above the list, or use the **Green group ▾** button that shows while one colour is shown. It offers *Note, type and Detect…* (one note and photo type for every photo with that label, then Detect them with new titles), Detect, Reword, Check before upload, Save, Upload… and *Check only these*.
- Pop-up messages no longer vanish right after appearing when one comes soon after another.

## 2026.11.10.4

- **Colour labels, as in Lightroom:** give photos a red, yellow, green, blue or purple label to sort them into groups (one shoot, one store, the ones to redo…):
  - Keys **6, 7, 8, 9** label the selected photos red, yellow, green or blue; press the same key again to take it off. Right-click → **Colour label** has all five, and the side panel has a dot for each next to the file name. On a checked photo, the label goes to every checked photo.
  - The label shows as a coloured frame round the thumbnail.
  - **The coloured dots above the list** show only that group (click again for all), and *Show* has *With a colour label*, *No colour label* and each colour. Then **Check All** ticks just that group, and Detect, notes, type, Reword, Save, CSVs and Upload all work on it.
  - Labels you set in Lightroom come in with the photos, and Save writes a label you changed here into the file (XMP Label), so Lightroom and Bridge see it too.
- **Ticking several photos at once:** with several photos selected, a click on any of their check boxes ticks (or unticks) all of them, and the selection stays. **Space** does the same. A click anywhere in the check cell now counts, not just on the little box.
- **Check All and Uncheck All moved** next to *Show: All photos*, right above the list.
- A long message in the bottom bar no longer makes the window wider than a laptop screen.

## 2026.11.10.3

- **CSV names have the time too:** *shutterstock_2026-11-10_08-18.csv*, so two exports on the same day can't be mixed up. If one with that name is already there (the same minute), the new one is saved as *(2)*, *(3)*…: an earlier CSV is never replaced. This applies to Make Shutterstock CSV, Export CSV for every site, and the single-file export.
- **Adobe Stock CSV, the way Adobe asks:** titles are plain text, without commas, at most 70 characters (cut at a whole word), and without the editorial location and date in front, because Adobe doesn't want editorial-style captions. Keywords are made plain too.
- **Country filled in:** the 123RF CSV's country column, and Pond5's city, state and country, now come from the photo's location (*Tokyo, Japan* → Japan). *Austin, TX* or *Austin, Texas* is read as a US state, with United States as the country, for the CSVs and for the State / Country saved in the file.
- **What's left to do on the sites:** after making CSVs, the message lists what a CSV can't do:
  - **Model release:** photos not marked editorial that seem to show people. Attach a release on each site, or make them editorial.
  - **Property release:** pets, artwork or a home interior. Adobe Stock in particular asks for these.
  - **Adobe Stock editorial:** Adobe only takes editorial as *Illustrative Editorial* (an account with 100+ downloads, no recognisable people), marked on its upload page.
  - **Country:** photos with no location, so their country column is empty.

## 2026.11.10.2

- **Photo notes up front:** the **Photo notes** column now comes right after the file name (your own column order is moved once to match), and empty cells say *Add a note for the AI…*. In the side panel the notes sit right under the photo.
- **One note for all checked photos:** type a note on a checked photo and every checked photo gets it (the same goes for Avoid). In the side panel, **Use for N checked** gives the note shown to every checked photo.
- **Notes now change the title:** a photo whose note is new since its title was written gets a new title when you Detect it, even with *Keep existing titles* on. **Detect Again** on one photo always writes a new title. Until a note has been used, the side panel says *Not used yet: Detect Again*.
- **Saved or not, at a glance:**
  - The side panel says it in one coloured line: *● Not saved yet: 2 changes only in this list. Save now*, or *✓ Saved to the file at 7:52*.
  - Each edited box (title, description, keywords) gets an orange edge and *● edited* next to its name until it's saved.
  - In the list, an orange dot by the file name means unsaved and a green tick means saved, with the time in the Status column.
  - At the bottom, **● 3 not saved yet · Save all** saves them in one click, then turns into *✓ All changes saved to the files*. The window title shows the count too.
- **Instructions for the AI take far less room:** the box is now four tabs, about a third of the height: *Instructions for the AI* (with the photo and text models), *Editorial titling* (all of it, as before), *Keywords & title text* and *Options*. The tab names show what's on (*Editorial titling: Tokyo, Japan*). The profile stays beside the tabs. The tab you were on is remembered.
- **Smaller side panel:** the counts sit next to each field's name instead of on lines of their own, the description and keywords boxes are shorter (they scroll), and the preview scales to fit. Before, the preview could push the panel wider than its space and cut things off on the right.
- **Photo type, lighter:** the list's Type column is a short label (*Default*, *Product on a store shelf*) instead of a full-width dropdown in every row. Click it to choose, and on a checked photo the choice goes to every checked photo. In the side panel it's one small *Type* row, and the default type is under *Options*.

## 2026.11.10.1

- **Dark mode:** the app now follows Windows' light or dark setting, and switches by itself when Windows does. To choose one, use **Settings → Appearance** (*Follow Windows*, *Light*, *Dark*). The banners, the Upload window and the drop area have dark versions too.
- **New icons:** every button has an icon from one modern set (Lucide), drawn to suit light and dark, instead of the mix of old built-in icons.
- **Clearer buttons:** **Detect**, **Save Metadata** and **Upload** are the solid blue buttons; everything else is plain until you point at it, so the main steps stand out.
- **Progress in each row:** a photo being detected, reworded or saved shows a thin moving bar in its Status cell, so you can see at a glance which ones are being worked on.
- **Pop-up messages:** when a batch finishes (*Detect finished: 12 photos*), an upload is done or CSVs are saved, a short message pops up at the bottom right and fades away. It turns yellow-edged if something had a problem. A click hides it.

## 2026.11.09.10

- **Categories for every stock site, picked for you:** each photo now gets its Shutterstock, Adobe Stock and Dreamstime categories, shown in the side panel (*Categories: Shutterstock: Food and drink, Objects · Adobe: Drinks · Dreamstime: Commercial, Food & Beverages*). The AI picks the Adobe Stock category along with the Shutterstock ones; for photos detected earlier they're worked out from the title and keywords (StockUp's category picker), and categories the photo gives no sign of are left out. **Make Shutterstock CSV** no longer asks you for categories.
- **A CSV for each stock site:** **Export CSV…** is now one window: tick Shutterstock, Adobe Stock, Dreamstime, Depositphotos, 123RF, Pond5 or *All details*, choose the folder, and each gets its own CSV in the format that site asks for, with its categories filled in and the date in the name (*dreamstime_2026-11-09.csv*). Your choice of sites is remembered.
- **More agencies to upload to:** besides Shutterstock, Adobe Stock, Dreamstime, Alamy, Depositphotos, 123RF and Pond5, **Upload…** now sends to **Freepik (Magnific)**, **Vecteezy**, **Yay Images** and **Colourbox**, which show their upload server on your contributor page (copy it into Logins once), and to **any other agency with FTP**: the two *Other agency* slots take a name and the server details. Agencies such as iStock, Canva and Pixta only take uploads through their websites.
- **Logins, organised:** **Logins…** groups the agencies (*known upload server*, *server on your contributor page*, *your own*), with server, port and FTP / FTPS / SFTP for the ones that need them. The Upload window only lists agencies you've added a login for, so it stays short.
- Example locations in the notes and tests are now Tokyo, Japan.

## 2026.11.09.9

- **Fits a laptop screen, with a photo selected too:** the side panel now scrolls when the window is shorter than everything in it. Before, selecting a photo made the window grow to about 1,100 pixels tall, taller than a laptop's screen. At 1366 × 768 everything now fits.
- **Title and Keywords come first in the list:** right after the file name, so they're in view on a laptop, with the status, the Detect / Save buttons, type and notes after them. Drag a column heading to put the columns in your own order: it's remembered.
- **The Instructions fold into one line:** **Hide Instructions** now leaves a summary of what's in use (profile, instructions, Editorial location, model) with an **Edit Instructions** button, so the photo list gets the screen. It's folded to start with on a smaller screen, and remembered.
- **One way to upload:** **Upload…** is now the main blue button at the bottom right, where you finish a batch. *Send to StockUp* is still there, as one **Open in StockUp** button with a menu (the checked photos, or all).
- **No more squashed buttons in the side panel:** Detect, Redo with Reasons, Reword, Revert, Copy and Paste wrap onto a second line in a narrow panel instead of being cut off.

## 2026.11.09.8

- **Upload to your stock sites:** a new **Upload…** button sends the checked photos (or the selected, or all shown) to Shutterstock, Adobe Stock, Dreamstime, Alamy, Depositphotos, 123RF and Pond5, all at once, in its own window, so you can keep working while it runs. The uploading comes from **StockUp**:
  - A progress bar for each site, with the photo it's on and whether the connection is encrypted.
  - **Pause**, **Resume** and **Stop** work part-way through a photo, and a half-sent photo is removed from the site, so a site never gets half a photo. A dropped connection is tried again.
  - Encrypted connections first (FTPS or SFTP); plain FTP only for a site that doesn't offer encryption, and you can turn that off.
  - Nothing already on a site's server is overwritten, and nothing is sent twice: photos already sent to a site, by IPTC Tagger or by StockUp, are skipped, even when renamed.
  - Photos with changes that aren't saved yet have to be saved first (**Save Metadata first**), since the sites read the title, description and keywords from inside the photos. Only JPEGs are sent, exactly as they are.
  - Afterwards it lists what's left on each site, with **Make Shutterstock CSV** right there (Shutterstock needs the CSV for categories and the editorial flag), and **Open Receipt**: every photo sent, where, when and whether encrypted.
  - **Logins…** keeps your username for each site, and the password in your computer's keychain (Windows Credential Manager). **Test** tries a login. Logins you saved in StockUp are used here too, so you don't type them again.
- **Fits a laptop screen:** the top buttons wrap onto a second line when the window is narrower, instead of the window needing 1,734 pixels. It now fits a 1366-pixel screen.
- **Profiles keep the newer Editorial choices too:** *Add to keywords*, *Give to AI* and before or after the title.

## 2026.11.09.7

- **The store in your photo note decides the title:** with a note like *BJ's Wholesale* (or the store in your Instructions), titles and descriptions no longer name Costco, Kirkland or any other chain, even when the AI thinks it read *Kirkland* on a sign. If the title names no store at all, *at BJ's Wholesale Club* is added to the end. A store's own brand in your note (*Kirkland almonds at BJ's*) stays, and isn't mistaken for the store. Related stores and brands are still fine as keywords.
- **The AI is reminded of your note last:** the photo note is now repeated at the very end of what the AI reads, with "the title must agree with it, and never name a different store, brand or species". Small models follow what they read last much better. Text saved on the photo from an earlier Detect is marked as possibly wrong where it differs from your note.
- **Two stores in one title:** without a note, the store named as the place (*BJS Wholesale Grocery Store Setting*) now wins over one used like a brand (*Costco Baking Nuts*), instead of whichever came first.

## 2026.11.09.6

- **Editorial titles without the place and date in the keywords:** a new **Add to keywords** box next to *Editorial (include location and date)*. Untick it and the location and date still go in front of the title (and the description), but *tokyo*, *japan*, *november 2026* and *2026* aren't added to the keywords. Ones the AI writes or that are already saved on the photo are taken out too, and the AI is told to leave them out. Your keywords to put first are always kept, even with a place or *{month} {year}* in them. Unticking it offers to take them out of the photos already in the list as well. On to start with, as before.

## 2026.11.09.5

- **Change models from the main window:** **Photo model** and **Text model** dropdowns now sit on the right of the *Instructions for the AI* box, so you can switch models without opening Settings. The photo model list only shows models that can see images.
- **Two models, each doing what it's best at:** the **Text model** does the steps that only write text: **Reword / Double-check**, the automatic checks (*Fix automatically*), and rewriting a title to take out words or to match your notes. The photo model still does everything that looks at the photo (the title and keywords, reading the label, the description). A larger text-only model, such as *llama3.1:8b* or *qwen2.5:14b*, often writes better-worded titles than a small vision model. Leave it on **Same as photo model** to use one model for everything, as before. Both models stay loaded while you work, so a second model needs more memory on your graphics card. If the text model you chose isn't downloaded, the photo model does those steps instead. It's also in **Settings → Model for text steps**.
- **Fix: Reword made titles shorter, even with Make it fuller ticked:** Reword never told the AI how long the title should be, and it took whatever came back, so small models trimmed titles down. It now gets a length target: your title length setting, never shorter than the title is now, and a few words longer with *Make it fuller*, *Make it longer*, *Add the context* or *More specific*. A title that still comes back shorter is asked for again once, with the word count spelled out, and if it's still shorter your old title is kept (the Status column says so). Only *Make it shorter* may shorten a title. *Just double-check* no longer says "keep everything else exactly" when you've also ticked other changes, which was cancelling them out.

## 2026.11.09.4

- **Titles and descriptions with context, not just what's on the shelf:** instead of *Protein Shakes on Store Shelf*, the AI now ends the title with what the product is for, who buys it or the theme around it: *Protein Shakes on a Store Shelf for Health-Conscious Consumers, Nutrition and Supplementation*, *Canned Beef Stew for Quick Comfort Food Meals and Pantry Stocking*, *Christmas Lights Display for Holiday Decorating and Seasonal Shopping*. The description gets a second sentence about the context and the mood. It stays general and true for that kind of product, never an invented detail about the photo. On to start with: **Settings → Titles & keywords → Add the context to titles and descriptions**. For photos already done, **Reword → Add the context (what it's for, who buys it)**.
- **More wrong parent companies caught in titles:** a parent company put in front of another brand (*Campbell's StoveTop Stuffing*, but Stove Top isn't Campbell's) and a store claimed as the brand (*BelVita…, BJ's Wholesale Club Brand*) come out when the label doesn't show them. A brand the label does show (*Kellogg's Frosted Flakes*) stays.

## 2026.11.09.3

- **The AI only sees the photo unless you give it the location:** with Editorial on and **Give to AI** (next to the location, was *AI uses it*) unticked, nothing about the place or date reaches the AI any more. Before, the location box was held back but the title and keywords already saved on the photo still went along (*Tokyo, Japan - November 6, 2026: …*, *tokyo*, *november 2026*), and so did keywords to put first with *{month} {year}* in them, so the AI picked the place and date up anyway. Now they're taken out of what it's given, and out of what comes back, and the app adds the location and date to the title and the place and date keywords itself after detecting. Tick **Give to AI** to have the AI know where and when, as before. Your own Instructions and photo notes are always given to it.

## 2026.11.09.2

- **Related stores and brands stay in the keywords:** other stores and brands are only taken out of the **title** and description now, which have to be accurate. A BJ's photo can keep *costco* or *kirkland* as keywords, but its title won't say *Costco*.
- **Parent companies only when they're real:** the AI likes to add who owns a brand (*BelVita… by General Mills*, though BelVita is Mondelez). A parent company now goes in a title only when its name is printed on the package or sign, or is in your notes or Instructions. Detect takes out *by…*, *from…* and *…, a [Company] Brand* when the company isn't on the label, and the AI is told never to add one from memory. A brand named on its own (*Hormel Chili with Beans*) is never touched, and parent companies are still fine as keywords.

## 2026.11.09.1

- **The AI's usual mistakes are fixed automatically:** after Detect and Reword, and before a Shutterstock CSV is made, the app now fixes the mistakes that turned up again and again in real Shutterstock CSVs, without asking the AI again:
  - **Leftover text in titles:** an answer that ran on into the title (*…on Grocery Shelf”, “Keywords*) is cut off there, and a quote that doesn't pair up (*Real Broth”, Six Boxes*) is taken out.
  - **The wrong store:** a photo from a named store no longer gets another chain's name or house brand. A BJ's photo loses *Costco*, *Kirkland*, *KrogerPay* and *Walmart* from its title and keywords, and a photo from Ace, Home Depot or Lowe's says *Store Shelf*, not *Grocery Store Shelf*.
  - **Junk keywords:** prices (*13 79*, *save 30*), product and date codes (*cs1301*, *l2 0933*, *best before date*) and numbers copied off the package (*150 calories*, *90 pouches*) are taken out. Real numbers stay (*18 pack*, *60v max*, *ms 162 chainsaw*, *november 2026*). So are *frozen* keywords for canned or boxed food, and the extras the AI adds to almost every store photo (*shopping cart*, *supply chain*, *customer service*, *warehouse*) unless the title or description shows them. Detect then tops the keywords back up to your minimum.
  - **Shutterstock categories that don't fit:** *People* or *Celebrities* with nobody in the photo, *Parks/Outdoor* for a box of pancake mix, *Buildings/Landmarks* for paint on a shelf (*Interior Exterior* on the can doesn't count). A product photo gets *Objects* instead. The AI is also told what each category is for.
  - Your Instructions, photo notes and keywords to put first are never touched. Text read off the label keeps brand names, but not prices or codes.
- **Check before the CSV:** **Make Shutterstock CSV** and **Export CSV** (Shutterstock) look for these problems first, show every change (*Show Details*) and fix them with one click, or make the CSV as is. The photos in the list are fixed too, and the text from before is under right-click → **Earlier suggestions**.
- **Right-click → Fix common problems** does the same for any photos already in the list.
- **The Shutterstock CSV always uses plain characters:** curly quotes and long dashes (*BJ’s*, *–*) are made plain in the CSV whatever the Symbols setting is, since Shutterstock asks for titles without special characters.
- The AI is told not to use prices, codes or package numbers as keywords, not to name other stores, and to name a parent company only when it's on the package.
- Turn it off in **Settings → Titles & keywords → Fix the AI's usual mistakes automatically**.

## 2026.11.07.1

- **Reword: Make it longer, add what the subject is:** a new Reword option for titles that came out too short. The AI makes the title about 10 to 16 words by adding general background about what it names: what kind of thing it is, what it's made of or used for, its category, or what it's known for. It doesn't invent details about the photo itself, and it never adds a date or the place. The old *Make it fuller* is now *Make it fuller (same details)*: it only rewords what's already there. *Make it shorter* is unchanged.
- **Fix: location and date stay out when they're off:** with Editorial off, the AI could still pick up a place or a date from text saved on the photo earlier, such as an old title with a dateline or keywords like *tokyo* or *october 2026*, and put them back into the new title and keywords. Detect and Reword now take the location and the month/year out of what the AI is given, and out of what comes back, including old keywords kept with *Keep existing keywords*. With Editorial and the date both off, the AI is also told not to add any date. A place or date you wrote yourself in your Instructions, a photo note or your keywords to put first is still used.

## 2026.11.05.4

- **Fix: photo notes are kept:** the photo note (and your Instructions) went into the AI's first draft, but two later steps rewrote the title without seeing them: lengthening a title that came out too short, and the new automatic checks. So a note like *it's a heron, not a crane* could be undone and the title end up saying *crane* again. Every step that writes or rewrites a title now gets your photo note, your instructions and the text read from the label, and is told to keep them. This affects Detect and Redo with reasons; Reword / Double-check already had them.

## 2026.11.05.3

- **Make Shutterstock CSV:** a one-click button at the bottom right, next to the StockUp buttons. It makes the Shutterstock upload CSV for the checked photos (or all of them when none are checked) and saves it next to the photos as *shutterstock_<date>.csv*, never replacing an earlier one. It uses the titles exactly as you see them in the list. Upload the photos to Shutterstock, then on the Submit page click **CSV** and choose the file: it sets the titles, keywords, categories and editorial. (**Export CSV…** still makes the Adobe Stock CSV and the full spreadsheet.)
- **StockUp 1.8.1** sends Shutterstock your title too, so the titles are right whichever CSV you upload.

## 2026.11.05.2

- **Fix: Send to StockUp opens your newest StockUp:** with more than one copy of StockUp on the computer (an old one and a newer download such as *StockUp-Windows (1)*), the app could open the old one, because it went by the folder name, and a StockUp once chosen in Settings was always used even after you got a newer one. It now finds every copy (the usual folders, OneDrive and Downloads, and the one in Settings) and opens the newest, never StockUp's own update backup. **Settings → Saving → StockUp** shows which one it opens, and the status line says where it opened it from.

## 2026.11.05.1

- **Fix: Shutterstock gets your title:** the Shutterstock CSV put a photo's description (the longer, sentence-style caption) in Shutterstock's one text column whenever the photo had one, so after uploading the CSV Shutterstock showed *…: A colorful display of Gerber Graduates Puffs cereal snacks is arranged on a grocery store shelf…* instead of the title you saw in the list. That column, called *Description* in the CSV, is what Shutterstock shows as the photo's title, so it now gets the title. To send the description instead, choose it in **Settings → Stock sites → Shutterstock gets**. The Shutterstock checks (200 characters, at least 5 words, special characters) now look at whichever one is sent.

## 2026.11.04.4

- **Stock-site safe symbols:** a new choice in **Settings → Titles & keywords → Symbols**. Shutterstock asks for titles without special characters, so this makes the whole title, description and keywords plain: symbols become words (*Fish & Chips* → *Fish and Chips*, *50%* → *50 percent*), accents become plain letters (*Café* → *Cafe*, *Zürich* → *Zurich*), curly quotes and long dashes become plain ones, and ™, ®, emoji and other special characters are taken out. Unlike the other choices it also covers your own text (text before titles, location, keywords to put first). Choosing it offers to clean the titles already in the list, and right-click → **Clean special characters** does it for any photos. **Show → Breaks a stock site's rules** now also lists photos with special characters for Shutterstock.
- **Fix automatically:** new checks in **Settings → Titles & keywords** catch common problems in what the AI wrote, and have the AI reword just that, once and text only. The rewording is only kept if it's really better, and nothing extra is asked when all is well.
  - **Too many numbers in a title:** reworded with less emphasis on them, keeping a number only when it defines the subject (a model number, an important year).
  - **The same word twice in a title** (*Coffee Cup with Coffee Beans and a Coffee Grinder*).
  - **A title that reads like a list of keywords** (four or more commas): reworded as one natural phrase.
  - **A description that only repeats the title:** rewritten to add what the title leaves out.
  - **Too long for my stock sites:** shortened to fit the limits of the sites you sell on (for example Adobe Stock's 70-character title), counting the location, date and text before titles. Off by default, because it makes detailed titles shorter.
  The first four are on to start with.

## 2026.11.04.3

- **Fix: iPhone stays iPhone:** a title starting with a word that has its own capitals, like *iPhone* or *eBay*, could come out as *IPhone* or *EBay* since the last update. Fixed.
- **Tested before every release:** every new version is now checked automatically on Windows before it's built: 78 tests covering titles, keywords, locations, the AI's instructions, saving with ExifTool, Undo, the list kept after a crash, StockUp and the window itself. If any test fails, nothing is built or published, so a mistake can't reach the update button.

## 2026.11.04.2

- **Every Detect checklist:** under **Instructions for the AI**, **Every Detect → Choose…** lets you tick how the AI should write titles and keywords for *every* photo, the way Redo with reasons does for one. **Always:** plain and factual, only what's visible, no brand or people's names, use the place and names from my notes, specific keywords. **Focus on:** the words and signs, the main subject, what's happening, colours and light, mood, the setting, the people, concepts for commercial buyers, the season. What's ticked shows on one line (*Factual · Main subject · Signs*). It's used by Detect, the double-check and Reword; Redo still adds its own reasons on top. Nothing is ticked to start with.
- **Profiles:** **Save Profile…** keeps your Instructions text, the Every Detect choices, the default photo type, keywords to put first, text before titles, Editorial and the location under a name, like *Grocery products* or *Travel – editorial*. Pick one from **Profile** to switch everything at once. *(changed)* shows when you've edited a profile's settings since saving it, and switching asks first if your current setup isn't saved.
- **Near-identical titles:** after a Detect batch, the app tells you when photos came out with the same or nearly the same title (the location and date aren't counted), with a link to show them. **Show → Near-identical titles** lists them any time, and **Redo Similar…** keeps the first photo of each group and redoes the rest, telling the AI which title to steer away from.
- **Faster saving:** one ExifTool is now kept running in the background instead of being started three times for every photo saved (and for reading photos when they're added). In testing, 10 saves went from 5.2 seconds to 0.2. If it ever stops answering, the app quietly goes back to the old way.
- **Faster Detect for scenery:** the label-reading first look is skipped for photo types that rarely have text (landscapes, beaches, aerial, night sky, wildlife, pets, flowers, portraits, farming, abstract), saving one full look at each photo. It still runs when *Focus on the words and signs* is ticked.
- **Review with the keyboard:** Ctrl+↓ / Ctrl+↑ next and previous photo (also while typing in the side panel), Ctrl+D Detect, Ctrl+R Redo with reasons, Ctrl+T tick, alongside Ctrl+Enter save and next. On a Mac use ⌘. They work on the selected photo, never the whole list. Press F1 for the full list, also under **Settings → General → Keyboard shortcuts…**.

## 2026.11.04.1

- **Titles start with the subject:** titles are now direct, like *Magazines on a Shelf for Reading*, instead of *A Picture of Magazines on a Shelf…*. The AI is told to begin with what's in the photo, and openings like *A picture of*, *A photo of*, *An image showing*, *This is a photo of* or a leading *A* / *An* are taken off whatever it writes. Describing words that tell you something, such as *Aerial Photo of* or *Close-Up Photo of*, are kept. On by default; turn it off in **Settings → Titles & keywords → Title wording**.
- **Capitalize Every Word:** a new **Title capitals** choice in **Settings → Titles & keywords**. *Title Case* (as before) keeps short words like *on*, *for* and *a* lowercase in the middle; *Capitalize Every Word* gives *Magazines On A Shelf For Reading*. Words with their own capitals (iPhone, NASA, McDonald's) stay as they are.
- **Update the titles you already have:** when you change either option, the app offers to apply it to the titles already in the list, without detecting again. Only the part the AI wrote changes: your text before titles, the location and the date stay exactly as they are. Both options also apply to **Reword / Double-check**.

## 2026.11.03.1

- **Reword / Double-check:** a new **Reword…** button (at the top, in the side panel, and right-click → **Reword / double-check text…**) goes over the titles, descriptions and keywords already written, *text only*, so it takes a few seconds and doesn't look at the photo again. Tick what it should work on (title, description, keywords), then pick clean-up options (*Just double-check*, *Make it shorter*, *Plainer words*, *Remove repeated words*, *Main subject first*, *Remove duplicate keywords*, *Remove keywords that don't fit*, and more) and/or type your own instructions, like *it's a heron, not a crane*. With only *Just double-check* ticked, it fixes mistakes and leaves the rest alone.
- **The location and date stay as they are:** the location, date and text before titles in front of the title (and description) are never sent to be rewritten. They're put back exactly as they were, along with your keywords to put first and the place and date keywords. Tick *Tell the AI the location and date* to have them in mind for the wording anyway. The text from before each reword is kept under right-click → **Earlier suggestions**, and your choices are remembered for next time.
- **A description of its own:** every photo now has a **Description** (caption) next to its title, for Alamy, Dreamstime and the other sites that want a proper description and not just the title again. The AI writes it right after the title: one or two plain sentences (up to 200 characters) that add what the title leaves out. Edit it in the side panel, under the title. It's saved as the *Description / Caption* (XMP and IPTC), which Alamy, Dreamstime and Shutterstock read. With *Editorial* on, the location and date go in front of it too (*Tokyo, Japan - October 26, 2026: A crowd…*). Leave it empty and the title is saved as the description, as before. Turn the AI's description off in **Settings → Description**.
- **Shutterstock CSV fixed:** Shutterstock rejected the upload CSV because its *Categories* column was empty. Export now asks for one or two Shutterstock categories (remembered for next time), writes *Yes* / *No* the way Shutterstock's sample does, and puts each photo's description in the *Description* column.
- **Dated CSV names:** Export CSV suggests a name with today's date, like *shutterstock_2026-11-02.csv*. The *All details* CSV also has the description and its length.
- Dreamstime and Alamy are flagged in the stock-site rules when a photo has no description of its own.
- **Shutterstock categories per photo:** while writing the description, the AI also picks the one or two Shutterstock categories that fit each photo best, and the upload CSV uses them. You're only asked for categories for photos that don't have their own (photos detected before this version, or with the description turned off). They're shown under the description in the side panel.
- **The description follows along:** *Find and replace* has a new **Descriptions** box, changing a photo's location swaps it in the description's *Place - Date:* too, and copying a photo's title and keywords and pasting them onto other photos brings its description with them.

## 2026.11.01.1

- **RAW files:** drop CR3, CR2, NEF, ARW, RAF, ORF, RW2 and other camera RAW files in, alongside your JPEGs. Their titles and keywords are saved into Lightroom's **.xmp sidecar** next to the RAW (photo.CR3 → photo.xmp), so Lightroom picks them up and carries them into every JPEG you export. The RAW file itself is never written to: it's checked byte for byte before and after every save. Anything else in the sidecar, like Lightroom or RAW Review edits and star ratings, is kept. The AI and the thumbnails use the preview the camera stores inside the RAW. RAW files aren't sent to StockUp (export JPEGs first), and Export CSV lists them by the name of the JPEG Lightroom will make (photo.jpg). DNG files aren't included: Lightroom keeps their metadata inside the file. For RAW photos already in your Lightroom catalog, see *RAW files and Lightroom* in the README first.
- **Watch Folder:** a new **Watch Folder…** button next to *Add Folder*. Point it at the folder Lightroom exports to and every new photo that lands there is added to the list by itself, a few seconds after it's finished writing. Tick *Run Detect on new photos* and they get titles and keywords straight away too. Watching carries on after a restart until you stop it.
- **Stock-site rules while you edit:** titles and keywords that a stock site would reject are tinted amber, with the reason in the tooltip and under the keywords in the side panel (e.g. *Adobe Stock: title 84 characters (most 70)*). **Show → Breaks a stock site's rules** lists them all. The rules are the same ones StockUp checks. Choose your sites in the new **Settings → Stock sites** page, or let it use the sites you ticked in StockUp.
- A Sponsor button, license and security policy on the GitHub page.

## 2026.10.29.3

- **Send to StockUp buttons:** two buttons at the bottom right of the window replace *Open in StockUp*: **Send All to StockUp** sends every photo in the list when you're done, and **Send Checked to StockUp** sends only the photos you ticked. They show how many photos they'll send (*Send All 24 to StockUp*, *Send 3 Checked to StockUp*). As before, photos with unsaved changes can be saved first in the same click.

## 2026.10.29.2

- **Open in StockUp:** a new **Open in StockUp** button (and right-click → **Open in StockUp**) hands the saved photos to [StockUp](https://github.com/kefirman9000/stockup), which checks them against each stock site's rules and uploads them to Shutterstock, Adobe Stock and the others. It uses the checked photos, else the selected ones, else all shown. Photos from one folder open right where they are, with only those photos in the batch.
- **Save, then open:** if some of the photos have unsaved titles or keywords, you can **Save and open in StockUp** in one click (StockUp reads them from the files, so it would otherwise see the old ones), or open only the saved ones.
- **After Save Metadata:** tick **After Save Metadata, open the saved photos in StockUp** in **Settings → Saving** to send every batch on as soon as it's saved.
- StockUp is found by itself in Applications (Mac) or in the usual folders and Downloads (Windows), or choose it in **Settings → Saving → StockUp**. StockUp.exe, StockUp.app or stockup.py all work. If StockUp is already open, the app asks first, because StockUp only takes photos when it starts.

## 2026.10.29.1

- **Set the location for many photos at once:** with *Editorial* on, check the photos (or select them) and click **Set for Photos…** next to the location under *Instructions for the AI*, or right-click → **Set location…**. Their titles, location keywords and City / State / Country are changed right away, *without detecting again*: *Tokyo, Japan - October 26, 2026: Temple Gate* becomes *Kyoto, Japan - October 26, 2026: Temple Gate*, and the keyword *tokyo* becomes *kyoto*. Nothing is saved into the files until you save.
- **A location per photo:** the location in *Instructions* is now the main one, used by every photo that hasn't got its own. A single photo's location is also in the side panel (press Enter to apply it), and hovering over a file name shows the location it uses. Leave it empty to go back to the main one. Locations are kept with the list if the app closes unexpectedly.
- **No more doubled locations:** changing the main location and clicking **Add to titles now** used to leave the old location in the title as well (*Nara, Japan - … : Tokyo, Japan - … : Deer in the Park*). The old location, and its keywords, are now swapped out, even in titles saved in an earlier session.

## 2026.10.28.8

- **Avoid column on or off:** a new **Avoid column** checkbox under **Instructions for the AI** (next to *Double-check against my instructions*) shows or hides the Avoid column and the Avoid box in the side panel. It's off to start with. While it's off, the AI ignores Avoid, but what you typed there is kept for when you turn it back on. The *Never use* list in Settings works either way.

## 2026.10.28.7

- **Avoid column:** next to *Photo notes* there's now an **Avoid** column (also in the side panel) for what the AI should *not* say about that photo, e.g. *crane, sunset* or *don't mention the weather*. The AI is told to stay away from it, and short entries (up to 3 words, separated by commas) are also removed from its title and keywords for certain. It stays when you redo, so *it's a heron* in the notes plus *crane* in Avoid is the quickest fix for a wrong subject.
- **Never-use words:** list words the AI must never use on any photo (*beautiful, stunning*, a competitor's name…) in **Settings → Titles & keywords → Never use**. Any AI keyword containing one is removed, and by default they're taken out of titles too. Your own keywords are never touched.
- **No holes in titles:** when the AI uses a never-use or Avoid word anyway, it's asked once to rewrite the title without it, instead of the word just being cut out and leaving a title with no subject.
- **Find and replace:** fix a word or phrase in the titles and keywords of the checked photos (or the selected ones, or all shown) at once, e.g. *Cocacola → Coca-Cola*, or leave *Replace with* empty to remove it. Use the **Find & Replace…** button next to the search box, right-click → **Find and replace…**, or Ctrl+H (⌘⌥F on a Mac). It shows how many photos will change before you click Replace, and nothing is saved into the files until you save.

## 2026.10.28.6

- **Your list is kept safe:** while you work, the app keeps a copy of the photo list with every title and keyword that isn't saved yet, plus photo notes and types. If the app closes unexpectedly (a crash, a power cut, Windows restarting for an update), it offers to **bring the list back** the next time it starts. Photos that were deleted or moved meanwhile are left out. When you close the app normally, the copy is removed.
- **Alert when a batch finishes:** when a Detect, Save or Undo batch that took longer than 20 seconds is done, the app plays a sound and shows a notification with how many photos and how long it took. The taskbar button flashes (Windows) or the Dock icon bounces (Mac) until you look. Choose sound, notification, both or nothing in **Settings → General → When a batch finishes**. There's no alert after you press Stop.
- **Earlier suggestions:** each photo remembers its last 5 AI suggestions, and what it had before a redo. Right-click a photo → **Earlier suggestions** to put one back, for example when a redo came out worse than the first try. They're kept with the list, so they come back after a crash too.

## 2026.10.28.5

- **Stop really stops:** pressing **Stop** just as a photo came back empty or ran over the time limit could start that photo again by itself. A Stop is now final: no automatic retry after it.
- **Force Stop:** if photos are still busy a couple of seconds after **Stop**, a **Force Stop** button appears. It lets go of every photo at once, takes the waiting photos out of the line, and resets the model in Ollama so it drops what it was working on. Answers that still arrive afterwards are ignored. A redo that's force stopped gets its old title and keywords back.
- **Reset the model:** the green status light now has a **Reset** link that unloads the model and, with *Keep it ready* on, loads it again fresh. Use it when the AI seems stuck.
- **Restart Ollama:** if Ollama doesn't answer the reset, the app offers to close Ollama completely and start it again. It's also on the red light when the model couldn't be loaded.

## 2026.10.28.4

- **Redo starts fresh:** **Redo with reasons** now clears the photo's title and keywords first, whether they were saved in the file or suggested by the AI, and doesn't show them to the AI. It writes new ones from the photo, your **Instructions for the AI**, the photo note and the reasons you ticked or typed, so it no longer leans on the old wording. Keywords from the file are no longer added back after a redo either; your *Keywords to put first* still are. If you stop a redo, or it fails, the old title and keywords come back. Nothing changes in the file until you save.

## 2026.10.28.3

- **Model status light:** the bottom left of the window shows whether the AI model is ready. **Green**: it's loaded and photos start right away (hover to see whether it's on the graphics card or the processor). **Amber**: it's loading. **Red**: it isn't loaded (click **Load now**), it isn't downloaded, or Ollama isn't running (click **Open it**).
- **Load the model when the app starts:** the model now loads in the background as soon as the app opens, so the first photo no longer waits while it loads.
- **Keep it ready:** Ollama unloads a model after 15 minutes without a photo. While the app is open, it's now kept loaded, and it's unloaded 15 minutes after you close the app. Both options are in **Settings → AI → Model in memory**.
- **Fewer time-outs after a break:** the first photo after the model was unloaded gets the extra loading time again, as the very first photo does. Before, it could run out of time.

## 2026.10.28.2

- **Check for Updates button:** a **Check for Updates** button now sits next to **Settings** at the top of the window. It answers in the blue banner: the new version with *Yes, download it*, *You have the latest version ✓*, or *GitHub couldn't be reached* with **Try again**. When a new version is found, the button changes to **Update Available**, and once it's downloaded, to **Restart to Update**. **Settings → General → Check now** also says now whether you're up to date or the check couldn't get through, instead of one message for both.

## 2026.10.28.1

- **Open Ollama from the app:** when Ollama isn't running, the banner, **Settings** and the *Detect* message now offer **Open Ollama** as well as the download link. It starts Ollama if it's installed and checks again by itself every couple of seconds until it's ready. If Ollama can't be found on the computer, the app offers to download it instead.

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
