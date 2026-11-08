from datetime import datetime, timedelta, timezone
from .db import connect
from .push import ingest_event


def seed():
    with connect() as db:
        if db.execute('SELECT count(*) FROM events WHERE demo=1').fetchone()[0]:
            return
    current = datetime.now(timezone.utc)
    cases = [
        ('DIXON', 'Capacity expansion approved', 'Illustrative event: a new plant has been approved. Check funding and utilisation assumptions before drawing a conclusion.'),
        ('HDFCBANK', 'CFO resignation and appointment', 'Illustrative management change. Incoming executive background is not supplied; open the actual filing before relying on a biography.'),
        ('TCS', 'Quarterly financial results', 'Illustrative results event. No financial figures have been invented; live mode extracts the underlying filing.'),
        ('ASTRAL', 'Credit rating reaffirmed', 'Illustrative rating reaffirmation. Existing rating retained; no downgrade.'),
        ('RELIANCE', 'Investor meeting schedule', 'Illustrative investor meeting invitation.'),
        ('TITAN', 'Newspaper publication of results', 'Illustrative administrative publication notice.'),
        ('CDSL', 'Compliance certificate', 'Illustrative compliance certificate.'),
    ]
    for i, (symbol, title, body) in enumerate(cases):
        ingest_event(symbol, (current - timedelta(hours=i+1)).isoformat(), title, body,
                     '', demo=True, use_model=False)
