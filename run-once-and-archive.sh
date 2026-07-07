#!/bin/sh
set -eu

cd "$(dirname "$0")"

lockdir=".podcast-publish.lock"
if ! mkdir "$lockdir" 2>/dev/null; then
  echo "Podcast publish already running; skipping this cycle."
  exit 0
fi
trap 'rmdir "$lockdir"' EXIT INT TERM

notify() {
  title="$1"
  message="$2"
  osascript -e "display notification \"${message}\" with title \"${title}\"" >/dev/null 2>&1 || true
}

sweep_upload_folder() {
  label="$1"
  folder="$2"
  if [ ! -d "$folder" ]; then
    return 0
  fi

  find "$folder" -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.wav' -o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.flac' \) -print 2>/dev/null |
  while IFS= read -r source; do
    base="$(basename "$source")"
    destination="incoming/$base"
    if [ -e "$destination" ]; then
      stem="${base%.*}"
      ext="${base##*.}"
      counter=1
      while [ -e "incoming/${stem}-${counter}.${ext}" ]; do
        counter=$((counter + 1))
      done
      destination="incoming/${stem}-${counter}.${ext}"
    fi
    mv "$source" "$destination"
    echo "Moved $label upload into incoming: $base"
  done
}

sweep_upload_folder "iCloud" "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Podcast Upload"
sweep_upload_folder "Google Drive" "$HOME/My Drive/Podcast Upload"

audio_count="$(find -L incoming -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.wav' -o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.flac' \) | wc -l | tr -d ' ')"

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
