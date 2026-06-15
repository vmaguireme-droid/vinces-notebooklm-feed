#!/usr/bin/env python3
import csv
import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMUTES = ROOT / "commutes"
COMMUTE_FILE = COMMUTES / "commute"
COMMUTE_COMPLETE = COMMUTES / "commute complete"
JOBS = COMMUTES / "jobs"
PROCESSED = COMMUTES / "processed"
GOOGLE_DOCS_READY = COMMUTES / "google-docs-ready"
ELEVENLABS_READY = COMMUTES / "elevenlabs-ready"
LOGS = COMMUTES / "logs"
STATE_PATH = COMMUTES / "state.json"
PODCAST_INCOMING = ROOT / "incoming"
GOOGLE_COMMUTES = ROOT.parent / "commutes"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}
SCRIPT_EXTENSIONS = {".txt", ".md"}
STATUS_ALIASES = {
    "queued-for-gemini-flash": "needs-gemini-script"
}


def slugify(value):
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "commute"


def load_state():
    if not STATE_PATH.exists():
        return {"seen": [], "jobs": {}}
    state = json.loads(STATE_PATH.read_text())
    state.setdefault("seen", [])
    state.setdefault("jobs", {})
    return state


def notify(title, message):
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    subprocess.run([
        "osascript",
        "-e",
        f'display notification "{safe_message}" with title "{safe_title}"'
    ], check=False)


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def read_job(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_job(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def unique_path(folder, name):
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        candidate = folder / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def update_job_status(job_dir, status, **extra):
    job_path = job_dir / "job.json"
    data = read_job(job_path)
    data["status"] = status
    data["updated"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    data.update(extra)
    write_job(job_path, data)
    return data


def rebuild_jobs_from_disk(state):
    jobs = state.setdefault("jobs", {})
    seen = set(state.get("seen", []))
    for job_path in JOBS.glob("*/job.json"):
        data = read_job(job_path)
        status = STATUS_ALIASES.get(data.get("status"), data.get("status", "needs-gemini-script"))
        if status != data.get("status"):
            data["status"] = status
            data["updated"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            write_job(job_path, data)
        key = job_key(data["topic"], data["duration"])
        seen.add(key)
        jobs[key] = {
            "topic": data["topic"],
            "duration": data["duration"],
            "job_dir": str(job_path.parent),
            "status": status
        }
    state["seen"] = sorted(seen)
    return state


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


def parse_commute_line(line):
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    parsed = next(csv.reader([line]))
    if len(parsed) < 2:
        return None
    topic = parsed[0].strip()
    duration = normalize_duration(parsed[1])
    if not topic:
        return None
    return {"topic": topic, "duration": duration}


def job_key(topic, duration):
    return hashlib.sha256(f"{topic}|{duration}".encode("utf-8")).hexdigest()[:16]


def write_job_files(job_dir, topic, duration):
    created = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    (job_dir / "job.json").write_text(json.dumps({
        "topic": topic,
        "duration": duration,
        "created": created,
        "status": "needs-gemini-script",
        "notes": [
            "Use Gemini Flash, not Deep Research, to create the script.",
            "Check and correct the text before using it for audio.",
            "Save final text as a .txt or .md file in commutes/google-docs-ready.",
            "Use ElevenLabs Studio to create audio, then export/copy audio to commutes/elevenlabs-ready."
        ]
    }, indent=2) + "\n")

    prompt = f"""Create a commute-learning script for this topic using Gemini Flash, not Deep Research.

Topic: {topic}
Target listening duration: {duration}

Instructions:
- Make this educational for my own learning.
- Keep the structure clear and suitable for spoken audio.
- Explain important terms simply before using them deeply.
- Include practical examples and careful caveats.
- Avoid hype and unsupported claims.
- Check the script for internal consistency and flag anything that may require outside verification.
- Correct anything that seems uncertain, misleading, or overstated.
- Output the final corrected script only.
"""
    (job_dir / "gemini-flash-prompt.txt").write_text(prompt, encoding="utf-8")

    checklist = f"""# Commute Job: {topic}

Duration: {duration}
Created: {created}

## Workflow

1. Open Gemini.
2. Select Gemini Flash if the UI asks for a model.
3. Paste `gemini-flash-prompt.txt`.
4. Generate the script.
5. Review/correct the final text.
6. Save checked text as a `.txt` or `.md` file in `commutes/google-docs-ready`.
7. Put the final checked text into ElevenLabs Studio.
8. Export the audio.
9. Move/copy the audio to `commutes/elevenlabs-ready`.

## Status

- [ ] Gemini Flash script complete
- [ ] Accuracy checked/corrected
- [ ] Saved to `commutes/google-docs-ready`
- [ ] ElevenLabs Studio project created
- [ ] Audio exported
- [ ] Audio copied to `commutes/elevenlabs-ready`
"""
    (job_dir / "README.md").write_text(checklist, encoding="utf-8")


def remove_completed_topics_from_commute(completed_keys):
    if not COMMUTE_FILE.exists():
        return 0

    original_lines = COMMUTE_FILE.read_text(encoding="utf-8-sig").splitlines()
    remaining = []
    removed = 0

    for line in original_lines:
        parsed = parse_commute_line(line)
        if not parsed:
            remaining.append(line)
            continue

        key = job_key(parsed["topic"], parsed["duration"])
        if key in completed_keys:
            removed += 1
        else:
            remaining.append(line)

    while remaining and not remaining[-1].strip():
        remaining.pop()
    COMMUTE_FILE.write_text("\n".join(remaining) + "\n", encoding="utf-8")

    return removed


def append_commute_complete(topic, duration):
    key = job_key(topic, duration)
    if COMMUTE_COMPLETE.exists():
        for line in COMMUTE_COMPLETE.read_text(encoding="utf-8-sig").splitlines():
            parsed = parse_commute_line(line)
            if parsed and job_key(parsed["topic"], parsed["duration"]) == key:
                return False

    timestamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    with COMMUTE_COMPLETE.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([topic, duration, timestamp])
    return True


def file_matches_job(path, job):
    haystack = slugify(path.stem)
    return slugify(job["topic"]) in haystack or job_key(job["topic"], job["duration"]) in haystack


def pending_jobs(state, statuses):
    matches = []
    for item in state.get("jobs", {}).values():
        job_dir = Path(item["job_dir"])
        if not (job_dir / "job.json").exists():
            continue
        data = read_job(job_dir / "job.json")
        if data.get("status") in statuses:
            matches.append((job_dir, data))
    return matches


def pick_job_for_file(path, candidates):
    named = [(job_dir, job) for job_dir, job in candidates if file_matches_job(path, job)]
    if len(named) == 1:
        return named[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def process_script_drops(state):
    processed = 0
    candidates = pending_jobs(state, {"needs-gemini-script", "script-needs-review"})
    for script_path in sorted(GOOGLE_DOCS_READY.iterdir()):
        if not script_path.is_file() or script_path.suffix.lower() not in SCRIPT_EXTENSIONS:
            continue
        picked = pick_job_for_file(script_path, candidates)
        if not picked:
            notify("Commute automation needs help", f"Could not match script file {script_path.name} to one commute topic.")
            continue

        job_dir, job = picked
        final_script = job_dir / "final-script.txt"
        final_script.write_text(script_path.read_text(encoding="utf-8-sig"), encoding="utf-8")

        GOOGLE_COMMUTES.mkdir(parents=True, exist_ok=True)
        google_copy = unique_path(GOOGLE_COMMUTES, f"{slugify(job['topic'])}.txt")
        google_copy.write_text(final_script.read_text(encoding="utf-8"), encoding="utf-8")

        archived = unique_path(PROCESSED, script_path.name)
        script_path.replace(archived)
        update_job_status(
            job_dir,
            "script-ready-for-elevenlabs",
            final_script=str(final_script),
            google_docs_copy=str(google_copy)
        )
        state["jobs"][job_key(job["topic"], job["duration"])]["status"] = "script-ready-for-elevenlabs"
        processed += 1
        notify("Commute script ready", f"{job['topic']} script is ready for ElevenLabs.")
    return processed


def process_audio_drops(state):
    processed = 0
    completed_keys = set()
    candidates = pending_jobs(state, {"script-ready-for-elevenlabs", "needs-gemini-script", "audio-needs-review"})
    for audio_path in sorted(ELEVENLABS_READY.iterdir()):
        if not audio_path.is_file() or audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        picked = pick_job_for_file(audio_path, candidates)
        if not picked:
            notify("Commute automation needs help", f"Could not match audio file {audio_path.name} to one commute topic.")
            continue

        job_dir, job = picked
        podcast_name = f"{slugify(job['topic'])}{audio_path.suffix.lower()}"
        destination = unique_path(PODCAST_INCOMING, podcast_name)
        audio_path.replace(destination)
        archived_copy = job_dir / f"final-audio{destination.suffix}"
        archived_copy.write_bytes(destination.read_bytes())
        update_job_status(
            job_dir,
            "audio-sent-to-podcast",
            podcast_drop_file=str(destination),
            final_audio=str(archived_copy)
        )
        key = job_key(job["topic"], job["duration"])
        state["jobs"][key]["status"] = "audio-sent-to-podcast"
        state.setdefault("completed", [])
        if key not in state["completed"]:
            state["completed"].append(key)
        completed_keys.add(key)
        append_commute_complete(job["topic"], job["duration"])
        processed += 1
        notify("Commute audio sent", f"{job['topic']} audio was moved to the podcast drop folder.")

    removed = remove_completed_topics_from_commute(completed_keys)
    return processed, removed


def maybe_notify_pending(state):
    waiting = pending_jobs(state, {"needs-gemini-script"})
    if not waiting:
        return
    now = dt.datetime.now().astimezone()
    last = state.get("last_pending_gemini_notice")
    if last:
        last_dt = dt.datetime.fromisoformat(last)
        if (now - last_dt).total_seconds() < 3600:
            return
    notify("Commute automation waiting", f"{len(waiting)} topic(s) still need Gemini script output.")
    state["last_pending_gemini_notice"] = now.isoformat(timespec="seconds")


def open_browser_targets():
    subprocess.run(["open", "https://gemini.google.com/"], check=False)
    subprocess.run(["open", "https://elevenlabs.io/app/studio"], check=False)


def main():
    for folder in [COMMUTES, JOBS, PROCESSED, GOOGLE_DOCS_READY, ELEVENLABS_READY, LOGS, PODCAST_INCOMING]:
        folder.mkdir(parents=True, exist_ok=True)

    state = load_state()
    state = rebuild_jobs_from_disk(state)
    seen = set(state.get("seen", []))
    rows = parse_commute_file(COMMUTE_FILE)
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
        state.setdefault("jobs", {})[key] = {
            "topic": row["topic"],
            "duration": row["duration"],
            "job_dir": str(job_dir),
            "status": "needs-gemini-script"
        }
        created_jobs.append(job_dir)

    state["seen"] = sorted(seen)
    scripts_processed = process_script_drops(state)
    audio_processed, completed_count = process_audio_drops(state)
    maybe_notify_pending(state)
    state.setdefault("completed", [])
    state["completed"] = sorted(set(state["completed"]))
    save_state(state)

    log_line = (
        f"{dt.datetime.now().isoformat(timespec='seconds')} - "
        f"created {len(created_jobs)} job(s), "
        f"processed {scripts_processed} script(s), "
        f"sent {audio_processed} audio file(s), "
        f"moved {completed_count} topic(s) to commute complete\n"
    )
    (LOGS / "commute-watch.log").open("a", encoding="utf-8").write(log_line)

    if created_jobs:
        notify("Commute automation", f"Created {len(created_jobs)} commute job(s).")
        open_browser_targets()
        for job_dir in created_jobs:
            print(f"Created {job_dir}")
    else:
        print("No new commute jobs.")


if __name__ == "__main__":
    main()
