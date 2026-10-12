#!/usr/bin/env python3
"""Refresh an exact whitelist and publish only authorized, decoded public HLS."""
import argparse
import collections
import concurrent.futures
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlsplit, parse_qsl, urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import probe

LABELS = {'included':'已收录', 'geo':'地区限制', 'paid':'需要付费', 'missing':'未找到有效源'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def normalized(value):
    value = re.sub(r'\s*\[[A-Z-]+\]\s*$', '', value.strip())
    value = re.sub(r'(?:超高清|高清|标清|FHD|HD)$', '', value, flags=re.I)
    return re.sub(r'[\s\-－_·]', '', value).casefold()


def parse_m3u(body):
    if not body.lstrip('\ufeff \r\n').startswith('#EXTM3U'):
        raise ValueError('not_m3u')
    rows, pending = [], None
    for line in body.splitlines():
        line = line.strip().lstrip('\ufeff')
        if line.startswith('#EXTINF:'):
            quote, comma = False, None
            for i, char in enumerate(line):
                if char == '"':quote = not quote
                elif char == ',' and not quote:
                    comma = i; break
            if comma is None:raise ValueError('invalid_extinf')
            pending = {'title':line[comma+1:].strip(), 'attrs':dict(re.findall(r'([\w-]+)="([^"\r\n]*)"',line[:comma])), 'labels':[], 'feed':None}
        elif line and not line.startswith('#') and pending:
            rows.append({**pending,'url':line}); pending = None
    if pending or not rows:raise ValueError('empty_or_incomplete_m3u')
    return rows


def parse_text(body):
    rows = []
    for line in body.splitlines():
        if ',' not in line:continue
        name, url = line.split(',',1)
        # $emo线路 is the upstream player's display decoration, not part of the URL.
        url = url.split('$emo线路',1)[0].strip()
        if url.startswith(('http://','https://')):
            rows.append({'title':name.strip(),'url':url,'labels':[],'feed':None})
    if not rows:raise ValueError('empty_text_catalogue')
    return rows


def fetch_source(source, cache_dir=None):
    if cache_dir:
        data = (cache_dir / source['file']).read_bytes()
    else:
        error = None
        for i in range(3):
            try:
                with urlopen(Request(source['url'],headers={'User-Agent':'global-iptv-premium/2.0'}),timeout=35) as response:
                    data = response.read(20_000_001)
                break
            except Exception as exc:
                error = exc
                if i == 2:raise error
                time.sleep(i+1)
    if len(data)>20_000_000:raise ValueError('catalogue_too_large')
    body=data.decode('utf-8-sig');kind=source['kind']
    if kind=='m3u':parsed=parse_m3u(body)
    elif kind=='txt':parsed=parse_text(body)
    elif kind=='cctv_player':
        parsed=[{'channel':'CCTV1.cn','feed':'HD','url':url,'labels':[],'from_official_player':True}
                for url in dict.fromkeys(re.findall(r'https?://[^\s\x27\x22]+\.m3u8',body)) if '/ldcctv1_2/' in url]
    else:parsed=json.loads(body)
    if not isinstance(parsed,list) or not parsed:raise ValueError('invalid_or_empty_catalogue')
    for row in parsed:
        if not isinstance(row,dict):raise ValueError('invalid_catalogue_record')
        if kind=='streams' and not isinstance(row.get('url'),str):raise ValueError('invalid_stream_url')
        if kind=='channels' and not row.get('id'):raise ValueError('invalid_channel_id')
        if kind=='blocklist' and not row.get('channel'):raise ValueError('invalid_blocklist')
    return source,parsed,{'name':source['name'],'url':source['url'],'sha256':hashlib.sha256(data).hexdigest(),'records':len(parsed),'status':'loaded'}


def unsafe_url(url):
    if not isinstance(url,str) or any(c in url for c in '\r\n\x00"|') or re.search(r'\[[A-Z_]+\]',url):return 'invalid_or_template'
    try:
        p=urlsplit(url); _=p.port
    except ValueError:return 'invalid_url'
    if p.scheme not in ['http','https'] or not p.hostname:return 'not_public_http'
    if p.username or p.password or re.fullmatch(r'/live/[^/]+/[^/]+/\d+(?:\.(?:ts|m3u8))?',p.path):return 'credentials_or_subscription'
    if p.path.lower().endswith(('.mpd','.mp4','.mp3','.aac')):return 'not_supported_video_hls'
    keys={k.casefold() for k,v in parse_qsl(p.query)}
    if keys & {'token','auth','authinfo','authorization','password','txsecret','txtime','expires','exp','sig','signature','hdnea','hdntl','apikey','wmsauthsign'} or 'cdrm' in url.lower():return 'special_auth_or_drm'
    if 'cnn_slate' in url.lower() or 'CNNFAST' in url:return 'wrong_or_placeholder_channel'
    return None


def authorized(row,target,policy):
    if row.get('from_official_player') and target['channel_id']=='CCTV1.cn':return True
    rule=policy['channels'].get(target.get('channel_id'),{})
    return any(row['url'].startswith(prefix) for prefix in rule.get('prefixes',[]))


def prepare(whitelist,loaded,policy,feedback):
    catalogue,logos,blocked={},[],{}
    pool=collections.defaultdict(list)
    alias=collections.defaultdict(set)
    for target in whitelist['channels']:
        for name in target.get('source_aliases',[]):alias[normalized(name)].add(target['key'])
    supplement=collections.defaultdict(list)
    for source,data,meta in loaded:
        kind=source['kind']
        if kind=='channels':catalogue.update({r['id']:r for r in data})
        elif kind=='logos':logos.extend(data)
        elif kind=='blocklist':
            for r in data:blocked.setdefault(r['channel'],[]).append(r)
        elif kind in ['streams','cctv_player']:
            for row in data:pool[row.get('channel')].append({**row,'source':source['name'],'source_url':source['url']})
        elif kind in ['m3u','txt']:
            for row in data:
                for key in alias.get(normalized(row['title']),[]):supplement[key].append({**row,'source':source['name'],'source_url':source['url']})
    for row in policy.get('observed_streams', []):
        pool[row['channel']].append({**row,'source':'Observed official manifest','source_url':row['origin_url']})
    results=[]
    for target in whitelist['channels']:
        record={**target,'status':'missing','candidates':[],'sources':[],'excluded':[],'checks':[]}
        if target['key'] in feedback.get('disabled_channels',[]):
            record['reason']='用户停用';results.append(record);continue
        block_entries=[]
        for id in [target.get('channel_id'),*target.get('alternate_ids',[])]:block_entries+=blocked.get(id,[])
        if block_entries:
            record.update({'reason':'遵守上游权利屏蔽清单，不通过其他仓库重新收录。','blocklist':block_entries});results.append(record);continue
        restriction=target.get('restriction')
        if restriction in ['paid','geo']:
            record.update({'status':restriction,'reason':'目标版本需要订阅/授权。' if restriction=='paid' else '目标版本或公开免费分发平台有已知地区限制，不进入主订阅。'});results.append(record);continue
        if restriction=='audio_only':
            record['reason']='BBC World Service是广播，不计为电视直播；没有对应的视频源。';results.append(record);continue
        if restriction=='ambiguous':
            record['reason']='未确定独立频道版本，不能使用其他CNBC频道代替。';results.append(record);continue
        if catalogue.get(target.get('channel_id'),{}).get('closed'):
            record['reason']='原频道已停播；未确认替代服务的合格常驻公开HLS。';results.append(record);continue
        rows=[]
        for id in [target.get('channel_id'),*target.get('alternate_ids',[])]:
            for row in pool[id]:
                if row.get('from_official_player') or not target.get('feeds') or row.get('feed') in target['feeds']:rows.append(row)
        rows+=supplement[target['key']]
        seen=set()
        for row in rows:
            if row['url'] in seen:continue
            seen.add(row['url']);reason=unsafe_url(row['url'])
            if row['url'] in feedback.get('blocked_urls',[]):reason=reason or 'user_reported_bad'
            if any(x in row['url'] for x in target.get('reject_url_patterns',[])):reason=reason or 'identity_or_encoded_signature'
            if row.get('referrer'):reason=reason or 'requires_referrer'
            if 'Geo-blocked' in (row.get('labels') or []):reason=reason or 'geo'
            if not authorized(row,target,policy):reason=reason or 'authorization_not_verified'
            if reason:
                record['excluded'].append({'source':row['source'],'reason':reason,'host':urlsplit(row['url']).hostname if reason!='invalid_url' else None})
            else:record['candidates'].append(row)
        record['candidates'].sort(key=lambda r:(r['url']!=feedback.get('preferred_urls',{}).get(target['key']),-int(re.search(r'\d+',str(r.get('quality') or '0'))[0]),r['url']))
        channel_logos=[x for x in logos if x.get('channel')==target.get('channel_id') and x.get('in_use',True) and x.get('url','').startswith('https://')]
        channel_logos.sort(key=lambda x:(x.get('feed') not in target.get('feeds',[]),not x.get('url','').lower().endswith('.png'),-(x.get('width') or 0)))
        record['logo']=channel_logos[0]['url'] if channel_logos else ''
        record['reason']='没有经过授权来源审核的固定公开HD HLS。' if not record['candidates'] else '等待大陆直连分片与视频解码检查。'
        record['distribution_evidence']=policy['channels'].get(target.get('channel_id'),{}).get('evidence',[])
        results.append(record)
    return results


def network_country(mode='direct'):
    data,_,_=probe.get('https://www.cloudflare.com/cdn-cgi/trace',mode,limit=8000)
    fields=dict(line.split('=',1) for line in data.decode().splitlines() if '=' in line)
    return {'country':fields.get('loc','unknown'),'mode':mode,'checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'method':'Cloudflare loc; IP address discarded'}


def resolve_bloomberg(row,mode):
    data,final,_=probe.get(row['url'],mode)
    manifest=probe.parse(data.decode('utf-8-sig'),final)
    variants=[x for x in manifest['variants'] if 720<=x['height']<=1080]
    if not variants:raise probe.ProbeError('no_published_hd_variant')
    chosen=sorted(variants,key=probe.video_rank)[0]
    return {**row,'origin_url':row['url'],'url':chosen['url'],'source':'Bloomberg official manifest',
            'resolved_from':final,'discovery_manifest_sha256':hashlib.sha256(data).hexdigest()}


def perform_checks(records,settings,mode,cache=None):
    # Cache is for reproducing the already measured initial mainland result, never for daily CI.
    cached={x['url']:x for x in (cache or [])}
    all_candidates={}
    for target in records:
        for row in target['candidates'][:8]:all_candidates[row['url']]=row
    checked={}
    def check(row):
        current=row
        try:
            if row.get('channel')=='BloombergTV.us' and '/media-manifest/streams/' in row['url']:
                current=resolve_bloomberg(row,'default') # discovery only; playback is always probed directly below
            if current['url'] in cached:
                result={k:v for k,v in cached[current['url']].items() if k!='frame_path'}
                if result.get('proxy_mode')!=mode or time.time()-result.get('checked_at_unix',0)>6*3600:
                    raise probe.ProbeError('stale_or_wrong_network_cache')
            else:result=probe.probe(current['url'],mode,minimum_height=settings['minimum_height'])
            if result.get('elapsed_seconds',0)>settings['maximum_probe_seconds']:
                result={**result,'passed':False,'failure':'probe_too_slow_for_stable_playback'}
            return row['url'],{**current,'check':result}
        except Exception as exc:
            return row['url'],{**current,'check':{'passed':False,'failure':str(exc) if isinstance(exc,probe.ProbeError) else type(exc).__name__}}
    with concurrent.futures.ThreadPoolExecutor(max_workers=settings['probe_workers']) as executor:
        for original,result in executor.map(check,all_candidates.values()):
            checked[original]=result
            c=result['check'];print(('PASS' if c.get('passed') else 'FAIL'),urlsplit(result['url']).hostname,c.get('height') or c.get('failure'),flush=True)
    for target in records:
        valid=[]
        for row in target['candidates']:
            if row['url'] not in checked:continue
            result=checked[row['url']];c=result['check']
            target['checks'].append({'source':result['source'],'origin_url':row['url'],'url':result['url'],'check':c})
            if c.get('passed'):valid.append(result)
        valid.sort(key=lambda x:(-min(x['check'].get('height') or 0,1080),x['check'].get('elapsed_seconds',999)))
        target['sources']=valid[:settings['max_backups']]
        if valid:
            target['status']='included';target['reason']='大陆直连：HLS有效、最新分片可下载、视频实际解码且直播序列推进。'
        elif target['candidates']:
            target['reason']='公开候选未通过大陆直连HD播放验证。'
    return records


def attr(value):
    return str(value or '').replace('"',"'").replace('\n',' ').replace('\r',' ')


def playlist(records,stamp,backups=False,overseas=False):
    lines=['#EXTM3U','# Checked in mainland China (CN), direct: '+stamp,'# Only reviewed public streams with decoded, advancing live video']
    for target in records:
        if overseas and int(target['group'][:2])<4:continue
        for i,row in enumerate(target['sources'] if backups else target['sources'][:1]):
            name=target['name']+(f' [源{i+1}]' if backups else '')
            feed=row.get('feed')
            tvg=(target.get('channel_id') or target['id'])+('@'+feed if feed else '')
            logo=' tvg-logo="'+attr(target.get('logo'))+'"' if target.get('logo') else ''
            lines.extend([f'#EXTINF:-1 tvg-id="{attr(tvg)}" tvg-name="{attr(target["name"])}"{logo} group-title="{attr(target["group"])}",{attr(name)}',row['url']])
    return '\n'.join(lines)+'\n'


def markdown(report):
    lines=['# 完整频道清单与缺失报告','',f'检查时间（北京时间）：{report["checked_at_shanghai"]}',
           f'检测出口：{report["network"]["country"]}；模式：direct（不使用系统代理）。',
           '这是本机大陆直连检测，不能等同于所有运营商或你家小米电视都已实测。通过项检查了HLS、最新分片、实际视频解码和直播推进；不是长期可用保证。','',
           '| 分类 | 目标数 | 当前通过大陆直连检查并收录 |','|---|---:|---:|']
    for group,c in report['groups'].items():lines.append(f'| {group} | {c["targets"]} | {c["included"]} |')
    lines += ['',f'总目标 **{report["target_count"]}**；海外目标 **{report["overseas_target_count"]}**；当前收录 **{report["mainland_verified_count"]}**，其中海外 **{report["overseas_verified_count"]}**。',
              '缺失分类不添加假链接或占位节目。PBS按用户要求删除。BBC World Service是广播，单列目标但不计作可播放电视台。',
              '', '| 分类 | 频道 | 频道标识/版本 | 状态 | 画质/失败原因 |','|---|---|---|---|---|']
    for row in report['channels']:
        detail=row['reason']
        if row['sources']:
            x=row['sources'][0];detail=f'{x["check"]["width"]}×{x["check"]["height"]} / {x["check"]["codec"]} / [播放URL]({x["url"]})'
        else:
            failures=sorted({x['check'].get('failure','unknown') for x in row['checks'] if not x['check'].get('passed')})
            if failures:detail+=' '+', '.join(failures)
        if row.get('note'):detail+=' '+row['note']
        if row.get('blocklist'):detail+=' '+', '.join(f'[上游移除记录]({x["ref"]})' for x in row['blocklist'] if x.get('ref'))
        lines.append('| '+' | '.join(str(x).replace('|','\\|').replace('\n',' ') for x in [row['group'],row['name'],row['key'],LABELS[row['status']],detail])+' |')
    lines += ['','## 来源与判断依据','',
              '合法性采用逐频道来源审核：电视台官方网站/播出CDN或已确认的免费分发渠道。授权不明的IP转播、付费频道盗播、错台/城市景观占位、地区受限、登录/特殊鉴权端点不进入最终M3U。',
              '具体候选、HTTP/解码失败、来源依据与检测时间见 [status.json](status.json)。HTTP 403或超时只记为访问失败，不擅自断言是地区限制。','']
    for source in report['catalogues']:lines.append(f'- [{source["name"]}]({source["url"]})：{source["status"]}；{source.get("records",0)} 条。')
    return '\n'.join(lines)


def atomic(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as handle:handle.write(text);temporary=handle.name
    os.replace(temporary,path)


def validate(whitelist):
    if len(whitelist['groups'])!=8:raise ValueError('expected_eight_groups')
    keys=[r['key'] for r in whitelist['channels']]
    if len(keys)!=len(set(keys)):raise ValueError('duplicate_target_key')
    if sum(int(r['group'][:2])>=4 for r in whitelist['channels'])!=63:raise ValueError('expected_user_exact_63_overseas_targets')
    if any(r['name']=='PBS' for r in whitelist['channels']):raise ValueError('PBS_must_be_removed')
    if 'CCTV5.cn' not in keys:raise ValueError('CCTV5_target_missing')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir',type=Path)
    parser.add_argument('--probe-results',type=Path,help='Reproduce recent checks performed in the same direct CN mode')
    parser.add_argument('--catalogue-only',action='store_true',help='Fetch approved candidates without modifying validated subscriptions')
    parser.add_argument('--proxy-mode',choices=['direct','default'],default='direct')
    args=parser.parse_args()
    config=read(ROOT/'data/sources.json');whitelist=read(ROOT/'data/whitelist.json');policy=read(ROOT/'data/source-policy.json');feedback=read(ROOT/'data/feedback.json')
    validate(whitelist);settings=config['settings'];loaded=[];meta=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        pending={executor.submit(fetch_source,s,args.cache_dir):s for s in config['sources']}
        for future in concurrent.futures.as_completed(pending):
            source=pending[future]
            try:
                result=future.result();loaded.append(result);meta.append(result[2])
            except Exception as exc:
                if source['required']:raise RuntimeError('Required catalogue failed; preserving prior playlist: '+source['name']) from exc
                meta.append({'name':source['name'],'url':source['url'],'status':'fetch_failed'})
    records=prepare(whitelist,loaded,policy,feedback)
    if args.catalogue_only:
        atomic(ROOT/'output/candidates.json',json.dumps({'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'not_playback_verified':True,'catalogues':meta,'channels':records},ensure_ascii=False,indent=2)+'\n')
        print('Catalogues refreshed; validated subscriptions unchanged.');return
    network=network_country(args.proxy_mode)
    if network['country']!='CN' or args.proxy_mode!='direct':raise RuntimeError('Publication requires actual direct CN network; current country='+network['country'])
    probe.decoder_path() # Fail before creating output when the decoder is missing.
    cache=read(args.probe_results) if args.probe_results else None
    records=perform_checks(records,settings,args.proxy_mode,cache)
    count=sum(bool(r['sources']) for r in records)
    if not count:raise RuntimeError('No valid public mainland playback sources; preserving prior playlist.')
    now=datetime.now(timezone.utc);stamp=now.isoformat(timespec='seconds')
    groups={g:{'targets':sum(r['group']==g for r in records),'included':sum(r['group']==g and bool(r['sources']) for r in records)} for g in whitelist['groups']}
    report={'version':2,'checked_at':stamp,'checked_at_shanghai':now.astimezone(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),'network':network,
            'target_count':len(records),'overseas_target_count':63,'mainland_verified_count':count,'overseas_verified_count':sum(int(r['group'][:2])>=4 and bool(r['sources']) for r in records),
            'television_confirmed_count':0,'television_confirmation_note':'本版全部源未经用户小米电视逐台确认；用户旧版CCTV2/3反馈不替代本版URL与授权检查。',
            'groups':groups,'catalogues':sorted(meta,key=lambda x:x['name']),'channels':records}
    outputs={'premium.m3u':playlist(records,stamp),'premium-backups.m3u':playlist(records,stamp,True),'overseas.m3u':playlist(records,stamp,overseas=True),
             'output/status.json':json.dumps(report,ensure_ascii=False,indent=2)+'\n','output/CHANNELS.md':markdown(report)}
    for path,text in outputs.items():atomic(ROOT/path,text)
    print(json.dumps({'targets':len(records),'overseas_targets':63,'mainland_verified':count,'overseas_verified':report['overseas_verified_count'],'groups':groups},ensure_ascii=False))

if __name__=='__main__':main()
