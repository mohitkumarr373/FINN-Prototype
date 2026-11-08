import hmac
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import demo, llm
from .config import CATEGORIES, DEFAULT_RULES, PORTFOLIO, RESEARCH_PICKS
from .db import connect, get_state, init, now, set_state
from .push import window_digest
from .research import research, sync_company
from .sources import ticker

ROOT = Path(__file__).resolve().parent.parent
POOL = ThreadPoolExecutor(max_workers=2)
JOBS = {}
LOCK = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    init()
    demo.seed()
    yield


app = FastAPI(title='FINN', lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware('http')
async def guard(request: Request, call_next):
    token = os.getenv('FINN_ACCESS_TOKEN', '')
    if request.url.path.startswith('/api/') and token:
        supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if not hmac.compare_digest(supplied, token):
            return JSONResponse({'detail': 'Access token required'}, status_code=401)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/api/meta')
def meta():
    return {'portfolio': PORTFOLIO, 'research_picks': RESEARCH_PICKS, 'categories': CATEGORIES,
            'rules': get_state('rules', DEFAULT_RULES), 'server_time': now(),
            'models': {'extract': llm.enabled(), 'reason': llm.enabled('reason')},
            'filings_status': get_state('filings_status'), 'prices_status': get_state('prices_status')}


@app.get('/api/digest')
def get_digest(hours: int = 24, start: datetime | None = None, end: datetime | None = None,
               mode: Literal['demo', 'live'] = 'demo'):
    if not 1 <= hours <= 8760:
        raise HTTPException(422, 'Hours must be between 1 and 8760')
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=hours)
    try:
        return window_digest(start, end, mode == 'demo')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class Rules(BaseModel):
    price_pct: float = Field(ge=.1, le=50)
    volume_multiple: float = Field(ge=1.1, le=50)
    lookback: int = Field(ge=5, le=60)
    categories: list[str]
    min_priority: int = Field(ge=40, le=95)


@app.put('/api/rules')
def put_rules(rules: Rules):
    if any(c not in CATEGORIES for c in rules.categories):
        raise HTTPException(422, 'Unknown category')
    set_state('rules', rules.model_dump())
    return {'saved': True, 'applies': 'Future ingestion only; existing alerts remain an audit trail.'}


@app.get('/api/alerts')
def alerts(after: int = 0, mode: Literal['demo', 'live'] = 'demo'):
    with connect() as db:
        rows = db.execute('SELECT a.* FROM alerts a JOIN events e ON a.event_id=e.id '
                          'WHERE a.seq>? AND e.demo=? ORDER BY a.seq LIMIT 200',
                          (after, int(mode == 'demo'))).fetchall()
    return [{'seq': r['seq'], 'event_id': r['event_id'], 'rule': r['rule'],
             'created': r['created'], **json.loads(r['payload'])} for r in rows]


class Question(BaseModel):
    ticker: str = Field(min_length=1, max_length=25)
    question: str = Field(min_length=5, max_length=1000)
    deep: bool = True


class Company(BaseModel):
    ticker: str = Field(min_length=1, max_length=25)


def launch(kind, symbol, fn):
    try:
        symbol = ticker(symbol)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with LOCK:
        active = [j for j in JOBS.values() if j['status'] in ('queued', 'running')]
        if len(active) >= 2:
            raise HTTPException(429, 'Two jobs are running; try again shortly.')
        for key in list(JOBS):
            if len(JOBS) <= 50:
                break
            if JOBS[key]['status'] not in ('queued', 'running'):
                del JOBS[key]
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {'id': job_id, 'kind': kind, 'ticker': symbol, 'status': 'queued'}
    def run():
        with LOCK:
            JOBS[job_id]['status'] = 'running'
        try:
            result = fn(symbol)
            with LOCK:
                JOBS[job_id].update(status='done', result=result)
        except Exception as exc:
            with LOCK:
                JOBS[job_id].update(status='failed', error=str(exc)[:350])
    POOL.submit(run)
    return {'job_id': job_id}


@app.post('/api/company/sync', status_code=202)
def company_sync(company: Company):
    return launch('sync', company.ticker, sync_company)


@app.post('/api/research', status_code=202)
def ask(q: Question):
    def answer(symbol):
        with connect() as db:
            count = db.execute('SELECT count(*) FROM documents WHERE ticker=?', (symbol,)).fetchone()[0]
        load_report = sync_company(symbol) if not count else None
        result = research(symbol, q.question, q.deep)
        if load_report and load_report['errors']:
            result['gaps'].append(f"Document loading reported {len(load_report['errors'])} source errors; coverage may be incomplete.")
        return result
    return launch('research', q.ticker, answer)


@app.get('/api/jobs/{job_id}')
def job(job_id: str):
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, 'Job not found; it may have expired after a restart.')
        return dict(JOBS[job_id])


@app.get('/api/usage')
def usage():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT lane,model,status,count(*) calls, '
                 'sum(input_tokens) input_tokens,sum(output_tokens) output_tokens FROM usage '
                 'WHERE created>=? GROUP BY lane,model,status', (now()[:10],))]


@app.get('/')
def index():
    return FileResponse(ROOT / 'static/index.html')


app.mount('/', StaticFiles(directory=ROOT / 'static'), name='static')
