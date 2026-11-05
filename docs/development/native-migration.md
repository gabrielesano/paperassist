# Native migration acceptance inventory

This is the current web application's parity contract, inspected on 2026-11-05.
It does not choose a toolkit or PDF engine. Unchecked boxes are acceptance work
for the future native UI, not claims that these features are missing from the web
app. Read this alongside [the product specification](../specification.md) and
[the roadmap](../roadmap.md). The specification's proposed six-entity model is
not fully normalized in today's database; do not build against imaginary tables.

Sources below are relative to `src/paperassist/`. `app`, `reader`, `comments`,
`citation-steps`, `preferences`, `sources`, `shortcuts`, and `app-menu` mean the
corresponding `web/*.js` files. `HTML` and `CSS` mean `web/index.html` and
`web/style.css`. `D` abbreviates `/api/documents/{document_id}`, `R` is
`D/references`, `J` is `/api/jobs/{job_id}`. An em dash means no HTTP operation.
GET is implicit; other methods are written explicitly. Opening a PDF uses `D/pdf`.

Each item has one implementation tag:

- **backend**: Python already owns the data or operation; reuse it directly.
- **UI**: interaction/presentation that a native UI must reproduce.
- **PDF.js**: depends on rendering, text/annotation layers, viewports or editor
  internals; reimplement through the chosen PDF engine.
- **browser-only**: transport/browser machinery without native parity requirements.

## Library and import

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] Open Library from toolbar or welcome screen; show an empty-library hint. | app `library`, `listLibrary`; HTML `library-dialog` | `/api/documents?q=` | UI |
| [ ] Search titles, authors, year, DOI and metadata status without hiding unidentified/scanned PDFs. | app `listLibrary`; `library.py:list_documents` | `/api/documents?q=` | backend |
| [ ] Select multiple PDFs or a directory tree; ignore non-PDF uploads and show selected count/size. | app `importFiles`; HTML `files`, `folder` | POST `/api/import` multipart | UI |
| [ ] Accept filesystem paths for deterministic recursive imports without following directory symlinks. | `workspace.py:import_paths`; `library.py:import_paths` | POST `/api/import` JSON `{paths}` | backend |
| [ ] Copy originals, hash/deduplicate content, preserve source bytes, retain uncertain metadata and manual corrections. | `library.py:import_stream`, `import_paths`; `metadata.py:extract_metadata` | POST `/api/import` | backend |
| [ ] Show incremental progress, cancellation, added/duplicate/review/error counts and per-file failures; import opens no tabs/downloads. | app `importFiles`, `job`; Workspace import methods | POST `/api/import`; `J`; POST `J/cancel` | backend |
| [ ] Edit title, authors, year and DOI; show validation failure and refresh the library/reference views. | app `editMetadata`, metadata-form submit; `library.py:update_metadata` | PATCH `D` | backend |
| [ ] Link a reference through the library picker, rank suggested matches and require the user to verify them. | app `listLibrary`, `linkReference`; `references.py:local_candidates`, `link` | `/api/documents?q=`; PATCH `R/{id}` | backend |

## Tabs

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] Open papers in independent in-app tabs; an already-open file selects its existing owner instead of creating a second editable view. | app `openDocument`, `byId`, `selectTab` | `D/pdf`, `D/view`, `R`, `D/notes` on opening | UI |
| [ ] Switching tabs preserves page, zoom, find query, comment draft, unsaved editors and each tab's quick view. | app `selectTab`, `refreshToolbar`; reader `release`, `show` | `D/view`; `R`, `D/notes` | PDF.js |
| [ ] Show full title in accessible names, shortened visible labels, active tab and modified state; last close restores welcome. | app `renderTabs`, `closeTab`; CSS `.tab`, `.current` | — | UI |
| [ ] Rename with double-click/F2; Enter/blur commits, Escape cancels; an empty label restores the title without changing bibliography or filenames. | app `renameTab`; `library.py:set_label` | PUT `D/label` | backend |
| [ ] Release hidden page canvases and recreate visible pages while retaining unsaved edits; share parsing resources where supported. | reader `release`, `show`, `open`; app `selectTab` | `D/pdf` on open only | PDF.js |
| [ ] Tabs are session-local; there is no automatic saved-tab-session restoration today. | app `state.tabs`, `openDocument` | — | UI |

## Reader view and zoom

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] Render local PDFs with text, links, existing annotations and embedded/system font fallbacks; report load errors. | reader `open`, `PDFViewer`, `PDFLinkService`; bundled PDF.js assets | `D/pdf`; vendor font/cmap/wasm/ICC resources | PDF.js |
| [ ] Render outline hierarchy; use numbered page buttons when no outline exists. | app `showOutline`; `pdf.getOutline`, `links.goToDestination` | `D/pdf` | PDF.js |
| [ ] Previous/next and typed page number clamp to valid pages and show total pages. | app `wireControls`, `refreshControls` | PUT `D/view` | UI |
| [ ] Zoom in/out and fit width; retain a numeric scale or fit mode and one-based page per exact PDF. | app `wireControls`; reader pagesinit, `remember`; Library view methods | GET/PUT `D/view` | backend |
| [ ] Persist page and zoom, not pixel scroll offset, rotation or open tabs; initial restore must not overwrite the saved position. | reader `open`, pagesinit, `remember` | GET/PUT `D/view`; PUT `D/view?if_missing=1` during migration | UI |
| [ ] Each paper has independent case/accent-insensitive find text, all-match highlighting, previous/next and Enter/Shift+Enter. | app `wireControls`, `refreshControls`; reader `PDFFindController` | — after `D/pdf` | PDF.js |
| [ ] Pointer/focus selects the active pane; annotation and Save controls target it; main and second-paper navigation remain explicitly separate. | app callbacks `activate`, `syncSecondPaper`, `refreshToolbar`; reader constructor | — | UI |
| [ ] Top toolbar reflows above the document, contextual options stay in the header, and second-paper controls use available row space. | app `placeSecondPaper`, `updateEditorOptions`; CSS `.toolbar`, `#editor-options` | — | UI |
| [ ] Collapsible outline and References/Comments columns return space to the PDF; the common toggle hides both only when both are visible. | app `setPanel`; CSS `.outline-collapsed`, `.details-collapsed` | GET/PUT `/api/preferences` | UI |
| [ ] Narrow windows retain reachable controls and internal document scrolling; UI scaling never scales a PDF viewer ancestor. | CSS media query at 650px, `.workspace`, `.viewer-container`, `--ui-zoom`; preferences `applyZoom` | `/api/preferences` | UI |

