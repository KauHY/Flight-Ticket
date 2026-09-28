import json, os, re, sqlite3, threading, time, ssl, smtplib, urllib.request, urllib.parse, uuid
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from engine import AIRPORTS, money, normalize, compare, flight_board
from providers import CATALOG, presets, search, validate_provider
import airports
from runtime import bind_server

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('FLIGHT_DATA',str(ROOT/'data')))
DATA.mkdir(exist_ok=True)
DB=DATA/'flights.db'
LOCK=threading.Lock()
POOL=ThreadPoolExecutor(max_workers=2)
RUNNING=set()
PROVIDER_HEALTH={}
QUERIES={}
DEFAULT={'providers':[], 'fee_rules':[], 'smtp':{'host':'','port':465,'security':'ssl','username':'','password':'','sender':''}}

@contextmanager
def db():
    c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()

def init():
    airports.initialize()
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS monitors(id INTEGER PRIMARY KEY, body TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, checked REAL DEFAULT 0, result TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, monitor_id INTEGER, created REAL, kind TEXT, message TEXT);
        CREATE TABLE IF NOT EXISTS alerts(monitor_id INTEGER, offer_id TEXT, price INTEGER, sent REAL, PRIMARY KEY(monitor_id,offer_id));
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, monitor_id INTEGER, created REAL, price INTEGER, demo INTEGER);''')

def config():
    p=DATA/'config.json'
    return json.loads(p.read_text('utf-8')) if p.exists() else json.loads(json.dumps(DEFAULT))

def install_provider_presets():
    """One-time, additive migration. Preserve all existing sources and credentials."""
    marker=DATA/'providers-v2.json'
    if marker.exists(): return
    cfg=config()
    for p in presets():
        if not any(x.get('catalog_id')==p['catalog_id'] or x['name']==p['name'] for x in cfg['providers']):
            cfg['providers'].append(p)
    (DATA/'config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),'utf-8')
    marker.write_text(json.dumps({'installed':time.time()}),'utf-8')

def event(mid,kind,message):
    with db() as c:
        c.execute('INSERT INTO events(monitor_id,created,kind,message) VALUES(?,?,?,?)',(mid,time.time(),kind,message))

def validate_route(v):
    origin=airports.resolve(v['origin']);dest=airports.resolve(v['destination'])
    if set(origin['codes']) & set(dest['codes']):raise ValueError('始发和目的机场范围不能重叠')
    start=date.fromisoformat(v['date_from']); end=date.fromisoformat(v.get('date_to') or v['date_from'])
    if start<date.today() or end<start or (end-start).days>30:
        raise ValueError('日期须从今天起，区间最多 31 天')
    count=len(origin['codes'])*len(dest['codes'])*((end-start).days+1)*(2 if v.get('connections',True) else 1)
    if count>120:raise ValueError(f'当前选择需 {count} 次航线查询，超过单次 120 次限制；请缩小日期范围或选择具体机场')
    return dict(origin=origin['id'],destination=dest['id'],origin_airports=origin['codes'],destination_airports=dest['codes'],
                origin_label=origin['label'],destination_label=dest['label'],date_from=str(start),date_to=str(end),
                connections=bool(v.get('connections',True)),self_transfer=bool(v.get('self_transfer',False)),demo=bool(v.get('demo',False)))

def validate_monitor(v):
    route=validate_route(v)
    email=v['email'].strip()
    if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',email):
        raise ValueError('请输入有效提醒邮箱')
    target=money(v['target'])
    if target<=0: raise ValueError('目标价必须大于零')
    interval=int(v.get('interval',60))
    if not 15<=interval<=1440: raise ValueError('检查间隔应为 15–1440 分钟')
    return dict(**route,target=target,email=email,interval=interval)

def demo(m):
    if m['origin'].startswith('CITY:') or m['destination'].startswith('CITY:'):
        return [o for a in airports.scope(m,'origin') for b in airports.scope(m,'destination')
                for o in demo({**m,'origin':a,'destination':b,'origin_airports':[a],'destination_airports':[b]})]
    day=m['date_from']; hub=next(x for x in ['WUH','XIY','CKG'] if x not in (m['origin'],m['destination']))
    def leg(a,b,dep,arr,flight,base):
        return {'currency':'CNY','base_fare':base,'taxes_included':False,'segments':[{'origin':a,'destination':b,'departure':day+'T'+dep+':00+08:00','arrival':day+'T'+arr+':00+08:00','flight':flight,'carrier':'DEMO','airport_fee':50,'fuel_fee':60}]}
    direct=leg(m['origin'],m['destination'],'08:00','10:20','演示 101',580)
    a=leg(m['origin'],hub,'07:00','09:00','演示 201',180)
    b=leg(hub,m['destination'],'12:30','14:30','演示 202',160)
    through={'currency':'CNY','base_fare':370,'taxes_included':False,'segments':a['segments']+b['segments']}
    return [normalize(o,'演示数据 · 非真实票价',[]) for o in [direct,a,b,through]]

class OfferBatch(list):
    def __init__(self,values,hints):
        super().__init__(values); self.hints=hints

def fetch(provider,m):
    values,hints=search(provider,m)
    return OfferBatch(values,hints)

def candidate(raw,source):
    return {'source':source,'display_price':money(raw['base_fare']),'segments':raw['segments'],
            'url':raw.get('url',''),'note':raw.get('price_note','含税总价未确认'),
            'transfer_unverified':raw.get('transfer_unverified',False)}

def send_mail(cfg,recipient,subject,body):
    s=cfg['smtp']
    if not s.get('host') or not s.get('sender'): raise ValueError('SMTP 尚未配置')
    message=EmailMessage(); message['From']=s['sender']; message['To']=recipient; message['Subject']=subject; message.set_content(body)
    context=ssl.create_default_context()
    if s['security']=='ssl':
        client=smtplib.SMTP_SSL(s['host'],int(s['port']),timeout=20,context=context)
    else:
        client=smtplib.SMTP(s['host'],int(s['port']),timeout=20); client.starttls(context=context)
    with client:
        if s.get('username'): client.login(s['username'],s['password'])
        client.send_message(message)

def collect(m,cfg):
    offers=[];errors=[];candidates=[];hints=[]
    if m['date_to']<date.today().isoformat():raise ValueError('出发日期已过期，请重新选择')
    if m['demo']: offers=demo(m)
    else:
        providers=[p for p in cfg['providers'] if p.get('enabled')]
        if not providers:raise ValueError('待配置：尚未启用真实票价接口')
        for p in providers:
            try:
                raw=fetch(p,m);hints.extend(getattr(raw,'hints',[]))
                for o in raw:
                    try:
                        if o.get('price_status')=='unverified':candidates.append(candidate(o,p['name']))
                        else:offers.append(normalize(o,p['name'],cfg['fee_rules']))
                    except Exception as e:errors.append(p['name']+'：报价不可用（'+str(e)[:160]+'）')
            except Exception as e:
                errors.append(p['name']+'：'+(str(e) if isinstance(e,ValueError) else '接口请求失败（'+type(e).__name__+'）'))
    result=compare(offers,m)
    result.update(demo=m['demo'],warnings=errors[:20],candidates=sorted(candidates,key=lambda x:x['display_price']),
                  hints=sorted(set(hints)),flights=flight_board(offers,candidates,m),checked=time.time(),
                  coverage='仅覆盖已接入来源本轮返回的直飞航班，不是全市场完整时刻表。飞猪单次最多返回 10 条结果。')
    return result

def query_job(qid,m):
    try:
        result=collect(m,config())
        with LOCK:QUERIES[qid].update(status='done',result=result)
    except Exception as e:
        with LOCK:QUERIES[qid].update(status='error',error=str(e)[:250])

def run(mid):
    try:
        with db() as c: row=c.execute('SELECT * FROM monitors WHERE id=?',(mid,)).fetchone()
        if not row or not row['enabled']: return
        m=json.loads(row['body']);cfg=config();result=collect(m,cfg);errors=result['warnings']
        all_offers=sorted(result['direct']+result['connections'],key=lambda x:x['total'])
        with db() as c:
            c.execute('UPDATE monitors SET checked=?,result=?,error=? WHERE id=?',(time.time(),json.dumps(result,ensure_ascii=False),'；'.join(errors[:3]),mid))
            if all_offers: c.execute('INSERT INTO history(monitor_id,created,price,demo) VALUES(?,?,?,?)',(mid,time.time(),all_offers[0]['total'],m['demo']))
        event(mid,'demo' if m['demo'] else 'check',f"检查完成：{len(result['direct'])} 个直飞 / {len(result['connections'])} 个转机方案 / {len(result['candidates'])} 个费用待确认报价" + ('；存在接口或费用错误' if errors else ''))
        if m['demo']: return
        for o in all_offers[:1]:
            if o['total']>=m['target']: continue
            with db() as c:
                old=c.execute('SELECT * FROM alerts WHERE monitor_id=? AND offer_id=?',(mid,o['id'])).fetchone()
                active=c.execute('SELECT enabled FROM monitors WHERE id=?',(mid,)).fetchone()
            if not active or not active['enabled']: return
            if old and (old['price']<=o['total'] or time.time()-old['sent']<3600): continue
            route='\n'.join(f"{s.get('flight','')} {s['origin']} → {s['destination']} {s['departure']} / {s['arrival']}" for s in o['segments'])
            body=f"发现低于目标价的机票：¥{o['total']/100:.2f} / 成人单程\n目标价：¥{m['target']/100:.2f}\n{route}\n票面 ¥{o['base']/100:.2f}；税费 ¥{o['taxes']/100:.2f}；其他必付费用 ¥{o['extra']/100:.2f}\n来源：{o['source']}\n"+'\n'.join(o['breakdown'])+'\n'+('分开出票，需自行提取行李并重新值机，误机衔接不受联程保障。\n' if o['self_transfer'] else '')+'价格与余票可能变化，请在购买前再次核验。'
            try:
                send_mail(cfg,m['email'],'低价机票提醒 '+m['origin']+' → '+m['destination'],body)
                with db() as c: c.execute('INSERT OR REPLACE INTO alerts VALUES(?,?,?,?)',(mid,o['id'],o['total'],time.time()))
                event(mid,'sent','低价邮件已发送至 '+m['email'])
            except Exception as e: event(mid,'error','邮件发送失败（'+type(e).__name__+'），下次检查将重试')
    except Exception as e:
        with db() as c: c.execute('UPDATE monitors SET checked=?,error=? WHERE id=?',(time.time(),str(e)[:200],mid))
        event(mid,'error',str(e)[:200])
    finally:
        with LOCK: RUNNING.discard(mid)

def queue(mid):
    with LOCK:
        if mid in RUNNING: return
        RUNNING.add(mid)
    POOL.submit(run,mid)

def scheduler():
    while True:
        with db() as c: rows=c.execute('SELECT * FROM monitors WHERE enabled=1').fetchall()
        for r in rows:
            m=json.loads(r['body'])
            if m['date_to']>=date.today().isoformat() and time.time()-r['checked']>=m['interval']*60: queue(r['id'])
        time.sleep(10)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
    def trusted(self):
        allowed={'127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)}
        return self.headers.get('Host') in allowed and (not self.headers.get('Origin') or self.headers['Origin'] in {'http://'+x for x in allowed})
    def do_GET(self):
        if not self.trusted(): return self.respond({'error':'禁止跨站访问'},403)
        if self.path=='/api/health':
            return self.respond({'app':'flight-ticket-radar','status':'ready'})
        if self.path.startswith('/api/airports?'):
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return self.respond(dict(items=airports.search(params.get('q',[''])[0],all_records=params.get('all',['0'])[0]=='1'),stats=airports.stats()))
        if self.path.startswith('/api/flights/'):
            with LOCK:value=dict(QUERIES.get(self.path.split('/')[-1],{}))
            return self.respond(value if value else {'error':'查询不存在或已过期'},200 if value else 404)
        if self.path=='/api/state':
            with db() as c:
                rows=c.execute('SELECT * FROM monitors ORDER BY id DESC').fetchall()
                monitors=[dict(id=r['id'],**json.loads(r['body']),enabled=bool(r['enabled']),checked=r['checked'],result=json.loads(r['result']) if r['result'] else None,error=r['error'],running=r['id'] in RUNNING) for r in rows]
                events=[dict(r) for r in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 60')]
                history=[dict(r) for r in c.execute('SELECT * FROM history ORDER BY id DESC LIMIT 500')]
            return self.respond(dict(monitors=monitors,events=events,history=history,airports=AIRPORTS,airport_stats=airports.stats()))
        if self.path=='/api/config':
            cfg=config(); cfg['smtp']['password']=''
            for p in cfg['providers']:
                p['has_token']=bool(p.get('token')); p['token']=''
                p['health']=PROVIDER_HEALTH.get(p['name'])
            cfg['catalog']=CATALOG
            return self.respond(cfg)
        files={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/explore.js':'explore.js'}
        if self.path not in files: return self.respond({'error':'未找到'},404)
        p=ROOT/'static'/files[self.path]; body=p.read_bytes(); self.send_response(200)
        self.send_header('Content-Type',{'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[p.suffix[1:]])
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if not self.trusted(): return self.respond({'error':'禁止跨站访问'},403)
        try:
            size=int(self.headers.get('Content-Length',0))
            if size>200000: raise ValueError('请求过大')
            v=json.loads(self.rfile.read(size))
            if self.path=='/api/flights':
                m=validate_route({**v,'connections':False,'self_transfer':False})
                qid=uuid.uuid4().hex
                with LOCK:
                    for old in list(QUERIES):
                        if QUERIES[old]['status']!='running' and time.time()-QUERIES[old]['created']>3600:del QUERIES[old]
                    if sum(q['status']=='running' for q in QUERIES.values())>=2:raise ValueError('已有两项查询运行中，请等待完成')
                    if len(QUERIES)>=30:
                        old=next((k for k,x in QUERIES.items() if x['status']!='running'),None)
                        if old:del QUERIES[old]
                    QUERIES[qid]=dict(id=qid,status='running',created=time.time(),route=m)
                POOL.submit(query_job,qid,m)
                return self.respond({'id':qid},202)
            if self.path=='/api/monitors':
                m=validate_monitor(v)
                with db() as c: mid=c.execute('INSERT INTO monitors(body) VALUES(?)',(json.dumps(m,ensure_ascii=False),)).lastrowid
                queue(mid); return self.respond({'id':mid})
            if self.path=='/api/action':
                mid=int(v['id'])
                with db() as c:
                    row=c.execute('SELECT * FROM monitors WHERE id=?',(mid,)).fetchone()
                    if not row: raise ValueError('监控不存在')
                    if v['action']=='toggle': c.execute('UPDATE monitors SET enabled=1-enabled WHERE id=?',(mid,))
                    elif v['action']=='delete':
                        if mid in RUNNING: raise ValueError('检查进行中，请稍后删除')
                        for table in ['events','history','alerts']: c.execute(f'DELETE FROM {table} WHERE monitor_id=?',(mid,))
                        c.execute('DELETE FROM monitors WHERE id=?',(mid,))
                    elif v['action']!='check': raise ValueError('未知操作')
                if v['action']=='check': queue(mid)
                return self.respond({'ok':True})
            if self.path=='/api/config':
                old=config()
                if v['smtp']['security'] not in ['ssl','starttls']: raise ValueError('SMTP 必须启用 TLS')
                v['smtp']['port']=int(v['smtp']['port'])
                if not 1<=v['smtp']['port']<=65535: raise ValueError('SMTP 端口无效')
                if not v['smtp'].get('password'): v['smtp']['password']=old['smtp'].get('password','')
                names=set()
                for p in v['providers']:
                    if not p['name'].strip() or p['name'] in names: raise ValueError('接口名称须唯一且非空')
                    names.add(p['name'])
                    validate_provider(p,old['providers'])
                for r in v['fee_rules']:
                    if not r.get('source') or not r.get('carrier'): raise ValueError('费用规则需要航司代码和来源')
                    if date.fromisoformat(r['valid_from'])>date.fromisoformat(r['valid_to']): raise ValueError('费用规则日期无效')
                    for key in ['airport_fee','fuel_short','fuel_long']: money(r[key])
                temp=DATA/'config.tmp'; temp.write_text(json.dumps(v,ensure_ascii=False,indent=2),'utf-8'); temp.replace(DATA/'config.json')
                return self.respond({'ok':True})
            if self.path=='/api/provider-test':
                cfg=config(); provider=next((p for p in cfg['providers'] if p['name']==v['name']),None)
                if not provider: raise ValueError('请先保存来源配置')
                m=validate_monitor(dict(origin=v['origin'],destination=v['destination'],date_from=v['date'],
                    target=1,email='probe@example.com',connections=False,self_transfer=False))
                try:
                    raw=fetch(provider,m); usable=0; pending=0
                    for o in raw:
                        try: normalize(o,provider['name'],cfg['fee_rules']); usable+=1
                        except Exception: pending+=1
                    health=dict(ok=True,checked=time.time(),message=f'查询成功：{len(raw)} 条匹配机场报价，{usable} 条含税价可用，{pending} 条费用待确认',hints=getattr(raw,'hints',[]))
                except Exception as e:
                    health=dict(ok=False,checked=time.time(),message=str(e) if isinstance(e,ValueError) else '连接失败（'+type(e).__name__+'）')
                PROVIDER_HEALTH[provider['name']]=health
                return self.respond(health)
            if self.path=='/api/test-email':
                if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',v['email']): raise ValueError('邮箱格式无效')
                try: send_mail(config(),v['email'],'机票雷达 · 测试邮件','邮件配置成功。真实报价低于目标含税价时，将通过此邮箱发送提醒。')
                except Exception as e: raise ValueError('测试发送失败：'+type(e).__name__)
                return self.respond({'ok':True})
            return self.respond({'error':'未找到'},404)
        except Exception as e: return self.respond({'error':str(e)[:250]},400)

def main():
    try:
        port=int(os.environ.get('PORT','8765'))
        if not 1<=port<=65535:raise ValueError('PORT 必须是 1–65535 的整数')
        server=bind_server(Handler,port)
        if server is None:return 0
    except (ValueError,OSError) as e:
        print(f'启动失败：{e}',flush=True);return 1
    with server:
        from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
        try:ZoneInfo('Asia/Shanghai')
        except ZoneInfoNotFoundError:
            print('当前 Python 缺少 tzdata 时区库。请运行 .\\start.ps1 使用项目虚拟环境，或安装 requirements.txt。',flush=True)
            return 1
        init(); install_provider_presets(); threading.Thread(target=scheduler,daemon=True).start()
        print(f'Flight Radar running at http://127.0.0.1:{port}',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:print('\n机票雷达已停止。',flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
