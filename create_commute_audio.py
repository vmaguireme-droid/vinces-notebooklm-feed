#!/usr/bin/env python3
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMUTES = ROOT / "commutes"
JOBS = COMMUTES / "jobs"
ELEVENLABS_READY = COMMUTES / "elevenlabs-ready"
LOGS = COMMUTES / "logs"


def notify(title, message):
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    subprocess.run([
        "osascript",
        "-e",
        f'display notification "{safe_message}" with title "{safe_title}"'
    ], check=False)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def log(message):
    LOGS.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    with (LOGS / "commute-audio.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} - {message}\n")


def make_audio(job_dir, job):
    script_path = job_dir / "final-script.txt"
    if not script_path.exists():
        return False

    ELEVENLABS_READY.mkdir(parents=True, exist_ok=True)
    base = job["topic"].lower().replace(" ", "-").replace("/", "-")
    aiff_path = job_dir / "local-tts.aiff"
    m4a_path = ELEVENLABS_READY / f"{base}.m4a"

    subprocess.run(["say", "-v", "Samantha", "-f", str(script_path), "-o", str(aiff_path)], check=True)
    subprocess.run([
        "afconvert",
        "-f", "m4af",
        "-d", "aac@44100",
        "-b", "128000",
        str(aiff_path),
        str(m4a_path)
    ], check=True)

    job["status"] = "local-audio-ready"
    job["updated"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    job["local_audio_file"] = str(m4a_path)
    job["local_audio_note"] = "Created with macOS say fallback because ElevenLabs automation was not available."
    write_json(job_dir / "job.json", job)
    log(f"CREATED {m4a_path}")
    notify("Commute audio created", f"{job['topic']} audio was created and staged for podcast publishing.")
    return True


def main():
    created = 0
    for job_path in sorted(JOBS.glob("*/job.json")):
        job = read_json(job_path)
        if job.get("status") in {"script-ready-for-elevenlabs", "local-audio-failed"}:
            try:
                if make_audio(job_path.parent, job):
                    created += 1
            except subprocess.CalledProcessError as exc:
                job["status"] = "local-audio-failed"
                job["updated"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
                job["local_audio_error"] = str(exc)
                write_json(job_path, job)
                log(f"FAILED {job['topic']}: {exc}")
                notify("Commute audio failed", f"{job['topic']} audio failed. Check commutes/logs/commute-audio.log.")
                return 1
    print(f"Created {created} local audio file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