## Quick view

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] A local reference opens one secondary pane per tab without recursively proposing its bibliography. | app `openReference`, `preview` | `D/pdf`, `D/view`, `D/notes` for target | UI |
| [ ] Stack main above secondary by default or place main left/secondary right; retain the user's preference but force stacked below 720px reader width. | app `applyLayout`, callback `toggleLayout`; reader `setLayout`; CSS `[data-layout]` | GET/PUT `/api/preferences` | UI |
| [ ] Resize using the separator: pointer drag, arrows and Home/End; clamp 20–75%, retain per-pane orientation sizes in memory. | `web/split-resize.js:setupSplitResize`; reader constructor; CSS `.split-handle` | — | UI |
| [ ] Panes scroll/zoom independently; primary position survives opening/closing/replacing the secondary. | app `preview`, `closePreview`; reader `setLayout`, `show` | PUT `D/view` | PDF.js |
| [ ] Side-by-side secondary initially fits width until explicitly zoomed; hidden panes defer refits until visible. | reader `fitsWidth`, `show`, `setLayout` | PUT `D/view` | PDF.js |
| [ ] Open in tab moves the existing editable reader, retaining page, zoom and unsaved edits; a new main tab proposes reference acquisition. | app `promote` | `R`; no PUT `D/pdf` | UI |
| [ ] Preview an already editable file read-only, label a dirty owner's preview Last saved version; promotion returns to the editable owner. | app `preview`, `promote`; reader `readOnly`, `setKind` | `D/pdf`, `D/view` | UI |
| [ ] Closing or replacing a dirty preview runs the same Save/Discard/Cancel flow as tabs. | app `prepareClose`, `closePreview`, `preview` | PUT `D/pdf` only after Save choice | UI |

## Selection, highlight, draw and annotation editing

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] Select and copy PDF text, including browser/engine keyboard selection behavior. | reader `PDFViewer`; PDF.js text layer | — after `D/pdf` | PDF.js |
| [ ] Highlight selected text as continuous per-line bands including spaces, keeping column gutters separate. | reader override `getSelectionBoxes`; `web/highlight-lines.js:mergeHighlightLines` | PUT `D/pdf` on Save | PDF.js |
| [ ] Draw ink strokes and freehand highlights, finish strokes on pointer-up and expose modified status immediately. | reader pointerup/endDrawingSession; app editorModes | PUT `D/pdf` on Save | PDF.js |
| [ ] Provide yellow, green, blue, pink and dark-green colors and thickness 1–20 (Draw default 2, Highlight toolbar default 12). | app `colors`, `setParam`; HTML `editor-thickness`; reader highlight palette | PUT `D/pdf` on Save | PDF.js |
| [ ] Select saved/new ink or highlights in Select mode; recolor, delete, move/resize through the editor; keep Select selected after deselection. | reader `editSaved`, `followBorrowedSelection`, `leaveBorrowed`, `setAnnotationColor`, `deleteSelectedAnnotation` | PUT `D/pdf` on Save | PDF.js |
| [ ] Contextual color/delete controls and thickness stay above the PDF; disable irrelevant/read-only controls and suppress PDF.js floating toolbars. | app `updateEditorOptions`; CSS PDF.js editor toolbar rules | — | UI |
| [ ] Toggle highlight visibility without altering PDF bytes. | app `show-highlights` handler; PDF.js `HIGHLIGHT_SHOW_ALL` | — | PDF.js |
| [ ] Undo/redo editor changes; serialized state returning to its saved hash clears dirty status; changing tools alone is clean. | reader `dirty`, `refreshStatus`; shortcuts `editingAction` | — | PDF.js |
| [ ] Do not invent app tools from every PDF.js capability: FreeText/stamp/signature creation is not exposed in today's toolbar. | app `editorModes`; HTML tools | — | UI |

## Comments

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] Comment uses the selected phrase or the current page, leaves Draw/Highlight, and composes in the right column. | app `comment`, pendingAnchor, `showComments` | `D/notes` | UI |
| [ ] Capture quote plus per-line PDF-space rectangles, including multi-page selections; later PDF selections can update an open draft. | comments `captureCommentAnchor`; app `setDraftAnchor` | PUT `D/notes` on Save comment | PDF.js |
| [ ] Save comment explicitly; preserve drafts across tabs, Cancel drops a draft, and errors keep the text. | app `saveCommentDraft`, `showComments`, `prepareClose` | GET/PUT `D/notes` | UI |
| [ ] Show page/text/quote; click Page to navigate to the phrase after zoom, rotation and reopen; support legacy page-only notes and `selection` quotes. | app `showComments`; comments `revealCommentAnchor`, `topLeftDestination` | `D/notes` | PDF.js |
| [ ] Show persistent red dashed unfilled phrase outlines even with panels collapsed; draw focus separately using comment color. | comments `setCommentIndicators`, `drawIndicators`, `draw`, `clearCommentIndicators` | `D/notes` | PDF.js |
| [ ] Click a commented phrase in Select to select its note and open Comments; smallest band wins, later note wins equal-size ties. | comments `commentAt`, `commentAtClient`; app `selectComment`, document click handler | `D/notes` | PDF.js |
| [ ] Recolor a selected comment or delete it using toolbar, list action, Delete/Backspace; Escape deselects; offer one deletion Undo. | app `updateComment`, `offerUndo`, `markSelectedNote`; key handler | GET/PUT `D/notes` | UI |
| [ ] Notes are immediate independent sidecar saves; saving notes never increments a PDF revision. | `library.py:save_notes`; app `saveCommentDraft`, `updateComment` | PUT `D/notes` | backend |

