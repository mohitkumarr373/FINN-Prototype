import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from . import llm, prompts, sources
from .db import connect, digest, now, set_state


def normalize(text):
    return ' '.join(text.split())


def chunk_pages(pages, size=3200, overlap=300):
    chunks = []
    for page, text in pages:
        text = normalize(text)
        for start in range(0, len(text), size - overlap):
            piece = text[start:start + size]
            if len(piece) >= 40:
                chunks.append({'page': page, 'text': piece})
    return chunks


def store_document(symbol, kind, title, url, data, discovery, published=None):
    if kind not in ('annual_report', 'transcript'):
        raise ValueError('Pull accepts annual reports and transcripts only')
    pages = sources.pdf_pages(data)
    sha = hashlib.sha256(data).hexdigest()
    document_id = digest(symbol + kind + sha)
    chunks = chunk_pages(pages)
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?,?,?,?)',
                   (document_id, symbol, kind, title, url, sha, now(), published, discovery))
        for i, chunk in enumerate(chunks):
            db.execute('INSERT OR IGNORE INTO chunks VALUES(?,?,?,?)',
                       (document_id[:16] + ':' + str(i), document_id, chunk['page'], chunk['text']))
    return {'document_id': document_id, 'pages': len(pages), 'chunks': len(chunks)}


def sync_company(symbol):
    symbol = sources.ticker(symbol)
    report = {'ticker': symbol, 'loaded': [], 'errors': [], 'fetched': now()}
    try:
        found = sources.discover(symbol)
    except Exception as exc:
        found = {'links': [], 'official_hosts': []}
        report['errors'].append({'title': 'Screener discovery', 'error': str(exc)[:200]})
    loaded_counts = Counter()
    blocked_hosts = set()
    seen_urls = set()

    def load_links(links):
        from urllib.parse import urlparse
        import httpx
        for kind, limit in [('annual_report', 2), ('transcript', 6)]:
            candidates = [x for x in links if x['kind'] == kind][:limit]
            for link in candidates:
                if loaded_counts[kind] >= limit or link['url'] in seen_urls:
                    continue
                host = urlparse(link['url']).hostname
                if host in blocked_hosts:
                    continue
                seen_urls.add(link['url'])
                with connect() as db:
                    prior = db.execute('SELECT id,fetched FROM documents WHERE ticker=? AND url=? ORDER BY fetched DESC LIMIT 1',
                                       (symbol, link['url'])).fetchone()
                if prior and (datetime.now(timezone.utc) - datetime.fromisoformat(prior['fetched'])).days < 1:
                    report['loaded'].append({'document_id': prior['id'], 'cached': True})
                    loaded_counts[kind] += 1
                    continue
                try:
                    data, final = sources.fetch(link['url'], sources.EXCHANGES | set(found['official_hosts']))
                    report['loaded'].append(store_document(symbol, link['kind'], link['title'],
                        final, data, link['discovery'], link.get('published')))
                    loaded_counts[kind] += 1
                except Exception as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                        blocked_hosts.add(host)
                    report['errors'].append({'title': link['title'], 'error': str(exc)[:250]})
    load_links(found['links'])
    if loaded_counts['annual_report'] < 2 or loaded_counts['transcript'] < 6:
        try:
            load_links(sources.nse_research_links(symbol))
        except Exception as exc:
            report['errors'].append({'title': 'NSE primary-source fallback', 'error': str(exc)[:250]})
    report['coverage'] = dict(loaded_counts)
    if not report['loaded']:
        report['errors'].append({'error': 'No primary-source PDFs loaded. Source-passage research cannot run yet.'})
    set_state('research_sync:' + symbol, report)
    return report


STOP = {'the', 'a', 'an', 'of', 'and', 'is', 'in', 'to', 'for', 'what', 'how', 'on', 'its', 'with', 'has', 'have'}
SYNONYMS = {'cash': ['cash', 'flows', 'operating', 'receivables', 'conversion'],
            'guidance': ['guidance', 'expect', 'outlook', 'growth', 'margin'],
            'debt': ['debt', 'borrowings', 'lease', 'liabilities'],
            'business': ['segments', 'revenue', 'customers', 'business'],
            'risk': ['risk', 'uncertainty', 'contingent', 'litigation']}


def tokens(text):
    return [w for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in STOP and len(w) > 1]


