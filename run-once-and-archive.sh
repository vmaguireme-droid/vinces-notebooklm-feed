#!/bin/sh
set -eu

cd "$(dirname "$0")"

notify() {
  title="$1"
  message="$2"
  osascript -e "display notification \"${message}\" with title \"${title}\"" >/dev/null 2>&1 || true
}

audio_count="$(find incoming -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.wav' -o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.flac' \) | wc -l | tr -d ' ')"

if [ "$audio_count" = "0" ]; then
  echo "No incoming audio files to publish."
  exit 0
fi

if ./deploy.sh; then
  notify "Podcast published" "${audio_count} audio file(s) published and moved to old-files."
else
  notify "Podcast publish failed" "Check Podcast Automation logs/watch.log for details."
  exit 1
fi
