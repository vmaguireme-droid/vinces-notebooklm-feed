#!/bin/sh
set -eu

cd "$(dirname "$0")"

lockdir=".podcast-publish.lock"
lock_acquired=0
if mkdir "$lockdir" 2>/dev/null; then
  lock_acquired=1
else
  if [ -r "$lockdir/pid" ]; then
    read -r lock_pid < "$lockdir/pid" || lock_pid=""
    case "$lock_pid" in
      ''|*[!0-9]*) ;;
      *)
        if ! kill -0 "$lock_pid" 2>/dev/null; then
          echo "Removing stale podcast publish lock from PID $lock_pid."
          rm -f "$lockdir/pid"
          rmdir "$lockdir" 2>/dev/null || true
          if mkdir "$lockdir" 2>/dev/null; then
            lock_acquired=1
          fi
        fi
        ;;
    esac
  fi
fi

if [ "$lock_acquired" -ne 1 ]; then
  echo "Podcast publish already running; skipping this cycle."
  exit 0
fi

echo "$$" > "$lockdir/pid"
trap 'rm -f "$lockdir/pid"; rmdir "$lockdir" 2>/dev/null || true' EXIT INT TERM
export PODCAST_DEPLOY_LOCK_HELD=1

notify() {
  title="$1"
  message="$2"
  osascript -e "display notification \"${message}\" with title \"${title}\"" >/dev/null 2>&1 || true
}

file_is_stable() {
  file="$1"
  now="$(date +%s)"
  modified="$(stat -f %m "$file" 2>/dev/null || printf '0')"
  case "$modified" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ $((now - modified)) -ge 120 ]
}

sweep_upload_folder() {
  label="$1"
  folder="$2"
  if [ ! -d "$folder" ]; then
    return 0
  fi

  find "$folder" -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.wav' -o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.flac' \) -print 2>/dev/null |
  while IFS= read -r source; do
    if ! file_is_stable "$source"; then
      echo "Waiting for $label upload to finish syncing: $(basename "$source")"
      continue
    fi
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

# Both Macs may keep the watcher installed for upload intake and recovery, but
# only the selected host is allowed to deploy the shared feed.  A local mkdir
# lock cannot safely coordinate two separately synced Google Drive clients.
publisher_host_file="publisher-host.json"
active_host="$(python3 - "$publisher_host_file" <<'PY'
import json
import sys
from pathlib import Path
try:
    print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("activeHost", ""))
except (OSError, ValueError, json.JSONDecodeError):
    pass
PY
)"
local_host="$(scutil --get LocalHostName 2>/dev/null || hostname)"
if [ -n "$active_host" ] && [ "$(printf '%s' "$active_host" | tr '[:upper:]' '[:lower:]')" != "$(printf '%s' "$local_host" | tr '[:upper:]' '[:lower:]')" ]; then
  echo "Audio publisher is assigned to $active_host; this host ($local_host) will not deploy."
  exit 0
fi

audio_count=0
for candidate in incoming/*; do
  [ -f "$candidate" ] || continue
  case "${candidate##*.}" in
    mp3|MP3|m4a|M4A|wav|WAV|aac|AAC|ogg|OGG|flac|FLAC)
      if file_is_stable "$candidate"; then
        audio_count=$((audio_count + 1))
      fi
      ;;
  esac
done

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
