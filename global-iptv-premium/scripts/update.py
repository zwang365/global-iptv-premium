#!/usr/bin/env python3
"""Refresh a fixed whitelist from public catalogues; never probe video streams."""
import argparse
import collections
import concurrent.futures
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, parse_qsl
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 20_000_000


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalized(value):
    # Exact aliases only; remove quality suffixes, never fuzzy-match another station.
    value = re.sub(r"(?:超高清|高清|标清|FHD|HD|4K)$", "", value.strip(), flags=re.I)
    return re.sub(r"[\s\-－_·]", "", value).casefold()


def parse_m3u(body):
    if not body.lstrip("\ufeff \r\n").startswith("#EXTM3U"):
        raise ValueError("上游返回内容不是 M3U")
    rows, pending = [], None
    for line in body.splitlines():
        line = line.strip().lstrip("\ufeff")
        if line.startswith("#EXTINF:"):
            # The separator is the first comma outside quoted attributes.
            quoted, separator = False, None
            for i, char in enumerate(line):
                if char == '"':
                    quoted = not quoted
                elif char == "," and not quoted:
                    separator = i
                    break
            if separator is None:
                raise ValueError("EXTINF 缺少频道名分隔符")
            attrs = dict(re.findall(r'([\w-]+)="([^"\r\n]*)"', line[:separator]))
            pending = {"title": line[separator + 1:].strip(), "attrs": attrs,
                       "labels": [], "referrer": None, "user_agent": None,
                       "feed": None, "quality": None}
        elif line.startswith("#EXTVLCOPT:") and pending:
            key, _, val = line[len("#EXTVLCOPT:"):].partition("=")
            if key == "http-referrer":
                pending["referrer"] = val
            elif key == "http-user-agent":
                pending["user_agent"] = val
        elif line and not line.startswith("#"):
            if pending is None:
                continue
            pending["url"] = line
            rows.append(pending)
            pending = None
    if pending is not None:
        raise ValueError("最后一条 EXTINF 没有 URL")
    if not rows:
        raise ValueError("上游 M3U 没有频道记录")
    return rows


