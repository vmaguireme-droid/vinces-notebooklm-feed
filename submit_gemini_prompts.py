#!/usr/bin/env python3
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMUTES = ROOT / "commutes"
JOBS = COMMUTES / "jobs"
LOGS = COMMUTES / "logs"
STATE_PATH = COMMUTES / "state.json"


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


def load_state():
    if not STATE_PATH.exists():
        return {"seen": [], "jobs": {}}
    state = read_json(STATE_PATH)
    state.setdefault("seen", [])
    state.setdefault("jobs", {})
    return state


def save_state(state):
    write_json(STATE_PATH, state)


def log(message):
    LOGS.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    with (LOGS / "gemini-submit.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} - {message}\n")


def submit_prompt(prompt_text):
    subprocess.run(["pbcopy"], input=prompt_text, text=True, check=True)
    script = """
tell application "Gemini"
  activate
end tell
delay 2
tell application "System Events"
  tell process "Gemini"
    set frontmost to true
  end tell
  keystroke "n" using command down
  delay 1
  keystroke "v" using command down
  delay 1
  key code 36
end tell
"""
    subprocess.run(["osascript", "-e", script], check=True)


def pending_jobs():
    jobs = []
    for job_path in sorted(JOBS.glob("*/job.json")):
        data = read_json(job_path)
        if data.get("status") in {"needs-gemini-script", "gemini-submit-failed"}:
            prompt_path = job_path.parent / "gemini-flash-prompt.txt"
            if prompt_path.exists():
                jobs.append((job_path.parent, job_path, prompt_path, data))
    return jobs


def main():
    state = load_state()
    jobs = pending_jobs()
    if not jobs:
        print("No Gemini prompts need submission.")
        return 0

    submitted = 0
    for job_dir, job_path, prompt_path, job in jobs:
        try:
            submit_prompt(prompt_path.read_text(encoding="utf-8"))
        except subprocess.CalledProcessError as exc:
            job["status"] = "gemini-submit-failed"
            job["updated"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            job["submit_error"] = str(exc)
            write_json(job_path, job)
            log(f"FAILED {job['topic']}: {exc}")
            notify("Gemini submit failed", f"{job['topic']} could not be submitted. Check commutes/logs/gemini-submit.log.")
            return 1

        submitted_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        job["status"] = "submitted-to-gemini"
        job["updated"] = submitted_at
        job["submitted_to_gemini_at"] = submitted_at
        job["submitted_with"] = "Gemini Mac app UI automation"
        job.pop("submit_error", None)
        write_json(job_path, job)

        for key, item in state.get("jobs", {}).items():
            if item.get("job_dir") == str(job_dir):
                item["status"] = "submitted-to-gemini"

        submitted += 1
        log(f"SUBMITTED {job['topic']} from {prompt_path}")
        notify("Gemini prompt submitted", f"{job['topic']} was submitted to Gemini.")

    save_state(state)
    print(f"Submitted {submitted} Gemini prompt(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
