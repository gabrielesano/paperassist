"""Compile the single-file per-user Windows installer with Inno Setup 6."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from paperassist import __version__


def build_installer(bundle: Path, output_dir: Path) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("The Windows installer must be built on Windows")
    bundle, output_dir = bundle.resolve(), output_dir.resolve()
    if not (bundle / "PaperAssist.exe").is_file():
        raise FileNotFoundError(f"Missing PaperAssist.exe in {bundle}")
    candidates = [
        Path(os.environ.get(variable, "C:/Program Files (x86)")) / "Inno Setup 6/ISCC.exe"
        for variable in ("ProgramFiles(x86)", "ProgramFiles")
    ]
    if discovered := shutil.which("ISCC"):
        candidates.append(Path(discovered))
    # Chocolatey's PATH entry is a launcher, without the compiler's license files.
    compiler = next((path for path in candidates if path.is_file() and (path.parent / "license.txt").is_file()), None)
    if compiler is None:
        raise RuntimeError("Install Inno Setup 6.3+ with its license.txt next to ISCC.exe")
    root = Path(__file__).resolve().parents[2]
    manifest = root / "build/windows/install-manifest.txt"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(f"Managed-By: PaperAssist-Windows-Installer\nAppId=PaperAssist.Native\nVersion={__version__}\n")
    compiler_license = compiler.parent / "license.txt"
    output_dir.mkdir(parents=True, exist_ok=True)
    definitions = {
        "AppVersion": __version__,
        "AppNumericVersion": re.match(r"\d+\.\d+\.\d+", __version__).group() + ".0",
        "BundleDir": bundle, "OutputDir": output_dir,
        "Manifest": manifest, "CompilerLicense": compiler_license,
        "AppIcon": root / "build/icons/PaperAssist.ico",
    }
    subprocess.run([compiler, "/Qp", *(f"/D{key}={value}" for key, value in definitions.items()),
                    str(Path(__file__).with_name("installer.iss"))], check=True)
    output = output_dir / f"PaperAssist-{__version__}-windows-x86_64-setup.exe"
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + ".sha256").write_text(f"{digest}  {output.name}\n")
    print(f"Created {output.name} ({output.stat().st_size:,} bytes); SHA-256 {digest}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, default=Path("dist/PaperAssist"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    build_installer(args.bundle_dir, args.output_dir)
