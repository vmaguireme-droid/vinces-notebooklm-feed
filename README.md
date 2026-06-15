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

The watcher sends macOS notifications when podcast publishing succeeds or fails.

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

Create or edit this Desktop shortcut/file:

`/Users/vincemaguire/Desktop/commute`

The Desktop item points to the real automation-readable file:

`/Users/vincemaguire/My Drive/Podcast Automation/commutes/commute`

Add one topic per line in this format:

```csv
Topic,Duration
Active-active HA and precision AI architecture,12 minutes
Segment routing versus MPLS,20 minutes
```

The commute watcher checks every 10 minutes. For each new `topic,duration` pair, it creates a job folder under:

`commutes/jobs`

Each job contains:

- `gemini-flash-prompt.txt`
- `README.md`
- `job.json`

The automation opens Gemini and ElevenLabs Studio when new jobs are created. Use Gemini Flash for the Gemini step, not Deep Research.

The watcher also runs `submit_gemini_prompts.py`. That helper opens the Gemini Mac app, starts a new chat, pastes each pending `gemini-flash-prompt.txt`, presses Return, and marks the job as `submitted-to-gemini`.

After a Gemini script is captured, the watcher runs `create_commute_audio.py`. If ElevenLabs audio is not already available, this creates a local spoken-audio fallback with the Mac speech engine, stages it, and lets the normal podcast watcher publish it.

The watcher sends macOS notifications when it creates jobs, when it is waiting for Gemini output, when a script is ready for ElevenLabs, when audio is sent to the podcast drop folder, or when it hits an error.

When Gemini finishes a topic, save the checked script as a `.txt` or `.md` file here:

`/Users/vincemaguire/Desktop/Commute Gemini Scripts`

The watcher copies that text into the Google Drive `commutes` folder and marks the job as ready for ElevenLabs.

When ElevenLabs finishes the audio, save or move the `.mp3`, `.m4a`, `.wav`, `.aac`, `.ogg`, or `.flac` file here:

`/Users/vincemaguire/Desktop/Commute ElevenLabs Audio`

The watcher moves that audio into the podcast drop folder. The podcast watcher then publishes it on its next 5-minute check.

When a topic successfully has audio sent to the podcast drop folder, it is removed from `commute` and appended to:

`/Users/vincemaguire/My Drive/Podcast Automation/commutes/commute complete`

Current limitation: Gemini and ElevenLabs Studio are app/browser products. This local watcher can create the job, submit prompts to the Gemini Mac app, pick up completed script files, create fallback audio when needed, pick up completed audio files, and publish the audio. Copying Gemini's finished response into `Commute Gemini Scripts` may still require your logged-in session and occasional human confirmation.

For best results, keep Chrome logged in to:

- Gemini / Google
- ElevenLabs

You do not need to keep the pages open all the time, but leaving Chrome logged in makes the automation much smoother.
