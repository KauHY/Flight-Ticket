"""Documented provider integrations. Never infer an all-in price from ticketPrice."""
import json
import os
import re
import shutil
import subprocess
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from engine import AIRPORTS

ROOT = Path(__file__).resolve().parent
FLYAI_CLI = ROOT / 'node_modules/@fly-ai/flyai-cli/dist/flyai-bundle.cjs'
CATALOG = [
    dict(id='fliggy', name='飞猪 FlyAI', kind='flyai', website='https://flyai.open.fliggy.com/',
         docs='https://github.com/alibaba-flyai/flyai-skill', status='已实现查询适配器',
         note='支持官方体验查询；正式 API Key 可在控制台申请。结果数量受限，费用口径未确认的报价不触发提醒。'),
    dict(id='qunar', name='去哪儿', kind='partner', website='https://open.qunar.com/data/detail/open',
         docs='https://open.qunar.com/data/detail/open', status='待申请与协议适配',
         note='官方商旅 API 需注册、签署协议并申请。取得搜索接口文档及凭据后才能实现原生适配。'),
    dict(id='ctrip', name='携程', kind='partner', website='https://u.ctrip.com/alliance/',
         docs='https://open.ctrip.com/', status='待确认查询权限',
         note='原开放平台入口跳转至联盟。联盟链接不等于实时机票 API；需向平台确认数据合作权限和文档。'),
    dict(id='ceair', name='中国东方航空', kind='partner', website='https://developer.ceair.com/',
         docs='https://developer.ceair.com/', status='待申请 NDC',
         note='官方流程包括卖家注册、公司资料审核、测试环境申请与生产上线。尚未实现私有协议适配。'),
    dict(id='csair', name='中国南方航空', kind='partner', website='https://www.csair.com/cn/czadscale/2019/20190521_8/index.html',
         docs='https://www.csair.com/cn/czadscale/2019/20190521_2/index.html', status='待申请 NDC',
         note='官方合作指南提供查询接口与合作申请。具体接入地址、凭据和版本由合作方确认。'),
    dict(id='xiamenair', name='厦门航空', kind='partner', website='https://open.xiamenair.com/',
         docs='https://open.xiamenair.com/doc/zh/ndc-v213/1_General_Introduction.html', status='待申请 NDC',
         note='官方有 NDC 21.3 文档；需分销方身份和销售地等参数。取得授权后再完成协议适配。'),
]

def presets():
    return [dict(catalog_id=c['id'],name=c['name'],kind=c['kind'],endpoint='',token='',enabled=c['kind']=='flyai') for c in CATALOG]

def parse_flyai(payload, monitor):
    if payload.get('status') not in (0, '0') or not isinstance(payload.get('data',{}).get('itemList'),list):
        raise ValueError('飞猪未返回有效查询结果，请检查 Key、额度或稍后重试')
    offers=[]
    for item in payload['data']['itemList']:
        try:
            journeys=item['journeys']
            if len(journeys)!=1: continue  # Do not turn round-trip or mixed bundles into one-way fares.
            segments=[]
            for s in journeys[0]['segments']:
                origin=s['depStationCode']; dest=s['arrStationCode']
                # Official output gives local times without timezone. Only map known mainland airports.
                if origin not in AIRPORTS or dest not in AIRPORTS: raise ValueError('unsupported timezone')
                if s.get('stopInfos'): raise ValueError('unmapped technical stop')
                flight=s['marketingTransportNo']
                if not re.fullmatch(r'[A-Z0-9]{2}\d{2,5}',flight): raise ValueError('invalid flight')
                segments.append(dict(origin=origin,destination=dest,
                    departure=s['depDateTime'].replace(' ','T')+'+08:00',
                    arrival=s['arrDateTime'].replace(' ','T')+'+08:00',flight=flight,carrier=flight[:2]))
            if not segments or len(segments)>2: continue
            if segments[0]['origin']!=monitor['origin'] or segments[-1]['destination']!=monitor['destination']: continue
            if not monitor['date_from']<=segments[0]['departure'][:10]<=monitor['date_to']: continue
            price=item.get('ticketPrice',item.get('adultPrice'))
            if price is None: continue
            price=str(price).replace('¥','').replace('￥','').replace(',','').strip()
            # Neither ticketPrice nor adultPrice promises the payable all-in total in public docs.
            offers.append(dict(currency='CNY',base_fare=price,segments=segments,url=item.get('jumpUrl',''),
                price_status='unverified',price_note='飞猪返回展示价，未提供完整机建燃油及必付费用，不参与低价提醒',
                self_transfer=len(segments)>1,transfer_unverified=len(segments)>1))
        except (KeyError,TypeError,ValueError):
            continue
    return offers

