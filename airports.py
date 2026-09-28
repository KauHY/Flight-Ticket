"""Offline SQLite airport directory, explicit city groups and timezone conversion."""
import csv
import hashlib
import json
import os
import re
import sqlite3
import threading
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('FLIGHT_DATA',str(ROOT/'data')))/'airports.db'
LOCK=threading.Lock()
NAMES={'PEK':'北京首都','PKX':'北京大兴','SHA':'上海虹桥','PVG':'上海浦东','CAN':'广州白云','SZX':'深圳宝安','CTU':'成都双流','TFU':'成都天府','CKG':'重庆江北','HGH':'杭州萧山','XIY':'西安咸阳','KMG':'昆明长水','WUH':'武汉天河','CSX':'长沙黄花','NKG':'南京禄口','XMN':'厦门高崎','TAO':'青岛胶东','DLC':'大连周水子','SYX':'三亚凤凰','HAK':'海口美兰','CGO':'郑州新郑','TSN':'天津滨海','SHE':'沈阳桃仙','HRB':'哈尔滨太平','URC':'乌鲁木齐天山','LXA':'拉萨贡嘎','FOC':'福州长乐','TNA':'济南遥墙','NGB':'宁波栎社','KWE':'贵阳龙洞堡','NNG':'南宁吴圩','HKG':'香港国际','MFM':'澳门国际','TPE':'台北桃园','TSA':'台北松山','NRT':'东京成田','HND':'东京羽田','KIX':'大阪关西','ITM':'大阪伊丹','ICN':'首尔仁川','GMP':'首尔金浦','SIN':'新加坡樟宜','BKK':'曼谷素万那普','DMK':'曼谷廊曼','LHR':'伦敦希思罗','LGW':'伦敦盖特威克','JFK':'纽约肯尼迪','LGA':'纽约拉瓜迪亚','EWR':'纽瓦克自由','CDG':'巴黎戴高乐','ORY':'巴黎奥利','DXB':'迪拜国际','DWC':'迪拜阿勒马克图姆'}
CITY_ZH={'BJS':'北京','SHA':'上海','CTU':'成都','TYO':'东京','OSA':'大阪','SEL':'首尔','LON':'伦敦','NYC':'纽约','PAR':'巴黎','DXB':'迪拜','BKK':'曼谷','TPE':'台北','MOW':'莫斯科','YTO':'多伦多','ROM':'罗马','MIL':'米兰','MEL':'墨尔本'}

def key(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',s).casefold() if not unicodedata.combining(c))

@contextmanager
def connection():
    c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
    try:
        with c: yield c
    finally:c.close()

def initialize():
    with LOCK:
        DB.parent.mkdir(parents=True,exist_ok=True)
        with connection() as c:
            c.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)')
            if c.execute("SELECT 1 FROM metadata WHERE key='version' AND value='1'").fetchone():return
            c.executescript('''CREATE TABLE IF NOT EXISTS airports(icao TEXT PRIMARY KEY,iata TEXT,name TEXT,city TEXT,region TEXT,country TEXT,timezone TEXT,latitude REAL,longitude REAL,label TEXT,search TEXT);
            CREATE INDEX IF NOT EXISTS airport_iata ON airports(iata);
            CREATE INDEX IF NOT EXISTS airport_search ON airports(search);
            CREATE TABLE IF NOT EXISTS cities(id TEXT PRIMARY KEY,code TEXT,name TEXT,country TEXT,label TEXT,airports TEXT,search TEXT);''')
            with (ROOT/'resources/airports.csv').open(encoding='utf-8',newline='') as f: rows=list(csv.DictReader(f))
            if len(rows)<10000:raise ValueError('机场数据不完整，至少应有 10000 条')
            c.execute('DELETE FROM airports');c.execute('DELETE FROM cities')
            by_iata={r['iata']:r for r in rows if r['iata']}
            records=[]
            for r in rows:
                label=NAMES.get(r['iata'],r['name'])
                records.append((r['icao'],r['iata'],r['name'],r['city'],r['subd'],r['country'],r['tz'],float(r['lat']),float(r['lon']),label,key(' '.join([r['icao'],r['iata'],r['name'],r['city'],r['subd'],r['country'],label]))))
            c.executemany('INSERT INTO airports VALUES(?,?,?,?,?,?,?,?,?,?,?)',records)
            groups={}
            with (ROOT/'resources/iata_macs.csv').open(encoding='utf-8',newline='') as f:
                for r in csv.DictReader(f):
                    g=groups.setdefault(r['City Code'],dict(name=r['City Name'],country=r['Country'],airports=[]))
                    if r['Airport Code'] in by_iata:g['airports'].append(r['Airport Code'])
            # Chengdu is not present in all upstream MAC snapshots; make the scope explicit.
            groups['CTU']=dict(name='Chengdu',country='CN',airports=[x for x in ['CTU','TFU'] if x in by_iata])
            occupied={a for g in groups.values() for a in g['airports']}
            local=defaultdict(list)
            for r in rows:
                if r['iata'] and r['iata'] not in occupied and r['city']:
                    local[(r['country'],r['subd'],r['city'])].append(r['iata'])
            for (country,region,city),codes in local.items():
                if len(codes)>1:
                    code=hashlib.sha256((country+'|'+region+'|'+city).encode()).hexdigest()[:12].upper()
                    groups[code]=dict(name=city,country=country,airports=codes)
            for code,g in groups.items():
                codes=sorted(set(g['airports']))
                if len(codes)<2:continue
                label=CITY_ZH.get(code,g['name'])+' · 全部机场'
                terms=key(' '.join([code,g['name'],g['country'],label,*codes,*[NAMES.get(a,'') for a in codes]]))
                c.execute('INSERT INTO cities VALUES(?,?,?,?,?,?,?)',('CITY:'+code,code,g['name'],g['country'],label,json.dumps(codes),terms))
            c.execute("INSERT OR REPLACE INTO metadata VALUES('version','1')")
            c.execute("INSERT OR REPLACE INTO metadata VALUES('source','airportsdata / MIT')")
        airport.cache_clear()

