# PaperAssist

PaperAssist is a local, private reader and library for research papers. It runs a
small server on your computer and shows a reader in your web browser. There is no
account and no cloud: your PDFs, annotations and notes stay on your machine. The
only network traffic is the reference searches you explicitly start.

Status: alpha. Reading, annotating and importing work; reference recognition and
retrieval are partial.

## Install

Requires Python 3.11 or newer.

```bash
pipx install git+https://github.com/gabrielesano/paperassist
paperassist
```

`paperassist` starts the server and opens the app in your default browser. Stop it
with Ctrl+C. Without pipx, `pip install git+https://github.com/gabrielesano/paperassist`
inside a virtual environment works the same way. From a checkout:

```bash
git clone https://github.com/gabrielesano/paperassist
cd paperassist
python -m venv .venv
.venv/bin/python -m pip install -e .      # Windows: .venv\Scripts\python
.venv/bin/paperassist
```

Other commands:

| Command | What it does |
| --- | --- |
| `paperassist` | Start the reader and open the browser |
| `paperassist serve [--open] [--host H] [--port N] [--data-dir DIR]` | Start without opening a browser (default port 8765, loopback only) |
| `paperassist import PATH... [--data-dir DIR]` | Import PDFs or folders from the command line |

One server runs per library. If the library is already open, PaperAssist says so
and, for plain `paperassist`, opens the running instance instead. Do not import
from the command line while the app is running; use **Library** in the app.

## Where your library lives

The library holds your PDFs, annotations, notes, settings and a SQLite catalog.
PaperAssist prints its path when it starts, and the app shows it under
**Library location**.

| System | Default location |
| --- | --- |
| Linux | `$XDG_DATA_HOME/paperassist`, or `~/.local/share/paperassist` |
| macOS | `~/Library/Application Support/PaperAssist` |
| Windows | `%APPDATA%\PaperAssist` |

To change it in the app, open **Sources**, choose a folder under **Library
location** and click **Use this folder**:

- a folder that already holds a PaperAssist library is opened, and the current
  library stays where it is;
- an empty or new folder receives the current library: PaperAssist copies every
  file, checks the copy and then uses it. The old folder is kept; delete it
  yourself once you are satisfied;
- any other folder is refused, so PaperAssist never mixes its files with yours.

The choice is stored per user, outside the library, in
`~/.config/paperassist/config.json` (Linux),
`~/Library/Preferences/PaperAssist/config.json` (macOS) or
`%LOCALAPPDATA%\PaperAssist\config.json` (Windows). If that folder is missing
at start-up, for example on an unplugged drive, PaperAssist stops with a message
instead of starting an empty library. `--data-dir` (for `serve` or `import`)
wins over `PAPERASSIST_DATA_DIR`, which wins over the choice made in the app;
when either is set, the app shows the location as fixed.

PDFs are stored in `originals/` under readable names:
`Author_Year_Journal_Volume_Page.pdf` once a paper is identified, otherwise the
name of the imported file (`Untitled_<id>.pdf` if it had none). A number is added
when two papers would share a name. You can browse this folder, but rename or
move files only through PaperAssist: it identifies papers by content (SHA-256),
and the catalog records each file name. Saved annotated versions live in
`working/` under internal names.

To back up a library, stop the app and copy the whole directory. It contains
your API keys and email in `settings.json`, so keep backups private.

## Import and read

- **Import PDFs** and **Import folder** (in **Library**) add papers from the browser.
  Folder selection depends on browser support; the `import` command handles folders
  recursively.
- Your original files are never moved or changed. PaperAssist copies them into the
  library, recognizes duplicates by content, and keeps your corrections when you
  import a file again.
- Metadata comes from PDF properties, XMP and first-page text, with the filename as
  fallback. Uncertain results stay marked for review: check the title, authors,
  year and DOI with **Edit paper details**. A DOI found in the text is not trusted
  automatically.
- The reader has an outline, page navigation and zoom. **Draw**, **Highlight** and
  **Select** edit the PDF; **Comment** attaches a note to a selected phrase or page.
  Comments appear in the right column and mark their phrase in the document; clicking
  a marked phrase selects its comment and shows it in the Comments panel.
- **PDF edits are saved only when you choose Save** (or press Ctrl/Cmd+S). Closing a
  modified PDF offers Save, Discard or Cancel; switching tabs keeps unsaved edits.
  Comments have their own save button.
- Annotations and reading position belong to the exact PDF file, not to a
  bibliographic record.

## References and quick view

PaperAssist finds a PDF's bibliography and lists it in the right column. Opening a
reference that is already in your library shows it in a split below (or beside) the
paper you are reading, so you can consult it without losing your place. A quick-view
PDF can be moved into its own tab. Drag the divider to resize the split.

While a quick view is open, the toolbar gains a **Second paper** group with its own
page, find and zoom controls. The first set always drives the main paper, the second
one the quick view, whichever pane you clicked last; annotation tools still follow the
last clicked pane.

