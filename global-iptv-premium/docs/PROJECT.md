# 维护与交付

长期源项目保存在 `/Users/maxwang/Documents/Codex/projects/global-iptv-premium`。
本次对话的用户交付位于其 `outputs/` 下，包含仓库副本、ZIP、独立 M3U 和频道报告。

项目结构：

- `data/`：白名单、抓取地址、用户反馈。
- `scripts/`：目录更新器、发布助手。
- `tests/`：不访问视频流的生成器测试。
- `.github/workflows/`：定时更新、PR 逻辑测试。
- `output/`：生成后的频道报告。
- 根目录 `premium.m3u`、`premium-backups.m3u`：订阅文件。
- `src/`、`assets/`、`archive/`：后续扩展的预留位置。

不要把上游临时下载快照、GitHub 凭据或播放器账号提交到仓库。README 的初始计数是 dated snapshot；动态统计始终查看 `output/status.json` 和 `output/CHANNELS.md`。

用户 2026-10-12 的最新要求：先沿用 GitHub 公开目录中的源，不验证稳定性，等小米电视试播后迭代。每日更新不会默默恢复视频探测或删除尚未实测的频道；静态排除和权利屏蔽仍然生效。
