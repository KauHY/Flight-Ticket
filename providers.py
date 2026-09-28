"""Documented provider integrations. Never infer an all-in price from ticketPrice."""
import json
import os
import re
import shutil
import subprocess
import urllib.request
import urllib.parse
import hashlib
from datetime import date, timedelta
from pathlib import Path
from engine import AIRPORTS
from airports import local_time, scope

ROOT = Path(__file__).resolve().parent
FLYAI_CLI = ROOT / 'node_modules/@fly-ai/flyai-cli/dist/flyai-bundle.cjs'

def local_env():
    values={}
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text('utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1);values[key.strip()]=value.strip()
    for key in ('VARIFLIGHT_APP_ID','VARIFLIGHT_APP_SECURITY','VARIFLIGHT_BASE_URL'):
        if os.environ.get(key):values[key]=os.environ[key]
    return values

CATALOG = [
    dict(id='tongcheng', name='同程旅行 · 公开航班页', kind='tongcheng', website='https://www.ly.com/flights/home',
         docs='https://www.ly.com/robots.txt', status='已接入公开页面',
         note='无需密钥，使用本机 Edge 查询直飞航班、时间与展示价。每站至少间隔 120 秒，缓存 15 分钟；税费未确认不提醒。'),
    dict(id='spring', name='春秋航空 · 低价日历', kind='spring', website='https://flights.ch.com/',
         docs='https://flights.ch.com/robots.txt', status='试验接入 · 默认停用',
         note='初次查询成功，但后续日历请求返回非 JSON 页面，暂默认停用。可手动测试；仅提供航线起价，不参与逐航班比价或邮件提醒。'),
    dict(id='variflight', name='飞常准 · 航班动态 V3', kind='variflight', website='https://www.variflight.com/',
         docs='https://dataworks.variflight.com/blog/real-time-flight-data-api-for-air-travel-operations/', status='适配器已配置 · 等待数据权限',
         note='补充航班计划、预计/实际时间和运行状态，不提供票价。凭据只从本机 .env 读取；当前 APP ID 返回“暂无数据权限”。'),
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
    dict(id='airchina', name='中国国际航空', kind='partner', website='https://www.airchina.com.cn/', docs='https://www.airchina.com.cn/', status='待商务/NDC授权', note='官网可购票，但未发现面向个人开发者的公开实时运价 API。'),
    dict(id='hainanair', name='海南航空', kind='partner', website='https://www.hnair.com/', docs='https://www.hnair.com/', status='待商务授权', note='保留官网合作入口；不抓取登录、下单或验证码页面。'),
    dict(id='sichuanair', name='四川航空', kind='partner', website='https://www.sichuanair.com/', docs='https://www.sichuanair.com/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='shandongair', name='山东航空', kind='partner', website='https://www.sda.cn/', docs='https://www.sda.cn/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='shenzhenair', name='深圳航空', kind='partner', website='https://www.shenzhenair.com/', docs='https://www.shenzhenair.com/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='juneyao', name='吉祥航空', kind='partner', website='https://www.juneyaoair.com/', docs='https://www.juneyaoair.com/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='nineair', name='九元航空', kind='partner', website='https://www.9air.com/', docs='https://www.9air.com/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='chengduair', name='成都航空', kind='partner', website='https://www.cdal.com.cn/', docs='https://www.cdal.com.cn/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='chinaexpress', name='华夏航空', kind='partner', website='https://www.chinaexpressair.com/', docs='https://www.chinaexpressair.com/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='loongair', name='长龙航空', kind='partner', website='https://www.loongair.cn/', docs='https://www.loongair.cn/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='tibetair', name='西藏航空', kind='partner', website='https://www.tibetairlines.com.cn/', docs='https://www.tibetairlines.com.cn/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='okayair', name='奥凯航空', kind='partner', website='https://www.okair.net/', docs='https://www.okair.net/', status='待商务授权', note='需航司提供可用性和运价接口权限。'),
    dict(id='amadeus', name='Amadeus Self-Service', kind='partner', website='https://developers.amadeus.com/', docs='https://developers.amadeus.com/self-service/category/flights/api-doc/flight-offers-search', status='可申请 API · 待凭据', note='可返回航班报价及行程；生产数据、地区覆盖和配额以账户合同为准。'),
    dict(id='duffel', name='Duffel Flights API', kind='partner', website='https://duffel.com/', docs='https://duffel.com/docs/api/offer-requests', status='可申请 API · 待凭据', note='Offer Requests 提供航班和总价；测试环境价格不是真实市场报价。'),
    dict(id='skyscanner', name='Skyscanner Flights Live Prices', kind='partner', website='https://www.partners.skyscanner.net/', docs='https://developers.skyscanner.net/docs/flights-live-prices/overview', status='合作方 API · 待审批', note='需要合作伙伴审批及 API Key，不能用网页抓取替代授权接口。'),
    dict(id='travelpayouts', name='Travelpayouts / Aviasales', kind='partner', website='https://www.travelpayouts.com/', docs='https://support.travelpayouts.com/hc/en-us/articles/203956163', status='联盟 API · 待凭据', note='适合补充价格趋势或联盟报价，实时性和可预订性以具体 API 为准。'),
]

def presets():
    return [dict(catalog_id=c['id'],name=c['name'],kind=c['kind'],endpoint='',token='',enabled=c['kind'] in ('flyai','tongcheng')) for c in CATALOG]

def variflight(provider,monitor):
    cfg=local_env();appid=cfg.get('VARIFLIGHT_APP_ID','');security=cfg.get('VARIFLIGHT_APP_SECURITY','')
    endpoint=cfg.get('VARIFLIGHT_BASE_URL','https://open-al.variflight.com/api/flight')
    parsed=urllib.parse.urlsplit(endpoint)
    if parsed.scheme!='https' or parsed.hostname!='open-al.variflight.com' or parsed.path!='/api/flight':
        raise ValueError('飞常准地址必须是官方 HTTPS Flight V3 端点')
    if not appid or not security:raise ValueError('飞常准凭据未配置，请检查本机 .env')
    schedules=[];hints=[]
    day=date.fromisoformat(monitor['date_from']);end=date.fromisoformat(monitor['date_to'])
    for origin in scope(monitor,'origin'):
        for dest in scope(monitor,'destination'):
            current=day
            while current<=end:
                pairs=[('dep',origin),('arr',dest),('date',str(current)),('appid',appid),('lang','zh')]
                unsigned='&'.join(f'{k}={v}' for k,v in pairs)
                token=hashlib.md5(hashlib.md5((unsigned+security).encode()).hexdigest().encode()).hexdigest()
                req=urllib.request.Request(endpoint+'?'+urllib.parse.urlencode(pairs+[('token',token)]),headers={'Accept':'application/json','User-Agent':'FlightTicketMonitor/1.0'})
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self,*args,**kwargs):return None
                with urllib.request.build_opener(NoRedirect).open(req,timeout=25) as response:
                    raw=response.read(5_000_001)
                if len(raw)>5_000_000:raise ValueError('飞常准响应过大')
                try:payload=json.loads(raw.decode('utf-8'))
                except (UnicodeDecodeError,json.JSONDecodeError):payload=json.loads(raw.decode('gb18030'))
                if isinstance(payload,dict) and payload.get('error'):
                    raise ValueError('飞常准：'+str(payload['error'])+'（错误码 '+str(payload.get('error_code','?'))+'）')
                rows=payload.get('data',payload) if isinstance(payload,dict) else payload
                if not isinstance(rows,list):raise ValueError('飞常准返回格式异常')
                for item in rows:
                    try:
                        flight=str(item.get('fnum') or item.get('flightNo') or item.get('FlightNo')).upper()
                        a=str(item.get('dep') or item.get('org') or item.get('depCode') or origin).upper()
                        b=str(item.get('arr') or item.get('dst') or item.get('arrCode') or dest).upper()
                        departure=item.get('scheduledDeptime') or item.get('scheduledDeparture') or item.get('planDeptime')
                        arrival=item.get('scheduledArrtime') or item.get('scheduledArrival') or item.get('planArrtime')
                        if not re.fullmatch(r'[A-Z0-9]{2}\d{2,5}',flight) or not departure or not arrival:continue
                        schedules.append(dict(source='飞常准',status=str(item.get('status') or item.get('flightStatus') or ''),segments=[dict(origin=a,destination=b,
                            departure=local_time(departure,a),arrival=local_time(arrival,b),flight=flight,carrier=flight[:2],airline=str(item.get('airline') or item.get('airlineName') or ''))]))
                    except (ValueError,TypeError):continue
                current+=timedelta(days=1)
    hints.append(f'飞常准返回 {len(schedules)} 条航班动态记录；此接口不含机票价格。')
    return schedules,hints

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
                if s.get('stopInfos'): raise ValueError('unmapped technical stop')
                flight=s['marketingTransportNo']
                if not re.fullmatch(r'[A-Z0-9]{2}\d{2,5}',flight): raise ValueError('invalid flight')
                segments.append(dict(origin=origin,destination=dest,
                    departure=local_time(s['depDateTime'],origin),
                    arrival=local_time(s['arrDateTime'],dest),flight=flight,carrier=flight[:2],airline=s.get('marketingTransportName','')))
            if not segments or len(segments)>2: continue
            if segments[0]['origin'] not in scope(monitor,'origin') or segments[-1]['destination'] not in scope(monitor,'destination'): continue
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
    if kind=='variflight':return [],[]
    if kind in ('tongcheng','spring'):
        from crawlers import search_pages
        return search_pages(provider,monitor)
    if kind=='partner': raise ValueError('此来源尚待平台授权及协议适配，未启用实时查询')
    if kind not in ['generic','flyai']: raise ValueError('未知接口类型')
    output=[];hints=[];failures=[]
    for origin in scope(monitor,'origin'):
        for dest in scope(monitor,'destination'):
            query={**monitor,'origin':origin,'destination':dest,'origin_airports':[origin],'destination_airports':[dest]}
            try:
                values,notes=flyai(provider,query) if kind=='flyai' else (generic(provider,query),[])
                output.extend(values);hints.extend(notes)
            except Exception as e:
                failures.append(f'{origin} → {dest}：'+(str(e) if isinstance(e,ValueError) else type(e).__name__))
    if failures and not output:raise ValueError('；'.join(failures[:5]))
    hints.extend('部分机场查询失败：'+f for f in failures)
    return output,sorted(set(hints))

def validate_provider(p, old):
    kind=p.get('kind','generic'); p['kind']=kind
    if kind not in ['flyai','generic','partner','tongcheng','spring','variflight']: raise ValueError('未知接口类型')
    if kind=='partner' and p.get('enabled'): raise ValueError(p['name']+' 尚未完成授权及协议适配，不能启用')
    if kind=='generic' and not p.get('endpoint','').startswith('https://'): raise ValueError('接口地址须为 HTTPS')
    if kind!='generic': p['endpoint']=''
    if not p.get('token'):
        p['token']=next((x.get('token','') for x in old if x['name']==p['name'] and x.get('endpoint','')==p.get('endpoint','') and x.get('kind','generic')==kind),'')
    return p
