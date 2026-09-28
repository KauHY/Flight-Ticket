"""Manual live smoke test. Prints counts only; uses production cache/rate limits."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')
import airports
from providers import search
airports.initialize()
from datetime import date,timedelta
day=sys.argv[1] if len(sys.argv)>1 else str(date.today()+timedelta(days=7))
monitor=dict(origin='SHA',destination='CAN',date_from=day,date_to=day,connections=False)
for kind in (sys.argv[2:] or ['tongcheng']):
    offers,hints=search({'kind':kind},monitor)
    from app import candidate
    from engine import flight_board
    board=flight_board([], [candidate(o,kind) for o in offers],monitor)
    assert all(row['total'] is None for row in board)
    print('Board rows:',len(board))
    print(kind, 'flights:',len(offers),'minimum:',min((float(o['base_fare']) for o in offers),default=None),'hints:',hints)
