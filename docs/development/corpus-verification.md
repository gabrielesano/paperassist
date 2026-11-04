# Corpus verification

On 4 November 2026, the 19 PDFs in `BibArticolo` and 49 in `BibTesi`
were imported into a local library. All 68 imported successfully, with 1,145
pages and 166,000,769 source bytes. A repeat import recognized all 68 as
duplicates. The source files were not modified. On this machine the initial
import took 2.8 seconds and approximately 95 MiB peak process memory; the
repeat took 0.17 seconds. These timings are illustrative, not performance limits.

The metadata parser retains DOI and arXiv matches as unconfirmed candidates.
Its current title rules prefer usable PDF properties and otherwise use the
filename; first-page title lines are candidates for review. In this corpus,
34 titles came from PDF properties and 34 fell back to filenames. Tests cover
copyright, typesetting, author and institution lines that should not become
titles. First-page extraction has no hard process-level time or memory limit.

## Browser first-page rendering

A headless Brave session loaded PDF.js 6.4.299 from the application and each
PDF from its local document endpoint, then rendered page 1 at scale 0.8 onto
a canvas. Documents were processed sequentially and each PDF.js task and
canvas was released before the next. All 68 render promises completed; the
sum of individual load-and-render times was 22.27 seconds, with a maximum of
1.10 seconds for a single PDF. This is a rendering smoke test, not visual
inspection of every page, annotation test, or proof of complete fidelity.

The initial pass exposed missing JPEG 2000 and JBIG2 decoder configuration in
21 files. PDF.js completed those render calls while warning that images could
not be decoded, so completion alone did not establish a successful render.
After the reader's decoder assets and URLs were added, the repeat pass had no
image-decoder warnings or render exceptions. Two files still reported font
substitution warnings:

- `klimchuk2006.pdf`: Times New Roman bold and italic, and Courier New italic.
- `edwin1983.pdf`: Times New Roman italic and bold.

Fonts can therefore differ from the originals on systems without those fonts.
The test uses the local managed copies and does not write to the source PDFs.