def fetch_source(source, cache_dir=None):
    if cache_dir is not None:
        data = (cache_dir / source["file"]).read_bytes()
    else:
        error = None
        for attempt in range(3):
            try:
                request = Request(source["url"], headers={"User-Agent": "global-iptv-premium/1.0"})
                with urlopen(request, timeout=40) as response:
                    data = response.read(MAX_BYTES + 1)
                break
            except Exception as exc:
                error = exc
                if attempt == 2:
                    raise error
                time.sleep(attempt + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("上游目录超过大小上限")
    body = data.decode("utf-8-sig")
    parsed = parse_m3u(body) if source["kind"] == "m3u" else json.loads(body)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("上游目录为空或结构已改变")
    kind = source["kind"]
    for row in parsed:
        if not isinstance(row, dict):
            raise ValueError("目录记录类型错误")
        if kind == "channels" and not all(k in row for k in ["id", "name", "country"]):
            raise ValueError("频道字段缺失")
        if kind == "streams" and not all(k in row for k in ["channel", "url"]):
            raise ValueError("直播源字段缺失")
        if kind == "blocklist" and "channel" not in row:
            raise ValueError("屏蔽清单字段缺失")
    meta = {"name": source["name"], "url": source["url"], "sha256": hashlib.sha256(data).hexdigest(),
            "records": len(parsed), "status": "downloaded" if cache_dir is None else "cached_snapshot"}
    return source, parsed, meta


def url_rejection(row, feedback):
    url = row["url"]
    if url in feedback.get("blocked_urls", []):
        return "tv_feedback_blocked"
    if any(c in url for c in '\r\n\x00"|') or re.search(r"\[[^\]]+\]", url):
        return "unsafe_or_placeholder_url"
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        return "invalid_url"
    if parts.scheme in ["rtp", "udp", "rtsp"]:
        return "operator_network_only"
    if parts.scheme not in ["http", "https"] or not parts.hostname:
        return "unsupported_scheme"
    if parts.username or parts.password or re.search(r"/live/[^/]+/[^/]+/\d+", parts.path):
        return "credential_or_subscription_endpoint"
    if parts.hostname.casefold() in ["localhost", "localhost.localdomain"] or parts.hostname.endswith(".local"):
        return "private_network_url"
    try:
        if not ipaddress.ip_address(parts.hostname).is_global:
            return "private_network_url"
    except ValueError:
        pass
    if parts.path.lower().endswith((".mpd", ".mp4", ".mp3", ".aac")):
        return "unsupported_or_nonlive_format"
    if any(k.casefold() in {"token", "auth", "authorization", "password", "txsecret", "txtime",
                           "expires", "signature", "hdnea", "hdntl", "wmsauthsign", "key", "apikey"}
           for k, _ in parse_qsl(parts.query)) or "cdrm" in url.lower():
        return "signed_or_drm_url"
    if row.get("referrer") or row.get("user_agent"):
        return "custom_headers_required"
    if "cnn_slate" in url.lower():
        return "placeholder_stream"
    return None


def is_official_domain(url, website):
    if not website:
        return False
    candidate = urlsplit(url).hostname or ""
    reference = urlsplit(website).hostname or ""
    reference = reference.removeprefix("www.")
    return bool(reference and (candidate == reference or candidate.endswith("." + reference)))


def quality_score(value):
    match = re.search(r"\d+", str(value or ""))
    n = int(match[0]) if match else 0
    return n if n <= 1080 else 400


def rank(row, target, feedback):
    preferred_feeds = target.get("feed_preference", [])
    feed_rank = preferred_feeds.index(row.get("feed")) if row.get("feed") in preferred_feeds else len(preferred_feeds)
    return (row["url"] != feedback.get("preferred_urls", {}).get(target["id"]),
            not is_official_domain(row["url"], target.get("official_website")),
            (urlsplit(row["url"]).hostname or "") not in target.get("preferred_hosts", []),
            "Geo-blocked" in row.get("labels", []),
            "Not 24/7" in row.get("labels", []),
            feed_rank,
            not row["url"].startswith("https://"),
            not row["url"].split("?", 1)[0].endswith(".m3u8"),
            "/v1/manifest/" in row["url"] or "/t=" in row["url"],
            0 if row["source"].startswith("YueChan") else 1,
            -quality_score(row.get("quality")), row["url"])


REASONS = {
    "upstream_blocklisted": "上游权利/内容屏蔽清单：不收录",
    "subscription_only": "无确认的公开免费版本；付费/授权频道不收录第三方转播",
    "closed": "频道已停播或被替代",
    "disabled_by_feedback": "根据电视端反馈停用",
    "missing": "上游没有匹配的节目源",
    "no_eligible_source": "匹配到了源，但不适合普通公网订阅",
    "trial_unchecked": "已收录，未请求视频流、未做稳定性或播放验证",
    "operator_network_only": "运营商内网/组播，普通公网通常不能使用",
    "private_network_url": "仅私有网络地址",
    "credential_or_subscription_endpoint": "包含凭据或疑似订阅端点",
    "signed_or_drm_url": "签名/鉴权/DRM 地址不收录",
    "custom_headers_required": "需指定请求头；本试播版不收录",
    "unsupported_scheme": "不支持的协议",
    "unsupported_or_nonlive_format": "非本项目支持的直播格式",
    "unsafe_or_placeholder_url": "包含模板占位符或非法字符",
    "invalid_url": "URL 结构错误",
    "tv_feedback_blocked": "电视端反馈此源不可用",
    "geo_excluded": "配置要求排除上游标注的地区限制源",
    "placeholder_stream": "占位流，不是完整频道",
    "identity_or_encoded_signature": "频道身份存疑或包含编码签名的地址",
}


def build(whitelist, loaded, feedback, settings):
    catalogue, blocklist = {}, {}
    candidates = collections.defaultdict(list)
    targets = whitelist["channels"]
    aliases = {}
    for target in targets:
        for name in target["source_aliases"]:
            key = normalized(name)
            if key in aliases and aliases[key] != target["id"]:
                raise ValueError("白名单上游别名冲突: " + name)
            aliases[key] = target["id"]
    for source, data, _ in loaded:
        if source["kind"] == "channels":
            catalogue.update({r["id"]: r for r in data})
        elif source["kind"] == "blocklist":
            for r in data:
                blocklist.setdefault(r["channel"], []).append(r)
        elif source["kind"] == "streams":
            for row in data:
                candidates[row["channel"]].append({**row, "source": "iptv-org/iptv", "source_url": source["url"]})
        elif source["kind"] == "m3u":
            for row in data:
                identity = aliases.get(normalized(row["title"]))
                if identity:
                    candidates[identity].append({**row, "source": source["name"], "source_url": source["url"]})
    results = []
    for target in targets:
        record = {k: target[k] for k in ["id", "name", "group", "note", "official_website"]}
        all_rows = []
        ids = [target["id"], *target.get("alternate_ids", [])]
        for identity in ids:
            for row in candidates[identity]:
                if row["source"] == "iptv-org/iptv" and target.get("feeds") and row.get("feed") not in target["feeds"]:
                    continue
                all_rows.append(row)
        record["matched_sources"] = len(all_rows)
        record["rejections"] = []
        record["sources"] = []
        blocked = [r for identity in ids for r in blocklist.get(identity, [])]
        channel = catalogue.get(target["id"], {})
        if target["id"] in feedback.get("disabled_channels", []):
            status = "disabled_by_feedback"
        elif blocked:
            status = "upstream_blocklisted"
            record["blocklist"] = blocked
        elif channel.get("closed"):
            status = "closed"
            record["closed"] = channel["closed"]
            record["replaced_by"] = channel.get("replaced_by")
        elif target["subscription_only"]:
            status = "subscription_only"
        else:
            seen = set()
            for row in sorted(all_rows, key=lambda r: rank(r, target, feedback)):
                if row["url"] in seen:
                    continue
                seen.add(row["url"])
                rejection = url_rejection(row, feedback)
                if any(pattern in row["url"] for pattern in target.get("reject_url_patterns", [])):
                    rejection = rejection or "identity_or_encoded_signature"
                labels = list(row.get("labels") or [])
                # Explicit known-region notes must propagate across mirrors as well.
                for label in target.get("known_labels", []):
                    if label not in labels:
                        labels.append(label)
                if not settings["include_geo_blocked"] and "Geo-blocked" in labels:
                    rejection = rejection or "geo_excluded"
                if rejection:
                    # Never include potentially credential-bearing URL text in the public report.
                    record["rejections"].append({"reason": rejection, "source": row["source"],
                        "host": urlsplit(row["url"]).hostname if rejection != "invalid_url" else None})
                    continue
                record["sources"].append({"url": row["url"], "source": row["source"],
                    "source_url": row["source_url"], "feed": row.get("feed"), "quality": row.get("quality"),
                    "labels": labels, "playback_check": "not_tested",
                    "provenance": "broadcaster_domain" if is_official_domain(row["url"], target.get("official_website"))
                                  else "public_repository_rights_unverified"})
                if len(record["sources"]) >= settings["max_backups"]:
                    break
            status = "trial_unchecked" if record["sources"] else ("missing" if not all_rows else "no_eligible_source")
        record["status"] = status
        record["feedback_note"] = feedback.get("notes", {}).get(target["id"], "")
        results.append(record)
    return results


def attribute(value):
    return str(value or "").replace('"', "'").replace("\r", " ").replace("\n", " ")


def playlist(results, timestamp, backups=False):
    lines = ["#EXTM3U", "# Generated: " + timestamp, "# Playback: NOT TESTED; public catalogue URLs only"]
    for target in results:
        for i, source in enumerate(target["sources"] if backups else target["sources"][:1]):
            name = target["name"]
            if backups:
                name += f" [源{i + 1}]"
            if "Geo-blocked" in source["labels"]:
                name += "（地区限制）"
            lines.extend([f'#EXTINF:-1 tvg-id="{attribute(target["id"])}" tvg-name="{attribute(target["name"])}" '
                          f'group-title="{attribute(target["group"])}",{attribute(name)}', source["url"]])
    return "\n".join(lines) + "\n"


def md_escape(value):
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def markdown_report(report):
    lines = ["# 频道试播与缺失报告", "", "生成时间（北京时间）：" + report["generated_at_shanghai"], "",
             "所有收录源均**未实测**。本次只抓取目录，不访问视频流。公网地址、地区限制标注和频道身份可能有误，等待小米电视试播反馈。",
             "GitHub 上公开列出不等于版权授权已核实；第三方转播源标记为 `public_repository_rights_unverified`。", "",
             "| 分组 | 白名单目标 | 收录频道（未实测） |", "|---|---:|---:|"]
    for group, counts in report["groups"].items():
        lines.append(f'| {group} | {counts["target"]} | {counts["included"]} |')
    lines += ["", "每频道主订阅只选一个源；备选订阅最多保留三个源。缺失频道不会被写入空链接。", "",
              "| 频道 | ID | 状态 | 主源/原因 | 备注 |", "|---|---|---|---|---|"]
    for row in report["channels"]:
        details = REASONS[row["status"]]
        if row["sources"]:
            source = row["sources"][0]
            details = f'[原始 URL]({source["url"]}) / {source["source"]}'
            details += " / " + (", ".join(source["labels"]) or "未提供地区限制信息（不等于全球可用）")
        elif row["rejections"]:
            details += "：" + "; ".join(REASONS.get(x, x) for x in sorted({r["reason"] for r in row["rejections"]}))
        if row.get("blocklist"):
            details += " / " + ", ".join(f'[{r["reason"]}]({r["ref"]})' for r in row["blocklist"])
        lines.append("| " + " | ".join(md_escape(x) for x in [row["name"], row["id"], REASONS[row["status"]], details, row["note"]]) + " |")
    lines += ["", "## 来源抓取记录", ""]
    for source in report["sources"]:
        lines.append(f'- [{source["name"]}]({source["url"]})：{source["status"]}，{source.get("records", 0)} 条；SHA256 `{source.get("sha256", "无")}`')
    lines += ["", "完整备选、限制与排除原因见 [status.json](status.json)。", ""]
    return "\n".join(lines)


def validate_config(whitelist, feedback, settings):
    groups = whitelist["groups"]
    if len(groups) != 8 or len(set(groups)) != 8:
        raise ValueError("必须恰好有 8 个分组")
    ids = [r["id"] for r in whitelist["channels"]]
    if len(ids) != len(set(ids)):
        raise ValueError("白名单 ID 重复")
    if not isinstance(settings["include_geo_blocked"], bool) or not 1 <= settings["max_backups"] <= 5:
        raise ValueError("订阅设置错误")
    if not 0 < settings["minimum_previous_ratio"] <= 1:
        raise ValueError("异常缩水阈值错误")
    for target in whitelist["channels"]:
        if target["group"] not in groups or not isinstance(target["subscription_only"], bool):
            raise ValueError("白名单分组/权限设置错误")
        if not isinstance(target["source_aliases"], list):
            raise ValueError("上游别名必须是列表")
    unknown = (set(feedback.get("preferred_urls", {})) | set(feedback.get("disabled_channels", [])) | set(feedback.get("notes", {}))) - set(ids)
    if unknown:
        raise ValueError("反馈包含不在白名单中的 ID: " + ", ".join(sorted(unknown)))


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = handle.name
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, help="使用已下载目录快照；不访问网络")
    parser.add_argument("--allow-large-drop", action="store_true", help="人工确认上游大幅缩水后才使用")
    args = parser.parse_args()
    whitelist = load_json(ROOT / "data/whitelist.json")
    source_config = load_json(ROOT / "data/sources.json")
    feedback = load_json(ROOT / "data/feedback.json")
    settings = source_config["settings"]
    validate_config(whitelist, feedback, settings)
    loaded, metadata = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        pending = {pool.submit(fetch_source, source, args.cache_dir): source for source in source_config["sources"]}
        for future in concurrent.futures.as_completed(pending):
            source = pending[future]
            try:
                result = future.result()
                loaded.append(result)
                metadata.append(result[2])
            except Exception as exc:
                if source["required"]:
                    raise RuntimeError(source["name"] + " 抓取失败，保留上一版文件") from exc
                metadata.append({"name": source["name"], "url": source["url"], "status": "fetch_failed"})
                print(source["name"] + " 抓取失败，继续使用其他成功上游", file=sys.stderr)
    results = build(whitelist, loaded, feedback, settings)
    included = sum(bool(r["sources"]) for r in results)
    if not included:
        raise RuntimeError("没有可收录源，保留上一版订阅")
    previous_path = ROOT / "output/status.json"
    if previous_path.exists():
        previous = load_json(previous_path)
        disabled_now = set(feedback.get("disabled_channels", []))
        expected = sum(bool(r["sources"]) and r["id"] not in disabled_now for r in previous["channels"])
        if included < expected * settings["minimum_previous_ratio"] and not args.allow_large_drop:
            raise RuntimeError(f"频道异常缩水 {expected} -> {included}，保留上一版；人工检查后可用 --allow-large-drop")
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat(timespec="seconds")
    counts = {g: {"target": sum(r["group"] == g for r in results),
                  "included": sum(r["group"] == g and bool(r["sources"]) for r in results)} for g in whitelist["groups"]}
    report = {"generated_at": timestamp, "generated_at_shanghai": now.astimezone(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
              "stream_probes_performed": False, "channel_count": included, "target_count": len(results), "groups": counts,
              "sources": sorted(metadata, key=lambda x: x["name"]), "channels": results}
    artifacts = {ROOT / "premium.m3u": playlist(results, timestamp),
                 ROOT / "premium-backups.m3u": playlist(results, timestamp, backups=True),
                 ROOT / "output/status.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                 ROOT / "output/CHANNELS.md": markdown_report(report)}
    for path, content in artifacts.items():
        atomic_write(path, content)
    print(json.dumps({"targets": len(results), "included_untested": included, "groups": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
