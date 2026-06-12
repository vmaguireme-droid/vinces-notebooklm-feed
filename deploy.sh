#!/bin/sh
set -eu

python3 publish.py --publish-new

git add README.md automation config.json deploy.sh episodes.json publish.py public run-once-and-archive.sh watch-and-deploy.sh
if ! git diff --cached --quiet; then
  git commit -m "Update podcast feed"
fi
git push origin main

tmpdir="$(mktemp -d)"
cleanup() {
  git worktree remove "$tmpdir" --force >/dev/null 2>&1 || true
}
trap cleanup EXIT

git worktree add "$tmpdir" gh-pages
find "$tmpdir" -mindepth 1 ! -name .git -exec rm -rf {} +
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

python3 publish.py --archive-incoming

echo "Published site: https://vmaguireme-droid.github.io/vinces-notebooklm-feed/"
echo "Published RSS:  https://vmaguireme-droid.github.io/vinces-notebooklm-feed/feed.xml"
