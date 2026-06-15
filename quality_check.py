#!/usr/bin/env python3
import argparse
import datetime as dt
import math
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INCOMING = ROOT / "incoming"
NEEDS_REVIEW = ROOT / "needs-review"
REPORTS = ROOT / "quality-reports"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}


def run(command):
    return subprocess.run(command, check=False, capture_output=True, text=True)


def unique_path(folder, name):
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    counter = 1
    while True:
        candidate = folder / f"{stem}-{timestamp}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def parse_afinfo(path):
    result = run(["afinfo", str(path)])
    if result.returncode != 0:
        return {
            "ok": False,
            "error": result.stderr.strip() or result.stdout.strip() or "afinfo could not read file",
            "raw": result.stdout + result.stderr,
        }

    text = result.stdout
    duration_match = re.search(r"estimated duration:\s+([0-9.]+)\s+sec", text)
    bytes_match = re.search(r"audio bytes:\s+([0-9]+)", text)
    bitrate_match = re.search(r"bit rate:\s+([0-9]+)\s+bits per second", text)
    format_match = re.search(r"Data format:\s+(.+)", text)
    sample_rate_match = re.search(r"([0-9]+)\s+Hz", text)

    return {
        "ok": True,
        "duration": float(duration_match.group(1)) if duration_match else None,
        "audio_bytes": int(bytes_match.group(1)) if bytes_match else None,
        "bit_rate": int(bitrate_match.group(1)) if bitrate_match else None,
        "format": format_match.group(1).strip() if format_match else "Unknown",
        "sample_rate": int(sample_rate_match.group(1)) if sample_rate_match else None,
        "raw": text,
    }


