#!/bin/sh
set -eu

cd "$(dirname "$0")"
mkdir -p logs

while true; do
  ./run-once-and-archive.sh >> logs/watch.log 2>&1 || true
  sleep 300
done
