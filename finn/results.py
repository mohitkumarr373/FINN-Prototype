import re
from datetime import date
from decimal import Decimal, InvalidOperation
from . import llm, prompts
from .research import normalize

METRICS = {'Revenue', 'EBITDA', 'PBT', 'PAT'}
UNITS = {'INR crore', 'INR lakh', 'USD million'}


def quoted_number(value, quote):
    try:
        expected = Decimal(str(value))
        if not expected.is_finite():
            return False
        for token in re.findall(r'\(?-?\d[\d,]*(?:\.\d+)?\)?', quote):
            cleaned = token.replace(',', '')
            if cleaned.startswith('(') and cleaned.endswith(')'):
                cleaned = '-' + cleaned[1:-1]
            try:
                if Decimal(cleaned) == expected:
                    return True
            except InvalidOperation:
                continue
    except InvalidOperation:
        pass
    return False


def validate_facts(raw, chunks):
    lookup = {c['id']: c for c in chunks}
    valid = []
    for f in raw.get('facts', []):
        if not isinstance(f, dict):
            continue
        c = lookup.get(f.get('chunk_id'))
        q = normalize(str(f.get('quote', '')))
        try:
            date.fromisoformat(f['period_end'])
        except (KeyError, TypeError, ValueError):
            continue
        if (c and len(q) >= 20 and q in normalize(c['text']) and f.get('metric') in METRICS
            and f.get('months') == 3 and f.get('scope') in ('consolidated', 'standalone')
            and f.get('unit') in UNITS and quoted_number(f.get('value'), q)):
            valid.append({**f, 'value': float(f['value']), 'page': c['page']})
    return valid


def change(current, previous):
    if previous is None or previous <= 0:
        return None
    return round(100 * (current / previous - 1), 2)


def quarter_before(day):
    if (day.month, day.day) not in ((3, 31), (6, 30), (9, 30), (12, 31)):
        return None
    return {(3, 31): date(day.year-1, 12, 31), (6, 30): date(day.year, 3, 31),
            (9, 30): date(day.year, 6, 30), (12, 31): date(day.year, 9, 30)}[(day.month, day.day)]


def compare(facts):
    if not facts:
        return []
    latest = max(f['period_end'] for f in facts)
    period = date.fromisoformat(latest)
    previous = quarter_before(period)
    # Do not mix standalone and consolidated, currencies or fiscal durations.
    out = []
    for metric in ['Revenue', 'EBITDA', 'PBT', 'PAT']:
        options = [f for f in facts if f['metric'] == metric and f['period_end'] == latest]
        consolidated = [f for f in options if f['scope'] == 'consolidated']
        options = consolidated or options
        if not options:
            out.append({'metric': metric, 'value': None, 'note': 'Not explicitly reported or extraction uncertain.'})
            continue
        unique = {(f['value'], f['unit'], f['scope']) for f in options}
        if len(unique) != 1:
            out.append({'metric': metric, 'value': None, 'note': 'Conflicting reported values.'})
            continue
        f = options[0]
        def match(end):
            peers = [x for x in facts if x['metric'] == metric and x['period_end'] == end
                     and all(x[k] == f[k] for k in ('unit', 'scope', 'months'))]
            return peers[0] if peers and len({x['value'] for x in peers}) == 1 else None
        qoq = match(previous.isoformat()) if previous else None
        yoy = match(date(period.year - 1, period.month, period.day).isoformat()) if previous else None
        out.append({**f, 'qoq_pct': change(f['value'], qoq['value']) if qoq else None,
                    'yoy_pct': change(f['value'], yoy['value']) if yoy else None,
                    'prior_quarter': qoq, 'prior_year': yoy,
                    'note': 'Percent change unavailable for missing, zero or negative comparison bases.'})
    return out


def extract_results(chunks):
    if not llm.enabled():
        return {'metrics': [], 'status': 'Configure extraction model to parse financial tables.'}
    try:
        raw = llm.call('extract', prompts.RESULTS, {'chunks': chunks[:12]})
        facts = validate_facts(raw, chunks)
        return {'metrics': compare(facts), 'status': 'Draft extraction; inspect the cited filing.'}
    except Exception as exc:
        return {'metrics': [], 'status': 'Extraction unavailable: ' + type(exc).__name__}