## References column

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] References belong to the main PDF; Comments belong to the active pane; switching detail mode preserves this distinction. | app `switchDetail`, `loadReferences`, `showComments` | `R`, active `D/notes` | UI |
| [ ] Extract/analyze deterministically in a background job; show absent, loading, partial and failed states honestly. | app `analyzeReferences`, `emptyReferences`; `references.py:analyze` | POST `R/analyze`; `J`; `R` | backend |
| [ ] Rank by source-local detected citations; show ambiguous/unknown counts, not global provider citation counts. | app `loadReferences`, `count`, `updateCount`; `references.py:extract`, `_share` | `R` | backend |
| [ ] Display raw/candidate metadata, local availability, invalid self-link warnings, retrieval summaries and attempt details with distinct tones. | app `renderReferences`, `referenceTone`, `retrievalSummary`, `attemptList`; references `_resolve` | `R` | UI |
| [ ] Filter every term across label/authors/year/DOI/title/raw, ignoring case/accents; keep query per tab and show result/empty counts. | app `referenceFilter`; `web/reference-filter.js:referenceMatcher` | — after `R` | UI |
| [ ] Reference title opens quick view or expands missing-paper actions; card/count clicks step citations once available/expanded. | app `cardClick`, `openReference`, `renderReferences` | `R`; target `D/pdf` if local | UI |
| [ ] Change or reset manual links without deleting files; reject a new self-link and preserve flagged invalid legacy links for review. | app `linkReference`, `resetReferenceLink`; `references.py:link`, `_conflict` | PATCH `R/{id}` | backend |
| [ ] Reanalysis preserves manual links, retrieval and corroborated identities; unmatched old entries remain visible for verification. | `references.py:analyze`, `_resolve` | POST `R/analyze` | backend |

## Citation stepping

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] Consume only confidently mapped main-paper internal citation links; fall back to ordinary PDF navigation otherwise. | reader link click handler; app callback `citation` | `R`; target `D/pdf` | PDF.js |
| [ ] Named destinations map directly; numeric groups/ranges and unambiguous author-year links map entries; grouped matches offer a choice. | app callback `citation` | `R` | UI |
| [ ] Locate linked citations before the destination's bibliography page; order by nearest text item and merge touching links to one event. | citation-steps `linkIndex`, `locateLinked`, `bibliographyPage`, `nearestItem`, `mergeLinks` | — after `D/pdf`, `R` | PDF.js |
| [ ] Fall back to server occurrence markers; normalize whitespace, accents, ligatures, hyphens and dash styles, matching repeated markers in order. | citation-steps `locateByText`, `buildTextIndex`, `findMarkers` | `R` occurrence data | PDF.js |
| [ ] First click starts at/after current reading position, later clicks advance and wrap; briefly outline exact rectangles without dirtying the PDF. | app `stepCitation`; citation-steps `nextCitationIndex`, `readingPosition`, `citationPosition`, `revealCitation` | — | PDF.js |
| [ ] Page-only occurrences still navigate and disclose that exact text could not be located; counts update from located occurrences. | app `citationNote`, `updateCount`, `stepCitation`; citation-steps `revealCitation` | — | UI |

## Retrieval and downloads

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] Each newly opened main tab offers all/selected reference PDFs or Continue reading; existing tab switches/preview/import do not cascade. | app `openDocument`, `loadReferences(propose)`, `promote`; HTML `acquire-dialog` | `R`; POST `R/analyze` when needed | UI |
| [ ] Select all, Clear all, individual boxes and live count; local/legacy entries disabled; retain selection per source in the browser today. | app `renderAcquire`, `saveSelection`, `updateSelectionCount` | —; `paperassist-selection-{id}` remains localStorage | UI |
| [ ] Show provider configuration/enabled status before acquisition; starting a search is explicit and successful single search opens its PDF. | app `showSources`, `providerSummary`, `runAcquisition`, `showLinked` | `/api/config`; POST `R/download`; `J`; `R` | UI |
| [ ] Report per-provider progress, cancellation between references, outcomes and unsearched counts; reading remains usable. | app `runAcquisition`, `job`; Workspace `download_references`, `cancel_job` | POST `R/download`; `J`; POST `J/cancel` | backend |
| [ ] Recheck local reuse under the download lock, fetch legitimate open-access PDFs, validate identity/content, publish managed copies and persist each outcome. | Workspace `download_references`; `retrieval.py:download`; `references.py:link`, `set_retrieval` | POST `R/download` | backend |
| [ ] Distinguish downloaded/local/not_identified/ambiguous/blocked/not_found/failed; old unversioned results show Not searched. | app `currentRetrieval`, `referenceStatus`; retrieval result version 2 | `R` | backend |
| [ ] Offer publisher/DOI, open-PDF, prefilled Google/article/PDF searches and configured library-proxy browser links where relevant. | app `openPdfLink`, `libraryLinks`, `retrievalSummary`; sources `proxyUrl` | External URLs; no automatic proxy downloads | UI |
| [ ] Check downloads folder per reference, identify candidates first, show reasons/size and ask explicit choice for ambiguous candidates. | app `checkFolder`, `folderResult`, `linkFolderFile`; `inbox.py:check_folder`, `link_file` | POST `R/{id}/check-folder`; `J`; POST `R/{id}/link-file` | backend |
| [ ] Missing downloads-folder configuration opens Preferences at that field; repair earlier questionable downloads in a serialized job. | app `checkFolder`, repair button; Workspace `repair_downloads` | POST `R/repair-downloads`; `J` | backend |
| [ ] Expose DOI metadata resolution through the existing API even though the current UI does not call it. | Workspace `resolve_reference` | POST `R/{id}/resolve`; `J` | backend |