def retrieve(symbol, question, limit=12):
    with connect() as db:
        rows = [dict(r) for r in db.execute('''
            SELECT c.id,c.page,c.text,d.title,d.kind,d.url,d.sha,d.fetched,d.published,d.id AS document_id
            FROM chunks c JOIN documents d ON c.document_id=d.id
            WHERE d.ticker=? AND d.kind IN ('annual_report','transcript')''', (symbol,))]
    if not rows:
        return []
    query = set(tokens(question))
    for word in list(query):
        query.update(SYNONYMS.get(word, []))
    counts = [Counter(tokens(r['text'])) for r in rows]
    df = Counter(w for c in counts for w in c)
    avg = sum(sum(c.values()) for c in counts) / len(counts)
    scored = []
    for row, count in zip(rows, counts):
        length = sum(count.values())
        score = sum(math.log(1 + (len(rows)-df[w]+.5)/(df[w]+.5)) *
                    count[w] * 2.2 / (count[w] + 1.2 * (.25 + .75 * length / max(avg, 1)))
                    for w in query if count[w])
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    # Reserve one useful passage per document before filling remaining slots.
    chosen, per_doc = [], Counter()
    for _, row in scored:
        if not per_doc[row['document_id']]:
            chosen.append(row)
            per_doc[row['document_id']] += 1
        if len(chosen) >= limit:
            return chosen
    for _, row in scored:
        if row not in chosen and per_doc[row['document_id']] < 3:
            chosen.append(row)
            per_doc[row['document_id']] += 1
        if len(chosen) >= limit:
            break
    return chosen


def validated_claims(raw, chunks):
    lookup = {c['id']: c for c in chunks}
    claims = []
    for claim in raw.get('claims', [])[:12]:
        if not isinstance(claim, dict):
            continue
        chunk = lookup.get(claim.get('chunk_id'))
        quote = normalize(str(claim.get('quote', '')))
        if not chunk or not 20 <= len(quote) <= 600 or quote not in normalize(chunk['text']):
            continue
        if claim.get('kind') not in ('fact', 'guidance', 'risk') or not isinstance(claim.get('text'), str):
            continue
        # The verbatim quote, rather than the extractor's paraphrase, is the reasoning input.
        claims.append({'id': f'E{len(claims)+1}', 'kind': claim['kind'], 'quote': quote,
                       'chunk_id': chunk['id'], 'page': chunk['page'], 'title': chunk['title'],
                       'url': chunk['url'], 'published': chunk['published']})
    return claims


def research(symbol, question, deep=True):
    symbol = sources.ticker(symbol)
    chunks = retrieve(symbol, question)
    with connect() as db:
        docs = [dict(r) for r in db.execute('SELECT id,title,kind,url,fetched,published FROM documents '
                                          'WHERE ticker=? ORDER BY fetched DESC', (symbol,))]
    result = {'ticker': symbol, 'question': question, 'mode': 'source_passages', 'answer': [],
              'gaps': [], 'evidence': [], 'sources': docs, 'passages': chunks[:6]}
    if not chunks:
        result['gaps'] = ['No matching primary-source passages. Load company documents or narrow the question.']
        return result
    result['gaps'].append('Coverage is limited to the documents listed below; publication dates are unverified unless supplied.')
    if not llm.enabled() or not deep:
        result['gaps'].append('Source-passage mode: no model synthesis was run.')
        return result
    try:
        raw = llm.call('extract', prompts.EXTRACT, {'question': question, 'chunks': chunks})
        claims = validated_claims(raw, chunks)
        result['evidence'] = claims
        if not claims or not llm.enabled('reason'):
            result['gaps'].append('No validated claims or reasoning model unavailable; showing source passages.')
            return result
        draft = llm.call('reason', prompts.REASON, {'question': question, 'evidence': claims})
        allowed = {c['id'] for c in claims}
        points = [p for p in draft.get('answer', [])[:5] if isinstance(p, dict)
                  and isinstance(p.get('claim'), str) and isinstance(p.get('implication'), str)
                  and isinstance(p.get('evidence_ids'), list) and p['evidence_ids']
                  and all(isinstance(x, str) and x in allowed for x in p['evidence_ids'])]
        # Cheap second pass rejects unsupported reasoning; a failed check fails closed.
        checked = llm.call('extract', prompts.VERIFY, {'points': points, 'evidence': claims})
        accepted = {i for i in checked.get('accepted', []) if type(i) is int and 0 <= i < len(points)}
        result['answer'] = [p for i, p in enumerate(points) if i in accepted]
        result['mode'] = 'cited_analysis' if result['answer'] else 'source_passages'
        result['gaps'] += [g for g in draft.get('gaps', [])[:5] if isinstance(g, str)]
        if len(accepted) != len(points):
            result['gaps'].append('Some draft points were withheld after the evidence check.')
    except Exception as exc:
        result['gaps'].append('Analysis unavailable; source passages are still usable. ' + type(exc).__name__)
    return result
