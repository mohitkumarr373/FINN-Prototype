import os
import ipaddress
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, parse_qs, quote
import httpx
from bs4 import BeautifulSoup
import pymupdf
from .config import IST
from .db import get_state, set_state, now

EXCHANGES = {'nseindia.com', 'nsearchives.nseindia.com', 'archives.nseindia.com',
             'bseindia.com', 'api.bseindia.com'}
USER_AGENT = 'Mozilla/5.0 FINN-assessment/0.1'


def ticker(value):
    value = value.upper().strip()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9&_-]{0,24}', value):
        raise ValueError('Use an NSE symbol, for example INFY or M&M')
    return value


def host_matches(host, allowed):
    return any(host == d or host.endswith('.' + d) for d in allowed)


def check_url(url, allowed):
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or
            parsed.port not in (None, 443) or not host_matches(parsed.hostname or '', allowed)):
        raise ValueError('Unapproved source URL')
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError('Private source address')
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        # Use only with a trusted egress proxy that blocks private destination IPs.
        if os.getenv('FINN_TRUST_PROXY_DNS') == '1' and any(
                os.getenv(k) for k in ('HTTPS_PROXY', 'ALL_PROXY', 'https_proxy', 'all_proxy')):
            return
        raise
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('Source must resolve to public addresses')


def fetch(url, allowed, max_bytes=25_000_000, client=None):
    own = client is None
    client = client or httpx.Client(timeout=30, headers={'User-Agent': USER_AGENT})
    try:
        for _ in range(5):
            check_url(url, allowed)
            for attempt in range(3):
                with client.stream('GET', url, follow_redirects=False) as response:
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    if response.is_redirect:
                        url = urljoin(url, response.headers['location'])
                        break
                    response.raise_for_status()
                    data = bytearray()
                    for part in response.iter_bytes():
                        data.extend(part)
                        if len(data) > max_bytes:
                            raise ValueError('Document exceeds 25 MB limit')
                    return bytes(data), url
            else:
                raise RuntimeError('Source retries exhausted')
        raise ValueError('Too many source redirects')
    finally:
        if own:
            client.close()


def canonical_pdf(url):
    p = urlparse(url)
    if p.hostname in ('www.bseindia.com', 'bseindia.com') and p.path.lower().endswith('/annpdfopen.aspx'):
        name = parse_qs(p.query).get('Pname', [''])[0]
        if re.fullmatch(r'[a-zA-Z0-9-]+\.pdf', name):
            return 'https://www.bseindia.com/xml-data/corpfiling/AttachLive/' + name
    return url


def parse_screener(html, symbol, origin):
    soup = BeautifulSoup(html, 'html.parser')
    h1 = soup.find('h1')
    if not h1:
        raise ValueError('Screener returned no company page')
    # Bind a discovered document to the requested company, not to an arbitrary URL.
    nse_links = [a for a in soup.select('a[href]') if
                 host_matches(urlparse(a['href']).hostname or '', {'nseindia.com'})]
    if not any(parse_qs(urlparse(a['href']).query).get('symbol', [''])[0].upper() == symbol
               or re.search(r'\bNSE\s*:\s*' + re.escape(symbol) + r'(?:\s|$)', a.get_text())
               for a in nse_links):
        raise ValueError('Company page does not confirm the requested NSE symbol')
    official = set()
    bse_code = None
    for a in soup.select('a[href]'):
        label = a.get_text(' ', strip=True)
        if label.lower() == 'website':
            host = urlparse(a['href']).hostname
            if host:
                official.add(host.removeprefix('www.'))
        m = re.search(r'BSE:\s*(\d{6})', label)
        if m:
            bse_code = m[1]
    links = []
    for heading in soup.find_all(['h2', 'h3']):
        title = heading.get_text(' ', strip=True).lower()
        if title not in ('annual reports', 'concalls'):
            continue
        section = heading.find_parent(class_='documents')
        if section is None:
            continue
        kind = 'annual_report' if title == 'annual reports' else 'transcript'
        for a in section.select('a[href]'):
            label = a.get_text(' ', strip=True)
            if kind == 'annual_report' and not re.search(r'Annual Report\s+\d{4}', label, re.I):
                continue
            if kind == 'transcript' and label.lower() != 'transcript':
                continue
            url = canonical_pdf(urljoin(origin, a['href']))
            host = urlparse(url).hostname or ''
            if not host_matches(host, EXCHANGES | official):
                continue
            context = a.find_parent('li')
            raw_title = context.get_text(' ', strip=True) if context else label
            clean_title = label if kind == 'annual_report' else re.sub(r'AI Summary|PPT|REC', '', raw_title).strip()
            links.append({'kind': kind, 'title': clean_title[:180],
                          'url': url, 'discovery': origin})
    tables = {}
    for section_id in ('quarters', 'profit-loss', 'balance-sheet', 'cash-flow', 'ratios'):
        section = soup.find(id=section_id)
        if section:
            tables[section_id] = [[cell.get_text(' ', strip=True) for cell in tr.select('th,td')]
                                  for tr in section.select('table tr')]
    return {'ticker': symbol, 'name': h1.get_text(' ', strip=True), 'official_hosts': sorted(official),
            'bse_code': bse_code, 'links': list({x['url']: x for x in links}.values()),
            'standardized_tables': tables,
            'table_usage': 'Discovery/diagnostics only. Excluded from research evidence.', 'fetched': now()}


