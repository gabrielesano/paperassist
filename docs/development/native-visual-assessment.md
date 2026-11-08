# Native visual assessment

Assessment date: 2026-11-07. Scope: the uncommitted native visual changes compared
with the web palette and the workspace contract in `docs/specification.md` and
`.impeccable.md`.

## Verdict

The visual direction is appropriate: a restrained research workspace with warm
surfaces, teal controls, compact tool groups and a generous document canvas.
There are no decorative dashboard, gradient or generic card-grid patterns to
remove. Retain the design and fix the interaction and scaling defects below.

The strongest improvements are the unified References/Comments header, the
recognizable tool grouping, the actionable welcome screen and the distinction
between locally available references and entries requiring work. Collapsed
references still use a delegate rather than allocating a widget tree per entry.
The rendering and explicit PDF save model remain intact.

## Findings and refinement assignments

| Priority | Finding in the assessed implementation | User impact | Terra assignment |
| --- | --- | --- | --- |
| High | The contextual annotation strip hides its only Show highlights control in clean Select mode. | A reader cannot discover how to hide existing highlights without first changing tools. | Toolbar: keep the visibility action reachable independently of the contextual strip; verify saved-document reopening and the overflow menu. |
| Low | Rebuilding an expanded reference card leaves its old widget visible until Qt processes deferred deletion. | A manual event-pumping QA driver can show a stale card over the current one. Normal event-loop behavior must be distinguished from a product layout failure. | References: hide the old widget immediately; verify rebuilding and current child geometry. |
| Medium | References/Comments labels truncate within words at 800 px and 150%. | The two destinations become harder to identify. | References: respect the header's actual minimum width and retain the collapse controls. |
| Medium | Muted text uses `#6b7974`, with contrast ratios of approximately 4.23 on paper and 3.99 on toolbar surfaces. | Secondary instructions and state labels are harder to read; these pairs fall below 4.5:1. | Theme: darken the muted token while preserving the warm palette; check actual foreground/background pairs. |
| Medium | Several custom button styles and the painted reference delegate lack a distinct keyboard focus indicator. | Keyboard users cannot reliably identify the current control. | Theme and References: restore visible focus without erasing checked or selected states; inspect rendered controls. |
| Medium | Broad interface-zoom font rules override explicitly sized heading fonts. | Welcome and dialog headings lose their intended typographic hierarchy. | Theme: scale tagged headings consistently and verify the return to 100%. |

Save moving into overflow at larger interface sizes was also reviewed. It is
reachable through overflow and its shortcut, but placing it before secondary
tools improves visibility at narrow widths. The replacement of a color combo
with custom swatches also requires named, keyboard-accessible overflow actions.

## Verification

Before refinements, the full Python suite passed: 623 tests, four skipped.
This did not cover the live expanded-card scaling defect, illustrating why real
window checks remain necessary.

The initial native window was rendered with Qt's xcb backend in an isolated
Xvfb display at 1400×900 and 800×650, using a disposable QA library and separate
user configuration. The review included clean reading, drawing, expanded
reference actions and live interface scaling. Screenshots and logs stay in the
ignored `tmp/qa/theme-review` directory because they contain QA library content.

The initially reported expanded-card overlap was traced to the QA driver: repeated
`processEvents()` calls did not deliver Qt's deferred deletion events. The current
card had the correct height; an obsolete widget was still visible over it. The
driver now processes `DeferredDelete`, and card replacement hides the old widget
immediately. This is not evidence of a persistent zoom-layout regression in
Gemini's implementation.

The three Terra assignments were implemented and reviewed together. Show
highlights is now available from the main toolbar or its overflow menu, Save
appears before secondary tools, and color swatches offer a named overflow
submenu. Focus indicators distinguish focused controls from checked controls.
The References/Comments header retains complete labels at 150%. Muted text now
uses `#5c6b65`, meeting 4.5:1 on the five tested theme surfaces, and tagged
headings preserve their hierarchy through zoom and reset.

Final checks cover the real Qt window at 100% and 150%, narrow layout, expanded
references, quick view, find, comment composition, Library, acquisition, Sources
and Preferences. Targeted tests exercise rendered focus differences, checked
state preservation, overflow activation and submenu ownership, saved-document
highlight visibility, heading scaling and card replacement.

The integrated Python suite passes 632 tests with four skips; all 61 JavaScript
tests pass. `git diff --check` is clean.

The same four-paper QA benchmark before and after the refinements produced:

| Measurement | Before refinement | After refinement |
| --- | ---: | ---: |
| Median scroll-to-painted latency | 12.0 ms | 11.7 ms |
| Maximum scroll-to-painted latency | 94.4 ms | 91.5 ms |
| First citation lookup | 116.8 ms | 118.2 ms |
| Process-tree PSS after scrolling and 20 seconds idle | 288 MiB | 287 MiB |

These are single runs with warm-cache and allocator variability. They show no
obvious steady-state regression; they do not establish a performance improvement
over Gemini's implementation or compare it with the pre-theme revision.

One lower-priority layout follow-up remains: long acquisition checkbox captions
stay on one line and require horizontal scrolling. They retain their tooltip,
but wrapping them would make large reference lists easier to scan at 150%.

## Follow-up: rendering and navigation regressions

Subsequent user testing identified cases missed by the initial visual review:
blank pages at 400% until scrolling, shifting toolbar controls, excessive space
inside the page group, and missing dedicated second-paper controls.

The high-zoom failure was reproduced at a two-page boundary. Full-page HiDPI
bitmaps and prefetch exceeded the cache budget, evicting a visible page without
requesting it again until another movement. The render plan now budgets visible
pages first, admits prefetch only when it fits, protects visible cache entries,
and rejects obsolete deliveries. At extreme zoom/DPI, raster resolution is capped
to fit the existing budget; displayed zoom, document geometry and annotations
retain their normal scale. Tiled rendering at unrestricted native resolution is
outside this fix.

The rendering regression checks Letter and A4 pages at device ratios 1, 1.5 and
2, actual nonblank viewport pixels, two visible pages under a 64 MiB test budget,
and a settled state without further movement or repeated deliveries. Restoring
the previous render queue causes the boundary test to time out.

The web implementation confirms separate main/second-paper page, find and zoom
controls. Native controls now follow the same targeting rule: the main toolbar
drives the main paper and the quick-view header drives its own paper; editing,
Save and navigation shortcuts still follow the active pane. The quick-view
header uses one row and exposes hidden actions and editable page fields through
overflow. It does not allocate a second navigation tree for main-only tabs.

Fixed, font-aware page-group and zoom-control widths remove the expanding gap
and preserve neighboring button positions when labels change. The unnecessary
toolbar spacer was removed. New navigation tests cover independent page and
zoom operations in both split layouts, tab switching, Find and page entry from
overflow. XCB tests passed at screen scales 1 and 1.5.

The final real-window check at device ratio 1.5 kept both pages visible at 400%
without scrolling: both cache entries were fresh, the scroll position stayed
unchanged, and cached pixmaps totalled 117.6 MiB within the 120 MiB budget.
Main toolbar button rectangles were identical for Fit width and 140% labels.
Screenshots also verify a single quick-view header at 100%/150% interface size
and a narrow 800 px window.

A separate four-paper run at screen scale 1.5 measured median scroll-to-painted
latency 17.7 ms (maximum 105.4 ms) and 337 MiB process-tree PSS after scrolling
and 20 seconds idle. This is a different screen scale from the table above and
is not a before/after performance comparison.

Final follow-up integration: 637 Python tests pass with four skips, and
`git diff --check` is clean. Button-position regressions cover both papers at
80%, 100% and 150% interface size, Fit width/140%/400% document zoom and four
window widths. A styled-width clipping failure found by the full suite at 150%
was corrected using the actual Qt button size hints before the successful rerun.

## Follow-up: citation click behavior

The clarified native behavior is conditional: a citation to a valid local PDF
opens that paper in quick view; a missing or invalid local PDF reveals, expands
and scrolls to its References card. A filter hiding that card is cleared and the
right column opens in References mode. An existing quick view is preserved for
missing references. Grouped citations offer a choice using the same rule.

Unmapped destination names can fall back to complete numeric or author-year
markers, while explicit section, figure, equation and table destinations retain
ordinary navigation. Recognized bibliography destinations wait for pending
analysis and resolve once the current document loads. If no matching entry was
extracted, the sidebar reports that honestly and offers Analyze again instead
of navigating the PDF to its bibliography.

Real xcb mouse-click checks against a QA paper confirmed both branches, including
a hidden Comments column and a filter excluding the missing reference. Main
zoom, scroll, navigation history and PDF bytes remained unchanged; the local
quick-view PDF rendered successfully. The focused XCB citation suite passes
29 tests and the integrated Python suite passes 641 tests with four skips.
The retained web frontend was not changed in this follow-up.

## Limits

This is a Linux assessment. It does not establish Windows/macOS platform styling,
screen-reader behavior or release-package readiness. It makes no claim that a
passing unit suite alone proves visual or accessibility parity. No main-library
PDFs were edited, and no source PDFs were modified.
