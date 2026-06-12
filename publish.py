#!/usr/bin/env python3
import datetime as dt
import argparse
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
INCOMING = ROOT / "incoming"
OLD_FILES = ROOT / "old-files"
PUBLIC = ROOT / "public"
AUDIO_DIR = PUBLIC / "audio"
CONFIG_PATH = ROOT / "config.json"
EPISODES_PATH = ROOT / "episodes.json"

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}
MIME_OVERRIDES = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


def slugify(value):
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "episode"


def title_from_filename(path):
    stem = re.sub(r"[_-]+", " ", path.stem).strip()
    return re.sub(r"\s+", " ", stem).title()


def rfc2822_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")


def duration_from_afinfo(path):
    try:
        result = subprocess.run(
            ["afinfo", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    match = re.search(r"estimated duration:\s+([0-9.]+)\s+sec", result.stdout)
    if not match:
        return None
    seconds = int(float(match.group(1)))
    return str(dt.timedelta(seconds=seconds))


def load_json(path, fallback):
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, data):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def file_sha256(path):
    digest = __import__("hashlib").sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_audio_name(source, existing):
    base = slugify(source.stem)
    candidate = f"{base}{source.suffix.lower()}"
    while candidate in existing or (AUDIO_DIR / candidate).exists():
        candidate = f"{base}-{uuid.uuid4().hex[:6]}{source.suffix.lower()}"
    return candidate


def unique_archive_name(source):
    OLD_FILES.mkdir(parents=True, exist_ok=True)
    candidate = OLD_FILES / source.name
    if not candidate.exists():
        return candidate
    stem = source.stem
    suffix = source.suffix
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    counter = 1
    while True:
        candidate = OLD_FILES / f"{stem}-{timestamp}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def import_incoming(episodes, publish_new=False):
    INCOMING.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for episode in episodes:
        if episode.get("source_sha256"):
            continue
        audio_file = episode.get("audio_file")
        if not audio_file:
            continue
        audio_path = AUDIO_DIR / audio_file
        if audio_path.exists():
            episode["source_sha256"] = file_sha256(audio_path)
    known_hashes = {episode.get("source_sha256") for episode in episodes if episode.get("source_sha256")}
    known_audio = {episode.get("audio_file") for episode in episodes}
    imported = []

    for source in sorted(INCOMING.iterdir()):
        if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        source_hash = file_sha256(source)
        if source_hash in known_hashes:
            continue

        audio_name = unique_audio_name(source, known_audio)
        destination = AUDIO_DIR / audio_name
        shutil.copy2(source, destination)
        known_audio.add(audio_name)

        episode = {
            "title": title_from_filename(source),
            "description": "",
            "audio_file": audio_name,
            "source_name": source.name,
            "source_sha256": source_hash,
            "published": rfc2822_now(),
            "guid": str(uuid.uuid4()),
            "duration": duration_from_afinfo(destination),
            "draft": not publish_new
        }
        if publish_new:
            episode["description"] = episode["title"]
        episodes.insert(0, episode)
        imported.append(episode)
        known_hashes.add(source_hash)
    return imported


def archive_incoming():
    INCOMING.mkdir(parents=True, exist_ok=True)
    moved = []
    for source in sorted(INCOMING.iterdir()):
        if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        destination = unique_archive_name(source)
        shutil.move(str(source), str(destination))
        moved.append(destination)
    return moved


def absolute_url(base, *parts):
    base = base.rstrip("/")
    encoded = "/".join(quote(str(part)) for part in parts)
    return f"{base}/{encoded}"


def render_feed(config, episodes):
    site_url = config["site_url"].rstrip("/")
    items = []
    for episode in episodes:
        if episode.get("draft"):
            continue
        audio_file = episode["audio_file"]
        audio_path = AUDIO_DIR / audio_file
        mime_type = MIME_OVERRIDES.get(Path(audio_file).suffix.lower())
        mime_type = mime_type or mimetypes.guess_type(audio_file)[0] or "audio/mpeg"
        title = html.escape(episode["title"])
        description = html.escape(episode.get("description") or episode["title"])
        audio_url = absolute_url(site_url, "audio", audio_file)
        length = audio_path.stat().st_size if audio_path.exists() else 0
        duration = episode.get("duration") or ""
        items.append(f"""    <item>
      <title>{title}</title>
      <description>{description}</description>
      <pubDate>{episode["published"]}</pubDate>
      <guid isPermaLink="false">{episode["guid"]}</guid>
      <enclosure url="{audio_url}" length="{length}" type="{mime_type}"/>
      <itunes:duration>{html.escape(duration)}</itunes:duration>
      <itunes:explicit>{str(config.get("explicit", False)).lower()}</itunes:explicit>
    </item>""")

    owner_email = html.escape(config.get("owner_email") or config.get("email") or "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{html.escape(config["title"])}</title>
    <link>{html.escape(site_url)}</link>
    <language>{html.escape(config.get("language", "en-us"))}</language>
    <copyright>{html.escape(config.get("copyright", ""))}</copyright>
    <description>{html.escape(config["description"])}</description>
    <itunes:author>{html.escape(config.get("author", ""))}</itunes:author>
    <itunes:explicit>{str(config.get("explicit", False)).lower()}</itunes:explicit>
    <itunes:category text="{html.escape(config.get("category", "Technology"))}"/>
    <itunes:image href="{html.escape(config.get("image_url", ""))}"/>
    <itunes:owner>
      <itunes:name>{html.escape(config.get("owner_name", config.get("author", "")))}</itunes:name>
      <itunes:email>{owner_email}</itunes:email>
    </itunes:owner>
{chr(10).join(items)}
  </channel>
</rss>
"""


def render_index(config, episodes):
    published = [episode for episode in episodes if not episode.get("draft")]
    rows = []
    for episode in published:
        audio_url = f"audio/{quote(episode['audio_file'])}"
        rows.append(f"""      <article>
        <h2>{html.escape(episode["title"])}</h2>
        <p>{html.escape(episode.get("description") or "")}</p>
        <audio controls preload="metadata" src="{audio_url}"></audio>
      </article>""")
    if not rows:
        rows.append("      <p>No published episodes yet.</p>")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(config["title"])}</title>
  <link rel="alternate" type="application/rss+xml" title="{html.escape(config["title"])}" href="feed.xml">
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #17202a; background: #f7f5ef; }}
    main {{ max-width: 760px; margin: 0 auto; padding: 48px 20px; }}
    h1 {{ font-size: 36px; margin: 0 0 8px; }}
    article {{ border-top: 1px solid #d9d3c5; padding: 24px 0; }}
    audio {{ width: 100%; }}
    a {{ color: #0b5cad; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(config["title"])}</h1>
    <p>{html.escape(config["description"])}</p>
    <p><a href="feed.xml">Podcast RSS feed</a></p>
{chr(10).join(rows)}
  </main>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Generate the podcast site and RSS feed.")
    parser.add_argument(
        "--publish-new",
        action="store_true",
        help="Publish newly imported audio immediately instead of creating draft episodes.",
    )
    parser.add_argument(
        "--archive-incoming",
        action="store_true",
        help="Move audio files from incoming to old-files after importing/generating.",
    )
    args = parser.parse_args()

    config = load_json(CONFIG_PATH, {})
    episodes = load_json(EPISODES_PATH, [])
    imported = import_incoming(episodes, publish_new=args.publish_new)
    save_json(EPISODES_PATH, episodes)
    PUBLIC.mkdir(exist_ok=True)
    (PUBLIC / "feed.xml").write_text(render_feed(config, episodes), encoding="utf-8")
    (PUBLIC / "index.html").write_text(render_index(config, episodes), encoding="utf-8")
    archived = archive_incoming() if args.archive_incoming else []

    print(f"Imported {len(imported)} new audio file(s).")
    print(f"Episodes tracked: {len(episodes)}")
    if imported:
        if args.publish_new:
            print("New episodes were published immediately.")
        else:
            print("New episodes are drafts. Edit episodes.json and set draft to false when ready.")
    if archived:
        print(f"Moved {len(archived)} incoming audio file(s) to {OLD_FILES}.")
    print(f"Wrote {PUBLIC / 'feed.xml'}")
    print(f"Wrote {PUBLIC / 'index.html'}")


if __name__ == "__main__":
    sys.exit(main())
