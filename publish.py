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
MAX_DIRECT_AUDIO_BYTES = 90 * 1024 * 1024
# Cloud/iCloud uploads can appear in a watched folder before their bytes are
# complete.  Keep a file untouched until it has been idle for two watcher
# cycles; the watcher runs every five minutes, so this is deliberately modest.
MIN_INPUT_AGE_SECONDS = 120
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


def unique_converted_audio_name(source, existing):
    base = slugify(source.stem)
    candidate = f"{base}.m4a"
    while candidate in existing or (AUDIO_DIR / candidate).exists():
        candidate = f"{base}-{uuid.uuid4().hex[:6]}.m4a"
    return candidate


def should_convert_for_deploy(source):
    return source.stat().st_size > MAX_DIRECT_AUDIO_BYTES


def copy_or_convert_for_deploy(source, destination):
    if not should_convert_for_deploy(source):
        shutil.copy2(source, destination)
        return False

    result = subprocess.run(
        [
            "afconvert",
            "-f", "m4af",
            "-d", "aac@44100",
            "-b", "128000",
            str(source),
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "afconvert could not convert large audio file"
        raise RuntimeError(f"Could not convert {source.name} to AAC: {message}")
    return True


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
        if (dt.datetime.now().timestamp() - source.stat().st_mtime) < MIN_INPUT_AGE_SECONDS:
            print(f"Deferring {source.name}: upload is still new and may be syncing.")
            continue
        source_hash = file_sha256(source)
        if source_hash in known_hashes:
            continue

        converted = should_convert_for_deploy(source)
        audio_name = unique_converted_audio_name(source, known_audio) if converted else unique_audio_name(source, known_audio)
        destination = AUDIO_DIR / audio_name
        copy_or_convert_for_deploy(source, destination)
        if converted:
            print(f"Converted {source.name} to AAC for deploy because it is over 90 MB.")
        known_audio.add(audio_name)

        episode = {
            "title": title_from_filename(source),
            "description": "",
            "audio_file": audio_name,
            "source_name": source.name,
            "source_sha256": source_hash,
            "converted_to_aac": converted,
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


def import_exact_source(episodes, source, title, description="", guid=None, notebook_id=None, notebook_title=None, artifact_type=None, variant=None):
    """Publish one reviewed source without sweeping unrelated incoming files."""
    source = Path(source).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise RuntimeError("The exact source must be a supported audio file.")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    source_hash = file_sha256(source)
    prior = next((episode for episode in episodes if episode.get("source_sha256") == source_hash), None)
    if prior:
        if notebook_id and not prior.get("notebookId"):
            prior["notebookId"] = notebook_id
        if notebook_title and not prior.get("notebookTitle"):
            prior["notebookTitle"] = notebook_title
        if artifact_type and not prior.get("artifactType"):
            prior["artifactType"] = artifact_type
        if variant and not prior.get("variant"):
            prior["variant"] = variant
        return prior, True
    known_audio = {episode.get("audio_file") for episode in episodes}
    converted = should_convert_for_deploy(source)
    audio_name = unique_converted_audio_name(source, known_audio) if converted else unique_audio_name(source, known_audio)
    destination = AUDIO_DIR / audio_name
    copy_or_convert_for_deploy(source, destination)
    episode = {
        "title": title.strip() or title_from_filename(source),
        "description": description.strip() or title.strip() or title_from_filename(source),
        "audio_file": audio_name,
        "source_name": source.name,
        "source_sha256": source_hash,
        "converted_to_aac": converted,
        "published": rfc2822_now(),
        "guid": guid or str(uuid.uuid4()),
        "duration": duration_from_afinfo(destination),
        "draft": False,
    }
    if notebook_id:
        episode["notebookId"] = notebook_id
    if notebook_title:
        episode["notebookTitle"] = notebook_title
    if artifact_type:
        episode["artifactType"] = artifact_type
    if variant:
        episode["variant"] = variant
    episodes.insert(0, episode)
    return episode, False


def archive_incoming(known_hashes):
    INCOMING.mkdir(parents=True, exist_ok=True)
    moved = []
    for source in sorted(INCOMING.iterdir()):
        if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        if (dt.datetime.now().timestamp() - source.stat().st_mtime) < MIN_INPUT_AGE_SECONDS:
            print(f"Leaving {source.name} in incoming: upload is still new and may be syncing.")
            continue
        if file_sha256(source) not in known_hashes:
            print(f"Leaving {source.name} in incoming: it was not safely imported.")
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
    latest_links = []
    for episode in published[:4]:
        latest_links.append(
            f'<a class="latest-link" href="#episode-{html.escape(episode["guid"])}">'
            f'<span>{html.escape(episode["title"])}</span>'
            f'<small>{html.escape(episode.get("duration") or "Audio")}</small></a>'
        )
    latest_panel = ""
    if latest_links:
        latest_panel = f"""    <section class="latest-panel" aria-label="Latest audio episodes">
      <p class="section-label">Latest audio episodes</p>
      <div class="latest-grid">
        {chr(10).join(latest_links)}
      </div>
    </section>"""
    rows = []
    for episode in published:
        audio_url = f"audio/{quote(episode['audio_file'])}"
        episode_id = html.escape(episode["guid"])
        title = html.escape(episode["title"])
        description = html.escape(episode.get("description") or "")
        duration = html.escape(episode.get("duration") or "Audio")
        rows.append(f"""      <article class="episode" id="episode-{episode_id}" data-episode-id="{episode_id}" data-retention-kind="audio">
        <div class="episode-copy">
          <p class="eyebrow">{duration}</p>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <div class="player">
          <audio preload="metadata" src="{audio_url}"></audio>
          <div class="controls">
            <button class="control play" type="button" aria-label="Play {title}">Play</button>
            <button class="control resume" type="button" aria-label="Resume {title}">Resume</button>
            <button class="control stop" type="button" aria-label="Stop {title}">Stop</button>
          </div>
          <div class="progress-control">
            <input class="progress-slider" type="range" min="0" max="1000" value="0" step="1" aria-label="Playback position for {title}">
            <div class="time-row" aria-live="polite">
              <span><strong class="listened-time">0:00</strong> listened</span>
              <span><strong class="remaining-time">0:00</strong> left</span>
            </div>
          </div>
          <details class="availability-details"><summary>Availability</summary><span class="retention-status">Available for 10 days</span></details>
        </div>
      </article>""")
    if not rows:
        rows.append("      <p>No published episodes yet.</p>")

    # Preserve the current library shell when it has the shared organization
    # controls. Publishing new audio should only refresh the generated latest
    # links and episode cards; it must not roll navigation and filtering back
    # to the legacy embedded template below.
    current_index = PUBLIC / "index.html"
    if current_index.exists():
        current = current_index.read_text(encoding="utf-8")
        required_shell_markers = (
            'id="contentSideToggle"',
            'id="groupPicker"',
            'id="datePicker"',
            "nav.js?v=shared-navigation-20260827-7",
            '<section class="latest-panel" aria-label="Latest audio episodes">',
            '<section class="episodes" id="episodes">',
        )
        if all(marker in current for marker in required_shell_markers):
            latest_start = current.index(
                '    <section class="latest-panel" aria-label="Latest audio episodes">'
            )
            episodes_open = '    <section class="episodes" id="episodes">'
            episodes_start = current.index(episodes_open, latest_start)
            episodes_body_start = episodes_start + len(episodes_open)
            episodes_end = current.index("\n    </section>\n  </main>", episodes_body_start)
            return (
                current[:latest_start]
                + latest_panel
                + "\n"
                + episodes_open
                + "\n"
                + chr(10).join(rows)
                + current[episodes_end:]
            )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(config["title"])}</title>
  <link rel="alternate" type="application/rss+xml" title="{html.escape(config["title"])}" href="feed.xml">
  <style>
    :root {{
      color-scheme: dark;
      --bg: #071012;
      --panel: rgba(15, 31, 34, 0.86);
      --panel-strong: rgba(20, 45, 49, 0.96);
      --text: #ecf8f7;
      --muted: #a7bfbd;
      --line: rgba(132, 255, 239, 0.2);
      --cyan: #75f7e6;
      --cyan-light: #a9fff4;
      --amber: #ffbd61;
      --red: #ff7d73;
      --shadow: rgba(0, 0, 0, 0.36);
      --accent-rgb: 117, 247, 230;
      --secondary-rgb: 255, 189, 97;
      --page-2: #0d1d21;
      --page-3: #132529;
      --button-text: #061011;
    }}
    html[data-color-theme="red-blue"] {{
      --bg: #070b18;
      --panel: rgba(13, 25, 47, 0.88);
      --panel-strong: rgba(24, 44, 78, 0.96);
      --text: #f2f6ff;
      --muted: #b7c7df;
      --line: rgba(91, 160, 255, 0.27);
      --cyan: #5ba0ff;
      --cyan-light: #acd0ff;
      --amber: #ff5d69;
      --red: #ff8c96;
      --accent-rgb: 91, 160, 255;
      --secondary-rgb: 255, 93, 105;
      --page-2: #101a36;
      --page-3: #18294d;
      --button-text: #050816;
    }}
    * {{ box-sizing: border-box; }}
  body {{
      margin: 0;
    min-height: 100vh;
    overflow-x: hidden;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 18% 10%, rgba(var(--accent-rgb), 0.16), transparent 28rem),
        radial-gradient(circle at 88% 4%, rgba(var(--secondary-rgb), 0.12), transparent 24rem),
        linear-gradient(135deg, var(--bg) 0%, var(--page-2) 55%, var(--page-3) 100%);
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: 0.28;
      background-image:
        linear-gradient(rgba(var(--accent-rgb), 0.08) 1px, transparent 1px),
        linear-gradient(90deg, rgba(var(--accent-rgb), 0.08) 1px, transparent 1px);
      background-size: 44px 44px;
      mask-image: linear-gradient(to bottom, black, transparent 82%);
    }}
    main {{
      position: relative;
      max-width: 1220px;
      margin: 0 auto;
      padding: 0 20px 72px;
    }}
    header.topbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 24px;
      justify-content: space-between;
      align-items: center;
      margin: 32px 0 24px;
      padding: 24px clamp(20px, 4vw, 34px);
      border: 1px solid var(--line);
      border-radius: 28px;
      background:
        linear-gradient(135deg, rgba(var(--accent-rgb), 0.18), rgba(var(--secondary-rgb), 0.12)),
        linear-gradient(135deg, var(--panel-strong), var(--bg));
      box-shadow: 0 26px 70px var(--shadow);
    }}
    .brand {{
      align-items: center;
      display: flex;
      gap: 14px;
      min-width: 0;
    }}
    .artwork {{
      width: 42px;
      height: 42px;
      border-radius: 50%;
      box-shadow: 0 18px 48px var(--shadow);
      border: 1px solid var(--line);
      object-fit: cover;
      flex: 0 0 auto;
    }}
    .brand h1 {{
      font-size: 1.12rem;
      line-height: 1.1;
      margin: 0;
    }}
    .brand .subtitle {{
      color: var(--muted);
      font-size: 0.88rem;
      line-height: normal;
      margin: 4px 0 0;
    }}
    .audio-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin: 24px 0 34px;
      align-items: center;
    }}
    .site-nav {{
      display: flex;
      flex: 1 1 100%;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-start;
      max-width: 100%;
      margin: 0;
    }}
    .site-nav a,
    .site-nav .theme-toggle {{
      display: inline-flex;
      min-height: 42px;
      align-items: center;
      padding: 0 16px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(var(--accent-rgb), 0.08);
      color: var(--cyan);
      font-size: 0.92rem;
      font-weight: 800;
      text-decoration: none;
    }}
    .site-nav .theme-toggle {{
      appearance: none;
      cursor: pointer;
      font-family: inherit;
      font-size: inherit;
      justify-content: center;
      width: 146px;
    }}
    .theme-toggle-dots {{
      display: inline-flex;
      gap: 4px;
      margin-right: 7px;
    }}
    .theme-toggle-dots i {{
      border: 1px solid rgba(255, 255, 255, 0.55);
      border-radius: 50%;
      display: block;
      height: 10px;
      width: 10px;
    }}
    .theme-toggle-dots i:first-child {{ background: #ff5d69; }}
    .theme-toggle-dots i:last-child {{ background: #5ba0ff; }}
    html[data-color-theme="red-blue"] .theme-toggle-dots i:first-child {{ background: #ffbd61; }}
    html[data-color-theme="red-blue"] .theme-toggle-dots i:last-child {{ background: #75f7e6; }}
    .site-nav a[aria-current="page"],
    .site-nav a:hover,
    .site-nav .theme-toggle:hover {{
      color: var(--button-text);
      background: linear-gradient(135deg, var(--cyan), var(--cyan-light));
      border-color: transparent;
    }}
    .playlist-panel {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto auto;
      gap: 12px;
      align-items: center;
      margin: 0 0 20px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: rgba(var(--accent-rgb), 0.08);
      box-shadow: 0 14px 32px rgba(0, 0, 0, 0.22);
    }}
    .playlist-status {{
      color: var(--muted);
      line-height: 1.4;
    }}
    .latest-panel {{
      margin: 0 0 20px;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: rgba(var(--secondary-rgb), 0.08);
      box-shadow: 0 14px 32px rgba(0, 0, 0, 0.22);
    }}
    .section-label {{
      margin: 0 0 12px;
      color: var(--amber);
      font-size: 13px;
      font-weight: 800;
      text-transform: uppercase;
    }}
    .latest-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .latest-link {{
      display: grid;
      gap: 6px;
      min-height: 84px;
      align-content: center;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(7, 16, 18, 0.42);
      color: var(--text);
      text-decoration: none;
    }}
    .latest-link span {{
      font-weight: 800;
      line-height: 1.2;
    }}
    .latest-link small {{
      color: var(--muted);
    }}
    a, .secondary-button {{
      color: var(--cyan);
    }}
    .top-actions a {{
      display: inline-flex;
      min-height: 42px;
      align-items: center;
      padding: 0 16px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(var(--accent-rgb), 0.08);
      color: var(--cyan);
      font-weight: 800;
      text-decoration: none;
    }}
    .top-actions a[aria-current="page"],
    .top-actions a:hover {{
      color: #061011;
      background: linear-gradient(135deg, var(--cyan), var(--cyan-light));
      border-color: transparent;
    }}
    .secondary-button {{
      appearance: none;
      border: 1px solid var(--line);
      background: rgba(var(--accent-rgb), 0.08);
      border-radius: 999px;
      padding: 10px 16px;
      font: inherit;
      cursor: pointer;
    }}
    .refresh-button {{
      color: #061011;
      background: linear-gradient(135deg, var(--cyan), var(--cyan-light));
      border-color: transparent;
      font-weight: 800;
    }}
    .playlist-button {{
      min-height: 48px;
      border: 0;
      border-radius: 999px;
      padding: 0 18px;
      color: #061011;
      background: linear-gradient(135deg, var(--cyan), var(--cyan-light));
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }}
    .playlist-button.secondary {{
      color: var(--cyan);
      background: rgba(var(--accent-rgb), 0.08);
      border: 1px solid var(--line);
    }}
    .episodes {{
      display: grid;
      gap: 18px;
    }}
    .episode {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      gap: 26px;
      align-items: center;
      padding: 24px;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: linear-gradient(135deg, var(--panel), rgba(11, 23, 26, 0.92));
      box-shadow: 0 18px 50px var(--shadow);
      backdrop-filter: blur(12px);
    }}
    .audio-notebook-family {{
      border: 2px solid rgba(var(--accent-rgb), 0.62);
      border-radius: 22px;
      overflow: hidden;
      background: linear-gradient(145deg, rgba(3, 15, 16, 0.98), rgba(8, 27, 29, 0.96));
      box-shadow: inset 0 0 0 1px rgba(var(--accent-rgb), 0.08), 0 14px 34px rgba(0, 0, 0, 0.24);
    }}
    .audio-notebook-banner {{
      margin: 0;
      padding: 10px 16px;
      color: var(--cyan);
      background: rgba(var(--accent-rgb), 0.1);
      border-bottom: 1px solid rgba(var(--accent-rgb), 0.28);
      font-size: 0.92rem;
      font-weight: 900;
    }}
    .audio-notebook-family-body {{
      padding: 12px;
    }}
    .audio-notebook-list {{ background: rgba(var(--accent-rgb), .035); border: 1px solid rgba(var(--accent-rgb), .18); border-radius: 14px; display: grid; gap: 14px; min-width: 0; padding: 9px; }}
    .audio-library-filters {{ display: grid; gap: 12px; margin: 0 0 20px; }}
    .audio-type-filter {{ border: 1px solid var(--line); border-radius: 18px; margin: 0; padding: 11px 13px 10px; }}
    .audio-type-filter legend {{ color: var(--muted); font-size: .72rem; font-weight: 900; letter-spacing: .08em; padding: 0 5px; text-transform: uppercase; }}
    .audio-type-options {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .audio-type-option {{ align-items: center; background: rgba(var(--accent-rgb), .07); border: 1px solid var(--line); border-radius: 999px; cursor: pointer; display: inline-flex; font-size: .78rem; font-weight: 800; gap: 7px; min-height: 36px; padding: 6px 11px; }}
    .audio-type-option:has(input:checked) {{ background: rgba(var(--accent-rgb), .2); border-color: var(--cyan); }}
    .audio-type-option input {{ accent-color: var(--cyan); height: 16px; margin: 0; width: 16px; }}
    .audio-type-status {{ color: var(--muted); font-size: .72rem; margin: 8px 2px 0; }}
    .audio-library-pickers {{ display: flex; flex-wrap: wrap; gap: 12px; }}
    .audio-library-picker {{ display: grid; gap: 6px; }}
    .audio-library-picker span {{ color: var(--muted); font-size: 0.76rem; font-weight: 800; text-transform: uppercase; }}
    .audio-library-picker select {{ min-height: 44px; border: 1px solid var(--line); border-radius: 999px; padding: 8px 14px; color: var(--cyan); background: var(--panel-strong); font: inherit; font-weight: 800; }}
    .rolodex-active {{ animation: rolodex .85s ease; }}
    @keyframes rolodex {{ 0% {{ opacity: .35; transform: rotateX(18deg) translateY(-12px); }} 100% {{ opacity: 1; transform: none; }} }}
    .episode-related {{ grid-column: 1 / -1; border-top: 1px solid var(--line); padding-top: 14px; }}
    .episode-related h3 {{ margin: 0 0 10px; font-size: 0.9rem; }}
    .episode-related-links {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .episode-related-links a {{ border: 1px solid var(--line); border-radius: 999px; padding: 7px 10px; text-decoration: none; font-size: 0.82rem; font-weight: 800; }}
    .family-sibling-links {{ grid-column: 1 / -1; border-top: 1px solid var(--line); display: flex; flex-wrap: wrap; gap: 8px; padding-top: 14px; }}
    .family-sibling-label {{ flex: 1 0 100%; font-size: .9rem; font-weight: 800; }}
    .family-sibling-link {{ border: 1px solid var(--line); border-radius: 999px; color: var(--cyan); font-size: .82rem; font-weight: 800; min-height: 40px; padding: 9px 12px; text-decoration: none; }}
    .family-sibling-link:hover, .family-sibling-link:focus-visible {{ background: var(--cyan); color: #061011; outline: 3px solid var(--amber); outline-offset: 2px; }}
    .episode:target, .episode.linked-artifact {{ outline: 3px solid var(--amber); outline-offset: 3px; }}
    .episode.removing {{
      opacity: 0;
      transform: translateY(8px) scale(0.985);
      transition: opacity 220ms ease, transform 220ms ease;
    }}
    .eyebrow {{
      margin: 0 0 8px;
      color: var(--amber);
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
    }}
    h2 {{
      margin: 0 0 10px;
      font-size: clamp(21px, 3vw, 30px);
      line-height: 1.1;
      letter-spacing: 0;
    }}
    .episode-copy p:last-child {{
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
    }}
    .player {{
      display: grid;
      gap: 14px;
    }}
    .controls {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}
    .control {{
      min-height: 70px;
      border: 0;
      border-radius: 18px;
      color: #061011;
      font-size: 20px;
      font-weight: 800;
      cursor: pointer;
      box-shadow: 0 14px 30px rgba(0, 0, 0, 0.3);
    }}
    .play {{
      background: linear-gradient(135deg, var(--cyan), var(--cyan-light));
    }}
    .stop {{
      background: linear-gradient(135deg, var(--amber), var(--red));
    }}
    .resume {{
      color: var(--cyan);
      background: rgba(var(--accent-rgb), 0.08);
      border: 1px solid var(--line);
    }}
    .progress-control {{
      display: grid;
      gap: 8px;
    }}
    .progress-slider {{
      accent-color: var(--cyan);
      cursor: pointer;
      width: 100%;
    }}
    .time-row {{
      color: var(--muted);
      display: flex;
      font-size: 13px;
      font-weight: 700;
      justify-content: space-between;
      gap: 12px;
    }}
    .remove-option,
    .refresh-remove-option,
    .playlist-option {{
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      line-height: 1.35;
      user-select: none;
    }}
    .remove-option input,
    .refresh-remove-option input,
    .playlist-option input {{
      width: 24px;
      height: 24px;
      accent-color: var(--cyan);
      flex: 0 0 auto;
    }}
    .refresh-remove-option {{
      padding: 10px;
      border-radius: 14px;
      background: rgba(var(--secondary-rgb), 0.08);
    }}
    .playlist-option {{
      padding: 10px;
      border-radius: 14px;
      background: rgba(236, 248, 247, 0.06);
    }}
    .empty-state {{
      display: none;
      padding: 28px;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: var(--panel-strong);
      color: var(--muted);
    }}
    .availability-details {{
      color: var(--muted);
      font-size: 0.84rem;
      position: relative;
    }}
    .availability-details summary {{ cursor: pointer; font-weight: 800; color: var(--cyan); }}
    .availability-details[open] .retention-status {{
      display: block;
      margin-top: 8px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel-strong);
    }}
    @media (max-width: 760px) {{
      header.topbar {{
        align-items: flex-start;
      }}
      .episode {{
        grid-template-columns: 1fr;
      }}
      .playlist-panel {{
        grid-template-columns: 1fr;
      }}
      .latest-grid {{
        grid-template-columns: 1fr;
      }}
    }}
    @media (max-width: 620px) {{
      .site-nav {{
        flex-wrap: nowrap;
        margin-inline: -4px;
        overflow-x: auto;
        padding: 2px 4px 8px;
        scrollbar-width: thin;
      }}
      .site-nav a,
      .site-nav .theme-toggle {{
        flex: 0 0 auto;
        font-size: 0.75rem;
        min-height: 38px;
        padding-inline: 12px;
        white-space: nowrap;
      }}
      .brand .subtitle {{ display: none; }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div class="brand">
        <img class="artwork" src="artwork.png" alt="">
        <div>
          <h1>Vinces Podcasts</h1>
          <p class="subtitle">Audio podcast library</p>
        </div>
      </div>
      <nav class="site-nav" data-generator-nav aria-label="Generator navigation">
        <a href="https://generator.technologyandstuff.com/">Flight Deck</a>
        <a href="https://generator.technologyandstuff.com/knowledge/">Portal</a>
        <a href="https://vinces-public-knowledge.vmaguireme.chatgpt.site/generator">Private Control Center</a>
        <a href="https://vinces-public-knowledge.vmaguireme.chatgpt.site/notebook-artifacts">Artifact Factory</a>
        <a href="https://generator.technologyandstuff.com/generator.html">Suggest a Topic</a>
        <a href="https://generator.technologyandstuff.com/index.html">Videos</a>
        <a aria-current="page" href="https://generator.technologyandstuff.com/audio-podcasts/index.html">Audio</a>
        <a href="https://generator.technologyandstuff.com/reports/">Reports</a>
        <a href="https://generator.technologyandstuff.com/infographics/">Infographics</a>
        <a href="https://generator.technologyandstuff.com/mind-maps/">Mind Maps</a>
        <button class="theme-toggle" type="button" data-nav-utility data-theme-toggle aria-label="Change colors to red and blue" aria-pressed="false">
          <span class="theme-toggle-dots" aria-hidden="true"><i></i><i></i></span>
          <span class="theme-toggle-label">Red + Blue</span>
        </button>
      </nav>
    </header>
    <div class="audio-actions">
      <a href="feed.xml">Podcast RSS feed</a>
      <button class="secondary-button refresh-button" type="button" id="refresh-page">Refresh page</button>
    </div>
    <div class="audio-library-filters">
      <fieldset class="audio-type-filter" id="audioTypeFilter">
        <legend>Audio type</legend>
        <div class="audio-type-options" id="audioTypeOptions"></div>
        <p class="audio-type-status" id="audioTypeStatus" aria-live="polite"></p>
      </fieldset>
      <div class="audio-library-pickers" aria-label="Audio notebook selection">
        <label class="audio-library-picker"><span>Notebook</span><select id="notebookPicker"><option value="all">All notebooks</option></select></label>
      </div>
    </div>
{latest_panel}
    <section class="episodes" id="episodes">
{chr(10).join(rows)}
    </section>
  </main>
  <script src="../nav.js?v=shared-navigation-20260823-1"></script>
  <script src="../artifact-family-links.js?v=family-links-20260827-3"></script>
  <script>
    const positionKey = "vinces-notebooklm-feed-positions";
    const positions = JSON.parse(localStorage.getItem(positionKey) || "{{}}");
    const episodes = Array.from(document.querySelectorAll(".episode"));
    episodes.forEach((episode) => {{ episode.dataset.audioSubtype = "Legacy/Unlabeled"; }});
    let audioTypes = [];
    let audioTypeSelection = new Set();

    function familyItemHref(item) {{
      if (item.kind === "video") return `/index.html?episode=${{encodeURIComponent(item.id)}}`;
      if (item.kind === "audio") return `/audio-podcasts/index.html?episode=${{encodeURIComponent(item.id)}}`;
      const pages = {{ report: "/reports/", infographic: "/infographics/", mind_map: "/mind-maps/" }};
      return `${{pages[item.artifactType] || "/"}}?artifact=${{encodeURIComponent(item.id)}}`;
    }}

    function familyItemLabel(item) {{
      const type = item.kind === "video" ? "Video" : item.kind === "audio" ? "Audio"
        : item.artifactType === "mind_map" ? "Mind Map"
          : `${{item.artifactType?.[0]?.toUpperCase() || ""}}${{item.artifactType?.slice(1) || "Artifact"}}`;
      return `${{type}}${{item.variant ? ` (${{item.variant}})` : ""}}: ${{item.title}}`;
    }}

    function addRelatedArtifacts(episode, family, currentId) {{
      if (episode.querySelector(".family-sibling-links")) return;
      const links = document.createElement("div");
      links.className = "family-sibling-links";
      links.setAttribute("aria-label", "Other artifact types from this notebook");
      ArtifactFamilyLinks.render(links, [family], {{ kind: "audio", notebookId: family.notebookId, id: currentId }});
      if (!links.hidden) episode.append(links);
    }}

    function applyNotebookFamilies(index) {{
      const picker = document.querySelector("#notebookPicker");
      const notebookTargets = new Map();
      (index.families || []).forEach((family) => {{
        const audioItems = family.items.filter((item) => item.kind === "audio");
        const nodes = audioItems.map((item) => document.querySelector(`[data-episode-id="${{CSS.escape(item.id)}}"]`)).filter(Boolean);
        nodes.forEach((node) => {{ node.dataset.notebookId = family.notebookId; }});
        audioItems.forEach((item) => {{
          const node = document.querySelector(`[data-episode-id="${{CSS.escape(item.id)}}"]`);
          if (node) node.dataset.audioSubtype = item.variant || "Legacy/Unlabeled";
        }});
        nodes.forEach((node) => addRelatedArtifacts(node, family, node.dataset.episodeId));
        if (!nodes.length) return;
        const first = nodes[0];
        const parent = first.parentElement;
        if (!parent || nodes.some((node) => node.parentElement !== parent)) return;
        const wrapper = document.createElement("section");
        wrapper.className = "audio-notebook-family";
        wrapper.dataset.notebookId = family.notebookId;
        const banner = document.createElement("h2");
        banner.className = "audio-notebook-banner";
        banner.textContent = family.notebookTitle || "NotebookLM notebook";
        const body = document.createElement("div");
        body.className = "audio-notebook-family-body";
        const list = document.createElement("div");
        list.className = "audio-notebook-list";
        parent.insertBefore(wrapper, first);
        nodes.forEach((node) => list.append(node));
        body.append(list);
        wrapper.append(banner, body);
        notebookTargets.set(family.notebookId, {{ title: family.notebookTitle, target: wrapper }});
      }});
      (index.families || []).forEach((family) => {{
        if (notebookTargets.has(family.notebookId)) return;
        const firstAudio = family.items.find((item) => item.kind === "audio");
        const target = firstAudio ? document.querySelector(`[data-episode-id="${{CSS.escape(firstAudio.id)}}"]`) : null;
        if (target) notebookTargets.set(family.notebookId, {{ title: family.notebookTitle, target }});
      }});
      Array.from(notebookTargets).sort((a, b) => a[1].title.localeCompare(b[1].title)).forEach(([id, entry]) => {{
        const option = document.createElement("option"); option.value = id; option.textContent = entry.title; picker.append(option);
      }});
      picker.addEventListener("change", () => {{
        const entry = notebookTargets.get(picker.value);
        const target = entry?.target || document.querySelector("#episodes");
        target?.scrollIntoView({{ behavior: "smooth", block: "start" }});
        if (entry?.target) {{ entry.target.classList.add("rolodex-active"); setTimeout(() => entry.target.classList.remove("rolodex-active"), 900); }}
      }});
      const requested = new URLSearchParams(location.search).get("episode");
      const target = requested ? document.querySelector(`[data-episode-id="${{CSS.escape(requested)}}"]`) : null;
      if (target) {{
        picker.value = target.dataset.notebookId || "all";
        target.classList.add("linked-artifact");
        target.scrollIntoView({{ behavior: "smooth", block: "center" }});
      }}
      initializeAudioTypeFilters(notebookTargets);
    }}

    function renderAudioTypeFilters() {{
      const options = document.querySelector("#audioTypeOptions");
      options.innerHTML = "";
      const makeOption = (value, label, checked, extraClass = "") => {{
        const wrapper = document.createElement("label");
        wrapper.className = `audio-type-option ${{extraClass}}`.trim();
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = value;
        input.checked = checked;
        if (value === "__all__") input.indeterminate = audioTypeSelection.size > 0 && audioTypeSelection.size < audioTypes.length;
        const text = document.createElement("span");
        text.textContent = label;
        wrapper.append(input, text);
        options.append(wrapper);
      }};
      makeOption("__all__", "All", audioTypeSelection.size === audioTypes.length, "audio-type-option-all");
      audioTypes.forEach((type) => makeOption(type, type, audioTypeSelection.has(type)));
      document.querySelector("#audioTypeStatus").textContent = `${{audioTypeSelection.size}} of ${{audioTypes.length}} types selected`;
    }}

    function applyAudioTypeFilters(notebookTargets) {{
      episodes.forEach((episode) => {{ episode.hidden = !audioTypeSelection.has(episode.dataset.audioSubtype); }});
      document.querySelectorAll(".audio-notebook-family").forEach((family) => {{
        family.hidden = !Array.from(family.querySelectorAll(".episode")).some((episode) => !episode.hidden);
      }});
      document.querySelectorAll(".latest-link").forEach((link) => {{
        const target = document.querySelector(link.hash);
        link.hidden = Boolean(target?.hidden || target?.closest(".audio-notebook-family")?.hidden);
      }});
      const picker = document.querySelector("#notebookPicker");
      Array.from(picker.options).forEach((option) => {{
        if (option.value === "all") return;
        const target = notebookTargets.get(option.value)?.target;
        const visible = target?.classList.contains("episode") ? !target.hidden : Boolean(target?.querySelector(".episode:not([hidden])"));
        option.disabled = !visible;
      }});
      if (picker.selectedOptions[0]?.disabled) picker.value = "all";
      renderAudioTypeFilters();
    }}

    function initializeAudioTypeFilters(notebookTargets) {{
      audioTypes = [...new Set(episodes.map((episode) => episode.dataset.audioSubtype))].sort((a, b) => {{
        const order = ["Deep Dive", "Brief", "Critique", "Debate", "Legacy/Unlabeled"];
        return (order.indexOf(a) < 0 ? 99 : order.indexOf(a)) - (order.indexOf(b) < 0 ? 99 : order.indexOf(b)) || a.localeCompare(b);
      }});
      audioTypeSelection = new Set(audioTypes);
      renderAudioTypeFilters();
      document.querySelector("#audioTypeFilter").addEventListener("change", (event) => {{
        const checkbox = event.target.closest('input[type="checkbox"]');
        if (!checkbox) return;
        if (checkbox.value === "__all__") audioTypeSelection = checkbox.checked ? new Set(audioTypes) : new Set();
        else if (checkbox.checked) audioTypeSelection.add(checkbox.value);
        else audioTypeSelection.delete(checkbox.value);
        applyAudioTypeFilters(notebookTargets);
      }});
    }}

    fetch(`/api/public-library-families?v=${{Date.now()}}`)
      .then((response) => response.ok ? response.json() : {{ families: [] }})
      .then(applyNotebookFamilies)
      .catch(() => applyNotebookFamilies({{ families: [] }}));

    function savePositions() {{
      localStorage.setItem(positionKey, JSON.stringify(positions));
    }}

    function formatTime(seconds) {{
      if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
      const total = Math.floor(seconds);
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      if (hours) {{
        return `${{hours}}:${{String(minutes).padStart(2, "0")}}:${{String(secs).padStart(2, "0")}}`;
      }}
      return `${{minutes}}:${{String(secs).padStart(2, "0")}}`;
    }}

    function saveAudioPosition(episode, audio) {{
      const id = episode.dataset.episodeId;
      if (Number.isFinite(audio.currentTime) && audio.currentTime > 1 && (!audio.duration || audio.currentTime < audio.duration - 2)) {{
        positions[id] = audio.currentTime;
      }} else {{
        delete positions[id];
      }}
      savePositions();
    }}

    function restoreAudioPosition(episode, audio) {{
      if (audio.currentTime > 1) return audio.currentTime;
      const saved = Number(positions[episode.dataset.episodeId] || 0);
      if (saved > 1 && Number.isFinite(saved)) {{
        audio.currentTime = saved;
        return saved;
      }}
      return 0;
    }}

    function seekWhenReady(audio, target, afterSeek) {{
      const applySeek = () => {{
        if (Number.isFinite(target) && target >= 0) {{
          const limit = audio.duration ? Math.max(0, audio.duration - 0.5) : target;
          audio.currentTime = Math.min(target, limit);
        }}
        afterSeek();
      }};
      if (audio.readyState >= 1) {{
        applySeek();
      }} else {{
        audio.addEventListener("loadedmetadata", applySeek, {{ once: true }});
        audio.load();
      }}
    }}

    function stopAllAudio(exceptAudio = null) {{
      document.querySelectorAll("audio").forEach((audio) => {{
        if (audio !== exceptAudio) {{
          audio.pause();
        }}
      }});
    }}

    function playEpisode(episode, fromBeginning = false) {{
      const audio = episode.querySelector("audio");
      if (!audio) return;
      stopAllAudio(audio);

      const target = fromBeginning ? 0 : (audio.currentTime > 1 ? audio.currentTime : Number(positions[episode.dataset.episodeId] || 0));

      if (fromBeginning) {{
        delete positions[episode.dataset.episodeId];
        savePositions();
      }}

      const applySeek = () => {{
        if (Number.isFinite(target) && target >= 0) {{
          const limit = audio.duration ? Math.max(0, audio.duration - 0.5) : target;
          try {{
            audio.currentTime = Math.min(target, limit);
          }} catch (e) {{}}
        }}
      }};

      if (audio.readyState >= 1) {{
        applySeek();
      }} else {{
        audio.addEventListener("loadedmetadata", applySeek, {{ once: true }});
      }}

      const playPromise = audio.play();
      if (playPromise !== undefined) {{
        playPromise.catch((err) => {{
          console.warn("Audio playback prevented or failed:", err);
        }});
      }}
    }}

    episodes.forEach((episode) => {{
      const id = episode.dataset.episodeId;
      const audio = episode.querySelector("audio");
      const play = episode.querySelector(".play");
      const resume = episode.querySelector(".resume");
      const stop = episode.querySelector(".stop");
      const slider = episode.querySelector(".progress-slider");
      const listenedTime = episode.querySelector(".listened-time");
      const remainingTime = episode.querySelector(".remaining-time");
      let sliding = false;

      play.addEventListener("click", () => {{
        playEpisode(episode, true);
      }});

      resume.addEventListener("click", () => {{
        playEpisode(episode, false);
      }});

      stop.addEventListener("click", () => {{
        audio.pause();
        saveAudioPosition(episode, audio);
        updateProgress();
      }});

      audio.addEventListener("play", () => {{
        episode.classList.add("is-playing");
      }});

      audio.addEventListener("pause", () => {{
        episode.classList.remove("is-playing");
      }});

      audio.addEventListener("error", () => {{
        console.warn(`Audio error for episode ${{id}}:`, audio.error);
      }});

      function updateProgress() {{
        const percent = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
        if (!sliding) {{
          slider.value = String(Math.round(percent * 10));
        }}
        listenedTime.textContent = formatTime(audio.currentTime);
        remainingTime.textContent = formatTime((audio.duration || 0) - audio.currentTime);
      }}

      audio.addEventListener("loadedmetadata", () => {{
        restoreAudioPosition(episode, audio);
        updateProgress();
      }});

      audio.addEventListener("timeupdate", () => {{
        updateProgress();
        saveAudioPosition(episode, audio);
      }});

      slider.addEventListener("input", () => {{
        sliding = true;
        if (audio.duration) {{
          const nextTime = (Number(slider.value) / 1000) * audio.duration;
          listenedTime.textContent = formatTime(nextTime);
          remainingTime.textContent = formatTime(audio.duration - nextTime);
        }}
      }});

      slider.addEventListener("change", () => {{
        if (audio.duration) {{
          audio.currentTime = (Number(slider.value) / 1000) * audio.duration;
          saveAudioPosition(episode, audio);
        }}
        sliding = false;
        updateProgress();
      }});

      audio.addEventListener("ended", () => {{
        slider.value = "1000";
        listenedTime.textContent = formatTime(audio.duration || audio.currentTime);
        remainingTime.textContent = "0:00";
        delete positions[id];
        savePositions();
      }});
    }});

    document.getElementById("refresh-page").addEventListener("click", () => {{
      window.location.reload();
    }});

    fetch("/api/public-content-retention").then((response) => response.ok ? response.json() : null).then((data) => {{
      if (!data) return;
      const byId = new Map(data.items.filter((item) => item.kind === "audio").map((item) => [item.id, item]));
      episodes.forEach((episode) => {{
        const item = byId.get(episode.dataset.episodeId);
        const status = episode.querySelector(".retention-status");
        if (!item || !status) return;
        if (item.keepIndefinitely) {{ status.textContent = "Kept until the owner removes it."; return; }}
        const days = Math.max(1, Math.ceil((item.remainingSeconds || 0) / 86400));
        status.textContent = `Available for ${{days}} more day${{days === 1 ? "" : "s"}}.`;
      }});
    }}).catch(() => {{}});
  </script>
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
    parser.add_argument("--exact-source", help="Publish only this reviewed audio source.")
    parser.add_argument("--exact-title", default="", help="Title for --exact-source.")
    parser.add_argument("--exact-description", default="", help="Description for --exact-source.")
    parser.add_argument("--exact-guid", default="", help="Stable GUID for --exact-source.")
    parser.add_argument("--exact-notebook-id", default="", help="Notebook UUID for --exact-source.")
    parser.add_argument("--exact-notebook-title", default="", help="Notebook title for --exact-source.")
    parser.add_argument("--exact-artifact-type", default="", help="Artifact type for --exact-source.")
    parser.add_argument("--exact-variant", default="", help="Variant for --exact-source.")
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Regenerate the public page and feed from episodes.json without importing incoming files.",
    )
    args = parser.parse_args()

    config = load_json(CONFIG_PATH, {})
    episodes = load_json(EPISODES_PATH, [])
    if args.render_only:
        imported = []
    elif args.exact_source:
        episode, duplicate = import_exact_source(
            episodes,
            args.exact_source,
            args.exact_title,
            args.exact_description,
            args.exact_guid or None,
            notebook_id=args.exact_notebook_id or None,
            notebook_title=args.exact_notebook_title or None,
            artifact_type=args.exact_artifact_type or None,
            variant=args.exact_variant or None,
        )
        imported = [] if duplicate else [episode]
    else:
        imported = import_incoming(episodes, publish_new=args.publish_new)
    save_json(EPISODES_PATH, episodes)
    PUBLIC.mkdir(exist_ok=True)
    (PUBLIC / "feed.xml").write_text(render_feed(config, episodes), encoding="utf-8")
    (PUBLIC / "index.html").write_text(render_index(config, episodes), encoding="utf-8")
    known_hashes = {episode.get("source_sha256") for episode in episodes if episode.get("source_sha256")}
    archived = archive_incoming(known_hashes) if args.archive_incoming and not args.render_only else []

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
