import json
import math
import re
from datetime import datetime, timedelta, timezone
from .config import CATEGORIES, DEFAULT_RULES
from .db import connect, digest, get_state, now
from . import llm, prompts


def classify(title, body=''):
    text = (title + ' ' + body).lower()
    # Explicit risk events outrank administrative labels, not generic risk boilerplate.
    risk_text = re.sub(r'no (?:loan )?defaults?|no downgrade|not downgraded', '', text)
    if (re.search(r'default|insolvency|fraud|downgrad', title.lower())
            or re.search(r'auditor.{0,35}(resign|qualif)|has defaulted|rating.{0,30}downgrad|admitted.{0,30}insolvency', risk_text)):
        return {'category': 'credit_rating' if 'downgrad' in text else 'litigation',
                'priority': 95, 'reason': 'Risk disclosure: read the source.'}
    if re.search(r'newspaper|publication|board meeting.*(intimat|notice)|intimat.*board meeting|trading window|compliance certificate|regulation 74', title.lower()):
        return {'category': 'results' if 'result' in text else 'unclassified',
                'priority': 10, 'reason': 'Administrative notice; collapsed by default.'}
    rules = [
        ('management_change', r'\b(cfo|ceo|managing director|chief financial|chief executive)\b.*(resign|appoint|retir)|(?:resign|appoint).*(?:director|\bcfo\b|\bceo\b)', 85),
        ('M&A', r'acquisition|merger|demerger|joint venture|divest', 80),
        ('results', r'financial results|audited results|unaudited results|quarterly results', 80),
        ('litigation', r'litigation|regulatory order|penalty|show cause', 75),
        ('pledging', r'pledge|pledging|encumbrance', 75),
        ('capex', r'capacity expansion|capital expenditure|new plant', 75),
        ('fund_raise', r'rights issue|buyback|preferential allotment|\bqip\b|commercial paper', 65),
        ('credit_rating', r'credit rating|rating reaffirm', 45),
        ('insider_trading', r'insider|promoter.*(buy|sell|disposal|acquis)', 50),
        ('earnings_call', r'transcript', 45),
        ('earnings_call', r'earnings call|conference call|concall', 15),
        ('dividend', r'dividend', 40), ('bonus_split', r'bonus issue|stock split|sub.division', 50),
        ('investor_meetings', r'investor meet|analyst meet|institutional investor', 10),
        ('postal_ballot', r'postal ballot', 10), ('agm_egm', r'\bagm\b|\begm\b|general meeting', 10),
        ('press_release', r'press release|order win|product launch', 50),
        ('unclassified', r'esop|allotment|compliance', 10),
    ]
    # A specific filing subject takes precedence over incidental mentions in its PDF.
    for subject in (title.lower(), text):
        for category, pattern, priority in rules:
            if re.search(pattern, subject):
                reasons = {
                    'management_change': 'Leadership change: check succession, effective date and the stated reason.',
                    'M&A': 'Capital allocation decision: check price, funding and integration risk.',
                    'results': 'Quarterly numbers are available: check margin movement and cash conversion.',
                    'litigation': 'Legal exposure changed: check the amount and operating impact.',
                    'pledging': 'Promoter collateral changed: check the extent and funding pressure.',
                    'capex': 'New spending commitment: check capacity, cost and funding.',
                    'fund_raise': 'Capital structure may change: check dilution, terms and use of funds.',
                    'credit_rating': 'Credit assessment update: distinguish a change from a reaffirmation.',
                    'earnings_call': 'Management commentary is available for primary-source review.',
                }
                return {'category': category, 'priority': priority,
                        'reason': reasons.get(category, 'Available for review.') if priority >= 20 else 'Routine update.'}

    # Unknown is visible. Uncertainty is not evidence of irrelevance.
    return {'category': 'unclassified', 'priority': 50, 'reason': 'Unclassified: needs review.'}


def enrich_classification(title, body, base):
    if not body or not llm.enabled() or base['priority'] <= 10:
        return base
    try:
        data = llm.call('extract', prompts.CLASSIFY,
                        {'categories': CATEGORIES, 'title': title, 'filing': body[:14000]})
        quote = ' '.join(str(data.get('quote', '')).split())
        if (data.get('category') in CATEGORIES and len(quote) >= 20
                and quote in ' '.join(body.split())):
            return {'category': data['category'],
                    'priority': max(base['priority'], 75 if data.get('material') is True else 40),
                    'reason': str(data.get('summary', base['reason']))[:300], 'quote': quote}
    except Exception:
        pass
    return base