def flyai(provider, monitor):
    if not FLYAI_CLI.exists() or not shutil.which('node'):
        raise ValueError('飞猪工具未安装：请安装 Node.js 并在项目目录运行 npm ci --ignore-scripts')
    env=os.environ.copy()
    # Do not inherit debug endpoint overrides which could redirect credentials.
    for k in ['DEBUG_FLYAI_MCP_URL','DEBUG_FLYAI_API_KEY','FLYAI_SIGN_SECRET']:
        env.pop(k,None)
    env['FLYAI_JSON']='1'
    if provider.get('token'): env['FLYAI_API_KEY']=provider['token']
    output=[]; hints=set()
    day=max(date.fromisoformat(monitor['date_from']),date.today()); end=date.fromisoformat(monitor['date_to'])
    if end<day or (end-day).days>30: raise ValueError('飞猪查询日期范围须为未来 31 天以内的区间')
    while day<=end:
        for journey in (['1','2'] if monitor['connections'] else ['1']):
            cmd=[shutil.which('node'),str(FLYAI_CLI),'search-flight','--origin',monitor['origin'],
                 '--destination',monitor['destination'],'--dep-date',str(day),'--journey-type',journey,'--sort-type','3']
            try:
                res=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',
                    timeout=40,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            except subprocess.TimeoutExpired:
                raise ValueError('飞猪查询超时，请缩短日期区间或稍后重试') from None
            if res.returncode or len(res.stdout)>5_000_000:
                raise ValueError('飞猪查询失败，请检查网络、API Key 和调用额度')
            try: payload=json.loads(res.stdout)
            except json.JSONDecodeError: raise ValueError('飞猪返回格式异常') from None
            output.extend(parse_flyai(payload,monitor))
            if payload.get('systemMessage'): hints.add(str(payload['systemMessage'])[:1000])
        day+=timedelta(days=1)
    return output,sorted(hints)

def generic(provider, monitor):
    query={k:monitor[k] for k in ('origin','destination','date_from','date_to','connections','self_transfer')}
    query.update(currency='CNY',adults=1)
    endpoint=provider['endpoint']
    if not endpoint.startswith('https://'): raise ValueError('票价接口必须使用 HTTPS')
    headers={'Accept':'application/json','Content-Type':'application/json'}
    if provider.get('token'): headers['Authorization']='Bearer '+provider['token']
    req=urllib.request.Request(endpoint,data=json.dumps(query).encode(),headers=headers,method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    with urllib.request.build_opener(NoRedirect).open(req,timeout=25) as res:
        payload=res.read(5_000_001)
        if len(payload)>5_000_000: raise ValueError('接口响应过大')
        values=json.loads(payload)['offers']
        if not isinstance(values,list) or len(values)>2000: raise ValueError('接口报价数量无效')
        return values

def search(provider, monitor):
    kind=provider.get('kind','generic')
    if kind=='flyai': return flyai(provider,monitor)
    if kind=='partner': raise ValueError('此来源尚待平台授权及协议适配，未启用实时查询')
    if kind!='generic': raise ValueError('未知接口类型')
    return generic(provider,monitor),[]

def validate_provider(p, old):
    kind=p.get('kind','generic'); p['kind']=kind
    if kind not in ['flyai','generic','partner']: raise ValueError('未知接口类型')
    if kind=='partner' and p.get('enabled'): raise ValueError(p['name']+' 尚未完成授权及协议适配，不能启用')
    if kind=='generic' and not p.get('endpoint','').startswith('https://'): raise ValueError('接口地址须为 HTTPS')
    if kind!='generic': p['endpoint']=''
    if not p.get('token'):
        p['token']=next((x.get('token','') for x in old if x['name']==p['name'] and x.get('endpoint','')==p.get('endpoint','') and x.get('kind','generic')==kind),'')
    return p
