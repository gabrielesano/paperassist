"""Generate application icons (SVG and 256x256 PNG) and desktop helpers for PaperAssist.

Visual identity matches src/paperassist/native/theme.py (brand_icon):
- Deep-teal rounded-square badge (#276c65, rx ~18% of badge size).
- Bold white serif "P" (Georgia, Liberation Serif, DejaVu Serif, serif).
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure offscreen Qt platform before importing QtGui/QtWidgets
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

TEAL = "#276c65"
WHITE = "#ffffff"

SVG_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="256" height="256" viewBox="0 0 256 256">
  <title>PaperAssist</title>
  <desc>PaperAssist application icon: deep-teal badge with a white serif P</desc>
  <rect x="12" y="12" width="232" height="232" rx="44" ry="44" fill="#276c65"/>
  <text x="128" y="185" font-family="Georgia, 'Liberation Serif', 'DejaVu Serif', serif" font-size="160" font-weight="bold" fill="#ffffff" text-anchor="middle">P</text>
</svg>
"""


def quote_exec_path(path: str | Path) -> str:
    """Format an executable path for Freedesktop Exec= desktop entry key.

    Per the Freedesktop Desktop Entry Specification:
    If the path contains spaces or special characters, it must be enclosed
    in double quotes, with backslashes, double quotes, dollar signs, and
    backticks escaped with backslashes.
    """
    path_str = str(path)
    if any(c in path_str for c in (" ", "\t", '"', "\\", "$", "`")):
        escaped = (
            path_str.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("`", "\\`")
            .replace("$", "\\$")
        )
        return f'"{escaped}"'
    return path_str


def generate_svg(output_path: Path) -> str:
    """Write the scalable SVG icon to output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(SVG_CONTENT, encoding="utf-8")
    return SVG_CONTENT


def generate_png(output_path: Path, size: int = 256) -> None:
    """Render a crisp RGBA PNG icon using PySide6 QPainter offscreen."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(["generate_icons", "-platform", "offscreen"])

    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(TEAL))

    # Badge geometry: 12px inset on 256x256 -> 232x232 badge with rx=44 (~18.9% radius)
    scale = size / 256.0
    badge_inset = 12.0 * scale
    badge_size = size - 2.0 * badge_inset
    corner_radius = 44.0 * scale
    painter.drawRoundedRect(
        QRectF(badge_inset, badge_inset, badge_size, badge_size),
        corner_radius,
        corner_radius,
    )

    # White serif "P"
    font = QFont("Georgia")
    font.setFamilies(["Georgia", "Liberation Serif", "DejaVu Serif", "serif"])
    font.setStyleHint(QFont.Serif)
    font.setPixelSize(round(160 * scale))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(WHITE))

    # Center optically within the badge rect (slight vertical offset for serif cap alignment)
    painter.drawText(
        QRectF(badge_inset, badge_inset + 10.0 * scale, badge_size, badge_size),
        Qt.AlignCenter,
        "P",
    )
    painter.end()

    image.save(str(output_path), "PNG")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate PaperAssist desktop icons.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Target directory for generated icons (default: script directory)",
    )
    parser.add_argument("--icns", type=Path, help="Also generate a macOS ICNS icon (requires iconutil)")
    parser.add_argument("--ico", type=Path, help="Also generate a Windows ICO icon")
    args = parser.parse_args()

    out_dir = args.output_dir
    app = QApplication.instance() or QApplication(["generate_icons", "-platform", "offscreen"])
    svg_file = out_dir / "paperassist.svg"
    png_file = out_dir / "paperassist.png"

    generate_svg(svg_file)
    generate_png(png_file)
    if args.ico:
        args.ico.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="paperassist-icon-") as temporary:
            images = []
            for size in (16, 32, 48, 64, 128, 256):
                path = Path(temporary) / f"{size}.png"
                generate_png(path, size)
                images.append((size, path.read_bytes()))
            header = struct.pack("<HHH", 0, 1, len(images))
            offset = len(header) + 16 * len(images)
            entries = []
            for size, data in images:
                entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
                offset += len(data)
            args.ico.write_bytes(header + b"".join(entries) + b"".join(data for _, data in images))
    if args.icns:
        args.icns.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="paperassist-icon-") as temporary:
            iconset = Path(temporary) / "PaperAssist.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    suffix = "@2x" if scale == 2 else ""
                    generate_png(iconset / f"icon_{size}x{size}{suffix}.png", size * scale)
            subprocess.run(["iconutil", "-c", "icns", "-o", str(args.icns), str(iconset)], check=True)
    print(f"Generated {svg_file} ({svg_file.stat().st_size} bytes)")
    print(f"Generated {png_file} ({png_file.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
