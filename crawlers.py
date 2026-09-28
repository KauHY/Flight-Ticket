"""Public-page readers with persistent caching, robots checks and host rate limits."""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
import urllib.robotparser
from datetime import date, timedelta
from pathlib import Path
from airports import local_time, scope
from engine import money, stamp

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('FLIGHT_DATA', str(ROOT/'data')))
LOCK = threading.Lock()
AGENT = 'FlightTicketMonitor'
TTL = 900

@contextmanager
def cache_db():
    db=sqlite3.connect(DATA/'crawler-cache.db',timeout=20)
    try:
        with db:
            yield db
    finally:
        db.close()

HOSTS = {'tongcheng': 'https://www.ly.com', 'spring': 'https://flights.ch.com'}

def cached_page(site, url):
    """Reserve host slot atomically; never bypass robots or serve expired quotes."""
    DATA.mkdir(exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    with LOCK, cache_db() as db:
        db.executescript('CREATE TABLE IF NOT EXISTS pages(key TEXT PRIMARY KEY, fetched REAL, body TEXT); CREATE TABLE IF NOT EXISTS hosts(site TEXT PRIMARY KEY, next REAL, robots TEXT, checked REAL);')
        now = time.time()
        row = db.execute('SELECT fetched,body FROM pages WHERE key=?', (key,)).fetchone()
        if row and now-row[0]<TTL:
            return json.loads(row[1]), row[0], True
        db.execute('BEGIN IMMEDIATE')
        host = db.execute('SELECT next,robots,checked FROM hosts WHERE site=?', (site,)).fetchone()
        if host and host[0]>now:
            raise ValueError(f'网站限速保护：约 {int(host[0]-now)+1} 秒后可查询下一页；请缩小日期范围或稍后重试')
        robots = host[1] if host and now-host[2]<86400 else None
        checked = host[2] if host else 0
        # Reserve before network access, including failed requests and concurrent processes.
        db.execute('INSERT OR REPLACE INTO hosts VALUES(?,?,?,?)', (site, now+120, robots, checked))
        db.commit()
    if robots is None:
        try:
            req = urllib.request.Request(HOSTS[site]+'/robots.txt', headers={'User-Agent': AGENT+'/1.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                robots = response.read(200001).decode('utf-8')
            if len(robots)>200000 or '<html' in robots.lower(): raise ValueError()
        except Exception:
            raise ValueError('无法核验网站 robots 规则，本轮停止抓取') from None
        checked = time.time()
    policy = urllib.robotparser.RobotFileParser()
    policy.parse(robots.splitlines())
    delay = max(120, policy.crawl_delay(AGENT) or 0)
    with cache_db() as db:
        db.execute('UPDATE hosts SET next=?,robots=?,checked=? WHERE site=?', (time.time()+delay, robots, checked, site))
    if not policy.can_fetch(AGENT, url): raise ValueError('网站 robots 规则不允许抓取此页面')
    node = shutil.which('node')
    if not node or not (ROOT/'node_modules/playwright').exists():
        raise ValueError('请安装 Node.js、Microsoft Edge，并运行 npm ci --ignore-scripts')
    try:
        result = subprocess.run([node, str(ROOT/'scripts/crawl_prices.cjs'), json.dumps({'site':site,'url':url})],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=65,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except subprocess.TimeoutExpired:
        raise ValueError('网站查询超时，本轮未获取报价') from None
    if result.returncode: raise ValueError('网站查询失败（可能访问受限、页面变化或 Edge 未安装）；未尝试绕过限制')
    if len(result.stdout)>5000000: raise ValueError('页面数据过大')
    payload = json.loads(result.stdout)
    fetched = time.time()
    with cache_db() as db:
        db.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?)',(key,fetched,json.dumps(payload)))
    return payload, fetched, False

def parse_tongcheng(rows, monitor, url):
    offers=[]
    for row in rows:
        try:
            a=row['originAirportCode']; b=row['arriveAirportCode']; flight=row['flightNo']
            if a not in scope(monitor,'origin') or b not in scope(monitor,'destination'): continue
            if row.get('stopNum')!=0 or not re.fullmatch(r'[A-Z0-9]{2}\d{2,5}',flight): continue
            departure=local_time(row['flyOffTime'],a); arrival=local_time(row['arrivalTime'],b)
            if not monitor['date_from']<=departure[:10]<=monitor['date_to'] or stamp(arrival)<=stamp(departure): continue
            if money(row['flightPrice'])<=0: continue
            offers.append(dict(currency='CNY',base_fare=row['flightPrice'],url=url,price_status='unverified',
                price_note='同程页面起价，完整机建燃油及必付费用待确认；不参与含税比价和邮件提醒',
                segments=[dict(origin=a,destination=b,departure=departure,arrival=arrival,flight=flight,
                    carrier=row.get('airCompanyCode') or flight[:2],airline=row.get('airCompanyName',''))]))
        except (KeyError, TypeError, ValueError, ArithmeticError): continue
    return offers

def search_pages(provider, monitor):
    site=provider['kind']; offers=[]; hints=[]; tasks={}
    for a in scope(monitor,'origin'):
        for b in scope(monitor,'destination'):
            if not re.fullmatch('[A-Z]{3}',a) or not re.fullmatch('[A-Z]{3}',b): continue
            day=date.fromisoformat(monitor['date_from']); end=date.fromisoformat(monitor['date_to'])
            while day<=end:
                url=(f'{HOSTS[site]}/flights/itinerary/oneway/{a}-{b}?date={day}' if site=='tongcheng'
                     else f'{HOSTS[site]}/{a}-{b}/')
                tasks[url]=day
                day+=timedelta(days=1)
    # Oldest/missing pages first so scheduled date ranges progress across runs.
    fetched_by_key={}
    if (DATA/'crawler-cache.db').exists():
        with cache_db() as db:
            fetched_by_key=dict(db.execute('SELECT key,fetched FROM pages'))
    ordered=sorted(tasks,key=lambda u:fetched_by_key.get(hashlib.sha256(u.encode()).hexdigest(),0))
    successes=0
    for url in ordered:
        try:
            payload, fetched, cached=cached_page(site,url); successes+=1
            label='同程' if site=='tongcheng' else '春秋官网'
            hints.append(f'{label}采集于 {time.strftime("%m-%d %H:%M:%S",time.localtime(fetched))}'+('（15 分钟内缓存）' if cached else ''))
            if site=='tongcheng': offers.extend(parse_tongcheng(payload['rows'],monitor,url))
            else:
                deals={r['Date']:r['Price'] for r in payload.get('calendar',[]) if monitor['date_from']<=r.get('Date','')<=monitor['date_to'] and r.get('Price') is not None and money(r['Price'])>0}
                for day,price in sorted(deals.items()):
                    hints.append(f'春秋官网航线日历 {url.split("/")[-2]} · {day} · ¥{price} 起。仅城市航线参考，机场、航班和税费未确认，不参与逐航班比价或提醒。查看：{url}')
                if not deals: hints.append('春秋官网当前日历未覆盖所选日期或没有报价；不代表没有航班。')
        except ValueError as exc: hints.append(str(exc))
    hints.append(f'{"同程" if site=="tongcheng" else "春秋官网"}本轮覆盖 {successes}/{len(tasks)} 个页面；每站至少间隔 120 秒，未覆盖的日期和机场需稍后单独查询。')
    if not successes: raise ValueError('；'.join(dict.fromkeys(hints)))
    if site=='tongcheng': hints.append('同程仅提取页面返回的直飞航班；经停和转机暂不抓取。')
    return offers,list(dict.fromkeys(hints))