def analyze_signal(path):
    with tempfile.TemporaryDirectory() as temp_dir:
        wav_path = Path(temp_dir) / "audio.wav"
        result = run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", str(path), str(wav_path)])
        if result.returncode != 0 or not wav_path.exists():
            raise RuntimeError(result.stderr.strip() or "afconvert could not decode file")

        total_samples = 0
        sum_squares = 0
        peak = 0
        clipped = 0
        silent = 0
        silence_threshold = int(32767 * (10 ** (-50 / 20)))

        with wave.open(str(wav_path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            if sample_width != 2:
                raise RuntimeError(f"Expected 16-bit PCM after conversion, got {sample_width * 8}-bit")
            while True:
                frames = wav.readframes(65536)
                if not frames:
                    break
                sample_count = len(frames) // 2
                total_samples += sample_count
                for index in range(0, len(frames), 2):
                    sample = int.from_bytes(frames[index:index + 2], "little", signed=True)
                    absolute = abs(sample)
                    peak = max(peak, absolute)
                    sum_squares += sample * sample
                    if absolute >= 32760:
                        clipped += 1
                    if absolute <= silence_threshold:
                        silent += 1

        if total_samples == 0:
            raise RuntimeError("Decoded audio had no samples")

        rms = math.sqrt(sum_squares / total_samples)
        rms_dbfs = 20 * math.log10(max(rms, 1) / 32767)
        peak_dbfs = 20 * math.log10(max(peak, 1) / 32767)

        return {
            "channels": channels,
            "rms_dbfs": rms_dbfs,
            "peak_dbfs": peak_dbfs,
            "clipped_percent": clipped / total_samples * 100,
            "silent_percent": silent / total_samples * 100,
        }


def check_file(path):
    failures = []
    warnings = []
    afinfo = parse_afinfo(path)
    signal = None

    if not afinfo["ok"]:
        failures.append(f"Cannot read audio metadata: {afinfo['error']}")
    else:
        duration = afinfo.get("duration")
        bit_rate = afinfo.get("bit_rate")
        audio_bytes = afinfo.get("audio_bytes")
        sample_rate = afinfo.get("sample_rate")

        if duration is None:
            warnings.append("Duration could not be read.")
        elif duration < 30:
            failures.append(f"Audio is very short: {duration:.1f} seconds.")
        elif duration > 4 * 60 * 60:
            warnings.append(f"Audio is unusually long: {duration / 3600:.1f} hours.")

        if bit_rate is not None and bit_rate < 24000:
            warnings.append(f"Bit rate is low for spoken audio: {bit_rate} bps.")
        if audio_bytes is not None and audio_bytes < 100_000:
            failures.append("Audio payload is suspiciously small.")
        if sample_rate is not None and sample_rate < 16000:
            warnings.append(f"Sample rate is low for speech: {sample_rate} Hz.")

    try:
        signal = analyze_signal(path)
        if signal["rms_dbfs"] < -45:
            failures.append(f"Audio is extremely quiet on average: {signal['rms_dbfs']:.1f} dBFS.")
        elif signal["rms_dbfs"] < -35:
            warnings.append(f"Audio may be quiet: {signal['rms_dbfs']:.1f} dBFS.")
        if signal["silent_percent"] > 85:
            failures.append(f"Audio appears mostly silent: {signal['silent_percent']:.1f}% near silence.")
        elif signal["silent_percent"] > 60:
            warnings.append(f"Audio has a lot of near-silence: {signal['silent_percent']:.1f}%.")
        if signal["clipped_percent"] > 0.5:
            warnings.append(f"Audio may be clipped/distorted: {signal['clipped_percent']:.2f}% clipped samples.")
    except Exception as error:
        failures.append(f"Could not decode and inspect waveform: {error}")

    return {
        "file": path,
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "afinfo": afinfo,
        "signal": signal,
    }


def write_report(result):
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = result["file"]
    report_path = REPORTS / f"{path.stem}.quality.md"
    status = "PASS" if result["passed"] else "NEEDS REVIEW"
    checked = dt.datetime.now().astimezone().strftime("%B %d, %Y at %I:%M %p %Z")

    lines = [
        f"# Audio Quality Report: {path.name}",
        "",
        f"Checked: {checked}",
        f"Status: {status}",
        "",
        "## What This Check Can Do",
        "",
        "- Confirm the file can be read and decoded.",
        "- Check duration, size, bit rate, loudness, near-silence, and clipping risk.",
        "- Catch many broken, empty, too-quiet, or distorted files before publishing.",
        "",
        "## What This Check Cannot Do Yet",
        "",
        "- It cannot prove educational accuracy without a trusted source or transcript.",
        "- It cannot fully judge whether every spoken word is correct without speech-to-text tooling.",
        "",
    ]

    if result["failures"]:
        lines.extend(["## Failures", ""])
        lines.extend([f"- {item}" for item in result["failures"]])
        lines.append("")
    if result["warnings"]:
        lines.extend(["## Warnings", ""])
        lines.extend([f"- {item}" for item in result["warnings"]])
        lines.append("")

    afinfo = result["afinfo"]
    if afinfo.get("ok"):
        duration = afinfo.get("duration")
        lines.extend([
            "## Metadata",
            "",
            f"- Format: {afinfo.get('format')}",
            f"- Duration: {duration:.1f} seconds" if duration else "- Duration: Unknown",
            f"- Bit rate: {afinfo.get('bit_rate') or 'Unknown'} bps",
            f"- Sample rate: {afinfo.get('sample_rate') or 'Unknown'} Hz",
            "",
        ])

    signal = result["signal"]
    if signal:
        lines.extend([
            "## Signal Check",
            "",
            f"- Channels: {signal['channels']}",
            f"- Average loudness: {signal['rms_dbfs']:.1f} dBFS",
            f"- Peak level: {signal['peak_dbfs']:.1f} dBFS",
            f"- Near-silence: {signal['silent_percent']:.1f}%",
            f"- Clipping risk: {signal['clipped_percent']:.3f}%",
            "",
        ])

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main():
    parser = argparse.ArgumentParser(description="Check incoming podcast audio before publishing.")
    parser.add_argument("--quarantine", action="store_true", help="Move failed files to needs-review.")
    args = parser.parse_args()

    INCOMING.mkdir(parents=True, exist_ok=True)
    NEEDS_REVIEW.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    audio_files = [path for path in sorted(INCOMING.iterdir()) if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS]
    if not audio_files:
        print("No incoming audio files to quality-check.")
        return 0

    failed = 0
    for path in audio_files:
        result = check_file(path)
        report_path = write_report(result)
        print(f"{'PASS' if result['passed'] else 'NEEDS REVIEW'} {path.name} ({report_path})")
        if not result["passed"]:
            failed += 1
            if args.quarantine:
                destination = unique_path(NEEDS_REVIEW, path.name)
                shutil.move(str(path), str(destination))
                review_report = unique_path(NEEDS_REVIEW, report_path.name)
                shutil.copy2(report_path, review_report)
                print(f"Moved to {destination}")

    if failed:
        print(f"{failed} file(s) need review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