def discover(symbol, refresh=False):
    symbol = ticker(symbol)
    saved = get_state('discovery:' + symbol)
    if saved and not refresh and (datetime.now(timezone.utc) - datetime.fromisoformat(saved['fetched'])).days < 1:
        return saved
    url = f'https://www.screener.in/company/{quote(symbol)}/consolidated/'
    try:
        data, _ = fetch(url, {'screener.in'})
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404:
            raise
        url = f'https://www.screener.in/company/{quote(symbol)}/'
        data, _ = fetch(url, {'screener.in'})
    result = parse_screener(data, symbol, url)
    set_state('discovery:' + symbol, result)
    return result


def pdf_pages(data):
    if not data.startswith(b'%PDF'):
        raise ValueError('Source did not return a PDF')
    with pymupdf.open(stream=data, filetype='pdf') as reader:
        if len(reader) > 1200:
            raise ValueError('PDF exceeds page budget')
        pages = [(i + 1, p.get_text('text', sort=True).strip()) for i, p in enumerate(reader)]
    readable = sum(len(re.sub(r'\s+', '', text)) >= 40 for _, text in pages)
    if not pages or readable < len(pages) * .5:
        raise ValueError('Scanned/poorly extracted document: OCR or manual review required')
    return pages


def nse_announcements(start, end, symbol=None):
    with httpx.Client(timeout=30, headers={'User-Agent': USER_AGENT,
                       'Referer': 'https://www.nseindia.com/'}) as client:
        fetch('https://www.nseindia.com/', {'nseindia.com'}, client=client)
        url = ('https://www.nseindia.com/api/corporate-announcements?index=equities'
               f'&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}')
        if symbol:
            url += '&symbol=' + quote(ticker(symbol))
        raw, _ = fetch(url, {'nseindia.com'}, client=client)
    import json
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError('NSE schema changed')
    out = []
    for r in rows:
        stamp = r.get('an_dt') or r.get('sort_date')
        if not stamp or not r.get('symbol'):
            raise ValueError('NSE row missing symbol or broadcast time')
        parsed = None
        for fmt in ('%d-%b-%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S', '%d-%m-%Y %H:%M:%S'):
            try:
                parsed = datetime.strptime(stamp, fmt).replace(tzinfo=IST)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError('Unknown NSE timestamp format')
        out.append({'ticker': r['symbol'], 'published': parsed.isoformat(),
                    'title': r.get('desc', ''), 'body': r.get('attchmntText', ''),
                    'source': r.get('attchmntFile', '')})
    return out


