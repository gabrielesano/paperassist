"""Check the GLIBC versions of every ELF in a Linux bundle."""

import os
from pathlib import Path
import re
import subprocess
import sys


VERSION = re.compile(r'\bGLIBC_([0-9]+(?:\.[0-9]+)+)\b')
BASELINE = (2, 35)


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
            if path.is_symlink():
                continue
            try:
                with path.open('rb') as stream:
                    if stream.read(4) != b'\x7fELF':
                        continue
                result = subprocess.run(
                    ['readelf', '-V', str(path)], capture_output=True, text=True,
                    errors='replace', env={**os.environ, 'LC_ALL': 'C'}, check=True,
                )
                found = [tuple(map(int, match.split('.'))) for match in VERSION.findall(result.stdout)]
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
            errors.append(f'{path}: GLIBC_{".".join(map(str, version))} exceeds GLIBC_2.35')
    if errors:
        print('GLIBC audit FAILED:', file=sys.stderr)
        for error in sorted(set(errors)):
            print(f'  {error}', file=sys.stderr)
        return 1
    maximum = max(version for version, _ in versions)
    print(f'Inspected {len(inspected)} ELF files; maximum GLIBC_{".".join(map(str, maximum))}')
    print('GLIBC audit PASSED: all ELF versions <= GLIBC_2.35')
    return 0


if __name__ == '__main__':
    sys.exit(audit(Path(sys.argv[1] if len(sys.argv) > 1 else 'dist/PaperAssist')))
