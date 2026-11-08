"""Package the Apple Silicon application as a drag-to-Applications disk image."""

import argparse
import hashlib
from pathlib import Path
import platform
import subprocess
import tempfile

from paperassist import __version__


def build_dmg(app: Path, output_dir: Path) -> Path:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("The macOS package must be built on Apple Silicon")
    app = app.resolve()
    if not (app / "Contents/MacOS/PaperAssist").is_file():
        raise FileNotFoundError(f"Missing application executable in {app}")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = (output_dir / f"PaperAssist-{__version__}-macos-arm64.dmg").resolve()
    with tempfile.TemporaryDirectory(prefix="paperassist-dmg-") as temporary:
        staging = Path(temporary)
        subprocess.run(["ditto", str(app), str(staging / "PaperAssist.app")], check=True)
        (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
        subprocess.run([
            "hdiutil", "create", "-volname", "PaperAssist", "-srcfolder", str(staging),
            "-fs", "HFS+", "-format", "UDZO", "-ov", str(output),
        ], check=True)
    subprocess.run(["hdiutil", "verify", str(output)], check=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + ".sha256").write_text(f"{digest}  {output.name}\n")
    print(f"Created {output.name} ({output.stat().st_size:,} bytes); SHA-256 {digest}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("dist/PaperAssist.app"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    build_dmg(args.app, args.output_dir)