def ingest_event(ticker, published, title, body, source, demo=False, use_model=True):
    stamp = datetime.fromisoformat(published)
    if stamp.tzinfo is None:
        raise ValueError('Exchange timestamp must include a timezone')
    published = stamp.astimezone(timezone.utc).isoformat()
    # Same document body across NSE/BSE collapses; every source link is retained.
    identity = re.sub(r'\s+', ' ', body or title).strip().lower()
    event_id = digest(ticker + published[:10] + identity + str(demo))
    base = classify(title, body)
    if use_model:
        base = enrich_classification(title, body, base)
    with connect() as db:
        existing = db.execute('SELECT payload FROM events WHERE id=?', (event_id,)).fetchone()
        if existing:
            payload = json.loads(existing[0])
            if source not in payload['sources']:
                payload['sources'].append(source)
                db.execute('UPDATE events SET payload=? WHERE id=?', (json.dumps(payload), event_id))
            return event_id
        payload = {'body': body, 'sources': [source], 'demo': demo, 'kind': 'filing'}
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',
                   (event_id, ticker, published, base['category'], base['priority'], title,
                    base['reason'], json.dumps(payload), int(demo)))
    evaluate_alert(event_id)
    return event_id


def evaluate_alert(event_id):
    rules = get_state('rules', DEFAULT_RULES)
    with connect() as db:
        row = db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        if not row:
            return
        event = dict(row)
        payload = json.loads(event['payload'])
        triggers = []
        if payload.get('kind') == 'eod':
            if payload.get('price_pct') is not None and abs(payload['price_pct']) > rules['price_pct']:
                triggers.append('price')
            if payload.get('volume_ratio') is not None and payload['volume_ratio'] > rules['volume_multiple']:
                triggers.append('volume')
        elif event['category'] in rules['categories'] and event['priority'] >= rules['min_priority']:
            triggers.append('material')
        for trigger in triggers:
            db.execute('INSERT OR IGNORE INTO alerts(event_id,rule,created,payload) VALUES(?,?,?,?)',
                       (event_id, trigger, now(), json.dumps({
                           'ticker': event['ticker'], 'title': event['title'],
                           'reason': event['reason'], 'demo': bool(event['demo'])})))


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def eod_signal(bars, lookback=20):
    if len(bars) < lookback + 1:
        return {'price_pct': None, 'volume_ratio': None, 'note': 'Insufficient prior sessions.'}
    ordered = sorted(bars, key=lambda b: b['date'])
    prior, current = ordered[-lookback-1:-1], ordered[-1]
    if len({b['date'] for b in prior + [current]}) != lookback + 1:
        raise ValueError('Duplicate trading session')
    prev = prior[-1]['adjusted_close']
    close = current['adjusted_close']
    change = 100 * (close / prev - 1) if finite(prev) and prev > 0 and finite(close) and close > 0 else None
    volumes = [b['volume'] for b in prior]
    valid_volume = all(finite(v) and v > 0 for v in volumes + [current['volume']])
    split = any(b.get('split', 0) for b in prior + [current])
    average = sum(volumes) / lookback if valid_volume else None
    ratio = current['volume'] / average if average and not split else None
    return {'price_pct': change, 'volume_ratio': ratio, 'previous_sessions': lookback,
            'average_volume': average, 'volume': current['volume'], 'session': current['date'],
            'note': 'Volume alert suppressed: split in comparison window.' if split else
                    'Incomplete volume history.' if not valid_volume else
                    'Adjusted-close move; volume versus prior sessions, excluding today.'}


def store_eod(ticker, published, bars):
    rules = get_state('rules', DEFAULT_RULES)
    signal = eod_signal(bars, rules['lookback'])
    event_id = digest('eod:' + ticker + ':' + bars[-1]['date'])
    unusual = ((signal['price_pct'] is not None and abs(signal['price_pct']) > rules['price_pct']) or
               (signal['volume_ratio'] is not None and signal['volume_ratio'] > rules['volume_multiple']))
    parts = []
    if signal['price_pct'] is not None:
        parts.append(f"{signal['price_pct']:+.1f}% adjusted close")
    if signal['volume_ratio'] is not None:
        parts.append(f"{signal['volume_ratio']:.1f}x volume")
    title = ' / '.join(parts) or signal['note']
    signal.update(kind='eod', sources=[f'https://finance.yahoo.com/quote/{ticker}.NS/'], demo=False)
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,0)',
                   (event_id, ticker, published, 'market_move', 70 if unusual else 5,
                    title, signal['note'], json.dumps(signal)))
    evaluate_alert(event_id)


def window_digest(start, end, demo=False):
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError('Use an ordered, timezone-aware time window')
    if end - start > timedelta(days=366):
        raise ValueError('Window must be at most 366 days')
    with connect() as db:
        rows = db.execute('SELECT * FROM events WHERE published>=? AND published<? AND demo=? '
                          'ORDER BY priority DESC,published DESC',
                          (start.astimezone(timezone.utc).isoformat(),
                           end.astimezone(timezone.utc).isoformat(), int(demo))).fetchall()
    items = [dict(r) for r in rows]
    for item in items:
        item['payload'] = json.loads(item['payload'])
    return {'attention': [e for e in items if e['priority'] >= 70],
            'review': [e for e in items if 20 <= e['priority'] < 70],
            'routine': [e for e in items if e['priority'] < 20], 'total': len(items),
            'start': start.isoformat(), 'end': end.isoformat()}
