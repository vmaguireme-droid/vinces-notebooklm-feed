#!/bin/sh
set -eu

cd "$(dirname "$0")"
mkdir -p commutes/logs

while true; do
  if ! python3 commute_jobs.py >> commutes/logs/commute-watch.log 2>&1; then
    osascript -e 'display notification "Commute topic watcher hit an error. Check commutes/logs/commute-watch.log." with title "Commute automation failed"' >/dev/null 2>&1 || true
  fi
  if ! python3 submit_gemini_prompts.py >> commutes/logs/gemini-submit.log 2>&1; then
    osascript -e 'display notification "Gemini prompt submission hit an error. Check commutes/logs/gemini-submit.log." with title "Commute automation failed"' >/dev/null 2>&1 || true
  fi
  sleep 600
done
