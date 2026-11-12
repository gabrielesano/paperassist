# Managed file opening and PDF integration

This record preserves file-delivery implementation and earlier candidate checks.
Current published package acceptance is recorded for
[0.2.0a2](release-0.2.0a2.md).

The 0.2.0a1 candidate combines native-only startup, bundled offline OCR and native
file delivery. The behavior is specified in [the desktop integration record](desktop-plan.md).

## Local acceptance

The native application passes the local full suite (836 tests, 20 expected
skips before building the new installer). This includes managed-copy imports,
exact-content reuse, latest revisions, unchanged external originals, dirty-pane
and comment-draft preservation, two real application owners, repeated CLI
invocations, protocol validation, Finder event buffering and lock/startup recovery.
Real X11 windows also pass the repeated-invocation source test.

All 13 installed Linux checks have passed, including X11/Wayland startup,
positions, explicit Save, Cancel/Discard/Save on close, quick view, upgrades,
rollback, repair and uninstall. The GIO test executes the actual installed
desktop entry from a path containing spaces and `%`, delivers files to a dirty
owner, reselects its unsaved tab, explicitly saves a revision, and preserves
external originals and `mimeapps.list` throughout installation/removal. The old
library automation now selects the row with the keyboard rather than obsolete
pixel coordinates. The close test uses the application's SIGTERM guard so a
headless window manager's shortcut focus cannot prevent the close request.

The local Fedora bundle contains 146,007,918 regular-file bytes (139.24 MiB),
3,377,986 bytes more than the preceding OCR bundle. The QtNetwork binding/library
accounts for almost all of that increase; TLS and network-information plugins
remain excluded. The local installer is 60,268,898 bytes (57.48 MiB).

An eight-page synthetic installed-app benchmark under private Xvfb measured
499 ms for a cold render self-test and 660 ms from launch to a mapped/painted
window. UI + render-worker PSS was 131.62 MiB at 100% and 199.40 MiB after zooming
to 400%. These scoped measurements are not a real-corpus performance guarantee.

## Hosted acceptance

[Build run 42672746164](https://github.com/gabrielesano/paperassist/actions/runs/42672746164)
at `cc0349a` passed Ubuntu 22.04 and Apple Silicon macOS. Ubuntu passed the
GLIBC_2.35 gate, installed-launcher OCR and copied-bundle file delivery without
system Python. The macOS DMG passed signature/resource checks, OCR from the copied
app, actual cold/warm LaunchServices PDF events and repeated CLI delivery to the
same owner. Those events import distinct PDFs before CLI reuse is tested, so
argument delivery cannot conceal a lost Finder event.

[CI run 42672670665](https://github.com/gabrielesano/paperassist/actions/runs/42672670665)
passed Ubuntu/Python 3.11 and Windows/Python 3.11 and 3.13. GitHub completed the
run without creating its fourth job; the same infrastructure condition omitted
the Windows package job and prevented a repeat Cocoa suite from starting.
The preceding Cocoa run exercised the application tests but failed discovery
on a subsequently corrected syntax error in the macOS package test.

The Windows package and a clean complete Cocoa run remain pending. Package runs
can now target a single platform, avoiding repeated Linux/macOS builds when
retrying Windows. Published 0.1.0a4 artifacts do not contain these changes.

## Remaining manual coverage

Clean Ubuntu/Debian GUI installations and interactive Windows/macOS reading,
editing, focus, display scaling and accessibility checks remain open. Local
socket acknowledgement confirms delivery to the owner, not that an operating
system permits foreground focus or that a PDF can be rendered successfully.
Per-file import/render errors are shown in the owner window. Large imports finish
before the window closes; PDF revisions are still saved only by explicit Save.

Windows publisher signing, macOS Developer ID signing/notarization, Intel macOS
and other unverified architectures remain outside this alpha's acceptance.
