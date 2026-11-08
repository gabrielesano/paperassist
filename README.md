# PaperAssist

Reading a research paper is rarely linear. Half the time you are scrolling to the back to check who *Vance & Lindström (2022)* is, hunting through a `Downloads` folder full of cryptic publisher filenames to see if you already have that PDF, and losing your place in the paragraph you were actually trying to understand.

**PaperAssist** is a calm, local reading desk and shared library for scientific PDFs. It runs a tiny server on your computer and opens in your web browser—no account, no cloud sync, no telemetry, and no language models guessing at your bibliography. Your PDFs, highlights, drawings, and notes stay on your machine, and nothing touches the network unless you explicitly search for a reference.

> **Project status:** Alpha. Reading, highlighting, drawing, phrase-linked comments, and local library management are ready for daily use; bibliography extraction and open-access retrieval work well on standard journal layouts and are actively improving.

![PaperAssist workspace showing the document outline on the left, a main paper with a cited paper open in Quick view below it, and ranked references on the right](docs/screenshots/workspace-split-view.png)

---

## Quick start

Requires **Python 3.11** or newer.

```bash
pipx install git+https://github.com/gabrielesano/paperassist
paperassist
```

Running `paperassist` starts the local server and opens your reading desk in the default browser. Press `Ctrl+C` in the terminal when you are done.

If you prefer a regular virtual environment instead of `pipx`, `pip install git+https://github.com/gabrielesano/paperassist` works the same way. To run directly from a source checkout:

```bash
git clone https://github.com/gabrielesano/paperassist
cd paperassist
python -m venv .venv
.venv/bin/python -m pip install -e .      # Windows: .venv\Scripts\python
.venv/bin/paperassist
```

### Desktop development preview

From a checkout, install and launch the desktop window:

```bash
.venv/bin/python -m pip install -e '.[desktop]'
.venv/bin/paperassist desktop
```

`paperassist-desktop` also launches it. If the library already has a running
server, the window attaches to it and leaves it running on close. Otherwise it
starts and stops its own server. Modified PDFs and comment drafts offer **Save**,
**Discard**, or **Cancel**. Windows/macOS are unverified. See the
[plan](docs/development/desktop-plan.md) and [verification](docs/development/desktop-verification.md).

### Native reader

#### Standalone Linux Installer (No Python Required)

For Linux `x86_64`, PaperAssist provides a self-extracting standalone installer that includes the complete Python runtime, Qt Widgets/PySide6, and PDFium rendering stack:

```bash
# 1. Verify package integrity
sha256sum -c PaperAssist-0.1.0a3-linux-x86_64.sh.sha256
sh ./PaperAssist-0.1.0a3-linux-x86_64.sh --verify

# 2. Install per-user (no root / sudo required)
sh ./PaperAssist-0.1.0a3-linux-x86_64.sh
```

- **Launch:** Start **PaperAssist** from your application menu, or run `paperassist` from a terminal (`~/.local/bin/paperassist`).
- **Upgrade:** Re-run the installer script for a newer version (`sh ./PaperAssist-<new-version>-linux-x86_64.sh`). Upgrades stage in isolation and swap atomically, rolling back automatically if validation fails.
- **Repair:** Recover an interrupted installation with `sh ./PaperAssist-0.1.0a3-linux-x86_64.sh --repair`. After an interrupted first installation, repair removes unchanged integration files so installation can be retried; it reports that no prior installation exists to restore.
- **Uninstall:** Run `~/.local/share/paperassist-app/uninstall.sh` or `sh ./PaperAssist-0.1.0a3-linux-x86_64.sh --uninstall`. Uninstallation removes only application-owned files, preserving your library, PDFs, notes, and preferences.
- **Requirements & Compatibility:** Linux `x86_64`, `flock` from util-linux for installation and maintenance, X11 or Wayland, and system OpenGL/EGL libraries. The local `0.1.0a3` candidate requires **glibc >= 2.42**. CI targets `ubuntu-22.04` with an ELF gate requiring GLIBC <= 2.35; that build and clean Ubuntu/Debian validation remain unexecuted. Prior installers `0.1.0a1` and `0.1.0a2` are retained in `dist/` as historical review evidence. See the [maintenance follow-up](docs/development/linux-alpha-maintenance.md).

#### Running from Source Checkout

To run the native reader directly from a local development checkout:

```bash
.venv/bin/python -m pip install -e '.[native]'
.venv/bin/python -m paperassist.native
```