def bse_announcements(symbol, code, start, end):
    import json
    out = []
    for page in range(1, 101):
        url = ('https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w'
               f'?pageno={page}&strCat=-1&strPrevDate={start:%Y%m%d}&strScrip={code}'
               f'&strSearch=P&strToDate={end:%Y%m%d}&strType=C&subcategory=-1')
        with httpx.Client(timeout=30, headers={'User-Agent': USER_AGENT,
                           'Referer': 'https://www.bseindia.com/'}) as client:
            raw, _ = fetch(url, {'bseindia.com'}, client=client)
        data = json.loads(raw)
        if not isinstance(data.get('Table'), list) or 'Table1' not in data:
            raise ValueError('BSE schema changed')
        rows = data['Table']
        for r in rows:
            if str(r.get('SCRIP_CD')) != str(code):
                raise ValueError('BSE returned another company')
            parsed = datetime.fromisoformat(r['NEWS_DT'])
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=IST)
            out.append({'ticker': symbol, 'published': parsed.isoformat(),
                        'title': r.get('NEWSSUB', ''), 'body': r.get('HEADLINE', ''),
                        'source': 'https://www.bseindia.com/xml-data/corpfiling/AttachLive/'
                                  + r.get('ATTACHMENTNAME', '')})
        total = int(data['Table1'][0]['ROWCNT']) if data['Table1'] else 0
        if len(out) >= total or not rows:
            return out
        time.sleep(.4)
    raise ValueError('BSE page budget reached; source coverage incomplete')


def yahoo_bars(symbol, current=None):
    import yfinance as yf
    current = current or datetime.now(IST)
    frame = yf.Ticker(ticker(symbol) + '.NS').history(
        period='6mo', interval='1d', auto_adjust=False, actions=True, repair=True, raise_errors=True)
    if frame.empty or 'Adj Close' not in frame:
        raise ValueError('Yahoo returned no adjusted-close history')
    rows = []
    for stamp, row in frame.iterrows():
        day = stamp.date()
        # Today's bar is not accepted before the EOD settlement buffer.
        if day > current.date() or (day == current.date() and (current.hour, current.minute) < (18, 30)):
            continue
        rows.append({'date': day.isoformat(), 'adjusted_close': float(row['Adj Close']),
                     'volume': float(row['Volume']), 'split': float(row.get('Stock Splits', 0))})
    if not rows:
        raise ValueError('No completed session available')
    return rows


def nse_research_links(symbol):
    import json
    symbol = ticker(symbol)
    cached = get_state('nse_research:' + symbol)
    if cached and (datetime.now(timezone.utc) - datetime.fromisoformat(cached['fetched'])).days < 1:
        return cached['links']
    current = datetime.now(IST)
    links = []
    for row in nse_announcements(current - timedelta(days=365), current, symbol):
        label = row['title'] + ' ' + row['body']
        if (row['ticker'] == symbol and re.search(r'transcript', label, re.I)
                and re.search(r'earnings|conference call|concall|con\.?\s*call', label, re.I)
                and not re.search(r'annual general meeting|investor.*day', label, re.I)):
            links.append({'kind': 'transcript', 'title': f"{symbol} earnings-call transcript - filed {row['published'][:10]}", 'url': row['source'],
                          'published': row['published'],
                          'discovery': 'https://www.nseindia.com/companies-listing/corporate-filings-announcements'})
    with httpx.Client(timeout=30, headers={'User-Agent': USER_AGENT,
                       'Referer': 'https://www.nseindia.com/'}) as client:
        fetch('https://www.nseindia.com/', {'nseindia.com'}, client=client)
        raw, _ = fetch('https://www.nseindia.com/api/annual-reports?index=equities&symbol='
                       + quote(symbol), {'nseindia.com'}, client=client)
    payload = json.loads(raw)
    if not isinstance(payload.get('data'), list):
        raise ValueError('NSE annual reports schema changed')
    years = set()
    # Keep the newest submission for each fiscal year, including revised filings.
    reports = sorted(payload['data'], key=lambda r: _nse_report_date(r) or datetime.min.replace(tzinfo=IST), reverse=True)
    for row in reports:
        year = (row.get('fromYr'), row.get('toYr'))
        if year in years or not row.get('fileName', '').lower().endswith('.pdf'):
            continue
        years.add(year)
        published = _nse_report_date(row)
        links.append({'kind': 'annual_report', 'title': f"Annual report FY{year[0]}-{year[1]}",
                      'url': row['fileName'], 'published': published.isoformat() if published else None,
                      'discovery': 'https://www.nseindia.com/companies-listing/corporate-filings-annual-reports'})
    set_state('nse_research:' + symbol, {'fetched': now(), 'links': links})
    return links


def _nse_report_date(row):
    try:
        return datetime.strptime(row.get('broadcast_dttm', ''), '%d-%b-%Y %H:%M:%S').replace(tzinfo=IST)
    except ValueError:
        return None
