#!/bin/sh
set -eu

cd "$(dirname "$0")"

# Manual runs must use the same local lock as the watcher.  The watcher holds
# it across upload-folder sweeping and exports this flag so its child deploy
# can proceed without trying to lock itself a second time.
owns_lock=0
lockdir=".podcast-publish.lock"
if [ "${PODCAST_DEPLOY_LOCK_HELD:-}" != "1" ]; then
  if ! mkdir "$lockdir" 2>/dev/null; then
    echo "Podcast publish already running; refusing concurrent manual deploy." >&2
    exit 1
  fi
  owns_lock=1
  echo "$$" > "$lockdir/pid"
fi

tmpdir=""
cleanup() {
  if [ -n "$tmpdir" ]; then
    rm -rf "$tmpdir"
  fi
  if [ "$owns_lock" -eq 1 ]; then
    rm -f "$lockdir/pid"
    rmdir "$lockdir" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [ -n "${PODCAST_EXACT_SOURCE:-}" ]; then
  python3 publish.py --publish-new \
    --exact-source "$PODCAST_EXACT_SOURCE" \
    --exact-title "${PODCAST_EXACT_TITLE:-}" \
    --exact-description "${PODCAST_EXACT_DESCRIPTION:-}" \
    --exact-guid "${PODCAST_EXACT_GUID:-}" \
    --exact-notebook-id "${PODCAST_EXACT_NOTEBOOK_ID:-}" \
    --exact-notebook-title "${PODCAST_EXACT_NOTEBOOK_TITLE:-}" \
    --exact-artifact-type "${PODCAST_EXACT_ARTIFACT_TYPE:-}" \
    --exact-variant "${PODCAST_EXACT_VARIANT:-}"
else
  python3 quality_check.py --quarantine
  python3 publish.py --publish-new
fi

git add -A -- episodes.json public
if ! git diff --cached --quiet; then
  git commit -m "Update podcast feed"
fi
git push origin main

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/podcast-gh-pages.XXXXXX")"

git clone --filter=blob:none --no-checkout --depth 1 --branch gh-pages --single-branch "$(git config --get remote.origin.url)" "$tmpdir"
git -C "$tmpdir" read-tree --empty
cp -R public/. "$tmpdir"/
touch "$tmpdir/.nojekyll"

(
  cd "$tmpdir"
  git add -A
  if ! git diff --cached --quiet; then
    git commit -m "Publish podcast site"
  fi
  git push origin gh-pages
)

if [ -z "${PODCAST_EXACT_SOURCE:-}" ]; then
  python3 publish.py --archive-incoming
fi
python3 update_old_files_index.py

echo "Published site: https://vmaguireme-droid.github.io/vinces-notebooklm-feed/"
echo "Published RSS:  https://vmaguireme-droid.github.io/vinces-notebooklm-feed/feed.xml"
