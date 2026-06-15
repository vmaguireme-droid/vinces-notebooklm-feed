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
        episode_id = html.escape(episode["guid"])
        title = html.escape(episode["title"])
        description = html.escape(episode.get("description") or "")
        duration = html.escape(episode.get("duration") or "Audio")
        rows.append(f"""      <article class="episode" data-episode-id="{episode_id}">
        <div class="episode-copy">
          <p class="eyebrow">{duration}</p>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <div class="player">
          <audio preload="metadata" src="{audio_url}"></audio>
          <div class="controls">
            <button class="control play" type="button" aria-label="Play {title}">Play</button>
            <button class="control stop" type="button" aria-label="Stop {title}">Stop</button>
          </div>
          <div class="progress-shell" aria-hidden="true">
            <div class="progress-bar"></div>
          </div>
          <label class="remove-option">
            <input type="checkbox">
            <span>Remove from this list after I listen</span>
          </label>
          <label class="playlist-option">
            <input class="playlist-check" type="checkbox">
            <span>Add to playlist</span>
          </label>
        </div>
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
    :root {{
      color-scheme: dark;
      --bg: #071012;
      --panel: rgba(15, 31, 34, 0.86);
      --panel-strong: rgba(20, 45, 49, 0.96);
      --text: #ecf8f7;
      --muted: #a7bfbd;
      --line: rgba(132, 255, 239, 0.2);
      --cyan: #75f7e6;
      --amber: #ffbd61;
      --red: #ff7d73;
      --shadow: rgba(0, 0, 0, 0.36);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 18% 10%, rgba(117, 247, 230, 0.16), transparent 28rem),
        radial-gradient(circle at 88% 4%, rgba(255, 189, 97, 0.12), transparent 24rem),
        linear-gradient(135deg, #071012 0%, #0d1d21 55%, #132529 100%);
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: 0.28;
      background-image:
        linear-gradient(rgba(117, 247, 230, 0.08) 1px, transparent 1px),
        linear-gradient(90deg, rgba(117, 247, 230, 0.08) 1px, transparent 1px);
      background-size: 44px 44px;
      mask-image: linear-gradient(to bottom, black, transparent 82%);
    }}
    main {{
      position: relative;
      max-width: 1080px;
      margin: 0 auto;
      padding: 32px 20px 72px;
    }}
    header {{
      display: grid;
      grid-template-columns: 180px 1fr;
      gap: 32px;
      align-items: center;
      min-height: 260px;
      margin: 0 0 30px;
      padding: 34px;
      border: 1px solid var(--line);
      border-radius: 28px;
      background:
        linear-gradient(135deg, rgba(117, 247, 230, 0.18), rgba(255, 189, 97, 0.12)),
        linear-gradient(135deg, rgba(20, 45, 49, 0.96), rgba(7, 16, 18, 0.92));
      box-shadow: 0 26px 70px var(--shadow);
      overflow: hidden;
    }}
    .artwork {{
      width: 180px;
      height: 180px;
      border-radius: 28px;
      box-shadow: 0 18px 48px var(--shadow);
      border: 1px solid var(--line);
      object-fit: cover;
    }}
    h1 {{
      font-size: clamp(34px, 6vw, 58px);
      line-height: 0.95;
      margin: 0 0 12px;
      letter-spacing: 0;
    }}
    .subtitle {{
      max-width: 700px;
      margin: 0;
      color: var(--muted);
      font-size: 18px;
      line-height: 1.55;
    }}
    .top-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin: 24px 0 34px;
      align-items: center;
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
      background: rgba(117, 247, 230, 0.08);
      box-shadow: 0 14px 32px rgba(0, 0, 0, 0.22);
    }}
    .playlist-status {{
      color: var(--muted);
      line-height: 1.4;
    }}
    a, .secondary-button {{
      color: var(--cyan);
    }}
    .secondary-button {{
      appearance: none;
      border: 1px solid var(--line);
      background: rgba(117, 247, 230, 0.08);
      border-radius: 999px;
      padding: 10px 16px;
      font: inherit;
      cursor: pointer;
    }}
    .refresh-button {{
      color: #061011;
      background: linear-gradient(135deg, var(--cyan), #a9fff4);
      border-color: transparent;
      font-weight: 800;
    }}
    .playlist-button {{
      min-height: 48px;
      border: 0;
      border-radius: 999px;
      padding: 0 18px;
      color: #061011;
      background: linear-gradient(135deg, var(--cyan), #a9fff4);
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }}
    .playlist-button.secondary {{
      color: var(--cyan);
      background: rgba(117, 247, 230, 0.08);
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
      grid-template-columns: 1fr 1fr;
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
      background: linear-gradient(135deg, var(--cyan), #a9fff4);
    }}
    .stop {{
      background: linear-gradient(135deg, var(--amber), var(--red));
    }}
    .progress-shell {{
      height: 12px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(236, 248, 247, 0.14);
      border: 1px solid var(--line);
    }}
    .progress-bar {{
      width: 0%;
      height: 100%;
      background: linear-gradient(90deg, var(--cyan), var(--amber));
    }}
    .remove-option,
    .playlist-option {{
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      line-height: 1.35;
      user-select: none;
    }}
    .remove-option input,
    .playlist-option input {{
      width: 24px;
      height: 24px;
      accent-color: var(--cyan);
      flex: 0 0 auto;
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
    @media (max-width: 760px) {{
      header {{
        grid-template-columns: 1fr;
        min-height: auto;
        padding: 24px;
      }}
      .episode {{
        grid-template-columns: 1fr;
      }}
      .playlist-panel {{
        grid-template-columns: 1fr;
      }}
      .artwork {{
        width: 132px;
        height: 132px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <img class="artwork" src="artwork.png" alt="">
      <div>
        <h1>{html.escape(config["title"])}</h1>
        <p class="subtitle">{html.escape(config["description"])}</p>
      </div>
    </header>
    <div class="top-actions">
      <a href="feed.xml">Podcast RSS feed</a>
      <button class="secondary-button refresh-button" type="button" id="refresh-page">Refresh page</button>
      <button class="secondary-button" type="button" id="restore-listened">Show hidden episodes</button>
    </div>
    <section class="playlist-panel" aria-label="Playlist controls">
      <div class="playlist-status" id="playlist-status">No playlist episodes selected.</div>
      <button class="playlist-button" type="button" id="play-playlist">Play playlist</button>
      <button class="playlist-button secondary" type="button" id="select-visible">Select visible</button>
      <button class="playlist-button secondary" type="button" id="clear-playlist">Clear playlist</button>
    </section>
    <section class="episodes" id="episodes">
{chr(10).join(rows)}
    </section>
    <p class="empty-state" id="empty-state">Everything in this browser has been marked listened. Use "Show hidden episodes" to bring them back.</p>
  </main>
  <script>
    const hiddenKey = "vinces-notebooklm-feed-hidden";
    const playlistKey = "vinces-notebooklm-feed-playlist";
    const hidden = new Set(JSON.parse(localStorage.getItem(hiddenKey) || "[]"));
    const playlist = new Set(JSON.parse(localStorage.getItem(playlistKey) || "[]"));
    const episodes = Array.from(document.querySelectorAll(".episode"));
    const emptyState = document.getElementById("empty-state");
    const playlistStatus = document.getElementById("playlist-status");
    let playlistQueue = [];
    let playlistIndex = -1;

    function saveHidden() {{
      localStorage.setItem(hiddenKey, JSON.stringify(Array.from(hidden)));
    }}

    function savePlaylist() {{
      localStorage.setItem(playlistKey, JSON.stringify(Array.from(playlist)));
    }}

    function updateEmptyState() {{
      const visible = episodes.some((episode) => episode.style.display !== "none");
      emptyState.style.display = visible ? "none" : "block";
    }}

    function selectedVisibleEpisodes() {{
      return episodes.filter((episode) => playlist.has(episode.dataset.episodeId) && episode.style.display !== "none");
    }}

    function updatePlaylistStatus() {{
      const count = selectedVisibleEpisodes().length;
      playlistStatus.textContent = count
        ? `${{count}} episode${{count === 1 ? "" : "s"}} in playlist.`
        : "No playlist episodes selected.";
    }}

    function stopAllAudio() {{
      document.querySelectorAll("audio").forEach((audio) => {{
        audio.pause();
      }});
    }}

    function playEpisode(episode) {{
      const audio = episode.querySelector("audio");
      stopAllAudio();
      audio.play();
    }}

    function playNextInPlaylist() {{
      playlistIndex += 1;
      if (playlistIndex >= playlistQueue.length) {{
        playlistIndex = -1;
        playlistQueue = [];
        updatePlaylistStatus();
        return;
      }}
      playEpisode(playlistQueue[playlistIndex]);
    }}

    function hideEpisode(episode) {{
      const id = episode.dataset.episodeId;
      hidden.add(id);
      saveHidden();
      episode.classList.add("removing");
      setTimeout(() => {{
        episode.style.display = "none";
        updateEmptyState();
      }}, 230);
    }}

    episodes.forEach((episode) => {{
      const id = episode.dataset.episodeId;
      const audio = episode.querySelector("audio");
      const play = episode.querySelector(".play");
      const stop = episode.querySelector(".stop");
      const removeAfterListen = episode.querySelector(".remove-option input");
      const playlistCheck = episode.querySelector(".playlist-check");
      const progress = episode.querySelector(".progress-bar");

      if (hidden.has(id)) {{
        episode.style.display = "none";
      }}

      if (playlist.has(id)) {{
        playlistCheck.checked = true;
      }}

      playlistCheck.addEventListener("change", () => {{
        if (playlistCheck.checked) {{
          playlist.add(id);
        }} else {{
          playlist.delete(id);
        }}
        savePlaylist();
        updatePlaylistStatus();
      }});

      play.addEventListener("click", () => {{
        playlistIndex = -1;
        playlistQueue = [];
        playEpisode(episode);
      }});

      stop.addEventListener("click", () => {{
        playlistIndex = -1;
        playlistQueue = [];
        audio.pause();
        audio.currentTime = 0;
      }});

      audio.addEventListener("timeupdate", () => {{
        const percent = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
        progress.style.width = `${{percent}}%`;
      }});

      audio.addEventListener("ended", () => {{
        progress.style.width = "100%";
        if (removeAfterListen.checked) {{
          hideEpisode(episode);
        }}
        if (playlistQueue.length) {{
          playNextInPlaylist();
        }}
      }});
    }});

    document.getElementById("restore-listened").addEventListener("click", () => {{
      hidden.clear();
      saveHidden();
      episodes.forEach((episode) => {{
        episode.classList.remove("removing");
        episode.style.display = "";
      }});
      updateEmptyState();
      updatePlaylistStatus();
    }});

    document.getElementById("refresh-page").addEventListener("click", () => {{
      window.location.reload();
    }});

    document.getElementById("play-playlist").addEventListener("click", () => {{
      playlistQueue = selectedVisibleEpisodes();
      playlistIndex = -1;
      if (playlistQueue.length) {{
        playNextInPlaylist();
      }}
    }});

    document.getElementById("select-visible").addEventListener("click", () => {{
      episodes.forEach((episode) => {{
        if (episode.style.display === "none") return;
        playlist.add(episode.dataset.episodeId);
        episode.querySelector(".playlist-check").checked = true;
      }});
      savePlaylist();
      updatePlaylistStatus();
    }});

    document.getElementById("clear-playlist").addEventListener("click", () => {{
      playlist.clear();
      playlistQueue = [];
      playlistIndex = -1;
      episodes.forEach((episode) => {{
        episode.querySelector(".playlist-check").checked = false;
      }});
      savePlaylist();
      updatePlaylistStatus();
    }});

    updateEmptyState();
    updatePlaylistStatus();
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
