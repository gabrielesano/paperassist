# README screenshots

These images show the native Qt Widgets reader in PaperAssist. Images were captured
on Linux with X11/Xvfb, the normal PDFium render worker, synthetic scientific PDFs,
a disposable library, and isolated user configuration. No private papers or credentials
were used, and no online acquisition was started.

The library screenshots (`library-catalog.png` and `library-refresh-menu.png`) were
captured from `0.1.0a4` to show the current catalog columns (Title, Tab name, Authors,
Year, Metadata) and the metadata refresh menu. The reader and workspace screenshots
come from `0.1.0a3` (commit `16a147c`).

| Image | Version | Application state |
| --- | --- | --- |
| `workspace-split-view.png` | `0.1.0a3` | Main paper and local reference in stacked Quick view, with outline and ranked references visible. |
| `workspace-side-by-side.png` | `0.1.0a3` | The same papers in horizontal Quick view with side columns hidden. |
| `reference-pdfs.png` | `0.1.0a3` | Acquire references dialog with available local copies and three missing references selected. |
| `annotations-and-comments.png` | `0.1.0a3` | Saved text highlights, an ink bracket, and two saved phrase-linked comments. |
| `library-catalog.png` | `0.1.0a4` | Five synthetic library entries with Tab name and Metadata status columns, including verified papers and an unconfirmed draft selected for metadata review. |
| `library-refresh-menu.png` | `0.1.0a4` | The Library window with the Refresh metadata dropdown menu open, showing OCR recovery, review-only refresh, and online lookup options. |

The PNGs are direct captures of the application's widgets; UI controls and PDF
content have not been composited or retouched. Both source PDFs and managed
originals were checked for unchanged SHA-256 content after capture. Only the
disposable library's working revision and sidecar comments were saved.

Fonts and window appearance can differ on other systems. Replace these images
when the visible native controls or documented workflows change; screenshots of
the transitional web interface do not represent the native installer.