Missing native dependencies produce an installation message. The browser command
`paperassist serve --open` remains available without the native extra. Standalone
builds report missing components as an installation problem and recommend
reinstalling the complete application.

### Command-line options

| Command | What it does |
| --- | --- |
| `paperassist` | Start the local server and open the reader in your browser |
| `paperassist serve [--open] [--host H] [--port N] [--data-dir DIR]` | Start the server without opening a browser (defaults to `127.0.0.1:8765`) |
| `paperassist desktop [--port N] [--data-dir DIR]` | Open a dedicated desktop window (requires the desktop extra) |
| `paperassist import PATH... [--data-dir DIR]` | Import PDF files or folders recursively from the terminal |

Only one server runs per library at a time. If your library is already open, running `paperassist` simply opens a browser tab to the running instance. While the app is running, add new papers from **Library** inside the browser rather than running `paperassist import` in parallel.

---

## What reading in PaperAssist feels like

### Follow citations without losing your place

PaperAssist extracts a paper's bibliography and ranks references in the right-hand **References** panel by how often they are cited in the paper you are currently reading.

- **Stacked or side-by-side split view:** Clicking a reference that is already in your library opens it in **Quick view** right below your main paper, or **Side by side** with a single toggle—ideal when you hide the side panels (`B`) to compare two papers across the full width of your screen. Drag the divider to resize either split, or click **Open in tab** to promote the cited paper to its own tab with page and zoom intact.
- **Independent controls for both panes:** Whenever Quick view is open, the top toolbar shows a dedicated **Second paper** bar with its own page jumper, search box, and zoom controls—so navigating the cited paper never disturbs the main paper.
- **Step through every in-text mention:** Click a reference card (or its citation count badge) to jump from one in-text citation to the next. Each click centers the passage and briefly outlines the citation, wrapping around after the last one.
- **Filter and nickname tabs:** Use **Filter references** (`/` key) to narrow long bibliographies by author, year, title, label, or DOI (ignoring case and accents; each tab remembers its own filter). Double-click any open tab—or focus it and press `F2`—to give it a short nickname like *"Vance 2022"* without altering its bibliographic title or filename on disk.

![Side-by-side Quick view with side panels hidden, showing the main paper on the left and the cited paper beside it on the right](docs/screenshots/workspace-side-by-side.png)

### Find missing references—via open access or your own downloads

When you open a paper for the first time, PaperAssist shows which cited works are already in your local library and lets you search for missing ones in a batch—or skip straight to **Continue reading** and look them up later.

![Reference PDFs dialog showing papers already in the local library alongside missing references selected for open-access lookup](docs/screenshots/reference-pdfs.png)

- **Open-access lookup:** Tick multiple entries in **Reference PDFs**—or click **Find open access** on a single reference card—to check legal open-access sources (Crossref, arXiv, OpenAlex, Semantic Scholar, Unpaywall, NASA ADS, and CORE). Every provider reports its own outcome clearly: a timeout or unindexed reference is reported honestly, never disguised as proof of a paywall.
- **Web search & library proxy links:** Unresolved references offer prefilled web and PDF searches, plus one-click links through your institution's **Library proxy** if you configure one.
- **Manual downloads without filename chaos:** Point **Folder for manual downloads** (in **Preferences**) at the folder where you save publisher PDFs from your browser. Clicking **Check folder** on a reference identifies the citation, finds the matching file in that folder, and copies it into your library under a clean name like `Antolin_2012_ApJ_745_152.pdf`—leaving your downloads folder completely untouched. If PaperAssist picks the wrong match, **Change PDF** fixes the link in two clicks.

### Highlight, draw, and attach notes to exact phrases

Annotation controls live in a calm contextual strip inside the top toolbar—never as floating popovers that cover the sentence or equation you are trying to read.

![PaperAssist reader with continuous line highlights, a margin ink bracket next to an equation, and phrase-linked notes in the Comments sidebar](docs/screenshots/annotations-and-comments.png)

- **Clean PDF annotations:** Use **Highlight** for continuous per-line bands (including spaces between words, without bleeding across columns), **Draw** for freehand ink notes in the margins, and **Select** to recolor or delete existing marks.
- **Phrase-linked comments:** Select a passage and click **Comment** (or press `C`) to attach a note directly to those words. Saved phrase comments leave a subtle red dashed outline on the page even when the sidebars are hidden; clicking the outlined text jumps straight to its note in the **Comments** panel, and clicking a note brings you back to that exact phrase. Creating a comment without selecting text attaches a general note to the current page.
- **Explicit saving—never behind your back:** PDF annotations are written **only when you click Save** (or press `Ctrl+S` / `Cmd+S`). PaperAssist never autosaves on timers, tab switches, or previews. Switching tabs keeps unsaved edits in memory, and closing a modified tab always asks whether to **Save**, **Discard**, or **Cancel**. Your imported original PDFs are never modified; annotated revisions are stored separately, and annotations stay tied to the exact PDF file you marked up.

