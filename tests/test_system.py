import unittest, tempfile, json
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import patch
import app
from engine import normalize, compare

class FlightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=app.DB; app.DB=Path(self.tmp.name)/'test.db'; app.init()
        self.m={'origin':'SHA','destination':'CAN','date_from':(date.today()+timedelta(days=2)).isoformat(),'date_to':(date.today()+timedelta(days=2)).isoformat(),'target':100000,'email':'a@example.com','interval':60,'connections':True,'self_transfer':True,'demo':False}
    def tearDown(self):
        app.DB=self.old; self.tmp.cleanup()
    def raw(self):
        return {'currency':'CNY','base_fare':300,'segments':[{'origin':'SHA','destination':'CAN','departure':self.m['date_from']+'T08:00:00+08:00','arrival':self.m['date_from']+'T10:00:00+08:00','carrier':'MU','airport_fee':50,'fuel_fee':60}]}
    def test_segment_fees(self):
        self.assertEqual(normalize(self.raw(),'x',[])['total'],41000)
    def test_no_double_tax(self):
        v=self.raw();v.update(taxes_included=True,total=410)
        self.assertEqual(normalize(v,'x',[])['total'],41000)
    def test_unknown_fee_rejected(self):
        v=self.raw();del v['segments'][0]['fuel_fee']
        with self.assertRaises(ValueError):normalize(v,'x',[])
    def test_connections_include_both_taxes(self):
        r=compare(app.demo(self.m),self.m)
        self.assertEqual(r['best_direct'],69000);self.assertEqual(r['best_connection'],56000)
        self.assertEqual(r['connections'][0]['taxes'],22000)
        self.assertEqual(r['connections'][0]['saving'],13000)
    def test_self_transfer_opt_in(self):
        self.m['self_transfer']=False
        r=compare(app.demo(self.m),self.m)
        self.assertEqual(r['best_connection'],59000)
        self.assertFalse(any(o['self_transfer'] for o in r['connections']))
    def test_short_connection_excluded(self):
        offers=app.demo(self.m);offers[2]['segments'][0]['departure']=self.m['date_from']+'T09:30:00+08:00'
        r=compare(offers[:3],self.m)
        self.assertEqual(r['connections'],[])
    def insert(self):
        with app.db() as c:return c.execute('INSERT INTO monitors(body) VALUES(?)',(json.dumps(self.m),)).lastrowid
    def test_email_dedup_and_retry(self):
        mid=self.insert();cfg={**app.DEFAULT,'providers':[{'name':'test','enabled':True}]}
        with patch.object(app,'config',return_value=cfg),patch.object(app,'fetch',return_value=[self.raw()]),patch.object(app,'send_mail',side_effect=[OSError('fail'),None]) as mail:
            app.run(mid);app.run(mid);app.run(mid)
            self.assertEqual(mail.call_count,2)
    def test_demo_never_sends(self):
        self.m['demo']=True;mid=self.insert()
        with patch.object(app,'send_mail') as mail:app.run(mid);mail.assert_not_called()
    def test_equal_threshold_no_send(self):
        self.m['target']=41000;mid=self.insert();cfg={**app.DEFAULT,'providers':[{'name':'test','enabled':True}]}
        with patch.object(app,'config',return_value=cfg),patch.object(app,'fetch',return_value=[self.raw()]),patch.object(app,'send_mail') as mail:app.run(mid);mail.assert_not_called()
    def test_invalid_dates(self):
        m={**self.m,'target':600,'date_to':(date.today()+timedelta(days=80)).isoformat()}
        with self.assertRaises(ValueError):app.validate_monitor(m)

if __name__=='__main__':unittest.main()
