#!/bin/sh
set -eu

cd "$(dirname "$0")"
mkdir -p commutes/logs

while true; do
  python3 commute_jobs.py >> commutes/logs/commute-watch.log 2>&1 || true
  sleep 600
done
