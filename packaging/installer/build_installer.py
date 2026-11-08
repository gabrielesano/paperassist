#!/usr/bin/env python3
"""Build the self-extracting Linux installer for PaperAssist.

Packages a PyInstaller one-dir bundle (and optional desktop assets) into
a single self-extracting POSIX shell script (PaperAssist-<version>-linux-x86_64.sh)
with an embedded tar.gz payload and detached .sha256 checksum file.
"""

import argparse
import gzip
import hashlib
import io
import os
import re
import sys
import tarfile
from pathlib import Path


def get_repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parent.parent.parent


def get_default_version(repo_root: Path) -> str:
    """Read default application version from src/paperassist/__init__.py."""
    init_py = repo_root / "src" / "paperassist" / "__init__.py"
    if init_py.is_file():
        content = init_py.read_text(encoding="utf-8")
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        if match:
            return match.group(1)
    return "0.1.0a1"


def create_payload_archive(
    bundle_dir: Path,
    desktop_dir: Path | None,
    uninstall_script: Path | None,
) -> bytes:
    """Create a deterministic gzip-compressed tar archive of the application bundle."""
    buffer = io.BytesIO()

    # mtime=0 for deterministic gzip compression
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w:") as tar:
            # 1. Add bundle directory files (preserving relative symlinks)
            for root, dirs, files in os.walk(bundle_dir, followlinks=False):
                # Sort dirs and files for deterministic archive order
                dirs.sort()
                files.sort()
                rel_root = Path(root).relative_to(bundle_dir)

                kept_dirs = []
                for d in dirs:
                    dir_path = Path(root) / d
                    arcname = str(rel_root / d).replace("\\", "/")
                    ti = tarfile.TarInfo(name=arcname)
                    ti.mtime = 0
                    ti.uid = 0
                    ti.gid = 0
                    ti.uname = ""
                    ti.gname = ""
                    if dir_path.is_symlink():
                        ti.type = tarfile.SYMTYPE
                        ti.linkname = os.readlink(dir_path)
                        ti.mode = 0o777
                    else:
                        ti.type = tarfile.DIRTYPE
                        ti.mode = 0o755
                        kept_dirs.append(d)
                    tar.addfile(ti)
                dirs[:] = kept_dirs

                for f in files:
                    file_path = Path(root) / f
                    arcname = str(rel_root / f).replace("\\", "/")
                    ti = tarfile.TarInfo(name=arcname)
                    ti.mtime = 0
                    ti.uid = 0
                    ti.gid = 0
                    ti.uname = ""
                    ti.gname = ""
                    if file_path.is_symlink():
                        ti.type = tarfile.SYMTYPE
                        ti.linkname = os.readlink(file_path)
                        ti.mode = 0o777
                        tar.addfile(ti)
                    else:
                        is_exec = os.access(file_path, os.X_OK)
                        ti.size = file_path.stat().st_size
                        ti.mode = 0o755 if is_exec else 0o644
                        with file_path.open("rb") as fp:
                            tar.addfile(ti, fp)

            # 2. Add desktop integration assets under desktop/ if provided
            if desktop_dir and desktop_dir.is_dir():
                desktop_files = sorted(
                    p for p in desktop_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in {".desktop", ".svg", ".png"}
                )
                if desktop_files:
                    # Add desktop directory entry
                    dir_ti = tarfile.TarInfo(name="desktop")
                    dir_ti.type = tarfile.DIRTYPE
                    dir_ti.mode = 0o755
                    dir_ti.mtime = 0
                    dir_ti.uid = 0
                    dir_ti.gid = 0
                    dir_ti.uname = ""
                    dir_ti.gname = ""
                    tar.addfile(dir_ti)

                    for df in desktop_files:
                        ti = tarfile.TarInfo(name=f"desktop/{df.name}")
                        ti.size = df.stat().st_size
                        ti.mode = 0o644
                        ti.mtime = 0
                        ti.uid = 0
                        ti.gid = 0
                        ti.uname = ""
                        ti.gname = ""
                        with df.open("rb") as fp:
                            tar.addfile(ti, fp)

            # 3. Add uninstall.sh if provided
            if uninstall_script and uninstall_script.is_file():
                ti = tarfile.TarInfo(name="uninstall.sh")
                ti.size = uninstall_script.stat().st_size
                ti.mode = 0o755
                ti.mtime = 0
                ti.uid = 0
                ti.gid = 0
                ti.uname = ""
                ti.gname = ""
                with uninstall_script.open("rb") as fp:
                    tar.addfile(ti, fp)

    return buffer.getvalue()


