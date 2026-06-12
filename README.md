# Podcast Automation

This folder turns audio files into a simple podcast website and RSS feed.

## Workflow

1. Put `.mp3`, `.m4a`, `.wav`, `.aac`, `.ogg`, or `.flac` files in `incoming`.
2. Run:

   ```bash
   python3 publish.py
   ```

3. Open `episodes.json`.
4. For each new episode, edit `title` and `description`.
5. Change `"draft": true` to `"draft": false`.
6. Run `python3 publish.py` again.
7. Upload or deploy the `public` folder to your podcast hosting location.

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