@lru_cache(maxsize=10000)
def airport(code):
    with connection() as c:
        row=c.execute('SELECT * FROM airports WHERE iata=? OR icao=? ORDER BY iata=? DESC LIMIT 1',(code,code,code)).fetchone()
    return dict(row) if row else None

def stats():
    with connection() as c:
        return dict(total=c.execute('SELECT COUNT(*) FROM airports').fetchone()[0],
                    selectable=c.execute("SELECT COUNT(*) FROM airports WHERE iata<>''").fetchone()[0],
                    countries=c.execute('SELECT COUNT(DISTINCT country) FROM airports').fetchone()[0],
                    cities=c.execute('SELECT COUNT(*) FROM cities').fetchone()[0],source='airportsdata / MIT')

def search(term,limit=30,all_records=False):
    q=key(term.strip())[:100];pattern='%'+q.replace('!','!!').replace('%','!%').replace('_','!_')+'%'
    with connection() as c:
        cities=c.execute("SELECT * FROM cities WHERE search LIKE ? ESCAPE '!' ORDER BY code=? DESC,label LIMIT 10",(pattern,term.upper())).fetchall()
        sql="SELECT * FROM airports WHERE search LIKE ? ESCAPE '!'"+("" if all_records else " AND iata<>''")+" ORDER BY iata=? DESC,icao=? DESC,iata<>'' DESC,name LIMIT ?"
        airports=c.execute(sql,(pattern,term.upper(),term.upper(),limit)).fetchall()
    output=[dict(id=r['id'],kind='city',label=r['label'],country=r['country'],codes=json.loads(r['airports']),selectable=True) for r in cities]
    output += [dict(id=r['iata'] or 'ICAO:'+r['icao'],kind='airport',label=r['label'],name=r['name'],city=r['city'],country=r['country'],icao=r['icao'],iata=r['iata'],timezone=r['timezone'],codes=[r['iata']] if r['iata'] else [],selectable=bool(r['iata'])) for r in airports]
    return sorted(output,key=lambda r:0 if r['id']==term.strip().upper() else 1 if r['kind']=='city' else 2)

def resolve(value):
    value=value.strip().upper()
    if value.startswith('CITY:'):
        with connection() as c:r=c.execute('SELECT * FROM cities WHERE id=?',(value,)).fetchone()
        if not r:raise ValueError('城市分组不存在，请从搜索结果选择')
        return dict(id=value,label=r['label'],codes=json.loads(r['airports']))
    a=airport(value)
    if not a or not a['iata']:raise ValueError('请选择有 IATA 三字码的机场，或搜索结果中的城市全部机场')
    return dict(id=a['iata'],label=a['label'],codes=[a['iata']])

def scope(m,side):
    return m.get(side+'_airports') or [m[side]]

def local_time(value,code):
    d=datetime.fromisoformat(value)
    if d.tzinfo:return d.isoformat()
    a=airport(code)
    if not a:raise ValueError('未知机场时区')
    try:z=ZoneInfo(a['timezone'])
    except ZoneInfoNotFoundError:raise ValueError('缺少时区数据库，请安装 requirements.txt') from None
    dt=d.replace(tzinfo=z)
    if dt.utcoffset()!=d.replace(tzinfo=z,fold=1).utcoffset() or dt.astimezone(timezone.utc).astimezone(z).replace(tzinfo=None)!=d:
        raise ValueError('航班当地时间存在夏令时歧义，需来源提供明确偏移')
    return dt.isoformat()