## Settings and preferences

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] Brand menu opens Preferences, Sources, Keyboard shortcuts and About; support arrows/Home/End, Enter/Space, Escape, outside click and Tab. | app-menu `installAppMenu`; HTML `app-menu`; CSS `.app-menu` | — | UI |
| [ ] About shows app version, license, library path and project information. | app About action; HTML `about-dialog` | `/api/config` | UI |
| [ ] Interface size choices 80/90/100/110/125/150% apply immediately to chrome only and persist per user across libraries. | preferences `parseZoom`, `applyZoom`, `createPreferencesDialog`; CSS `--ui-zoom` | GET/PUT `/api/preferences` | UI |
| [ ] Save/clear manual-download folder explicitly; Browse fills but does not save; validation rejects missing/nonabsolute/library-internal folders. | preferences `createPreferencesDialog`; `settings.py:_inbox` | `/api/settings`; PUT `/api/settings`; POST `/api/choose-folder` | backend |
| [ ] Inspect library folder, confirm a concrete Open/Move plan, reject stale/fixed/in-use targets or active jobs; copying retains old files. | preferences location flow; Workspace `inspect_library`, `prepare_switch`; `location.py` | POST `/api/library/inspect`, `/api/library/switch` | backend |
| [ ] Require unsaved work and other preference edits to be resolved before switching; wait for new server then reload in the web host. | preferences `canLeave`, `waitForLibrary`; `server.py:serve` fallback loop | `/api/config` polling | UI |
| [ ] Source switches, contact email, optional/required keys and HTTPS proxy use explicit Save; omitted values stay and empty/null clears. | sources `createSourcesDialog`; `settings.py:Settings`, `_merge`, `update` | GET/PUT `/api/settings`; `/api/config` | backend |
| [ ] Mask secrets, show only configured state/last four characters, stage Remove/Keep, and check access only against saved settings. | sources `renderRow`, `changes`, check handler; Settings `view`; Workspace `check_settings` | POST `/api/settings/check` | backend |
| [ ] Keep credentials/inbox in the existing library settings file and environment fallbacks; only interface preferences/library choice live in user config. | `settings.py:load`, `preferences`; `paths.py:config_file`, `locate` | `/api/settings`, `/api/preferences` | backend |
| [ ] Import localStorage values only for missing backend fields; existing backend state always wins on reload/restart. | app `loadPreferences`; reader `open` | PUT `/api/preferences?if_missing=1`, `D/view?if_missing=1` | browser-only |
| [ ] Token/origin/loopback-host protection and HTTP upload staging remain in the web path, without becoming native UI requirements. | `server.py:protect`, `_write_token`, `imports`; Workspace `import_files` | `/api/config`; all writes | browser-only |

## Keyboard shortcuts

All bindings come from `shortcuts.shortcuts`, `shortcutFor`, `installShortcuts`,
plus app Save/comment handlers and the PDF.js editing layer. Mod means Ctrl or
Command. Suppress plain bindings while typing, composing or a modal is open;
anywhere bindings may work in fields. Commands target the last active pane unless
a specific navigation control group is used. No new HTTP endpoints are involved;
Save uses PUT `D/pdf`, navigation uses PUT `D/view`, deletion of notes uses PUT
`D/notes`, Preferences uses `/api/preferences`.

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] `?` help, Mod+`,` Preferences, `L` Library, Mod+`S` explicit PDF Save. | shortcuts table; app Save handler | As above; `/api/documents` for Library | UI |
| [ ] `N/J` next page, `P/K` previous, Home/End first/last, `G` page field, Enter returns focus to the document. | shortcuts table, `focusControl`, `focusDocument` | PUT `D/view` | UI |
| [ ] Arrows, Space/Shift+Space and Page Up/Down scroll a focused document. | browser scrolling; shortcuts help | — | UI |
| [ ] Mod+`F` focuses active paper Find; Enter/Shift+Enter moves matches; Esc leaves toolbar field without clearing its query. | shortcuts `fieldReader`, `focusControl`; app `wireControls` | — | UI |
| [ ] Mod+`+`/`=` zoom in, Mod+`-` out, Mod+`0` fit width. | shortcuts `clickControl` | PUT `D/view` | UI |
| [ ] `V/D/H/C` Select/Draw/Highlight/Comment; Delete/Backspace deletes selection; Mod+Z undo and Mod+Shift+Z/Ctrl+Y redo. | shortcuts `editingAction`; app comment key handler; PDF.js editing actions | PDF/notes writes only as described above | PDF.js |
| [ ] `,`/`.` previous/next tab without wrap, `X` close tab, F2 rename only a focused tab title. | shortcuts `stepTab`, table; app `renameTab` | PUT `D/label` on commit | UI |
| [ ] `B` panels, `O` outline, `1/2` References/Comments, `/` filter; Esc clears filter then returns to PDF on next press. | shortcuts `showPanel`, table | PUT `/api/preferences`; `R`, `D/notes` | UI |
| [ ] Tab/Shift+Tab traverse controls, Enter/Space activate; dialog Esc closes/cancels and restores appropriate focus. | shortcuts help; HTML dialogs; app-menu | — | UI |
| [ ] Do not claim browser-reserved Ctrl+T/W/Tab or Alt+arrows as existing app commands. | shortcuts file preamble | — | browser-only |

