# 全球精选 IPTV · 大陆直连验证版

更新时间：2026-10-12T16:05:04+08:00。**178个目标，63个海外目标；本轮18个频道通过大陆直连短时检测，其中14个海外频道。小米电视逐台确认数为0。** 旧版CCTV2/3的用户反馈不能替代本版地址的验证。

当前收录：CGTN Arabic、CGTN Russian、CGTN Documentary、香港卫视、ABC News、BBC News、CGTN English、CNN-News18、DW English、Euronews English、France 24 English、NHK World Japan、TRT World、Bloomberg TV Asia、Bloomberg TV Europe、Schwab Network、Yahoo Finance、Red Bull TV。

完整178项状态与缺失原因见 [CHANNELS.md](output/CHANNELS.md)，逐源证据见 [status.json](output/status.json)。[DangJin补充源抽测](output/DANGJIN.md)。这版没有解决CCTV1/CCTV5：目标已保留，公开候选失败或无法确认普通公网分发资格，最终M3U不添加占位节目。省级卫视、纪录片、娱乐本轮暂无合格项，分组仍保存在白名单中。

## 订阅

发布到原仓库后继续使用原地址，无需修改XPlayer：

https://raw.githubusercontent.com/zwang365/global-iptv-premium/refs/heads/main/global-iptv-premium/premium.m3u

主订阅：`premium.m3u`，每频道一个通过检查的源；`premium-backups.m3u`最多两个；`overseas.m3u`仅04–08。使用`group-title`、`tvg-id`及可获得的`tvg-logo`。**本地生成文件不代表远端已经发布**，请以GitHub文件内容与最新提交为准。

## 检测与范围

从iptv-org API、电视台官方网站，以及YueChan、DangJin等公开目录发现候选。目录存在不等于授权或可播放：逐频道审核发布域名，遵守上游移除清单，再测试HLS清单、最新视频分片下载、实际视频解码和直播序列推进。1080P优先，720P其次；4K暂不纳入本版兼容性范围。普通公开AES-128支持；DRM、登录、特殊令牌、无法确认授权的转播和错台占位不收录。

测试通过专门的直连请求绕过系统代理，仅影响测试程序，不改变电脑设置；Cloudflare出口国家代码为CN，IP地址不保存。不能据此保证所有省份、运营商、IPv6环境或XPlayer设备都能播放，也不保证长期稳定。HTTP403/超时仅记访问失败；有独立依据时才标为地区限制或付费。

海外名单严格使用用户指定63项：澳洲ABC、FOX按Fox News、PBS删除。BBC World Service为广播，保留目标说明但不冒充电视流；CNBC International不以其他版本顶替。目标数和实际收录数始终分别统计。

## 定期更新

仓库根目录`.github/workflows/update.yml`每天北京时间07:17抓取来源（GitHub调度可能延迟），也可手动运行。默认GitHub托管设备只生成候选审核文件，**不会把境外检测写成大陆已验证，也不会自动刷新主M3U**。

要自动更新主M3U，在大陆常开设备安装GitHub self-hosted runner，增加标签`iptv-cn`，仓库变量`IPTV_CN_RUNNER_ENABLED`设为`true`。设备需要Python3.9+、直接大陆联网，工作流安装imageio-ffmpeg解码器。启用后每天重新验证并提交M3U与报告。不要仅设置变量而未配置设备，否则验证任务会等待设备上线。不要在公共仓库运行不可信PR的自托管任务；本工作流只由定时与手动入口触发验证。

官方设置说明：https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners

也可在大陆电脑进入程序目录执行：

```sh
python3 -m pip install --user imageio-ffmpeg==0.6.0
python3 scripts/update.py
```

非大陆出口、缺少解码器、必要目录读取失败或所有源失败时保留上次订阅，工作流会报错。报告内每条`checked_at_unix`是实际检测时间；报告生成时间不是所有源重新检测的时间。本次初版复用了本轮6小时内的直连测量缓存，日常工作流不使用缓存。

## 仓库布局与发布

完整更新包的根目录含`.github/workflows/`与`global-iptv-premium/`，后者为本程序目录。**必须在仓库根目录上传隐藏的`.github`目录**；只上传程序子目录不会启用Actions。

最短发布方式：用GitHub Desktop克隆`zwang365/global-iptv-premium`；将更新包内容覆盖到克隆目录（包括`.github`）；提交并Push。原订阅路径保持一致。命令行方式参见更新包根目录的`发布说明.md`。

代码测试：`python3 -m unittest discover -s tests -v`。频道反馈写入`data/feedback.json`：停用频道、屏蔽URL或指定已审核候选优先；反馈不能绕过授权与播放检查。节目源归各权利人，本项目只维护链接与检测记录，不转播或托管电视内容。
