#!/bin/sh
set -eu

cd "$(dirname "$0")"
mkdir -p logs

while true; do
  if ! ./run-once-and-archive.sh >> logs/watch.log 2>&1; then
    osascript -e 'display notification "Podcast watcher hit an error. Check logs/watch.log." with title "Podcast automation failed"' >/dev/null 2>&1 || true
  fi
  sleep 300
done