## Save, close and dirty semantics

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] PDF writes occur only through explicit Save or an explicit Save choice in the close prompt; navigation/timers/hiding/promotion/close itself never save PDF bytes. | reader `explicitSave`, `close`, `release`; app `saveReader`, `prepareClose` | PUT `D/pdf` | UI |
| [ ] Compare serialized annotation hash with saved hash for dirty state, including undo/redo/move/resize; display modified/saving/saved/error/read-only status. | reader `dirty`, `refreshStatus`, `explicitSave`; app `refreshToolbar` | — until Save | PDF.js |
| [ ] Commit current stroke/editor before testing dirty; block editing during serialization/write; failed/conflicting saves retain edits and require retry. | reader `commitEdits`, `explicitSave`; `library.py:save_pdf` | PUT `D/pdf`, required `X-PaperAssist-Revision`; conflict 409 | backend |
| [ ] Save validated working snapshots with increasing revisions, leaving original bytes immutable; stale revisions cannot overwrite current ones. | `library.py:save_pdf`, `get_path` | PUT/GET `D/pdf`; `D/pdf?original=1` | backend |
| [ ] Closing/replacing asks Save/Discard/Cancel for nonempty drafts and dirty PDFs; Cancel leaves panes and edits intact. | app `askUnsaved`, `prepareClose`, `closeReader`, `closeTab`, `preview` | PUT `D/notes` or `D/pdf` only for Save | UI |
| [ ] Collect all pane close decisions before removing a multi-pane tab; reject closes while saving. | app `prepareClose`; reader `close` | — | UI |
| [ ] Browser unload warns about dirty/saving PDFs or comment drafts; no timer or unload PDF write. | app `hasUnsavedEdits`, beforeunload; desktop `allowsUnload` | — | browser-only |
| [ ] View/preference persistence is independent of explicit PDF saving and must not mark a PDF dirty. | reader `remember`; Workspace/Library view methods | PUT `D/view`, `/api/preferences` | backend |

## Desktop-window behaviors

| Acceptance behavior | Source / function | HTTP | Tag |
| --- | --- | --- | --- |
| [ ] Desktop command opens a titled resizable window (1400×900, minimum 800×600) with text selection and persistent user profile. | `desktop.py:run`; `web/desktop.js:installDesktopBridge` | Current host uses the loopback app | UI |
| [ ] Window close uses the same async Save/Discard/Cancel path once; duplicate close requests share pending work and errors keep the window open. | DesktopBridge `closing`, `_request_close`, `_finish_close`; JS `requestClose`; app `prepareClose` | Save endpoints only after choice | UI |
| [ ] A window owns/stops only the server it started; attach to an existing locked library's recorded URL and leave that server running on exit. | `desktop.py:ManagedServer`, `run`; workspace `library_lock`; server `running_url` | `/api/config` via web host | backend |
| [ ] Switch/restart and startup fallback retain process ownership until requests/jobs finish; SIGTERM cleanup does not kill unrelated processes. | server `serve`, `_serve_library`; Workspace `close`; desktop `ManagedServer.stop` | `/api/library/switch` in web host | backend |
| [ ] Native folder chooser is serialized in the HTTP host, supports cancellation/unavailable errors and returns a path without importing or saving. | server `choose_folder_route`; `picker.py:choose_folder`; desktop chooser callback | POST `/api/choose-folder` | UI |
| [ ] Optional desktop dependency failure gives a useful message; browser CLI remains usable. | `desktop.py:run`; `cli.py:desktop_main`, `main` | — | backend |
| [ ] pywebview RPC, Chromium selection workaround, persistent webview storage, browser URL security and beforeunload are host-specific, not native engine contracts. | reader `enableSelectionRendering`; `web/desktop.js`; DesktopBridge origin check | — | browser-only |

## Pure JavaScript logic to port or consciously replace

None of these requires a JavaScript runtime in the native backend. Port only the
parts the native reader uses; retain their edge cases as acceptance tests. The
Python reference extractor already exists and must not be replaced by an LLM.

| Module / pure functions | Existing tests | Native disposition |
| --- | --- | --- |
| `web/highlight-lines.js:mergeHighlightLines` | `tests/highlight-lines.test.mjs` | Port band/gutter/rotation merging to Python if engine word boxes need it. |
| `web/citation-steps.js:buildTextIndex`, `findMarkers`, `matchRects`, `nearestItem`, `mergeLinks`, `nextCitationIndex` | `tests/citation-steps.test.mjs` | Port Unicode normalization, repeated-match tracking, link/event grouping and stepping; adapt text geometry input to the engine. The rest of the module uses PDF.js/DOM. |
| `web/reference-filter.js:referenceMatcher` | `tests/reference-filter.test.mjs` | Port case/accent folding and every-word matching for the reference column. |
| `web/comments.js:clientRectToPdf`, `pdfRectToViewport`, `topLeftDestination`, `commentAt` | `tests/comment-anchors.test.mjs` | Port hit testing/coordinate contracts; replace PDF.js viewport/DOM calls. This is a mixed module, not wholly pure. |
| `web/sources.js:proxyUrl` | `tests/sources.test.mjs` | Port safe web URL composition (encoded `{url}` vs raw prefix), or centralize in existing Python citation URL helpers. Other functions build DOM. |
| `web/preferences.js:parseZoom`, `ZOOM_CHOICES` | `tests/preferences.test.mjs` | Validation is also enforced by Python `update_preferences`; native can consume those same six choices. Other functions build the dialog. |
| `web/shortcuts.js:shortcutFor`, key predicates, `controlId` | `tests/shortcuts.test.mjs` | Translate the command table and typing/modal/composition guards into toolkit actions; DOM dispatch itself needs replacement. |
| `web/split-resize.js` clamping, orientation and key math inside `setupSplitResize` | `tests/split-resize.test.mjs` | Use the native splitter with equivalent constraints/keyboard behavior. The module itself installs DOM listeners. |
| `web/desktop.js:installDesktopBridge` close-promise coordination | `tests/desktop.test.mjs` | Replace browser RPC with the native asynchronous close guard. |

