#!/bin/sh
set -eu

cd "$(dirname "$0")"

if ! find incoming -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.wav' -o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.flac' \) | grep -q .; then
  echo "No incoming audio files to publish."
  exit 0
fi

./deploy.sh
