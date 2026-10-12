import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('update',ROOT/'scripts/update.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def target(**overrides):
    return {'key':'Example.us','id':'Example.us','channel_id':'Example.us','name':'Example','group':'04 国际新闻','source_aliases':['Example'],'feeds':['English'],**overrides}

def build(rows, t=None, blocks=None, extra=None):
    loaded=[({'kind':'streams','name':'test','url':'https://example.org/catalogue'},rows,{}),({'kind':'blocklist'},blocks or [],{})]
    if extra:loaded.append(extra)
    return m.prepare({'channels':[t or target()]},loaded,{'channels':{'Example.us':{'prefixes':['https://example.org/']}}},{})[0]

class Tests(unittest.TestCase):
    def test_exact_user_whitelist(self):
        w=m.read(ROOT/'data/whitelist.json');m.validate(w)
        self.assertEqual([sum(c['group']==g for c in w['channels']) for g in w['groups'][3:]],[17,11,11,10,14])
    def test_m3u_quotes_and_commas(self):
        r=m.parse_m3u('#EXTM3U\n#EXTINF:-1 tvg-logo="https://a.org/a,b.png",Example\nhttps://example.org/a.m3u8?a=1&b=2\n')[0]
        self.assertEqual(r['title'],'Example');self.assertTrue(r['url'].endswith('a=1&b=2'))
    def test_reject_html_and_incomplete_catalogue(self):
        for body in ['<html>error</html>','#EXTM3U','#EXTM3U\n#EXTINF:-1,Channel']:
            with self.assertRaises(ValueError):m.parse_m3u(body)
    def test_exact_feed_and_rights_policy(self):
        rows=[{'channel':'Example.us','feed':f,'url':u} for f,u in [('English','https://example.org/live.m3u8'),('Arabic','https://example.org/ar.m3u8'),('English','https://unreviewed.org/a.m3u8')]]
        r=build(rows);self.assertEqual(len(r['candidates']),1);self.assertEqual(r['sources'],[])
    def test_blocklist_applies_to_other_repositories(self):
        extra=({'kind':'m3u','name':'supplement','url':'https://example.org/list'},[{'title':'Example','url':'https://example.org/a.m3u8'}],{})
        r=build([],blocks=[{'channel':'Example.us','ref':'https://example.org/removal'}],extra=extra)
        self.assertFalse(r['candidates']);self.assertIn('屏蔽',r['reason'])
    def test_paid_geo_and_unsigned_only(self):
        for restriction in ['paid','geo']:
            r=build([],target(restriction=restriction));self.assertEqual(r['status'],restriction);self.assertNotIn('#EXTINF',m.playlist([r],'now'))
        for url in ['https://example.org/a?AuthInfo=secret','https://user:pass@example.org/a','https://example.org/live/user/pass/123.ts']:
            self.assertIsNotNone(m.unsafe_url(url))
        self.assertIsNone(m.unsafe_url('https://cdn-euronews.akamaized.net/live/eds/euronews-en/25002/index.m3u8'))
        self.assertIsNone(m.unsafe_url('http://[2409:8087:1a01:df::7005]/a.m3u8'))
    def test_private_address_rejected_before_connection(self):
        for url in ['http://127.0.0.1/a','http://[::1]/a','http://10.0.0.1/a']:
            with self.assertRaises(m.probe.ProbeError):m.probe.public_url(url)
    def test_hls_relative_urls_and_public_aes_iv(self):
        h=m.probe.parse('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:42\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:6,\na.ts\n#EXTINF:6,\nb.ts','https://example.org/live/index.m3u8')
        self.assertEqual(h['segments'][1]['url'],'https://example.org/live/b.ts')
        self.assertEqual(h['segments'][1]['key']['iv'],'0x'+format(43,'032x'))
        with self.assertRaises(m.probe.ProbeError):m.probe.parse('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key"','https://example.org/')
    def test_decode_failure_and_stale_video_never_pass(self):
        media={'segments':[{'url':'https://example.org/a'},{'url':'https://example.org/b'}],'init':None,'target_duration':3,'sequence':1,'endlist':False}
        with patch.object(m.probe,'media_manifest',return_value=('https://example.org/live',media,0,None)),patch.object(m.probe,'download_segment',return_value={'data':b'x','duration':6}),patch.object(m.probe,'decode',side_effect=m.probe.ProbeError('video_decode_failed')):
            self.assertFalse(m.probe.probe('https://example.org/live')['passed'])
        info={'height':720,'width':1280,'codec':'h264'}
        with patch.object(m.probe,'media_manifest',return_value=('https://example.org/live',media,0,None)),patch.object(m.probe,'download_segment',return_value={'data':b'x','duration':6}),patch.object(m.probe,'decode',return_value=info),patch.object(m.probe.time,'sleep'),patch.object(m.probe,'get',return_value=(b'x','https://example.org/live',0)),patch.object(m.probe,'parse',return_value=media):
            self.assertEqual(m.probe.probe('https://example.org/live')['failure'],'live_not_advancing')
    def test_failed_upstream_preserves_last_playlist(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'data').mkdir()
            for name in ['whitelist.json','sources.json','source-policy.json','feedback.json']:(root/'data'/name).write_bytes((ROOT/'data'/name).read_bytes())
            (root/'premium.m3u').write_text('LAST GOOD')
            with patch.object(m,'ROOT',root),patch.object(m,'fetch_source',side_effect=RuntimeError('Offline')),patch('sys.argv',['update']):
                with self.assertRaises(RuntimeError):m.main()
            self.assertEqual((root/'premium.m3u').read_text(),'LAST GOOD')
    def test_non_mainland_cannot_publish(self):
        with patch.object(m,'fetch_source',return_value=({'kind':'channels'},[],{})),patch.object(m,'network_country',return_value={'country':'US'}),patch('sys.argv',['update']),patch.object(m,'atomic') as output:
            with self.assertRaisesRegex(RuntimeError,'actual direct CN'):m.main()
            output.assert_not_called()

if __name__=='__main__':unittest.main()
