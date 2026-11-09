#!/usr/bin/env python3
"""Build the private OCR runtime from pinned upstream sources, without optional codecs."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    "tesseract": {
        "version": "5.5.4",
        "url": "https://codeload.github.com/tesseract-ocr/tesseract/tar.gz/refs/tags/5.5.4",
        "sha256": "a8dbf8b8c8ee427ff6ad1d99116502feaf59440af1adb893caf60a6576826cf4",
        "filename": "tesseract-5.5.4.tar.gz",
    },
    "leptonica": {
        "version": "1.87.0",
        "url": "https://codeload.github.com/DanBloomberg/leptonica/tar.gz/refs/tags/1.87.0",
        "sha256": "fa2b40c5caea96d1bb93a97486262aed8731b69ce25a84a6bf5d25323e33f631",
        "filename": "leptonica-1.87.0.tar.gz",
    },
    "tessdata_fast": {
        "version": "4.1.0",
        "url": "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata",
        "sha256": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
        "filename": "eng.traineddata",
    },
    "model_license": {
        "url": "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/LICENSE",
        "sha256": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        "filename": "tessdata-LICENSE",
    },
}
CODECS = ("ZLIB", "PNG", "GIF", "JPEG", "TIFF", "WEBP", "OPENJPEG")
PROFILE = 'lstm-pgm-tsv-english-v1'


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def download(item, directory):
    destination = directory / item['filename']
    if not destination.exists():
        temporary = destination.with_suffix('.part')
        try:
            with urllib.request.urlopen(item['url'], timeout=60) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output)
            if digest(temporary) != item['sha256']:
                raise ValueError(f"Source checksum mismatch: {item['filename']}")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    if digest(destination) != item['sha256']:
        raise ValueError(f"Source checksum mismatch: {destination.name}")
    return destination


def extract(archive, destination, name):
    source = destination / name
    if not source.is_dir():
        with tarfile.open(archive) as contents:
            contents.extractall(destination, filter='data')
    return source


def run(*command):
    print(' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True)


def verify_runtime(directory):
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest['sources'] != SOURCES or manifest['profile'] != PROFILE
            or manifest['platform'] != sys.platform or manifest['architecture'] != platform.machine()):
        raise ValueError('Rebuild the bundled OCR runtime for this source version and release host.')
    required = {'tesseract.exe' if sys.platform == 'win32' else 'tesseract', 'tessdata/eng.traineddata',
                'tessdata/configs/tsv', 'LICENSE.tesseract', 'LICENSE.leptonica', 'LICENSE.tessdata_fast'}
    if set(manifest['files']) != required:
        raise ValueError('The bundled OCR runtime is incomplete.')
    for name, expected in manifest['files'].items():
        path = directory / name
        if digest(path) != expected['sha256'] or path.stat().st_size != expected['bytes']:
            raise ValueError(f'Bundled OCR checksum mismatch: {name}')
    return manifest


def build(work, output, jobs):
    work, output = work.resolve(), output.resolve()
    downloads = work / 'downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    archives = {name: download(item, downloads) for name, item in SOURCES.items()}
    lept = extract(archives['leptonica'], work / 'sources', 'leptonica-' + SOURCES['leptonica']['version'])
    tess = extract(archives['tesseract'], work / 'sources', 'tesseract-' + SOURCES['tesseract']['version'])
    prefix = work / 'prefix'
    common = ['-DCMAKE_BUILD_TYPE=Release', '-DBUILD_SHARED_LIBS=OFF', '-DSW_BUILD=OFF',
              '-DCMAKE_INSTALL_LIBDIR=lib', f'-DCMAKE_INSTALL_PREFIX={prefix}']
    if sys.platform == 'win32':
        common += ['-DCMAKE_POLICY_DEFAULT_CMP0091=NEW', '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded']
    else:
        common += ['-DCMAKE_C_FLAGS_RELEASE=-Os -DNDEBUG -ffunction-sections -fdata-sections',
                   '-DCMAKE_CXX_FLAGS_RELEASE=-Os -DNDEBUG -ffunction-sections -fdata-sections']
    lept_options = ['-DBUILD_PROG=OFF', *(f'-DENABLE_{codec}=OFF' for codec in CODECS)]
    run('cmake', '-S', lept, '-B', work / 'leptonica', *common, *lept_options)
    run('cmake', '--build', work / 'leptonica', '--config', 'Release', '--parallel', jobs)
    run('cmake', '--install', work / 'leptonica', '--config', 'Release')

    tess_options = ['-DBUILD_TRAINING_TOOLS=OFF', '-DBUILD_TESTS=OFF', '-DGRAPHICS_DISABLED=ON',
                    '-DDISABLED_LEGACY_ENGINE=ON', '-DOPENMP_BUILD=OFF', '-DENABLE_NATIVE=OFF',
                    '-DDISABLE_TIFF=ON', '-DDISABLE_ARCHIVE=ON', '-DDISABLE_CURL=ON',
                    '-DINSTALL_CONFIGS=OFF', '-DENABLE_LTO=ON', '-DENABLE_CCACHE=OFF',
                    '-DENABLE_PRECOMPILED_HEADERS=OFF', f'-DCMAKE_PREFIX_PATH={prefix}']
    if sys.platform.startswith('linux'):
        tess_options += ['-DCMAKE_EXE_LINKER_FLAGS=-Wl,--gc-sections']
    elif sys.platform == 'darwin':
        tess_options += ['-DCMAKE_EXE_LINKER_FLAGS=-Wl,-dead_strip']
    run('cmake', '-S', tess, '-B', work / 'tesseract', *common, *tess_options)
    run('cmake', '--build', work / 'tesseract', '--config', 'Release', '--parallel', jobs)
    install = ['cmake', '--install', work / 'tesseract', '--config', 'Release']
    if sys.platform != 'win32':
        install.append('--strip')
    run(*install)

    output.mkdir(parents=True, exist_ok=True)
    (output / 'tessdata' / 'configs').mkdir(parents=True, exist_ok=True)
    executable = 'tesseract.exe' if sys.platform == 'win32' else 'tesseract'
    files = {
        executable: prefix / 'bin' / executable,
        'tessdata/eng.traineddata': archives['tessdata_fast'],
        'tessdata/configs/tsv': tess / 'tessdata' / 'configs' / 'tsv',
        'LICENSE.tesseract': tess / 'LICENSE',
        'LICENSE.leptonica': lept / 'leptonica-license.txt',
        'LICENSE.tessdata_fast': archives['model_license'],
    }
    for name, source in files.items():
        shutil.copy2(source, output / name)
    env = {**os.environ, 'TESSDATA_PREFIX': str(output / 'tessdata')}
    version = subprocess.check_output([str(output / executable), '--version'], env=env, text=True).splitlines()[0]
    manifest = {
        'profile': PROFILE, 'platform': sys.platform, 'architecture': platform.machine(),
        'engine': version, 'sources': SOURCES,
        'files': {name: {'bytes': (output / name).stat().st_size, 'sha256': digest(output / name)} for name in files},
    }
    manifest['runtime_bytes'] = sum(item['bytes'] for item in manifest['files'].values())
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f"OCR runtime: {manifest['runtime_bytes']:,} bytes in {output}", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, default=ROOT / 'build' / 'ocr')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build' / 'ocr' / 'runtime')
    parser.add_argument('--jobs', type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    build(args.work_dir, args.output_dir, args.jobs)