### Build a clean, private local library

Open **Library** (`L` key) to search your collection by title, author, year, or DOI, or to add new papers via **Import PDFs** and **Import folder**.

![Library catalog dialog showing imported papers with confirmed authors and years alongside a working draft flagged for metadata review](docs/screenshots/library-catalog.png)

- **Originals stay untouched:** Importing copies PDFs into PaperAssist's managed library and deduplicates them by file content (`SHA-256`). Re-importing a folder safely skips files you already have and preserves any manual metadata edits you have made.
- **Deterministic metadata—no LLMs:** Titles, authors, years, and candidate DOIs are read deterministically from PDF properties, XMP metadata, and first-page text. Because a DOI printed on page 1 can belong to a cited paper rather than the paper itself, PaperAssist never trusts unverified guesses blindly: uncertain entries remain clearly marked **Metadata needs review** until you confirm them with **Edit**.

---

## Keyboard shortcuts

Press `?` anywhere in the reader (or click the keyboard icon in the top bar) to view all shortcuts. Click **PaperAssist** at the top left for the application menu: **Preferences** (`Ctrl+,`), **Sources**, **Keyboard shortcuts** and **About**. On macOS, use `Cmd` in place of `Ctrl`. Letter shortcuts pause automatically whenever you are typing in an input field or have a dialog open.

| Keys | Action |
| --- | --- |
| `N` / `J`, `P` / `K` | Next page, previous page |
| `Home`, `End`, `G` | First page, last page, or jump to a specific page number |
| `Esc` | Close an open dialog, or return focus from a toolbar field back to the PDF so arrows, `Space`, and `Page Up`/`Down` scroll naturally |
| `Ctrl+F` | Find text in the paper pane you clicked last |
| `Ctrl++`, `Ctrl+-`, `Ctrl+0` | Zoom in, zoom out, or fit page width |
| `V`, `D`, `H`, `C` | Switch to **Select**, **Draw**, **Highlight**, or **Comment** |
| `Ctrl+Z`, `Ctrl+Shift+Z` (`Ctrl+Y`) | Undo or redo annotation changes |
| `Ctrl+S` | Save PDF annotations |
| `L` | Open **Library** |
| `Ctrl+,` | Open **Preferences** |
| `,` and `.`, `X`, `F2` | Switch tabs, close the current tab, or rename the focused tab |
| `B` | Hide or restore both side panels for distraction-free reading |
| `O`, `1`, `2` | Jump to **Outline**, **References**, or **Comments** |
| `/` | Focus **Filter references** (`Esc` clears the filter; a second `Esc` returns to the paper) |

When **Quick view** is open, page navigation, `G`, `Ctrl+F`, and zoom shortcuts act on whichever paper pane you clicked last.

### Interface size

**Preferences → Interface size** scales the toolbar, tabs, side panels, dialogs and menu from 80% to 150% and remembers the choice in this browser. Papers keep their own zoom controls.

---

## Where your library lives & backups

Everything PaperAssist manages—your copied PDFs, saved annotated revisions, comments, settings, and SQLite catalog—lives inside a single folder on disk. PaperAssist prints this path on startup and displays it in **Preferences → Library location**.

| Operating system | Default library folder |
| --- | --- |
| Linux | `$XDG_DATA_HOME/paperassist` (or `~/.local/share/paperassist`) |
| macOS | `~/Library/Application Support/PaperAssist` |
| Windows | `%APPDATA%\PaperAssist` |

Inside the library folder:
- **`originals/`** holds your unmodified PDFs under human-readable names—`Author_Year_Journal_Volume_Page.pdf` once a paper is identified, or its imported filename otherwise (with a numeric suffix if two papers would collide). Feel free to browse or read files from here, but rename or move them only through PaperAssist so the SQLite catalog stays in sync.
- **`working/`** holds saved annotated revisions under internal IDs.

### Moving or switching libraries

Open **Preferences**, pick a folder under **Library location**, and click **Use this folder**:

- **Existing PaperAssist library:** PaperAssist switches to it and leaves your previous library untouched.
- **Empty or new folder:** PaperAssist copies your entire current library there, verifies every file, and switches over. Your old folder is left intact until you choose to delete it yourself.
- **Non-empty unrelated folder:** Refused, so PaperAssist never mixes its files with unrelated documents.

