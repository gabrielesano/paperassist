# Managed file opening and PDF integration

The 0.2.0a1 candidate combines native-only startup, bundled offline OCR and native
file delivery. The behavior is specified in [the desktop integration record](desktop-plan.md).

## Verification in progress

Local focused tests cover managed-copy imports, exact-content reuse, latest
revisions, unchanged external originals, dirty-pane/comment-draft preservation,
two independent library owners, repeated CLI invocations, protocol validation,
Finder event buffering and lock/startup recovery. The initial full regression run
found a window retained across a library switch; releasing the receiver/server
reference fixed it, and its dedicated regression test passes.

Fresh package and hosted Ubuntu/Windows/macOS acceptance results will be recorded
here before treating the candidate as verified. Published 0.1.0a4 artifacts do
not contain these changes.

## Remaining manual coverage

Clean Ubuntu/Debian GUI installations and interactive Windows/macOS reading,
editing, focus, display scaling and accessibility checks remain open. Local
socket acknowledgement confirms delivery to the owner, not that an operating
system permits foreground focus or that a PDF can be rendered successfully.
Per-file import/render errors are shown in the owner window. Large imports finish
before the window closes; PDF revisions are still saved only by explicit Save.

Windows publisher signing, macOS Developer ID signing/notarization, Intel macOS
and other unverified architectures remain outside this alpha's acceptance.
