import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import airports
import crawlers
from engine import normalize

class CrawlerTests(unittest.TestCase):
    def setUp(self):
        airports.initialize()
        self.m=dict(origin='CITY:SHA',destination='CAN',origin_airports=['SHA','PVG'],destination_airports=['CAN'],date_from='2026-10-15',date_to='2026-10-15')
        self.row=dict(flightNo='AQ1006',originAirportCode='PVG',arriveAirportCode='CAN',flyOffTime='2026-10-15 22:25',arrivalTime='2026-10-16 01:00',airCompanyCode='AQ',airCompanyName='九元航空',flightPrice=369,stopNum=0)
    def test_midnight_and_unverified(self):
        result=crawlers.parse_tongcheng([self.row],self.m,'https://www.ly.com/')[0]
        self.assertEqual(result['segments'][0]['arrival'],'2026-10-16T01:00:00+08:00')
        with self.assertRaises(ValueError): normalize(result,'同程',[])
    def test_reject_wrong_airport_date_stop_or_price(self):
        for update in [dict(originAirportCode='PEK'),dict(flyOffTime='2026-10-14 22:25'),dict(stopNum=1),dict(flightPrice=0),dict(flightPrice='NaN'),dict(arrivalTime='2026-10-15 01:00')]:
            self.assertEqual(crawlers.parse_tongcheng([{**self.row,**update}],self.m,''),[])
    def test_specific_airport_not_city(self):
        self.m['origin_airports']=['SHA']
        self.assertEqual(crawlers.parse_tongcheng([self.row],self.m,''),[])
    def test_calendar_never_becomes_flight(self):
        with patch.object(crawlers,'cached_page',return_value=({'calendar':[{'Date':'2026-10-15','Price':370},{'Date':'2026-10-16','Price':1}]},0,False)):
            offers,hints=crawlers.search_pages({'kind':'spring'},self.m)
        self.assertEqual(offers,[])
        self.assertTrue(any('370' in h for h in hints))
        self.assertFalse(any('2026-10-16' in h for h in hints))
    def test_failed_robots_reserves_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(crawlers,'DATA',Path(tmp)),patch('urllib.request.urlopen',side_effect=OSError('offline')) as request:
            with self.assertRaisesRegex(ValueError,'robots'):crawlers.cached_page('tongcheng','https://www.ly.com/flights/home')
            with self.assertRaisesRegex(ValueError,'限速'):crawlers.cached_page('tongcheng','https://www.ly.com/flights/home')
            self.assertEqual(request.call_count,1)
    def test_cache_reuse_expiry_and_host_limit(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'User-agent: *\nAllow: /\nCrawl-delay: 120\n'
        process=MagicMock(returncode=0,stdout='{"rows":[]}')
        url='https://www.ly.com/flights/itinerary/oneway/SHA-CAN?date=2026-10-15'
        with tempfile.TemporaryDirectory() as tmp,patch.object(crawlers,'DATA',Path(tmp)),patch('urllib.request.urlopen',return_value=response),patch('subprocess.run',return_value=process) as run,patch('shutil.which',return_value='node'),patch('time.time',return_value=1000) as clock:
            first=crawlers.cached_page('tongcheng',url)
            self.assertFalse(first[2])
            clock.return_value=1001
            self.assertTrue(crawlers.cached_page('tongcheng',url)[2])
            with self.assertRaisesRegex(ValueError,'限速'):
                crawlers.cached_page('tongcheng',url.replace('CAN','PEK'))
            self.assertEqual(run.call_count,1)
            clock.return_value=1901
            self.assertFalse(crawlers.cached_page('tongcheng',url)[2])
            self.assertEqual(run.call_count,2)

if __name__=='__main__':unittest.main()