`app.js` also contains unextracted numeric/author-year citation mapping, retrieval
presentation/status mapping, acquisition counting and note structural matching.
These have no standalone unit suite; use the relevant checklist and browser
round trips when porting. `app-menu.js` and `reader.js` are interface/engine code,
not pure-logic modules.

## HTTP-independent workspace contract

`paperassist.workspace` imports no Flask and no desktop toolkit. The caller owns
`library_lock(directory)` for the entire active workspace lifetime, just as CLI
and HTTP hosts already do. Constructing `Workspace` alone does not take a second
OS lock; this preserves `create_app` test/embedding behavior and avoids nested
locks in `serve`. A native host should use this lifetime pattern:

```python
from paperassist.workspace import Workspace, library_lock

with library_lock(directory):
    workspace = Workspace(directory)
    try:
        started = workspace.import_paths(selected_paths)
        # Poll workspace.job_status(started['id']) from the host's event loop.
        # Use workspace.cancel_job(started['id']) on explicit cancellation.
    finally:
        workspace.close()
```

The existing direct `workspace.library` API owns document listing, import/storage,
metadata, labels, notes and revision-checked PDF saves. `references` functions
accept that Library for listing/linking. Workspace adds `get_settings`,
`update_settings`, `check_settings`, `inspect_library`, `prepare_switch`,
`import_paths`, `import_files`, `analyze_references`, `resolve_reference`,
`download_references`, `check_folder`, `link_file`, `repair_downloads`,
`job_status`, `cancel_job`, `get_view`, `save_view`, `get_preferences` and
`save_preferences`. Uploaded input is an iterable of `(filename, binary_stream)`;
staging finishes before the method returns and the job cleans it up.

Jobs retain the existing `{id, status, progress?, result?, error?}` JSON shape.
Statuses are pending/running/completed/failed/cancelled. Import progress is
`{completed, total}`; download progress can also include `reference_id, provider`.
Two worker threads, eight admitted jobs and fifty retained records remain the
bounds. Completed/failed/cancelled records are evicted first. Polling returns a
snapshot. Cancellation is cooperative between files/references; it does not abort
an in-flight provider request, analysis or folder scan. Closing sets cancellation
and waits for admitted work, including queued upload cleanup. Jobs are in-memory,
not restartable queues. Exceptions are `JobsFull` (HTTP 429), `WorkspaceConflict`
(409), missing IDs (404) and validation errors (400). PDF revision conflicts still
map to 409. Flask still owns same-origin/write-token enforcement and file responses.

Download, folder checking/linking and repair share the same workspace lock as
before. Library writes retain its RLock/SQLite transactions. Preparing a library
switch rechecks the plan and target process lock, rejects active jobs, copies when
needed and persists the choice. The host then closes this workspace and releases
its process lock before opening the target; HTTP defers that step until its
response is sent and keeps its existing fallback if target startup fails. The
backend has no widget callbacks or dependency on a particular PDF engine.

### Persisted view and interface state

- `document_views(document_id PRIMARY KEY REFERENCES documents(id), content TEXT)`
  stores JSON `{page, scale}`. The immutable original's document ID/SHA-256 owns the
  state, just like notes and working revisions. Correcting metadata, changing a
  preferred work file, or saving a revision never transfers it to another PDF.
- GET `D/view` returns that object, or JSON `null` when absent. PUT validates a
  one-based page within this PDF and a positive finite numeric scale (number or
  numeric string, at most 100) or `auto`, `page-width`, `page-fit`, `page-actual`.
  It replaces only this file's view. PUT `?if_missing=1` atomically inserts without
  overwriting an existing value. This flag is for importing legacy browser state.
- The platform user `paths.config_file()` retains `library` and now a
  `preferences` object: `ui_zoom` (one of .8/.9/1/1.1/1.25/1.5),
  `quick_view_layout` (`stacked`/`side`), `outline_expanded` and `details_expanded`
  (booleans). GET `/api/preferences` returns only stored fields (`{}` initially);
  PUT merges validated fields, preserving omitted fields and the library choice.
  `?if_missing=1` fills absent keys atomically. Config writes share a process lock
  and atomic replacement. Credentials remain in their existing library settings.
- Defaults are UI 100%, stacked, both panels open, page 1/fit width. Missing keys
  stay distinguishable from defaults for migration. With the upgraded backend, new state is never written
  back to localStorage; old entries are read only for missing backend values.
  Migration is per browser origin/profile, without a global migration marker.
  A 404-only localStorage fallback keeps already-running older Python services
  usable with source-served JS until their owner restarts them; other errors
  remain visible and never silently fall back.
- Legacy view keys contain only a numeric document ID, with no library identity.
  They cannot be disambiguated retrospectively if that browser origin previously
  served multiple libraries. A present backend value always wins. Acquisition
  checkbox localStorage is deliberately outside this task's requested state move.

## Saved annotation interchange format

