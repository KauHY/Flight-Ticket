import unittest
from unittest.mock import patch
from datetime import date,timedelta
import airports
import app
from engine import normalize,flight_board,compare
from providers import search,parse_flyai

class AirportFlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):airports.initialize()
    def setUp(self):
        self.day=(date.today()+timedelta(days=7)).isoformat()
        self.m=app.validate_route(dict(origin='CITY:SHA',destination='CAN',date_from=self.day,connections=False,demo=True))
    def raw(self,origin='SHA',price=400,flight='MU1234'):
        return dict(currency='CNY',base_fare=price,taxes_included=True,total=price+100,segments=[dict(origin=origin,destination='CAN',departure=self.day+'T08:00:00+08:00',arrival=self.day+'T10:20:00+08:00',flight=flight,carrier='MU')])
    def test_dataset_size_and_no_iata_exclusion(self):
        self.assertGreater(airports.stats()['total'],10000)
        self.assertGreater(airports.stats()['selectable'],7000)
        rows=airports.search('00AA',all_records=True)
        self.assertTrue(rows);self.assertFalse(rows[0]['selectable'])
        with self.assertRaises(ValueError):airports.resolve('00AA')
    def test_chinese_english_iata_icao_search(self):
        for query in ['上海','Shanghai','SHA','ZSSS']:
            self.assertTrue(airports.search(query))
        self.assertEqual(airports.resolve('ZSSS')['id'],'SHA')
    def test_explicit_city_vs_airport(self):
        self.assertEqual(set(airports.resolve('CITY:SHA')['codes']),{'SHA','PVG'})
        self.assertEqual(airports.resolve('SHA')['codes'],['SHA'])
        self.assertEqual(set(airports.resolve('CITY:CTU')['codes']),{'CTU','TFU'})
        self.assertIn('LHR',airports.resolve('CITY:LON')['codes'])
    def test_overlap_and_unknown_rejected(self):
        for origin,destination in [('CITY:SHA','PVG'),('ZZZ','CAN')]:
            with self.assertRaises(ValueError):app.validate_route(dict(origin=origin,destination=destination,date_from=self.day))
    def test_timezone_dst_and_invalid_local_time(self):
        self.assertTrue(airports.local_time('2026-07-01T12:00:00','JFK').endswith('-04:00'))
        self.assertTrue(airports.local_time('2026-01-01T12:00:00','JFK').endswith('-05:00'))
        self.assertTrue(airports.local_time('2026-07-01T12:00:00','NRT').endswith('+09:00'))
        with self.assertRaises(ValueError):airports.local_time('2026-11-01T01:30:00','JFK')
        with self.assertRaises(ValueError):airports.local_time('2026-03-08T02:30:00','JFK')
    def test_city_query_expands_all_pairs(self):
        with patch('providers.generic',return_value=[]) as generic:
            search(dict(kind='generic'),self.m)
        self.assertEqual({c.args[1]['origin'] for c in generic.call_args_list},{'SHA','PVG'})
    def test_city_matching_keeps_both_airports(self):
        offers=[normalize(self.raw(x),'test',[]) for x in ['SHA','PVG']]
        self.assertEqual(len(compare(offers,self.m)['direct']),2)
        m={**self.m,'origin':'SHA','origin_airports':['SHA']}
        self.assertEqual(len(compare(offers,m)['direct']),1)
    def test_each_flight_lowest_across_sources_no_tax_mixing(self):
        offers=[normalize(self.raw(price=400),'A',[]),normalize(self.raw(price=350),'B',[]),normalize(self.raw(flight='MU9999'),'A',[])]
        pending=app.candidate({**self.raw(price=200),'price_status':'unverified'},'C')
        rows=flight_board(offers,[pending],self.m)
        self.assertEqual(len(rows),2)
        r=next(r for r in rows if r['flight']=='MU1234')
        self.assertEqual(r['total'],45000);self.assertEqual(r['display_price'],20000);self.assertEqual(r['quote_count'],3)
        self.assertEqual(r['airline'],'中国东方航空')
    def test_no_top_30_truncation(self):
        offers=[normalize(self.raw(flight='MU'+str(1000+i)),'source',[]) for i in range(45)]
        self.assertEqual(len(flight_board(offers,[],self.m)),45)
        self.assertEqual(len(compare(offers,self.m)['direct']),45)
    def test_same_flight_different_airport_not_merged(self):
        offers=[normalize(self.raw(x),'source',[]) for x in ['SHA','PVG']]
        self.assertEqual(len(flight_board(offers,[],self.m)),2)
    def test_status_source_adds_flight_without_fake_price(self):
        raw=self.raw();segment=raw['segments'][0]
        status=[dict(source='飞常准',status='计划',segments=[segment])]
        rows=flight_board([],[],self.m,status)
        self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['total']);self.assertIsNone(rows[0]['display_price'])
        self.assertEqual(rows[0]['quote_count'],0);self.assertEqual(rows[0]['status'],'计划')
    def test_on_demand_never_sends_mail(self):
        with patch.object(app,'send_mail') as mail:
            result=app.collect(self.m,app.DEFAULT)
        mail.assert_not_called();self.assertEqual(len(result['flights']),2)
    def test_query_budget_explicit_error(self):
        with self.assertRaises(ValueError):
            app.validate_route(dict(origin='CITY:LON',destination='CITY:PAR',date_from=self.day,date_to=(date.fromisoformat(self.day)+timedelta(days=30)).isoformat()))

if __name__=='__main__':unittest.main()
