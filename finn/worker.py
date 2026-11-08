import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from . import sources
from .config import IST, PORTFOLIO
from .db import connect, digest, get_state, init, now, set_state
from .push import classify, ingest_event, store_eod
from .research import chunk_pages, sync_company
from .results import extract_results


def process_filing(row):
    key = 'filing:' + digest(row['source'] + row['published'] + row['title'])
    cached = get_state(key)
    if cached and datetime.fromisoformat(cached['checked']) > datetime.now(timezone.utc) - timedelta(hours=24):
        return cached['id']
    text, chunks, attachment_error = row['body'], [], None
    if row['source']:
        try:
            raw, _ = sources.fetch(row['source'], sources.EXCHANGES)
            pages = sources.pdf_pages(raw)
            text = '\n'.join(t for _, t in pages)
            chunks = chunk_pages(pages)
            for i, chunk in enumerate(chunks):
                chunk['id'] = f'filing:{i}'
        except Exception as exc:
            attachment_error = str(exc)[:220]
    event_id = ingest_event(**{**row, 'body': text})
    details = None
    if chunks and classify(row['title'], text)['category'] == 'results':
        ranked = sorted(chunks, key=lambda c: sum(c['text'].lower().count(w)
                         for w in ['revenue', 'profit before', 'profit after', 'ebitda', 'quarter ended']), reverse=True)
        details = extract_results(ranked[:12])
    with connect() as db:
        saved = db.execute('SELECT payload FROM events WHERE id=?', (event_id,)).fetchone()
        payload = json.loads(saved[0])
        payload.update(attachment_error=attachment_error, results=details)
        db.execute('UPDATE events SET payload=? WHERE id=?', (json.dumps(payload), event_id))
    if not attachment_error:
        set_state(key, {'id': event_id, 'checked': now()})
    return event_id


def sync_filings(days=3):
    current = datetime.now(IST)
    start = current - timedelta(days=days)
    symbols = {s for s, _ in PORTFOLIO}
    report = {'started': now(), 'processed': 0, 'errors': [], 'coverage': {}}
    try:
        rows = [r for r in sources.nse_announcements(start, current) if r['ticker'] in symbols]
        report['coverage']['NSE'] = 'ok'
    except Exception as exc:
        rows = []
        report['coverage']['NSE'] = 'failed'
        report['errors'].append('NSE: ' + str(exc)[:250])
    # BSE adds independent coverage; failures never turn into an empty-success digest.
    for symbol in symbols:
        try:
            code = sources.discover(symbol)['bse_code']
            if not code:
                raise ValueError('No BSE code discovered')
            rows.extend(sources.bse_announcements(symbol, code, start, current))
            report['coverage']['BSE:' + symbol] = 'ok'
        except Exception as exc:
            report['coverage']['BSE:' + symbol] = 'failed'
            report['errors'].append(symbol + ': ' + str(exc)[:200])
    for row in sorted(rows, key=lambda r: r['published']):
        try:
            process_filing(row)
            report['processed'] += 1
        except Exception as exc:
            report['errors'].append(row['ticker'] + ': ' + str(exc)[:200])
    report['finished'] = now()
    set_state('filings_status', report)
    return report


def sync_prices():
    report = {'started': now(), 'sessions': {}, 'errors': []}
    for symbol, _ in PORTFOLIO:
        try:
            bars = sources.yahoo_bars(symbol)
            published = datetime.fromisoformat(bars[-1]['date'] + 'T18:30:00').replace(tzinfo=IST)
            store_eod(symbol, published.astimezone(timezone.utc).isoformat(), bars)
            report['sessions'][symbol] = bars[-1]['date']
        except Exception as exc:
            report['errors'].append(symbol + ': ' + str(exc)[:220])
    report['finished'] = now()
    set_state('prices_status', report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['once', 'watch', 'prices', 'filings', 'research'])
    parser.add_argument('--ticker', default='INFY')
    args = parser.parse_args()
    init()
    if args.command == 'research':
        print(json.dumps(sync_company(args.ticker), indent=2)); return
    if args.command == 'prices':
        print(json.dumps(sync_prices(), indent=2)); return
    if args.command == 'filings':
        print(json.dumps(sync_filings(), indent=2)); return
    if args.command == 'once':
        print(json.dumps({'filings': sync_filings(), 'prices': sync_prices()}, indent=2)); return
    while True:
        try:
            sync_filings()
            current = datetime.now(IST)
            key = 'eod_complete:' + current.date().isoformat()
            # The returned last session is displayed, even on weekends and holidays.
            if (current.hour, current.minute) >= (18, 30) and not get_state(key):
                result = sync_prices()
                if not result['errors']:
                    set_state(key, True)
        except Exception as exc:
            set_state('worker_error', {'at': now(), 'error': str(exc)[:250]})
        time.sleep(300)


if __name__ == '__main__':
    main()