Your chosen library path is saved in your user config (`~/.config/paperassist/config.json` on Linux, `~/Library/Preferences/PaperAssist/config.json` on macOS, `%LOCALAPPDATA%\PaperAssist\config.json` on Windows). If that folder is unavailable at startup—for instance, on an unplugged external drive—PaperAssist stops with a clear message instead of silently creating a blank library. Passing `--data-dir` overrides `PAPERASSIST_DATA_DIR`, which in turn overrides the folder chosen in the app.

### Backing up

Stop PaperAssist (`Ctrl+C`) and copy the library directory. Keep backups private: `settings.json` inside the library stores your contact email and any API keys you configured.

---

## Open-access sources & institutional access

Open **Sources** to toggle lookup providers and add optional API keys or contact details. Credentials are saved locally in `settings.json` (readable only by your user account) and are never displayed again after saving.

| Service | Optional / required setting | What it enables |
| --- | --- | --- |
| **Crossref** | Contact email *(optional)* | Identifies DOIs and bibliographic records; an email uses Crossref's polite rate-limit pool |
| **arXiv** | None | Official preprint metadata and open-access PDFs |
| **OpenAlex** | API key *(optional)* | Open-access discovery with higher rate limits when a key is set |
| **Semantic Scholar** | API key *(optional)* | Metadata and open-access links with higher rate limits when a key is set |
| **Unpaywall** | Contact email *(required)* | Finds legal open-access copies for papers with a DOI |
| **NASA ADS** | API token *(required)* | Astrophysics & physics bibliography identification and lookups |
| **CORE** | API key *(required)* | Repository full-text discovery |

If the corresponding field in **Sources** is left blank, PaperAssist also checks the `PAPERASSIST_EMAIL` and `OPENALEX_API_KEY` environment variables.

- **Library proxy:** Optional `https://...` prefix (or template containing `{url}`) for your university's proxy. PaperAssist uses it strictly to build links that *you* click in your browser—it never runs automated downloads through your institutional session.
- **Folder for manual downloads (in Preferences):** A read-only watch folder (pickable with **Browse…** via `zenity`, `kdialog`, or `Tk`) where you drop PDFs downloaded by hand so **Check folder** can verify and copy them into your library.

---

## Privacy & current limitations

- **Strictly local by default:** The server binds to loopback (`127.0.0.1`) and enforces origin and token checks so external websites cannot talk to it. No network request leaves your computer unless you trigger a reference lookup or click an external link.
- **No AI guesswork:** Importing, metadata extraction, and citation matching are 100% deterministic.
- **Citation detection is partial:** PaperAssist counts `"Author year"` and numbered `[n]` citations, leaving counts blank when two bibliography entries are ambiguous. On a hand-audited benchmark of six diverse papers, it recovered roughly 96% of in-text citations with near-zero false positives—see [`docs/development/citation-accuracy.md`](docs/development/citation-accuracy.md) for the full breakdown. Scanned PDFs with poor text layers, superscript citation numbers, and unusual citation styles are not yet detected.
- **Scans & large files:** Scanned papers open and annotate normally, though built-in OCR is not included yet. Very large or malformed PDFs do not yet enforce hard time or memory caps during import.

---

## Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
npm ci && node --test tests/*.test.mjs
```

The Python test suite runs completely offline without a browser or private PDF corpus. The Node test suite exercises frontend modules against the pinned `pdfjs-dist` version in `package-lock.json` (matching the vendored viewer build in `src/paperassist/web/vendor/pdfjs`). The reader uses the official legacy build for embedded-WebView compatibility; [vendoring details](src/paperassist/web/vendor/pdfjs/README.md) record its provenance. See [desktop verification](docs/development/desktop-verification.md) for the opt-in real-WebView smoke test and remaining platform checks.

For architecture details and design decisions, see:
- [Product specification](docs/specification.md)
- [Implementation roadmap](docs/roadmap.md)
- [Verification & citation accuracy notes](docs/development/)

---

## License

PaperAssist is free software licensed under the **GNU General Public License v3.0 or later** (see [`LICENSE`](LICENSE)). It bundles [Mozilla PDF.js](https://github.com/mozilla/pdf.js) under the Apache License 2.0 (see [`src/paperassist/web/vendor/pdfjs/LICENSE`](src/paperassist/web/vendor/pdfjs/LICENSE)) for PDF rendering and annotation layers.
