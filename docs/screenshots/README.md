# README screenshots

These five images show the native Qt Widgets reader in PaperAssist `0.1.0a3`,
using the application code at commit `16a147c`. They were captured on Linux with
X11/Xvfb, the normal PDFium render worker, synthetic scientific PDFs, a disposable
library, and isolated user configuration. No private papers or credentials were
used, and no online acquisition was started.

| Image | Application state |
| --- | --- |
| `workspace-split-view.png` | Main paper and local reference in stacked Quick view, with outline and ranked references visible. |
| `workspace-side-by-side.png` | The same papers in horizontal Quick view with side columns hidden. |
| `reference-pdfs.png` | Acquire references dialog with available local copies and three missing references selected. |
| `annotations-and-comments.png` | Saved text highlights, an ink bracket, and two saved phrase-linked comments. |
| `library-catalog.png` | Five synthetic library entries, including an unconfirmed draft selected for metadata review. |

The PNGs are direct captures of the application's widgets; UI controls and PDF
content have not been composited or retouched. Both source PDFs and managed
originals were checked for unchanged SHA-256 content after capture. Only the
disposable library's working revision and sidecar comments were saved.

Fonts and window appearance can differ on other systems. Replace these images
when the visible native controls or documented workflows change; screenshots of
the transitional web interface do not represent the native installer.
