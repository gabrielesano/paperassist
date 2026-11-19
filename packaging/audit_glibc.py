"""Check the GLIBC versions and ELF dependencies of a Linux bundle."""

import os
from pathlib import Path
import re
import subprocess
import sys


ELF_MAGIC = b'\x7fELF'
VERSION = re.compile(r'\bGLIBC_([0-9]+(?:\.[0-9]+)+)\b')
GLIBCXX_VERSION = re.compile(r'\bGLIBCXX_([0-9]+)\.([0-9]+)(?:\.([0-9]+))?\b')
CXXABI_VERSION = re.compile(r'\bCXXABI_([0-9]+)\.([0-9]+)(?:\.([0-9]+))?\b')
NEEDED = re.compile(r'\(NEEDED\)\s+.*?:?\s*\[([^\]]+)\]')
BASELINE = (2, 35)


def is_elf(path: Path) -> bool:
    """Return True if path is a regular (non-symlink) ELF binary."""
    if path.is_symlink():
        return False
    with path.open('rb') as stream:
        return stream.read(4) == ELF_MAGIC


def readelf(flag: str, path: Path) -> str:
    """Run readelf with a C locale and return stdout."""
    result = subprocess.run(
        ['readelf', flag, str(path)], capture_output=True, text=True,
        errors='replace', env={**os.environ, 'LC_ALL': 'C'}, check=True,
    )
    return result.stdout


def parse_glibc_versions(output: str) -> list[tuple[int, ...]]:
    """Extract numeric GLIBC version tuples from `readelf -V` output."""
    return [tuple(map(int, match.split('.'))) for match in VERSION.findall(output)]


def format_glibc(version: tuple[int, ...]) -> str:
    return f'GLIBC_{".".join(map(str, version))}'


def _parse_tagged_versions(pattern: re.Pattern[str], output: str) -> list[tuple[int, int, int, str]]:
    return [
        (int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else 0, m.group(0))
        for m in pattern.finditer(output)
    ]


def inspect_bundle_elfs(bundle: Path) -> dict:
    """Inspect every ELF in bundle once and summarize GLIBC/C++ symbol and DT_NEEDED info."""
    bundled_filenames: set[str] = set()
    file_glibc: dict[str, tuple[int, ...]] = {}
    all_glibcxx: set[tuple[int, int, int, str]] = set()
    all_cxxabi: set[tuple[int, int, int, str]] = set()
    all_needed: set[str] = set()

    for root, _, files in os.walk(bundle):
        for name in sorted(files):
            bundled_filenames.add(name)
            path = Path(root) / name
            try:
                if not is_elf(path):
                    continue
                out_v = readelf('-V', path)
                out_d = readelf('-d', path)
            except (OSError, subprocess.SubprocessError):
                continue

            rel = str(path.relative_to(bundle)).replace('\\', '/')
            g_vers = parse_glibc_versions(out_v)
            if g_vers:
                file_glibc[rel] = max(g_vers)
            all_glibcxx.update(_parse_tagged_versions(GLIBCXX_VERSION, out_v))
            all_cxxabi.update(_parse_tagged_versions(CXXABI_VERSION, out_v))
            all_needed.update(NEEDED.findall(out_d))

    bootloader_max_glibc = format_glibc(file_glibc['PaperAssist']) if 'PaperAssist' in file_glibc else None
    bundle_max_glibc = None
    bundle_max_glibc_files: list[str] = []
    if file_glibc:
        max_ver = max(file_glibc.values())
        bundle_max_glibc = format_glibc(max_ver)
        bundle_max_glibc_files = sorted(rel for rel, ver in file_glibc.items() if ver == max_ver)

    return {
        'bootloader_max_glibc': bootloader_max_glibc,
        'bundle_max_glibc': bundle_max_glibc,
        'bundle_max_glibc_files': bundle_max_glibc_files,
        'bundle_max_glibcxx': max(all_glibcxx)[3] if all_glibcxx else None,
        'bundle_max_cxxabi': max(all_cxxabi)[3] if all_cxxabi else None,
        'external_shared_libraries': sorted(lib for lib in all_needed if lib not in bundled_filenames),
    }


def audit(bundle: Path) -> int:
    executable = bundle / 'PaperAssist'
    inspected = set()
    versions = []
    errors = []

    def walk_error(error):
        errors.append(f'{error.filename}: {error.strerror}')

    if not bundle.is_dir():
        errors.append(f'{bundle}: bundle directory is missing or inaccessible')
    for root, _, files in os.walk(bundle, onerror=walk_error):
        for name in sorted(files):
            path = Path(root) / name
            try:
                if not is_elf(path):
                    continue
                found = parse_glibc_versions(readelf('-V', path))
                inspected.add(path)
                versions.extend((version, path) for version in found)
                if path == executable and not found:
                    errors.append(f'{path}: executable has no numeric GLIBC version requirements')
            except (OSError, subprocess.SubprocessError) as error:
                detail = getattr(error, 'stderr', None) or str(error)
                errors.append(f'{path}: inspection failed: {detail.strip()}')

    if executable not in inspected:
        errors.append(f'{executable}: expected executable was not successfully inspected as ELF')
    elif not os.access(executable, os.X_OK):
        errors.append(f'{executable}: expected executable is not executable')
    for version, path in versions:
        if version > BASELINE:
            errors.append(f'{path}: {format_glibc(version)} exceeds {format_glibc(BASELINE)}')
    if errors:
        print('GLIBC audit FAILED:', file=sys.stderr)
        for error in sorted(set(errors)):
            print(f'  {error}', file=sys.stderr)
        return 1
    maximum = max(version for version, _ in versions)
    print(f'Inspected {len(inspected)} ELF files; maximum {format_glibc(maximum)}')
    print(f'GLIBC audit PASSED: all ELF versions <= {format_glibc(BASELINE)}')
    return 0


if __name__ == '__main__':
    sys.exit(audit(Path(sys.argv[1] if len(sys.argv) > 1 else 'dist/PaperAssist')))
