import json
import os
import httpx
from .db import connect, digest, now
from .prompts import VERSION


class ModelUnavailable(RuntimeError):
    pass


def enabled(lane='extract'):
    return bool(os.getenv('LLM_API_KEY') and os.getenv(lane.upper() + '_MODEL'))


def call(lane, prompt, payload):
    model = os.getenv(lane.upper() + '_MODEL', '')
    if not enabled(lane):
        raise ModelUnavailable(f'{lane} model is not configured')
    serialized = json.dumps(payload, sort_keys=True)
    if len(serialized) > 100_000:
        raise ValueError('Context budget exceeded')
    key = digest(VERSION + model + lane + prompt + serialized)
    with connect() as db:
        row = db.execute('SELECT payload FROM llm_cache WHERE key=?', (key,)).fetchone()
        if row:
            return json.loads(row[0])
        db.execute('BEGIN IMMEDIATE')
        used = db.execute('SELECT count(*) FROM usage WHERE created>=?',
                          (now()[:10],)).fetchone()[0]
        if used >= int(os.getenv('LLM_DAILY_CALL_LIMIT', '50')):
            raise ModelUnavailable('Daily model-call budget reached')
        usage_id = db.execute('INSERT INTO usage(created,lane,model,status) VALUES(?,?,?,?)',
                             (now(), lane, model, 'reserved')).lastrowid
    try:
        r = httpx.post(os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
                       + '/chat/completions',
                       headers={'Authorization': 'Bearer ' + os.environ['LLM_API_KEY']},
                       json={'model': model, 'messages': [
                           {'role': 'system', 'content': prompt},
                           {'role': 'user', 'content': serialized}],
                           'response_format': {'type': 'json_object'},
                           'max_completion_tokens': 2600}, timeout=90)
        r.raise_for_status()
        body = r.json()
        data = json.loads(body['choices'][0]['message']['content'])
        if not isinstance(data, dict):
            raise ValueError('Expected JSON object')
        usage = body.get('usage', {})
        with connect() as db:
            db.execute('INSERT OR REPLACE INTO llm_cache VALUES(?,?)', (key, json.dumps(data)))
            db.execute('UPDATE usage SET input_tokens=?, output_tokens=?, status=? WHERE id=?',
                       (usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0),
                        'ok', usage_id))
        return data
    except Exception:
        with connect() as db:
            db.execute('UPDATE usage SET status=? WHERE id=?', ('failed', usage_id))
        raise