The pinned bundled engine is **PDF.js 6.4.299** (legacy distribution). Evidence is
both the actual serializer in `web/vendor/pdfjs/build/pdf.worker.mjs`
(`HighlightAnnotation.createNewDict/createNewAppearanceStream`, the corresponding
`InkAnnotation` methods and `Annotation.createNewAnnotation`) and real saved
bytes in `tests/fixtures/pdfjs-annotations.pdf`. That three-page fixture contains
only synthetic text, five selected-text highlights, one black width-4 drawing and
one freehand highlight. It was created through headless Brave using the real
app's toolbar, pointer gestures and explicit Save, on a disposable library. The
matching `pdfjs-notes.json` came from the actual notes endpoint after selecting
two lines and submitting Save comment. Neither fixture was hand-authored to
imitate an annotation serializer. `tests/test_native_fixtures.py` reads them with
pypdf and checks geometry, colors, appearances and independent note persistence.

The save path is reader `explicitSave` → `pdf.saveDocument()` → PUT `D/pdf` with
expected revision → `Library.save_pdf`. PDF.js appends an incremental update to
its loaded bytes; the backend validates the resulting PDF and publishes a full
new `working/{document_id}-{revision}.pdf` snapshot, updating SQLite revision.
The original's SHA-256 and bytes stay unchanged. The fixture retains the complete
synthetic original as its byte prefix; another explicit Save appended its freehand
highlight. No annotations are injected by the backend.

### Common dictionary and appearance fields

New app-created annotations have `/Type /Annot`, `/Subtype`, `/Rect [x0 y0 x1 y1]`,
`/CreationDate (D:...)`, `/F 4` (Print), `/Rotate` (0 in this fixture), `/C [r g b]`
with floating RGB channels in 0–1, `/CA 1`, and `/AP << /N appearance-ref >>`.
Coordinates are PDF user-space coordinates, not CSS pixels or normalized fractions.
Rectangles include editor margins and need not equal the text quad bounds.
Existing annotations are updated from their original dictionary, with `/M` for
modification time; unknown existing keys are not intentionally stripped. The
serializer may write `/T` when given a user, but PaperAssist does not supply an
author and these samples have none.

Each normal appearance is an indirect Form XObject with `/Type /XObject`,
`/Subtype /Form`, `/FormType 1`, `/BBox` matching the annotation rect and a stream
(`/Length`, sometimes `/Filter /FlateDecode` after serialization). Consumers must
read the decoded stream and its resources. These appearances are present and
render independently in Poppler; do not assume a receiving reader will regenerate
them from geometry. Colors in painting commands are rounded relative to `/C`.

| Tool | Geometry/extra keys | Normal appearance in saved bytes |
| --- | --- | --- |
| Selected-text Highlight | `/Subtype /Highlight`, `/Border [0 0 0]`, `/QuadPoints` with eight numbers per text band, ordered top-left, top-right, bottom-left, bottom-right in the fixture | RGB nonstroking color (`rg`); `/R0 gs`; closed band paths (`m`, `l`, `h`) filled with `f*`; `/Resources/ExtGState/R0/BM /Multiply`. |
| Draw | `/Subtype /Ink`, `/InkList` array of sampled point arrays, `/BS << /W thickness >>`; no `/IT` in an ordinary drawing | Width `w`, round line caps and joins `1 J 1 j`, stroking color (`G` for black or RGB `RG`), move/Bezier curves (`m`, `c`), final stroke `S`. The sample starts `4 w 1 J 1 j` and `0 G`. |
| Freehand Highlight (drag outside text) | `/Subtype /Ink`, **`/IT /InkHighlight`**, `/InkList`, `/BS /W` (12 in sample); this is not a `/Highlight` with QuadPoints | Filled closed curved outline (`m`, `c`, `h f`), nonstroking RGB and `/R0 gs` with `/BM /Multiply`. `/InkList` is its sampled centerline; appearance is its filled shape. |

The five toolbar/highlighter colors are:

| Color | Hex | `/C` in saved PDF (approximately) |
| --- | --- | --- |
| Yellow | `#F5D76E` | `[0.96078431, 0.84313725, 0.43137255]` |
| Green | `#8FD3AA` | `[0.56078431, 0.82745098, 0.66666667]` |
| Blue | `#79C5DF` | `[0.47450980, 0.77254902, 0.87450980]` |
| Pink | `#E9A9BC` | `[0.91372549, 0.66274510, 0.73725490]` |
| Dark green | `#243F3A` | `[0.14117647, 0.24705882, 0.22745098]` |

Draw defaults to black (the same color controls can change it). The app exposes
thickness 1–20 with initial Draw 2 and freehand Highlight 12. Selected-text bands
follow the selected text's geometry; the thickness control governs freehand
highlights. There is no opacity UI: both current drawing and highlighting default
to **1**, confirmed in all seven saved annotations. Multiply blending supplies the
highlight effect; do not substitute a presumed 0.4 opacity. Serializer support
for non-1 opacity writes `/CA` on the annotation plus `CA` in the Ink stroke's
ExtGState or `ca` in a highlight fill's ExtGState; this is code-derived support,
not an app control exercised by these fixtures. Show highlights is a display
preference, not an opacity edit.

### Identifiers and recognition

**New annotations in this app do not get `/NM`.** The synthetic saved dictionaries
also contain no `/Contents`, `/Popup`, `/T` or PaperAssist-specific ownership key.
Do not require `/NM`, and do not invent a convention that marks all existing
annotations as “ours.” PDF.js identifies saved annotations by their indirect
object references (exposed as IDs such as `13R`); newly created editors have
session-local IDs. The editor's `annotationElementId`/serialized `id` refers back
to the original object so updates and deletions replace that annotation instead
of creating duplicates. These JavaScript IDs are not a custom `/NM` in the PDF.

