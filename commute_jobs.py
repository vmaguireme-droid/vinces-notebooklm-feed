#!/usr/bin/env python3
import csv
import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DESKTOP_COMMUTE = Path("/Users/vincemaguire/Desktop/commute")
COMMUTES = ROOT / "commutes"
JOBS = COMMUTES / "jobs"
PROCESSED = COMMUTES / "processed"
GOOGLE_DOCS_READY = COMMUTES / "google-docs-ready"
ELEVENLABS_READY = COMMUTES / "elevenlabs-ready"
LOGS = COMMUTES / "logs"
STATE_PATH = COMMUTES / "state.json"


def slugify(value):
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "commute"


def load_state():
    if not STATE_PATH.exists():
        return {"seen": []}
    return json.loads(STATE_PATH.read_text())


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def normalize_duration(value):
    value = value.strip()
    if not value:
        return "10 minutes"
    if re.fullmatch(r"\d+", value):
        return f"{value} minutes"
    return value


def parse_commute_file(path):
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []

    rows = []
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    for row in csv.reader(lines):
        if not row or len(row) < 2:
            continue
        topic = row[0].strip()
        duration = normalize_duration(row[1])
        if topic:
            rows.append({"topic": topic, "duration": duration})
    return rows


def job_key(topic, duration):
    return hashlib.sha256(f"{topic}|{duration}".encode("utf-8")).hexdigest()[:16]


def write_job_files(job_dir, topic, duration):
    created = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    (job_dir / "job.json").write_text(json.dumps({
        "topic": topic,
        "duration": duration,
        "created": created,
        "status": "queued-for-gemini-deep-research",
        "notes": [
            "Use Gemini Deep Research to research the topic.",
            "Check and correct the text before using it for audio.",
            "Save final text to Google Docs in the Commutes folder.",
            "Use ElevenLabs Studio to create audio, then export/copy audio to the podcast drop folder."
        ]
    }, indent=2) + "\n")

    prompt = f"""Create a commute-learning script for this topic:

Topic: {topic}
Target listening duration: {duration}

Instructions:
- Use Deep Research.
- Make this educational for my own learning.
- Keep the structure clear and suitable for spoken audio.
- Explain important terms simply before using them deeply.
- Include practical examples and careful caveats.
- Avoid hype and unsupported claims.
- After drafting, fact-check the claims against reliable sources.
- Correct anything uncertain or misleading.
- Output the final corrected script only.
"""
    (job_dir / "gemini-deep-research-prompt.txt").write_text(prompt, encoding="utf-8")

    checklist = f"""# Commute Job: {topic}

Duration: {duration}
Created: {created}

## Workflow

1. Open Gemini.
2. Paste `gemini-deep-research-prompt.txt`.
3. Run Deep Research.
4. Review/correct the final text.
5. Save checked text to Google Docs folder: `Commutes`.
6. Put the final checked text into ElevenLabs Studio.
7. Export the audio.
8. Move/copy the audio to the Desktop shortcut `Drop Audio for Podcast`.

## Status

- [ ] Gemini Deep Research complete
- [ ] Accuracy checked/corrected
- [ ] Saved to Google Docs / Commutes
- [ ] ElevenLabs Studio project created
- [ ] Audio exported
- [ ] Audio copied to podcast drop folder
"""
    (job_dir / "README.md").write_text(checklist, encoding="utf-8")


def open_browser_targets():
    subprocess.run(["open", "https://gemini.google.com/"], check=False)
    subprocess.run(["open", "https://elevenlabs.io/app/studio"], check=False)


def main():
    for folder in [COMMUTES, JOBS, PROCESSED, GOOGLE_DOCS_READY, ELEVENLABS_READY, LOGS]:
        folder.mkdir(parents=True, exist_ok=True)

    state = load_state()
    seen = set(state.get("seen", []))
    rows = parse_commute_file(DESKTOP_COMMUTE)
    created_jobs = []

    for row in rows:
        key = job_key(row["topic"], row["duration"])
        if key in seen:
            continue
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        job_dir = JOBS / f"{stamp}-{slugify(row['topic'])}"
        job_dir.mkdir(parents=True, exist_ok=False)
        write_job_files(job_dir, row["topic"], row["duration"])
        seen.add(key)
        created_jobs.append(job_dir)

    state["seen"] = sorted(seen)
    save_state(state)

    log_line = f"{dt.datetime.now().isoformat(timespec='seconds')} - created {len(created_jobs)} job(s)\n"
    (LOGS / "commute-watch.log").open("a", encoding="utf-8").write(log_line)

    if created_jobs:
        open_browser_targets()
        for job_dir in created_jobs:
            print(f"Created {job_dir}")
    else:
        print("No new commute jobs.")


if __name__ == "__main__":
    main()