def assemble_installer(
    template_path: Path,
    app_name: str,
    version: str,
    payload_bytes: bytes,
) -> tuple[bytes, int, str]:
    """Assemble the self-extracting script by embedding the payload and exact byte offsets."""
    template_text = template_path.read_text(encoding="utf-8")
    payload_size = len(payload_bytes)
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()

    header_template = template_text.replace("@@APP_NAME@@", app_name)
    header_template = header_template.replace("@@VERSION@@", version)
    header_template = header_template.replace("@@PAYLOAD_SIZE@@", str(payload_size))
    header_template = header_template.replace("@@PAYLOAD_SHA256@@", payload_sha256)

    # Iteratively calculate exact byte offset for @@PAYLOAD_START_BYTE@@
    dummy_header = header_template.replace("@@PAYLOAD_START_BYTE@@", "0")
    offset = len(dummy_header.encode("utf-8"))
    while True:
        candidate_header = header_template.replace("@@PAYLOAD_START_BYTE@@", str(offset))
        candidate_bytes = candidate_header.encode("utf-8")
        new_offset = len(candidate_bytes)
        if new_offset == offset:
            break
        offset = new_offset

    installer_bytes = candidate_bytes + payload_bytes
    assert len(installer_bytes) == offset + payload_size
    assert installer_bytes[offset:] == payload_bytes

    return installer_bytes, offset, payload_sha256


def build_installer(
    bundle_dir: Path,
    output_path: Path,
    desktop_dir: Path | None = None,
    version: str | None = None,
    app_name: str = "PaperAssist",
) -> tuple[Path, Path]:
    """Build the installer .sh and .sha256 files."""
    repo_root = get_repo_root()
    version = version or get_default_version(repo_root)

    # Validate bundle
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"PyInstaller bundle directory not found: {bundle_dir}")

    bundle_exe = bundle_dir / "PaperAssist"
    if not bundle_exe.is_file():
        raise FileNotFoundError(f"Missing PaperAssist executable in bundle directory: {bundle_exe}")

    bundle_internal = bundle_dir / "_internal"
    if not bundle_internal.is_dir():
        raise FileNotFoundError(f"Missing _internal directory in bundle directory: {bundle_internal}")

    bundle_license = bundle_dir / "LICENSE"
    if not bundle_license.is_file():
        raise FileNotFoundError(f"Missing LICENSE file in bundle directory: {bundle_license}")

    template_path = Path(__file__).resolve().parent / "installer_template.sh"
    if not template_path.is_file():
        raise FileNotFoundError(f"Installer template not found: {template_path}")

    uninstall_path = Path(__file__).resolve().parent / "uninstall.sh"

    print(f"Building {app_name} Linux installer v{version}...")
    print(f"  Bundle directory:   {bundle_dir}")
    if desktop_dir and desktop_dir.is_dir():
        print(f"  Desktop directory:  {desktop_dir}")
    else:
        print(f"  Desktop directory:  (none)")

    payload_bytes = create_payload_archive(bundle_dir, desktop_dir, uninstall_path)
    print(f"  Payload size:       {len(payload_bytes):,} bytes ({len(payload_bytes) / (1024*1024):.2f} MiB)")

    installer_bytes, payload_offset, payload_hash = assemble_installer(
        template_path=template_path,
        app_name=app_name,
        version=version,
        payload_bytes=payload_bytes,
    )
    print(f"  Payload offset:     {payload_offset:,} bytes")
    print(f"  Payload SHA-256:    {payload_hash}")

    # Ensure output parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write .sh package
    output_path.write_bytes(installer_bytes)
    os.chmod(output_path, 0o755)
    print(f"  Created package:    {output_path} ({len(installer_bytes):,} bytes)")

    # Compute installer SHA-256 and write checksum file
    installer_sha256 = hashlib.sha256(installer_bytes).hexdigest()
    checksum_path = output_path.with_name(output_path.name + ".sha256")
    checksum_content = f"{installer_sha256}  {output_path.name}\n"
    checksum_path.write_text(checksum_content, encoding="utf-8")
    print(f"  Created checksum:   {checksum_path}")
    print(f"  Installer SHA-256:  {installer_sha256}")

    return output_path, checksum_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = get_repo_root()
    parser = argparse.ArgumentParser(
        description="Build self-extracting Linux installer for PaperAssist"
    )
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=repo_root / "dist" / "PaperAssist",
        help="Directory of PyInstaller one-dir bundle (default: dist/PaperAssist)",
    )
    parser.add_argument(
        "--desktop-dir",
        type=Path,
        default=repo_root / "packaging" / "desktop",
        help="Directory with desktop entry and icons (default: packaging/desktop)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "dist",
        help="Output directory for generated installer (default: dist)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Exact output installer file path (overrides --output-dir)",
    )
    parser.add_argument(
        "--version",
        type=str,
        default=None,
        help="Application version string (default: read from src/paperassist/__init__.py)",
    )
    parser.add_argument(
        "--app-name",
        type=str,
        default="PaperAssist",
        help="Application name (default: PaperAssist)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = get_repo_root()
    version = args.version or get_default_version(repo_root)

    if args.output:
        output_path = args.output
    else:
        filename = f"{args.app_name}-{version}-linux-x86_64.sh"
        output_path = args.output_dir / filename

    desktop_dir = args.desktop_dir if args.desktop_dir and args.desktop_dir.is_dir() else None

    try:
        build_installer(
            bundle_dir=args.bundle_dir,
            output_path=output_path,
            desktop_dir=desktop_dir,
            version=version,
            app_name=args.app_name,
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
