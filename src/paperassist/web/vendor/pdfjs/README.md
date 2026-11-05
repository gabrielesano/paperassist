# Vendored PDF.js

PaperAssist serves Mozilla's official `pdfjs-dist` **6.4.299** (build
`d0991a0d5`), pinned in the repository's npm manifest and lockfile. The reader,
worker, and viewer modules are unmodified copies of the package's `legacy/`
artifacts, served under the existing paths:

| Package source | Vendored path |
| --- | --- |
| `legacy/build/pdf.mjs` | `build/pdf.mjs` |
| `legacy/build/pdf.worker.mjs` | `build/pdf.worker.mjs` |
| `legacy/web/pdf_viewer.mjs` | `web/pdf_viewer.mjs` |

The official legacy build supplies compatibility translations and polyfills for
embedded WebViews. The modern viewer failed on Linux QtWebEngine because
`Map.getOrInsertComputed` was unavailable. Keeping the complete reader, worker,
and viewer on the same official build avoids a custom compatibility patch. The
package's [README](https://github.com/mozilla/pdfjs-dist/tree/v6.4.299) and
[Mozilla compatibility FAQ](https://github.com/mozilla/pdf.js/wiki/Frequently-Asked-Questions#faq-support)
describe the legacy build. Existing Node PDF tests also use `legacy/build`.

To reproduce after restoring the pinned npm dependencies, run from the repository
root:

```sh
cp node_modules/pdfjs-dist/legacy/build/pdf.mjs src/paperassist/web/vendor/pdfjs/build/pdf.mjs
cp node_modules/pdfjs-dist/legacy/build/pdf.worker.mjs src/paperassist/web/vendor/pdfjs/build/pdf.worker.mjs
cp node_modules/pdfjs-dist/legacy/web/pdf_viewer.mjs src/paperassist/web/vendor/pdfjs/web/pdf_viewer.mjs
cmp node_modules/pdfjs-dist/legacy/web/pdf_viewer.css src/paperassist/web/vendor/pdfjs/web/pdf_viewer.css
```

At this version the modern and legacy viewer CSS are byte-identical; the existing
vendored CSS, images, fonts, character maps, and WebAssembly assets are unchanged.
No additional build tree is needed.

PDF.js's Apache-2.0 license is retained in `LICENSE`, with upstream notices inside
the modules and asset directories. The legacy modules also bundle core-js
**3.50.0**. Its MIT copyright and permission notice is included in
`LICENSE_CORE_JS`, reproduced from the
[upstream versioned license](https://github.com/zloirock/core-js/blob/v3.50.0/LICENSE).