The app recognizes editable saved ink/highlights through PDF.js's subtype,
annotation ID and editor APIs (`reader` click handler, `editSaved`, drawn path hit
testing). PDF.js uses `/IT /InkHighlight` to distinguish a freehand highlighter
from ordinary Ink. That key is semantically important. Existing `/NM`, author,
popup or other unknown keys from external PDFs should be preserved when editing;
they do not establish PaperAssist ownership. No subtype restriction is enforced
by the backend: it stores valid submitted PDFs, while the toolbar exposes only
Select/Draw/Highlight/Comment. PDF.js FreeText/stamp/signature serialization exists
in the bundled engine but is not an exposed app creation workflow.

A native engine needs to read, render, recolor and delete the original reference
objects and maintain appropriate appearances. Editing a copy of these fixtures,
saving and reopening in both engines is a concrete interoperability check; the
fixtures do not prove Acrobat/Preview/Okular/Zotero compatibility.

## Saved notes/comments format

Notes are **not PDF annotations** and are not written into the PDF on Save.
`Library.save_notes` stores an entire JSON list in SQLite
`notes(document_id PRIMARY KEY REFERENCES documents(id), content TEXT NOT NULL)`.
GET/PUT `D/notes` transfers that list. Document IDs identify exact immutable
originals, including when multiple files share a work or DOI. No separate sidecar
file is currently required; the fixture JSON is an export of the saved record.

The browser creates these fields in `app.saveCommentDraft`:

```json
[
  {
    "page": 1,
    "text": "Synthetic phrase comment for native compatibility.",
    "quote": "A phrase comment anchors this synthetic sentence.\nSecond line of the comment anchor.",
    "anchor": {
      "version": 1,
      "pages": [
        {
          "page": 1,
          "rects": [
            [49.997975, 556.481478, 415.544582, 574.164834],
            [49.997975, 526.464986, 306.159129, 544.148342]
          ]
        }
      ]
    }
  }
]
```

Coordinates above are rounded for readability; the fixture retains the actual
floating-point values. `page` is one-based, `text` is trimmed, `quote` is the
trimmed selection text (including internal line breaks), and anchor `pages`
contains one entry per selected page with per-line `[xmin, ymin, xmax, ymax]`
rectangles in that page's PDF user space. Conversion uses the PDF.js viewport
against text-layer content bounds (excluding page borders). Multi-page selections
use the first anchored page as the outer `page`. No-selection notes have just
`{page, text}`. Legacy `selection` is accepted for displaying a quote. Recoloring
adds a `color` CSS hex string to the saved object. Persistent indicators remain
red dashed outlines regardless of that color; the focused outline uses the note
color, with `#aa700f` fallback.

There are **no note IDs, timestamps, author fields or per-note revisions** in the
current format. Ordering in the JSON list is significant. The UI finds a note
for update/delete by `JSON.stringify` equality, uses its array position for Undo,
and rejects an edit if that structural record has changed. Identical duplicates
can therefore match the first equal note. Hit testing chooses the smallest
containing band and the later note on a tie. A native implementation should
preserve existing records exactly; adding stable IDs would be a separate schema
change, not an assumption in this migration.

The backend validates only that notes are a list, stores arbitrary record fields,
and replaces the whole list without revision checking. Concurrent independent
note writers can overwrite each other's changes; PDF revision checking does not
protect notes. PDF Save/Discard does not undo already saved notes. Unsaved comment
text is only a UI draft and participates in the close prompt separately.

## Verification record and migration questions

Backend unit tests exercise the Workspace directly without HTTP: file-specific
view persistence across metadata edits/revisions/reopen, separate same-DOI files,
user preferences across libraries, missing-only migration, upload cleanup,
progress/cancellation, bounded jobs, failures, folder/repair/download operations
and process-lock conflicts. API tests cover missing/invalid IDs, token protection,
unchanged PDF revisions, restart and partial migration. Existing server tests
remain the compatibility check for established URLs and responses.

Real headless Brave checks use a separate temporary library, temporary user config
and a private test port, with the installed Brave binary and Playwright. Verified:
legacy page/zoom and all four interface preferences import; page/zoom and size
changes survive reload; existing backend state wins over stale browser entries;
quick view side → stacked → side; server restart retaining the saved
page/zoom, interface size and panel state; the synthetic phrase note, all five highlights,
width-4 Ink and freehand InkHighlight save through the real UI without page errors.
Brave also verified preview-to-tab promotion, a read-only preview returning to its
existing editable owner, and the 404-only fallback for unupgraded servers. The
completed migration made exactly two initial writes (preferences and view), with
no additional migration writes after reload. Poppler rendered the completed
fixture and pypdf inspected every annotation. Final suites: 353 Python tests and
61 Node tests passed (including concurrently added native-reader tests); the
preparation adds 16 Python tests and 3 Node tests.

Migration decisions/questions to retain:

- Keep the toolkit and engine outside Workspace; their threading/render/process
  strategy is outside this backend contract.
- Page/scale parity deliberately does not add pixel scroll restoration or session
  tabs. Decide those separately if wanted.
- Specify native engine save/revision/appearance behavior against these fixtures,
  including `/IT /InkHighlight`, external annotations and rotation/crop boxes.
- Stable note IDs and conflict-safe note updates would improve concurrent writers,
  but are not silently introduced by this preparation.
- Acquisition checkbox selections remain browser-local. Decide whether to migrate
  them when implementing the native acquisition dialog.
- Native external links should open the system browser, including proxy/search
  links; pywebview transport and browser origin protection are not native features.
- Accessibility of custom PDF painting, platform file opening/associations,
  packaging and cross-platform annotation interoperability still need native QA.
