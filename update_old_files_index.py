#!/usr/bin/env python3
import datetime as dt
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OLD_FILES = ROOT / "old-files"
INDEX = OLD_FILES / "Listened Files.md"
DOCX_INDEX = OLD_FILES / "Listened Files.docx"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}


def size_mb(path):
    return path.stat().st_size / 1024 / 1024


def main():
    OLD_FILES.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [path for path in OLD_FILES.iterdir() if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS],
        key=lambda path: path.name.lower(),
    )
    updated = dt.datetime.now().astimezone().strftime("%B %d, %Y at %I:%M %p %Z")

    lines = [
        "# Listened / Archived Podcast Files",
        "",
        f"Last updated: {updated}",
        "",
        "This folder contains audio files after they have been published and moved out of the drop folder.",
        "",
    ]

    if files:
        lines.extend(["| File | Size |", "| --- | ---: |"])
        for path in files:
            lines.append(f"| `{path.name}` | {size_mb(path):.1f} MB |")
    else:
        lines.append("No listened / archived audio files yet.")

    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {INDEX}")
    if shutil.which("textutil"):
        subprocess.run(
            ["textutil", "-convert", "docx", "-output", str(DOCX_INDEX), str(INDEX)],
            check=True,
        )
        print(f"Wrote {DOCX_INDEX}")


if __name__ == "__main__":
    main()
