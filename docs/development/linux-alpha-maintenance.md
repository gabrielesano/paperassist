# Linux alpha maintenance follow-up: 0.1.0a3

Updated 2026-11-08. This candidate addresses the three findings from the
independent review of 0.1.0a2. Prior installers and review evidence are preserved.

## Changes

Install, repair, and uninstall hold a shared nonblocking `flock` throughout their
mutations. The stable `<install-dir>.lock.flock` file intentionally persists after
maintenance: removing it could let processes lock different inodes. A PID marker
is retained for detecting active older installers; stale-marker reclamation is
serialized under the flock. The installer `--uninstall` path retains its lock
without handing off to a second process. `flock` from util-linux is an explicit
installation and maintenance dependency.

An interrupted first installation records integration paths and expected file
contents before publishing launcher, desktop entry, and icons. Recovery removes
or restores an integration file only if its contents still match the transaction.
Modified files are preserved, and custom launcher directories can be recovered
without repeating `--bin-dir`. Existing launcher and desktop symlinks are refused
before replacement. Icon publication uses a temporary file and rename.

When no committed installation exists to restore, `--repair` exits 1 after
cleaning the interrupted first installation. A normal installer retry then works.
The interruption tests use SIGKILL; power-loss durability is not established.

The CI GLIBC gate now calls `packaging/audit_glibc.py`. Unreadable files, failed
`readelf` calls, a missing or uninspected executable, and missing executable GLIBC
requirements cause failure with the affected path. Successfully inspected ELF
libraries without GLIBC requirements are allowed. Requirements above GLIBC_2.35
are rejected. CI also runs the audit and concurrent-maintenance regression tests.

## Candidate artifacts

| Artifact | Size / SHA-256 |
| --- | --- |
| `dist/PaperAssist-0.1.0a3-linux-x86_64.sh` | 56,259,465 bytes |
| Installer SHA-256 | `4730f4831db708bdf708c8ba28b9c21fd36924b4906f8a545994e03e4fcbbf69` |
| Embedded payload | 56,214,390 bytes, offset 45,075 |
| Payload SHA-256 | `f80de45df3c9d8a87cea89c5d705dc548a3ee17f18a6186201bae07a5132d96a` |

The current bundle is `dist/PaperAssist/`, with notices and build provenance.
`dist/SHA256SUMS` covers a3, preserved a2 and a1 installers, and current bundle
notices/provenance. The prior unpacked a2 bundle is retained under ignored QA
storage. The source version, frozen executable, provenance, and installer report
0.1.0a3. The build records a dirty source checkout; no release commit or tag was
created.

## Verification

The integrated packaging/acceptance run passed **78/78 tests**:

| Suite | Tests |
| --- | ---: |
| Installer recovery and ownership | 23 |
| Concurrent maintenance | 5 |
| GLIBC audit failures and baseline | 7 |
| Frozen bundle | 9 |
| Desktop integration | 10 |
| Installed executable acceptance | 12 |
| Supplementary source-widget regressions | 4 |
| Source Wayland behavior and warning filter | 8 |

The installed executable was exercised under X11/Xvfb and headless Wayland,
including annotation Save/Discard/Cancel, citations, persistence, and renderer
process isolation. Measured combined PSS was 124.66 MiB at reported 100% zoom
and 189.29 MiB at reported 400%. First/repeated self-test runs took
470.2/482.9 ms, and the existing startup/render pixel criterion took 446.2 ms.
These are measurements from the existing acceptance workload, not proof of
cache-cold startup or performance on arbitrary PDFs.

The real a3 installer passed all six first-install SIGKILL cut points followed
by repair and a successful ordinary retry. All five maintenance concurrency
regressions also passed with the real package substituted for the synthetic
fixture. An actual a2 installation upgraded to a3 and uninstalled successfully,
preserving library/PDF/configuration sentinels byte for byte.

Evidence is under ignored `tmp/qa/release-review/`: `verify_a3.py` and
`verified-findings-0.1.0a3.json`. Existing a2 assessment and reproduction files
were not overwritten. Package `--verify` and all entries in `SHA256SUMS` pass.
The integrated run is recorded in `/tmp/paperassist-a3-acceptance.log`; real-package
maintenance checks are in `/tmp/paperassist-a3-real-package.log`.

## Remaining release limits

This local bundle still requires **glibc >= 2.42**. The corrected GLIBC_2.35
gate rejects it, as expected. The Ubuntu 22.04 CI build and clean Ubuntu/Debian
installation remain unexecuted; no portable Linux compatibility is claimed.

Older installer/uninstaller scripts cannot be retrofitted with the new locking
protocol. Do not run historical a1/a2 maintenance concurrently with a3. Normal
sequential upgrade from a2 is verified.

No personal library was used, and no commit, push, or release publication was
performed.
