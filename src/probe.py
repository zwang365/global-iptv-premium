"""Bounded public HLS checks: live movement, media downloads and actual decoding."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError


class ProbeError(Exception):
    pass


def public_url(url):
    try:
        p = urlsplit(url)
        _ = p.port
    except ValueError:
        raise ProbeError("invalid_url")
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ProbeError("invalid_public_url")
    if p.hostname == "localhost" or p.hostname.endswith(".local"):
        raise ProbeError("private_address")
    try:
        addresses = [ipaddress.ip_address(p.hostname)]
    except ValueError:
        addresses = [ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(p.hostname, p.port or 443, type=socket.SOCK_STREAM)]
    if not addresses or any(not ip.is_global for ip in addresses):
        raise ProbeError("private_address")
    return url


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get(url, proxy_mode="direct", limit=2_000_000, byte_range=None):
    public_url(url)
    handlers = [PublicRedirect()]
    if proxy_mode == "direct":
        handlers.append(ProxyHandler({}))
    opener = build_opener(*handlers)
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    if byte_range:
        headers["Range"] = byte_range
    last = None
    for attempt in range(2):
        try:
            start = time.monotonic()
            with opener.open(Request(url, headers=headers), timeout=10) as response:
                chunks, size = [], 0
                while size <= limit:
                    if time.monotonic() - start > 35:
                        raise ProbeError("download_time_limit")
                    chunk = response.read1(min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                data = b"".join(chunks)
                if len(data) > limit:
                    raise ProbeError("body_too_large")
                final_url = public_url(response.url)
                return data, final_url, round(time.monotonic() - start, 3)
        except HTTPError as exc:
            # 403 is an access failure, not proof of a specific geographical rule.
            raise ProbeError("http_" + str(exc.code))
        except ProbeError:
            raise
        except Exception as exc:
            last = exc
            if attempt == 0:
                time.sleep(.5)
    raise ProbeError(type(last).__name__)


def attributes(line):
    return {k: v.strip('"') for k, v in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line)}


def parse(body, base):
    if not body.lstrip("\ufeff \r\n").startswith("#EXTM3U"):
        raise ProbeError("not_hls_manifest")
    variants, segments, init = [], [], None
    pending_variant, duration, byte_range, offset = None, None, None, 0
    seq, target, endlist, key = None, 6, False, None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
            fields = attributes(line.split(":", 1)[1])
            method = fields.get("METHOD", "NONE")
            if method not in ("NONE", "AES-128") or fields.get("KEYFORMAT", "identity") != "identity":
                raise ProbeError("encrypted_or_drm_hls")
            key = {"url": urljoin(base, fields["URI"]), "iv": fields.get("IV")} if method == "AES-128" else None
        elif line.startswith("#EXT-X-STREAM-INF:"):
            pending_variant = attributes(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-MAP:"):
            fields = attributes(line.split(":", 1)[1])
            if fields.get("BYTERANGE"):
                raise ProbeError("init_byterange_unsupported")
            init = urljoin(base, fields["URI"])
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            target = float(line.split(":", 1)[1])
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",")[0])
        elif line.startswith("#EXT-X-BYTERANGE:"):
            value = line.split(":", 1)[1]
            length, _, explicit_offset = value.partition("@")
            start = int(explicit_offset) if explicit_offset else offset
            offset = start + int(length)
            byte_range = f"bytes={start}-{offset - 1}"
        elif line == "#EXT-X-ENDLIST":
            endlist = True
        elif line and not line.startswith("#"):
            url = urljoin(base, line)
            if pending_variant is not None:
                match = re.fullmatch(r"(\d+)x(\d+)", pending_variant.get("RESOLUTION", ""))
                variants.append({"url": url, "height": int(match[2]) if match else 0,
                                 "codecs": pending_variant.get("CODECS", ""),
                                 "bandwidth": int(pending_variant.get("AVERAGE-BANDWIDTH", pending_variant.get("BANDWIDTH", 0)))})
                pending_variant = None
            elif duration is not None:
                segment_key = dict(key) if key else None
                if segment_key is not None and not segment_key["iv"]:
                    segment_key["iv"] = "0x" + format((seq or 0) + len(segments), "032x")
                segments.append({"url": url, "duration": duration, "range": byte_range, "key": segment_key})
                duration, byte_range = None, None
    return {"variants": variants, "segments": segments, "init": init, "sequence": seq,
            "target_duration": target, "endlist": endlist}


def video_rank(v):
    # Prefer AVC at 1080p, then AVC at 720p. Avoid 4K bandwidth and codec surprises.
    h = v["height"]
    return (h > 1080, h < 720 if h else False, "avc1" not in v["codecs"], -min(h, 1080), -v["bandwidth"])


def media_manifest(url, proxy_mode, depth=0):
    if depth > 4:
        raise ProbeError("nested_manifest_limit")
    data, final_url, latency = get(url, proxy_mode)
    content = parse(data.decode("utf-8-sig"), final_url)
    if content["variants"]:
        failures = []
        for variant in sorted(content["variants"], key=video_rank)[:3]:
            if variant["height"] and not 720 <= variant["height"] <= 1080:
                continue
            try:
                leaf, parsed, latency, selected = media_manifest(variant["url"], proxy_mode, depth + 1)
                return leaf, parsed, latency, selected or variant
            except ProbeError as exc:
                failures.append(str(exc))
        raise ProbeError("no_usable_hd_variant:" + ",".join(failures))
    if content["endlist"]:
        raise ProbeError("vod_not_live")
    if len(content["segments"]) < 2:
        raise ProbeError("insufficient_live_segments")
    return final_url, content, latency, None


def decoder_path():
    binary = os.environ.get("IPTV_FFMPEG") or shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise ProbeError("decoder_not_installed")


def decode(data, frame_path=None, media_type="video", bundle=None):
    decoder = decoder_path()
    with tempfile.TemporaryDirectory(prefix="iptv-check-") as directory:
        folder = Path(directory)
        sample = folder / "sample.bin"
        sample.write_bytes(data)
        if bundle is not None:
            sample = folder / "sample.m3u8"
            lines = ["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-TARGETDURATION:30", "#EXT-X-MEDIA-SEQUENCE:0"]
            if bundle.get("init"):
                (folder / "init.mp4").write_bytes(bundle["init"])
                lines.append('#EXT-X-MAP:URI="init.mp4"')
            for i, part in enumerate(bundle["parts"]):
                if part.get("key_data"):
                    (folder / f"key{i}.bin").write_bytes(part["key_data"])
                    lines.append(f'#EXT-X-KEY:METHOD=AES-128,URI="key{i}.bin",IV={part["iv"]}')
                else:
                    lines.append("#EXT-X-KEY:METHOD=NONE")
                (folder / f"segment{i}.ts").write_bytes(part["data"])
                lines += ["#EXTINF:" + str(part["duration"]) + ",", f"segment{i}.ts"]
            lines.append("#EXT-X-ENDLIST")
            sample.write_text("\n".join(lines) + "\n")
        cmd = [decoder, "-nostdin", "-hide_banner", "-protocol_whitelist", "file,data,crypto",
               *(["-allowed_extensions", "ALL"] if bundle is not None else []), "-i", str(sample)]
        if media_type == "audio":
            cmd += ["-map", "0:a:0", "-t", "2", "-vn", "-f", "null", "-"]
        else:
            cmd += ["-map", "0:v:0", "-frames:v", "3", "-an", "-f", "null", "-"]
        result = subprocess.run(cmd, capture_output=True, timeout=25)
        log = result.stderr.decode("utf-8", "replace")
        if result.returncode or (media_type == "video" and not re.search(r"frame=\s*[3-9]\d*", log)):
            raise ProbeError("video_decode_failed" if media_type == "video" else "audio_decode_failed")
        match = re.search(r"Video: ([^,\s]+).*?\b(\d{2,5})x(\d{2,5})\b", log)
        info = {"codec": match[1] if match else "audio", "width": int(match[2]) if match else None,
                "height": int(match[3]) if match else None, "decoded_frames": 3 if match else None}
        if frame_path and media_type == "video":
            Path(frame_path).parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([decoder, "-nostdin", "-y", "-loglevel", "error", "-protocol_whitelist", "file,data,crypto",
                            *(["-allowed_extensions", "ALL"] if bundle is not None else []),
                            "-i", str(sample), "-frames:v", "1", "-q:v", "5", str(frame_path)],
                           capture_output=True, timeout=25)
        return info


def download_segment(segment, proxy_mode):
    data = get(segment["url"], proxy_mode, limit=16_000_000, byte_range=segment["range"])[0]
    if len(data) < 188 or data.lstrip().startswith((b"<", b"#EXTM3U")):
        raise ProbeError("not_video_segment")
    part = {"data": data, "duration": segment["duration"]}
    if segment.get("key"):
        key_data = get(segment["key"]["url"], proxy_mode, limit=32)[0]
        if len(key_data) != 16:
            raise ProbeError("invalid_public_aes_key")
        part.update({"key_data": key_data, "iv": segment["key"]["iv"]})
    return part


def probe(url, proxy_mode="direct", frame_path=None, minimum_height=720, media_type="video"):
    started = time.monotonic()
    result = {"url": url, "checked_at_unix": int(time.time()), "proxy_mode": proxy_mode,
              "passed": False, "media_type": media_type}
    try:
        leaf, before, latency, variant = media_manifest(url, proxy_mode)
        # Decode actual current segments rather than treating HTTP 200 as playback success.
        init_data = get(before["init"], proxy_mode, limit=2_000_000)[0] if before["init"] else b""
        parts = []
        for segment in before["segments"][-2:]:
            parts.append(download_segment(segment, proxy_mode))
        chunks = [init_data, *[p["data"] for p in parts]]
        info = decode(b"".join(chunks), frame_path, media_type, bundle={"init": init_data, "parts": parts})
        if media_type == "video" and (not info["height"] or info["height"] < minimum_height):
            raise ProbeError("below_hd_threshold")
        if media_type == "video" and info["height"] > 1080:
            raise ProbeError("above_1080p_preference")
        # Sample the same media playlist again after at least one segment interval.
        time.sleep(min(15, max(3, before["target_duration"] + 1)))
        later_data, later_url, _ = get(leaf, proxy_mode)
        after = parse(later_data.decode("utf-8-sig"), later_url)
        if after["endlist"] or not after["segments"]:
            raise ProbeError("live_ended")
        old = before["segments"][-1]["url"]
        new = after["segments"][-1]["url"]
        seq_progress = before["sequence"] is not None and after["sequence"] is not None and after["sequence"] > before["sequence"]
        if new == old and not seq_progress:
            raise ProbeError("live_not_advancing")
        latest = after["segments"][-1]
        part = download_segment(latest, proxy_mode)
        init = get(after["init"], proxy_mode, limit=2_000_000)[0] if after["init"] else b""
        later_info = decode(init + part["data"], media_type=media_type, bundle={"init": init, "parts": [part]})
        if later_info["codec"] != info["codec"] or later_info["height"] != info["height"]:
            raise ProbeError("live_format_changed")
        result.update(info)
        result.update({"passed": True, "hls_manifest": True, "live_advancing": True,
                       "segments_decoded": 3, "manifest_latency_seconds": latency,
                       "media_playlist_url": leaf, "sample_sha256": hashlib.sha256(b"".join(chunks)).hexdigest(),
                       "frame_path": str(frame_path) if frame_path and Path(frame_path).exists() else None})
    except Exception as exc:
        result["failure"] = str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return result
