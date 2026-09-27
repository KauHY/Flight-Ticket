"""Flight normalization, fee accounting and connection construction."""
from datetime import datetime, date
from decimal import Decimal
import hashlib

AIRPORTS = {'PEK':'北京首都','PKX':'北京大兴','SHA':'上海虹桥','PVG':'上海浦东','CAN':'广州','SZX':'深圳','CTU':'成都双流','TFU':'成都天府','CKG':'重庆','HGH':'杭州','XIY':'西安','KMG':'昆明','WUH':'武汉','CSX':'长沙','NKG':'南京','XMN':'厦门','TAO':'青岛','DLC':'大连','SYX':'三亚','HAK':'海口','CGO':'郑州','TSN':'天津','SHE':'沈阳','HRB':'哈尔滨','URC':'乌鲁木齐','LXA':'拉萨','FOC':'福州','TNA':'济南','NGB':'宁波','KWE':'贵阳','NNG':'南宁'}

def money(value):
    d = Decimal(str(value))
    if not d.is_finite() or d < 0 or d > 10000000:
        raise ValueError('金额必须是有效非负数')
    return int((d * 100).quantize(Decimal('1')))

def stamp(s):
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        raise ValueError('航班时间必须包含时区')
    return d

def normalize(raw, source, rules):
    if raw.get('price_status') == 'unverified':
        raise ValueError('价格口径未确认，不能当作含税总价')
    if raw.get('currency') != 'CNY':
        raise ValueError('接口必须返回人民币 CNY 报价')
    segs = raw['segments']
    if not 1 <= len(segs) <= 2:
        raise ValueError('仅支持直飞或一次转机')
    for i,s in enumerate(segs):
        if s['origin'] == s['destination'] or stamp(s['arrival']) <= stamp(s['departure']):
            raise ValueError('航段时间或机场无效')
        if i and (segs[i-1]['destination'] != s['origin'] or stamp(s['departure']) <= stamp(segs[i-1]['arrival'])):
            raise ValueError('转机航段不连续')
    base = money(raw['base_fare'])
    extra = money(raw.get('mandatory_fees', 0))
    breakdown=[]
    if raw.get('taxes_included') is True:
        total = money(raw['total'])
        if total < base + extra:
            raise ValueError('含税总价小于票面价及必付费用')
        taxes = total-base-extra
        breakdown.append('供应商确认含全部税费')
    else:
        taxes = 0
        for s in segs:
            if s.get('airport_fee') is not None and s.get('fuel_fee') is not None:
                fee=money(s['airport_fee'])+money(s['fuel_fee'])
            else:
                if s['origin'] not in AIRPORTS or s['destination'] not in AIRPORTS:
                    raise ValueError('国际/港澳台或未知机场必须提供完整含税价')
                # Fee rules are matched by ticketing date and carrier, not travel date.
                today=date.today().isoformat()
                matches=[r for r in rules if r['carrier']==s.get('carrier') and r['valid_from'] <= today <= r['valid_to']]
                if len(matches)!=1 or not s.get('distance_km'):
                    raise ValueError('缺少有效机建燃油规则或航段里程，报价不参与提醒')
                r=matches[0]
                fee=money(r['airport_fee'])+money(r['fuel_short'] if float(s['distance_km'])<=800 else r['fuel_long'])
            taxes+=fee
            breakdown.append(f"{s['origin']} → {s['destination']} 机建燃油 ¥{fee/100:.2f}")
        total=base+taxes+extra
    result={'source':source,'segments':segs,'base':base,'taxes':taxes,'extra':extra,'total':total,'breakdown':breakdown,'self_transfer':bool(raw.get('self_transfer',False)),'url':raw.get('url','')}
    identity=source+'|'+str([(s['origin'],s['destination'],s['departure'],s.get('flight','')) for s in segs])
    result['id']=hashlib.sha256(identity.encode()).hexdigest()[:24]
    return result

def compare(offers, monitor):
    direct=[]; connections=[]
    for o in offers:
        segs=o['segments']
        if segs[0]['origin']!=monitor['origin'] or segs[-1]['destination']!=monitor['destination']:
            continue
        day=stamp(segs[0]['departure']).date().isoformat()
        if not monitor['date_from'] <= day <= monitor['date_to']:
            continue
        if len(segs)==1:
            direct.append(o)
        elif monitor['connections']:
            gap=(stamp(segs[1]['departure'])-stamp(segs[0]['arrival'])).total_seconds()/60
            if (180 if o['self_transfer'] else 90)<=gap<=480 and (not o['self_transfer'] or monitor['self_transfer']):
                connections.append(o)
    if monitor['connections'] and monitor['self_transfer']:
        singles=[o for o in offers if len(o['segments'])==1]
        for a in singles:
            x=a['segments'][0]
            if x['origin']!=monitor['origin'] or not monitor['date_from']<=stamp(x['departure']).date().isoformat()<=monitor['date_to']:
                continue
            for b in singles:
                y=b['segments'][0]
                if x['destination']!=y['origin'] or y['destination']!=monitor['destination'] or x['destination'] in (monitor['origin'],monitor['destination']):
                    continue
                gap=(stamp(y['departure'])-stamp(x['arrival'])).total_seconds()/60
                if 180<=gap<=480:
                    connections.append({'id':a['id']+b['id'],'source':a['source']+' + '+b['source'],'segments':[x,y],'base':a['base']+b['base'],'taxes':a['taxes']+b['taxes'],'extra':a['extra']+b['extra'],'total':a['total']+b['total'],'self_transfer':True,'url':'','breakdown':a['breakdown']+b['breakdown']})
    direct.sort(key=lambda o:o['total']); connections.sort(key=lambda o:o['total'])
    best_direct=direct[0]['total'] if direct else None
    for o in connections:
        same_day=[d['total'] for d in direct if d['segments'][0]['departure'][:10]==o['segments'][0]['departure'][:10]]
        o['saving']=min(same_day)-o['total'] if same_day else None
    return {'direct':direct[:30], 'connections':connections[:30], 'best_direct':best_direct,'best_connection':connections[0]['total'] if connections else None}
