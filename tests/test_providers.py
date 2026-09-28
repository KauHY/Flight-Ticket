import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date,timedelta
import app
from providers import parse_flyai, validate_provider, presets, search, local_env
from engine import normalize

class ProviderTests(unittest.TestCase):
    def setUp(self):
        import airports
        airports.initialize()
        self.day=(date.today()+timedelta(days=7)).isoformat()
        self.m=dict(origin='SHA',destination='CAN',date_from=self.day,date_to=self.day,connections=True)
        self.item=dict(ticketPrice='380.00',jumpUrl='https://example.com/flight',journeys=[{'journeyType':'直达','segments':[
            dict(depStationCode='SHA',arrStationCode='CAN',depDateTime=self.day+' 21:55:00',
                 arrDateTime=(date.fromisoformat(self.day)+timedelta(days=1)).isoformat()+' 00:10:00',marketingTransportNo='MU5313')]}])
    def payload(self,item=None):return {'status':0,'data':{'itemList':[item or self.item]}}
    def test_live_schema_and_next_day(self):
        o=parse_flyai(self.payload(),self.m)[0]
        self.assertEqual(o['base_fare'],'380.00');self.assertEqual(o['segments'][0]['carrier'],'MU')
        self.assertTrue(o['segments'][0]['arrival'].endswith('+08:00'))
        self.assertEqual(o['price_status'],'unverified')
        with self.assertRaises(ValueError):normalize(o,'飞猪',[])
    def test_documented_adult_price(self):
        item=copy.deepcopy(self.item);item.pop('ticketPrice');item['adultPrice']='¥400.0'
        self.assertEqual(parse_flyai(self.payload(item),self.m)[0]['base_fare'],'400.0')
    def test_city_expansion_filtered(self):
        self.item['journeys'][0]['segments'][0]['depStationCode']='PVG'
        self.assertEqual(parse_flyai(self.payload(),self.m),[])
    def test_unknown_timezone_not_guessed(self):
        self.item['journeys'][0]['segments'][0]['arrStationCode']='ZZZ';self.m['destination']='ZZZ'
        self.assertEqual(parse_flyai(self.payload(),self.m),[])
    def test_cli_fake_success_rejected(self):
        with self.assertRaises(ValueError):parse_flyai({'tool':'search_flight','arguments':{}},self.m)
    def test_pending_source_cannot_enable(self):
        with self.assertRaises(ValueError):validate_provider(dict(kind='partner',name='东航',enabled=True),[])
        with self.assertRaises(ValueError):search(dict(kind='partner'),self.m)
    def test_secret_preservation_scoped_to_type_endpoint(self):
        old=[dict(name='a',kind='generic',endpoint='https://a.example',token='secret')]
        p=validate_provider(dict(name='a',kind='generic',endpoint='https://a.example',token=''),old)
        self.assertEqual(p['token'],'secret')
        p=validate_provider(dict(name='a',kind='flyai',endpoint='',token=''),old)
        self.assertEqual(p['token'],'')
    def test_variflight_secret_not_in_catalog_or_presets(self):
        source=next(p for p in presets() if p['kind']=='variflight')
        self.assertFalse(source['enabled']);self.assertEqual(source['token'],'')
        self.assertNotIn('APP_SECURITY',json.dumps(app.CATALOG))
    def test_migration_preserves_existing_and_runs_once(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DATA',Path(tmp)):
            existing={**app.DEFAULT,'providers':[dict(name='existing',endpoint='https://example.com',token='secret',enabled=True)]}
            (Path(tmp)/'config.json').write_text(json.dumps(existing),'utf-8')
            app.install_provider_presets();app.install_provider_presets()
            cfg=app.config();self.assertEqual(len(cfg['providers']),len(presets())+1)
            self.assertEqual(cfg['providers'][0]['token'],'secret')
            self.assertTrue(next(p for p in cfg['providers'] if p.get('kind')=='flyai')['enabled'])
            self.assertFalse(any(p['enabled'] for p in cfg['providers'] if p.get('kind')=='partner'))
    def test_unverified_offers_never_email(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DB',Path(tmp)/'test.db'):
            app.init();m={**self.m,'target':1000000,'email':'test@example.com','interval':60,'demo':False,'self_transfer':False}
            with app.db() as c:mid=c.execute('INSERT INTO monitors(body) VALUES(?)',(json.dumps(m),)).lastrowid
            cfg={**app.DEFAULT,'providers':[dict(name='FlyAI',enabled=True)]}
            with patch.object(app,'config',return_value=cfg),patch.object(app,'fetch',return_value=parse_flyai(self.payload(),self.m)),patch.object(app,'send_mail') as mail:
                app.run(mid);mail.assert_not_called()
            with app.db() as c:r=c.execute('SELECT result FROM monitors WHERE id=?',(mid,)).fetchone()
            result=json.loads(r['result']);self.assertEqual(len(result['candidates']),1);self.assertIsNone(result['best_direct'])

if __name__=='__main__':unittest.main()
