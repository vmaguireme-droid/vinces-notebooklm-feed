# Podcast Automation

This folder turns audio files into a simple podcast website and RSS feed.

## Automated Workflow

Drop new audio files into `incoming`. The automation publishes them and then moves the source files into `old-files`.

Supported audio files:

- `.mp3`
- `.m4a`
- `.wav`
- `.aac`
- `.ogg`
- `.flac`

Run once manually:

```bash
./run-once-and-archive.sh
```

Run continuously:

```bash
./watch-and-deploy.sh
```

The watcher checks every 5 minutes. If it finds audio in `incoming`, it runs a quality check, publishes passing files, deploys GitHub Pages, and moves the original source files into `old-files`.

If a file appears broken, extremely quiet, mostly silent, too short, or otherwise suspicious, it is moved to `needs-review` instead of being published. Quality reports are written in `quality-reports`.

Important: the quality check can catch technical audio problems, but it cannot prove educational accuracy or fully judge every spoken word without a trusted transcript or speech-to-text system.

The `old-files` folder also contains `Listened Files.md`, an automatically refreshed list of archived audio files.

Install as a Mac login automation:

```bash
mkdir -p ~/Library/LaunchAgents
cp automation/com.vincemaguire.podcast-feed.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vincemaguire.podcast-feed.plist
launchctl enable gui/$(id -u)/com.vincemaguire.podcast-feed
```

Stop the Mac login automation:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.vincemaguire.podcast-feed.plist
```

## Manual Workflow

1. Put `.mp3`, `.m4a`, `.wav`, `.aac`, `.ogg`, or `.flac` files in `incoming`.
2. Run:

   ```bash
   python3 publish.py --publish-new
   ```

3. Open `episodes.json`.
4. For each new episode, edit `title` and `description`.
5. Change `"draft": true` to `"draft": false`.
6. Run `python3 publish.py` again.
7. Upload or deploy the `public` folder to your podcast hosting location.

Once GitHub Pages is configured, use the one-command deploy:

```bash
./deploy.sh
```

That regenerates the feed, commits source changes to `main`, publishes the generated site to `gh-pages`, and pushes both branches.

## First-Time Setup

Edit `config.json` before submitting the feed anywhere:

- `title`: Podcast name.
- `description`: Public show description.
- `author`: Public author name.
- `email` and `owner_email`: Podcast directory contact email. Apple requires a valid owner email in the RSS metadata.
- `site_url`: Final public URL where `index.html`, `feed.xml`, and `audio/` will live.
- `image_url`: Public URL for square podcast artwork.
- `category`: Apple/Spotify category, such as `Technology`, `Education`, or `News`.

For GitHub Pages, run this after the repository is created:

```bash
./set_github_pages_url.sh YOUR_GITHUB_USERNAME
```

The expected public URLs will be:

- Site: `https://YOUR_GITHUB_USERNAME.github.io/vinces-notebooklm-feed/`
- RSS: `https://YOUR_GITHUB_USERNAME.github.io/vinces-notebooklm-feed/feed.xml`

## Recommended Hosting

The most automated setup is:

- Host `public` as a static site.
- Keep audio files in `public/audio`.
- Submit the final `feed.xml` URL to Spotify for Creators and Apple Podcasts Connect.

Good hosting options:

- GitHub Pages: simple and free, best for small audio files and low traffic.
- Cloudflare Pages plus R2: better for larger audio libraries and bandwidth.
- A podcast host such as Spotify for Creators: easiest account setup, but future uploads are more manual.

## Notes

- New files are imported as drafts so nothing is published accidentally.
- The script copies audio from `incoming` into `public/audio`; it does not delete your source files.
- Podcast directories need public, direct audio URLs in the RSS `<enclosure>` tags.

## Commute Topic Automation

Create or edit this Desktop file:

`/Users/vincemaguire/Desktop/commute`

Add one topic per line in this format:

```csv
Topic,Duration
Active-active HA and precision AI architecture,12 minutes
Segment routing versus MPLS,20 minutes
```

The commute watcher checks every 10 minutes. For each new `topic,duration` pair, it creates a job folder under:

`commutes/jobs`

Each job contains:

- `gemini-deep-research-prompt.txt`
- `README.md`
- `job.json`

The automation opens Gemini and ElevenLabs Studio in Chrome when new jobs are created.

Current limitation: Gemini Deep Research and ElevenLabs Studio are browser products. This local watcher can create the job, open the right sites, and prepare the prompts, but the actual Gemini Deep Research run, Google Docs save, and ElevenLabs export may still require your logged-in browser session and occasional human confirmation.

For best results, keep Chrome logged in to:

- Gemini / Google
- ElevenLabs

You do not need to keep the pages open all the time, but leaving Chrome logged in makes the automation much smoother.
