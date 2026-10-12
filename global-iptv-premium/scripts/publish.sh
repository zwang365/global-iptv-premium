#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v gh >/dev/null 2>&1; then
  echo '请先安装 GitHub CLI：brew install gh（已安装 Homebrew 时），或 https://cli.github.com/'
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  gh auth login --hostname github.com --git-protocol https --web
fi
if [ -d .git ]; then
  echo '此目录已经是 Git 仓库；请按 README 手动发布，脚本不会覆盖现有远程。'
  exit 1
fi
git init -b main
git add .
git -c user.name='IPTV project owner' -c user.email='iptv-local@users.noreply.github.com' commit -m 'Create global IPTV trial subscription'
gh repo create global-iptv-premium --public --source=. --remote=origin --push
owner_repo="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
echo "订阅地址：https://raw.githubusercontent.com/${owner_repo}/main/premium.m3u"
echo "备选地址：https://raw.githubusercontent.com/${owner_repo}/main/premium-backups.m3u"
echo "自动更新：https://github.com/${owner_repo}/actions"
