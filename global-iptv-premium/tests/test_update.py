import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("update", Path(__file__).resolve().parents[1] / "scripts/update.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def target(identity="Example.us", **kwargs):
    return {"id": identity, "name": "Example", "group": "04国际新闻", "note": "", "official_website": "https://example.org/",
            "source_aliases": ["Example"], "feeds": [], "subscription_only": False, **kwargs}


def loaded(rows, blocked=None):
    return [({"kind": "streams", "url": "https://iptv-org.github.io/api/streams.json"}, rows, {}),
            ({"kind": "channels"}, [{"id": "Example.us", "closed": None}], {}),
            ({"kind": "blocklist"}, blocked or [], {})]


SETTINGS = {"include_geo_blocked": True, "max_backups": 3, "minimum_previous_ratio": .5}


class GeneratorTests(unittest.TestCase):
    def build(self, rows, t=None, feedback=None, blocked=None, settings=None):
        return m.build({"channels": [t or target()]}, loaded(rows, blocked), feedback or {}, settings or SETTINGS)[0]

    def test_m3u_quotes_commas_and_exact_url(self):
        rows = m.parse_m3u('\ufeff#EXTM3U\n#EXTINF:-1 tvg-logo="https://a.org/a,b.png" group-title="News",Example\nhttps://example.org/live.m3u8?a=1&b=2\n')
        self.assertEqual(rows[0]["title"], "Example")
        self.assertEqual(rows[0]["url"], "https://example.org/live.m3u8?a=1&b=2")

    def test_no_empty_or_html_catalogue(self):
        for text in ["<html>error</html>", "#EXTM3U\n", "#EXTM3U\n#EXTINF:-1,Example\n"]:
            with self.assertRaises(ValueError):
                m.parse_m3u(text)

    def test_wrong_language_is_not_substituted(self):
        rows = [{"channel": "Example.us", "feed": "Arabic", "url": "https://example.org/ar.m3u8"},
                {"channel": "Example.us", "feed": "English", "url": "https://example.org/en.m3u8"}]
        result = self.build(rows, target(feeds=["English"]))
        self.assertEqual([s["url"] for s in result["sources"]], ["https://example.org/en.m3u8"])

    def test_blocklist_applies_to_supplemental_repository(self):
        data = loaded([], [{"channel": "Example.us", "reason": "dmca", "ref": "https://github.com/example/issue/1"}])
        data.append(({"kind": "m3u", "name": "YueChan/Live GNTV", "url": "https://github.com/source"},
                     [{"title": "Example", "url": "https://example.org/live.m3u8"}], {}))
        result = m.build({"channels": [target()]}, data, {}, SETTINGS)[0]
        self.assertEqual(result["status"], "upstream_blocklisted")
        self.assertEqual(result["sources"], [])

    def test_subscription_channel_has_no_placeholder(self):
        r = self.build([{"channel": "Example.us", "url": "https://example.org/live.m3u8"}], target(subscription_only=True))
        self.assertEqual(r["status"], "subscription_only")
        self.assertNotIn("#EXTINF", m.playlist([r], "now"))

    def test_bad_endpoints_and_multicast_are_excluded(self):
        reasons = {"rtp://239.3.1.129:8008": "operator_network_only", "http://127.0.0.1/live": "private_network_url",
                   "http://10.2.3.4/a": "private_network_url", "https://user:password@example.org/live": "credential_or_subscription_endpoint",
                   "https://example.org/a?token=abc": "signed_or_drm_url", "https://example.org/a?ads.id=[DEVICE_ID]": "unsafe_or_placeholder_url",
                   "https://example.org/live/user/password/123.ts": "credential_or_subscription_endpoint"}
        for url, expected in reasons.items():
            with self.subTest(url=url):
                self.assertEqual(m.url_rejection({"url": url}, {}), expected)

    def test_geo_is_annotated_and_can_be_excluded(self):
        rows = [{"channel": "Example.us", "url": "https://example.org/live.m3u8", "labels": ["Geo-blocked"]}]
        r = self.build(rows)
        self.assertIn("地区限制", m.playlist([r], "now"))
        r = self.build(rows, settings={**SETTINGS, "include_geo_blocked": False})
        self.assertEqual(r["status"], "no_eligible_source")

    def test_tv_feedback_selects_existing_backup_and_deduplicates(self):
        first = {"channel": "Example.us", "url": "https://example.org/1.m3u8"}
        second = {"channel": "Example.us", "url": "https://other.org/2.m3u8"}
        r = self.build([first, first, second], feedback={"preferred_urls": {"Example.us": second["url"]}})
        self.assertEqual(len(r["sources"]), 2)
        self.assertEqual(r["sources"][0]["url"], second["url"])
        self.assertEqual(self.build([first], feedback={"blocked_urls": [first["url"]]})["sources"], [])

    def test_refresh_never_requests_stream_urls(self):
        with patch.object(m, "urlopen", side_effect=AssertionError("Do not probe streams")):
            r = self.build([{"channel": "Example.us", "url": "https://example.org/live.m3u8"}])
            self.assertEqual(r["sources"][0]["playback_check"], "not_tested")

    def test_required_upstream_failure_preserves_existing_files(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            (destination / "data").mkdir()
            for name in ["whitelist.json", "sources.json", "feedback.json"]:
                (destination / "data" / name).write_bytes((root / "data" / name).read_bytes())
            (destination / "premium.m3u").write_text("LAST GOOD VERSION")
            with patch.object(m, "ROOT", destination), patch.object(m, "fetch_source", side_effect=RuntimeError("Offline")), patch("sys.argv", ["update"]):
                with self.assertRaises(RuntimeError):
                    m.main()
            self.assertEqual((destination / "premium.m3u").read_text(), "LAST GOOD VERSION")

    def test_repository_whitelist_has_exact_eight_groups_and_overseas_45(self):
        root = Path(__file__).resolve().parents[1]
        whitelist = m.load_json(root / "data/whitelist.json")
        m.validate_config(whitelist, m.load_json(root / "data/feedback.json"), SETTINGS)
        counts = {group: sum(r["group"] == group for r in whitelist["channels"]) for group in whitelist["groups"]}
        self.assertEqual([counts[g] for g in whitelist["groups"][3:]], [13, 7, 10, 8, 7])


if __name__ == "__main__":
    unittest.main()
