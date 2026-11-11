#!/usr/bin/env python3
"""Regenerate assets/demo/demo-take-spectrogram.png from assets/demo/demo-take.wav."""

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAV = ROOT / "assets" / "demo" / "demo-take.wav"
PNG = ROOT / "assets" / "demo" / "demo-take-spectrogram.png"

CAPTION = "demo take \u00b7 seed 4242 \u00b7 13.1 s"
WIDTH, HEIGHT = 700, 350
SPECTRUM = (
    f"showspectrumpic=s={WIDTH}x{HEIGHT}:mode=combined:color=viridis:"
    "fscale=log:legend=0"
)

ASS_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,3,2,0,2,24,24,16,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:20.00,Caption,,0,0,0,,{caption}
"""


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def escape_filter_path(path):
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def main():
    fd, ass_name = tempfile.mkstemp(suffix=".ass", prefix="demo-take-")
    ass_path = Path(ass_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as ass_file:
            ass_file.write(
                ASS_TEMPLATE.format(
                    width=WIDTH, height=HEIGHT, caption=CAPTION
                )
            )
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(WAV),
                "-filter_complex",
                f"{SPECTRUM},subtitles={escape_filter_path(ass_path)}",
                "-frames:v",
                "1",
                "-compression_level",
                "9",
                str(PNG),
            ],
            check=True,
        )
    finally:
        ass_path.unlink(missing_ok=True)
    print(f"{PNG.relative_to(ROOT)} sha256={sha256(PNG)} size={PNG.stat().st_size}")


if __name__ == "__main__":
    main()