Click a reference card, or its citation count, to step through where the paper cites
it: each click centres the next citation in the reader and outlines it briefly,
wrapping after the last. Citations are found through the PDF's own links when it has
them, otherwise by searching the text; if neither gives a position, the page opens.
The title still opens the reference. For a missing reference, the first click opens
its details and later clicks step.

Type in **Filter references** above the cards to show only the entries matching every
word (label, authors, year, DOI or title; case and accents ignored). Each paper tab
keeps its own filter.

If a reference is missing, **Find open access** searches for a legal copy; you can
also tick several entries in **Reference PDFs** and search them together. Each
source reports its own outcome, and a failed search never proves a paper is behind a
paywall. Unresolved references offer a prefilled web search. **Change PDF** corrects
a wrong match.

## Keyboard shortcuts

Press `?` or use the keyboard button in the toolbar for the full list. The main keys
(Command replaces Ctrl on macOS):

- `N`/`J` and `P`/`K` next and previous page; `Home`/`End` first and last page; `G`
  goes to a page number; `Esc` in a toolbar field returns to the document, where the
  arrow, Space and Page Up/Down keys scroll.
- `Ctrl+F` finds in the paper you last clicked; `Ctrl` with `+`, `-` or `0` zooms and
  fits the width. With a quick view open, page, find and zoom keys (and `G`) use the
  last clicked pane and its toolbar group; `Esc` in a Second paper field returns to the
  quick view.
- `V`, `D`, `H`, `C` choose Select, Draw, Highlight and Comment; `Ctrl+Z` and
  `Ctrl+Shift+Z` (or `Ctrl+Y`) undo and redo annotation edits; `Ctrl+S` saves.
- `L` opens the library; `,` and `.` switch tabs; `X` closes the tab; `B` hides the
  side panels; `1`, `2` and `O` go to References, Comments and the outline; `/` focuses
  the reference filter, where `Esc` clears it and a second `Esc` returns to the document.

Letter keys are ignored while you type in a field or a dialog is open. Without an open
paper, `Ctrl+F` and the zoom keys stay with the browser.

## Sources settings

Open **Sources** to switch search services on or off and enter optional
credentials. All are stored in the library's `settings.json`, readable only by you.

| Source | Setting | Notes |
| --- | --- | --- |
| Crossref | Contact email (optional) | Identifies DOIs; an email gives better rate limits |
| NASA ADS | API token | Needed for ADS lookups |
| arXiv | none | Official API |
| OpenAlex | API key (optional) | Raises rate limits |
| Semantic Scholar | API key (optional) | Raises rate limits |
| CORE | API key | Needed for CORE |
| Unpaywall | Contact email | Needed for Unpaywall; requires a DOI |

Environment variables `PAPERASSIST_EMAIL` and `OPENALEX_API_KEY` are used when the
corresponding setting is empty.

- **Library proxy** is an `https` address (with optional `{url}`) for your
  institution's access service. It is used only to build links that you click;
  PaperAssist never downloads through it.
- **Folder for manual downloads** is where you save PDFs obtained yourself. **Check
  folder** on a reference first identifies it (Crossref, and ADS when configured),
  then looks there and links a matching PDF, which keeps publisher downloads under
  your control. The library keeps a copy named after the paper, such as
  `Antolin_2012_ApJ_745_152.pdf`, never the browser's download name; the folder
  itself is never changed. A PDF you pick by hand gets the same name but stays
  marked for review.
  **Browse…** opens your system's folder window (zenity or kdialog on Linux when
  installed, otherwise Tk); the chosen folder is kept when you press **Save**.

## Privacy and limitations

- Everything runs on your computer. The server listens on loopback only and rejects
  requests from other sites. Requests leave your machine only when you start a
  reference search or click a link.
- PDF parsing is deterministic; no language model is involved in importing or
  organizing papers.
- Citation analysis is partial. It counts "Author year" and `[n]` citations and
  leaves a count blank when two entries cannot be told apart. On six hand-checked
  papers it found about 96% of citations and almost none wrongly, but scanned or
  badly extracted PDFs, superscript numbering and unusual citation forms are missed;
  see `docs/development/citation-accuracy.md`.
- Scanned PDFs open but are not OCRed. Very large or malformed PDFs have no hard
  time or memory limits during import.

## Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
npm ci && node --test tests/*.test.mjs
```

Python tests need no network, browser or private corpus. The Node tests use the
pinned `pdfjs-dist` from `package-lock.json`; the vendored copy under
`src/paperassist/web/vendor/pdfjs` is a build of that version. Code is in
`src/paperassist/`; see the [specification](docs/specification.md), the
[roadmap](docs/roadmap.md) and the [verification notes](docs/development/).

## License

PaperAssist is free software under the GNU General Public License, version 3 or
later (see `LICENSE`). It bundles [PDF.js](https://github.com/mozilla/pdf.js)
(Apache-2.0, license in `src/paperassist/web/vendor/pdfjs/LICENSE`) to render PDFs.
