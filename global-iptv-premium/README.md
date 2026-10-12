# 全球精选 IPTV：小米电视 XPlayer 试播订阅

按 8 类维护频道白名单，从 [iptv-org/iptv](https://github.com/iptv-org/iptv) 和用户指定的 [YueChan/Live](https://github.com/YueChan/Live) 抓取目录，自动生成 `premium.m3u`。

**当前是未实测的试播版。** 按用户最新要求，本项目不请求视频流、不验证稳定性，也不把“已收录”写成“可播放”。请先在小米电视上试播，再根据反馈选源和删源。公开仓库列出的第三方转播地址不代表版权授权已核实；来源信息逐条保存在报告中。

## 第一版范围

2026-10-12 的初始快照：**160 个目标频道，104 个收录频道**。下表是首次生成结果，后续实时数字以 [频道报告](output/CHANNELS.md) 为准。

| 分组 | 白名单目标 | 已收录（未实测） |
|---|---:|---:|
| 01央视（CCTV） | 44 | 28 |
| 02省级卫视 | 37 | 19 |
| 03港澳台主要频道 | 34 | 27 |
| 04国际新闻 | 13 | 11 |
| 05国际财经 | 7 | 5 |
| 06纪录片科学 | 10 | 5 |
| 07综合娱乐 | 8 | 4 |
| 08国际体育 | 7 | 5 |

央视目标包括 CCTV-1～17、CCTV-5+、CCTV-4 亚/欧/美洲版、4K/8K、专题数字频道、CCTV+ 和 CGTN 的其他语种/纪录频道。重复的 CCTV-11 别名不另算一台。省级卫视覆盖大陆 31 个省级行政区的主要卫视，另列深圳、厦门、兵团、康巴、大湾区、三沙。港澳台的各台候选及全部 45 个海外候选详见报告。

缺失目标仍保留在白名单，方便上游增加源后自动补齐。付费/授权台、停播台及上游权利屏蔽台保持目标记录，不生成占位 URL。NASA TV 已转向 [NASA+](https://www.nasa.gov/nasa-tv-on-satellite-galaxy-37/)。BBC Earth 只收录美国免费 FAST 版，与原付费频道编排不同；CNN US / CNN FAST 不替代 CNN International。

## 最短发布步骤：无需命令行

1. 登录 GitHub，在 [新建仓库页面](https://github.com/new) 创建 **Public** 仓库 `global-iptv-premium`，默认分支使用 `main`。
2. 解压交付 ZIP，进入 `global-iptv-premium` 文件夹，将**里面的文件与子目录**上传到仓库根目录。必须包含隐藏目录 `.github/workflows/`，不要只上传 ZIP，也不要把整个外层目录嵌进仓库。Mac Finder 用 `⌘⇧.` 显示隐藏文件；上传结果中 `.github`、`data`、`scripts`、`output`、`README.md`、`premium.m3u` 应在根目录同级。若网页上传遗漏 `.github`，用 GitHub **Add file → Create new file**，按包内路径分别创建两个 workflow 文件并粘贴原文。
3. 打开仓库根目录的 `premium.m3u`，点击 **Raw** 并复制地址，在 XPlayer 中添加为 M3U 订阅。Raw 链接使用跟随 `main` 分支的地址，不要用固定提交 SHA 的链接。打开 **Actions → Update IPTV playlist → Run workflow**，运行一次后确认结果。

Raw 地址会由你的实际账号和仓库产生。**本交付尚未在用户 GitHub 上发布，没有已验证的远程订阅 URL。** 无需开启 GitHub Pages。

## 使用发布助手

已安装 GitHub CLI 时，在解压后的仓库目录运行：

```bash
bash scripts/publish.sh
```

脚本会在必要时通过浏览器登录 GitHub，创建 public 仓库、上传文件并打印**实际** Raw 订阅链接。如果已有同名仓库或当前目录已有 Git 仓库，按提示手动发布；脚本不覆盖已有远程。未安装 CLI 时，可使用上面的网页步骤，或从 [GitHub CLI 官网](https://cli.github.com/) 安装。无需把 token 发到聊天里。

## 小米电视 XPlayer

在 [XPlayer](https://github.com/TNT-Likely/xplayer) 的直播源/订阅设置中添加发布后得到的 `premium.m3u` Raw 地址，刷新频道列表即可看到 8 类。具体按钮名称随版本可能不同。

- `premium.m3u`：每台一个主源，适合日常选台。
- `premium-backups.m3u`：每台最多三个源，标为 `[源1]`、`[源2]`、`[源3]`，供试播与换源；主源也在其中。
- `output/CHANNELS.md`：频道清单、缺失原因、地区限制、来源。
- `output/status.json`：完整源列表、抓取 SHA256、未实测状态与排除原因。

已知地区限制的频道名带 `（地区限制）`。未标限制只表示上游未注明，不能据此认定全球可用。GitHub 能打开不代表视频服务器在你家网络可达；Raw 订阅也可能因你家网络而无法访问。若 XPlayer 支持本地 M3U 导入，可先导入交付的文件试播，再改用发布后的订阅地址。

用户提供的 YueChan `IPTV.m3u` 当前主要是 `rtp://239...` 运营商组播，只适用于对应运营商 IPTV 网络，**未放入公网主订阅**。本项目未生成代理、中转或访问限制绕过地址。4K/8K、FLV、直接 TS 源的实际兼容性也等待电视实测。

## 自动更新

GitHub Actions 每天计划于**北京时间 07:17**（UTC 23:17）刷新，还支持手动运行，以及修改白名单/脚本后的运行。[GitHub 定时任务](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)可能延迟，并且只在默认分支执行；长期无仓库活动的 public 项目可能停用定时任务。

更新过程只下载频道目录、直播地址清单和权利屏蔽清单。它进行频道 ID/别名匹配、去重、分组和地址格式筛选，**不请求直播服务器、不检查视频分片、不测稳定性**。每次继续遵守你的固定白名单，不会恢复成全球数千频道。

优先采用电视台域名、人工指定的播出 CDN、较少限制的 HTTPS / HLS 地址；并优先从公网源中选择。GitHub 附加源使用手工明确的别名，避免把美国 ABC News 当作澳大利亚 ABC、把阿拉伯语源当英语源、或把地方综合台冒充卫视。

必要的 iptv-org API 或权利屏蔽清单抓取失败、目录为空、或数量异常缩水超过设定阈值时，工作流失败并保留上一版文件。YueChan 单个可选目录失败时会记录失败，继续使用其余成功上游；它不是流可用性的结论。使用最后成功版本时，请看报告的生成日期，不能把旧版本当作当天检查通过。手动确认上游确实减少后，可在 Run workflow 中勾选 `allow_large_drop`。

若 Actions 报写权限错误，在 **Settings → Actions → General → Workflow permissions** 启用 **Read and write permissions**。如果组织策略或受保护分支禁止机器人提交，需要调整仓库策略或手工提交生成文件。上传包包含 workflow，但当前未在 GitHub 环境中实际运行它。

## 电视试播后的迭代

反馈可直接用“频道名 — 能否播放 — 源编号 — 是否卡顿/是否错台”的形式。把需要长期保留的处理写入 `data/feedback.json`：

```json
{
  "preferred_urls": {"NHKWorldJapan.jp": "从 status.json 中复制你已试播成功的完整 URL"},
  "blocked_urls": ["从 status.json 中复制你已试播失败的完整 URL"],
  "disabled_channels": [],
  "notes": {"NHKWorldJapan.jp": "小米电视上源2正常"}
}
```

上面是填写示例，不是实际直播地址。优选 URL 必须仍出现在上游目录中；手工填写不会凭空造源。封禁完整 URL 不会妨碍未来出现的新 URL。停用频道按 ID 处理；修改后自动更新会继续保留反馈。

`data/whitelist.json` 是频道白名单。`subscription_only` 表示未找到确认的公开免费版本，不是对电视台全部传播方式的法律断言；例如 BBC One、France 2、NHK 综合的广播与官方应用仍可能有各自合法收看方式。确认某个付费台有官方免费 FAST 版后，应单独核实频道身份和公开分发依据再调整配置。Sky News 虽有官方直播，但本项目遵守 iptv-org 的权利屏蔽清单，不从另一个仓库自动重新收录。

`local.TVBPlus` 是明确的本地标识，因为此次 iptv-org 元数据没有该 ID；不把它冒充数据库 ID。它通过 YueChan 的准确频道名匹配。报告中的 `broadcaster_domain` 仅说明 URL 域名与官方站相符，也不代表完成了节目版权审查。

## 本地运行与检查

Python 3.9+，只用标准库，无需安装额外依赖：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/update.py
```

初始版本通过 11 项本地逻辑测试，覆盖分组、语种、权利屏蔽、付费台排除、内网与凭据地址排除、目录失败保留旧文件、电视反馈选源；**这些是生成器测试，不是频道播放测试**。

## 来源与权利

- [iptv-org API](https://github.com/iptv-org/api)：频道 ID、直播 URL、限制标注、停播元数据和 blocklist。
- [iptv-org/iptv](https://github.com/iptv-org/iptv)：公开直播地址集；其公开声明与移除规则由上游维护，本项目同步 blocklist。
- [YueChan/Live](https://github.com/YueChan/Live)：用户指定的参考目录，抓取当前 `GNTV.m3u` 与 `IPTV.m3u`。本次实时文件列表中没有 `Global.m3u`，不写入不存在的抓取地址。`XPTV.json` 是应用站点配置，不当作电视直播目录。

不收录账号/密码、疑似 Xtream 订阅端点、明显 token/签名/DRM URL、模板占位符、私网地址和权利屏蔽频道。数字付费台没有确认公开免费源时保留为缺失目标。公开仓库对地址的记载不构成授权证明；第三方源明确标记为授权未核实，仅供个人试播评估，权利人提出问题时应删除相应地址/频道。

生成器代码采用 MIT License；频道品牌、节目、上游目录内容和视频权利不因该许可证而授予。此仓库仅存储文本链接，不存储或转播视频。
