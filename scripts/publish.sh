#!/bin/sh
# Run from the root of the unpacked complete repository, after installing gh.
set -eu
cd "$(dirname "$0")/../.."
if [ ! -d .git ]; then
  echo '请先克隆现有仓库，再将完整更新包覆盖到仓库根目录。' >&2
  exit 1
fi
remote=$(git remote get-url origin)
case "$remote" in
  https://github.com/zwang365/global-iptv-premium.git|git@github.com:zwang365/global-iptv-premium.git) ;;
  *) echo '当前仓库不是已确认的 zwang365/global-iptv-premium，停止。' >&2; exit 1 ;;
esac
python3 -m unittest discover -s global-iptv-premium/tests -v
git add .github/workflows global-iptv-premium README.md
git diff --cached --stat
if ! git diff --cached --quiet; then
  git commit -m 'Publish reviewed mainland IPTV playlist and exact channel report'
  git push origin HEAD:main
fi
