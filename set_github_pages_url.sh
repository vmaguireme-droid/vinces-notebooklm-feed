#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: ./set_github_pages_url.sh GITHUB_USERNAME" >&2
  exit 1
fi

username="$1"
site_url="https://${username}.github.io/vinces-notebooklm-feed"

python3 - "$site_url" <<'PY'
import json
import sys
from pathlib import Path

site_url = sys.argv[1].rstrip("/")
path = Path("config.json")
config = json.loads(path.read_text())
config["site_url"] = site_url
config["image_url"] = f"{site_url}/artwork.png"
path.write_text(json.dumps(config, indent=2) + "\n")
PY

python3 publish.py
echo "Podcast URL set to ${site_url}"
echo "RSS feed will be ${site_url}/feed.xml"
