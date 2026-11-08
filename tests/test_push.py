from datetime import datetime, timedelta, timezone
import json
import pytest
from finn.config import DEFAULT_RULES
from finn.db import connect, set_state
from finn.push import classify, ingest_event, window_digest, eod_signal, store_eod


def bars():
    day = datetime(2026, 1, 1)
    return [{'date': (day+timedelta(days=i)).date().isoformat(), 'adjusted_close': 100.,
             'volume': 100., 'split': 0} for i in range(21)]


def test_actual_results_and_results_advert_are_different():
    assert classify('Financial results for quarter ended')['priority'] >= 70
    assert classify('Newspaper publication of financial results')['priority'] < 20


def test_administrative_label_does_not_hide_auditor_resignation():
    assert classify('Compliance certificate', 'The auditor has resigned')['priority'] == 95


def test_rating_reaffirmation_does_not_become_downgrade():
    assert classify('Credit rating reaffirmed', 'No downgrade has occurred')['priority'] < 70


def test_unknown_is_visible():
    assert classify('Unfamiliar exchange subject')['priority'] == 50


def test_dedup_preserves_both_exchange_links_and_alert_once():
    stamp = datetime.now(timezone.utc).isoformat()
    first = ingest_event('TCS', stamp, 'Financial results', 'Revenue grew.', 'https://nseindia.com/one')
    second = ingest_event('TCS', stamp, 'Results filing', 'Revenue   grew.', 'https://bseindia.com/two')
    assert first == second
    with connect() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 1
        row = db.execute('SELECT payload FROM events').fetchone()
        assert len(json.loads(row[0])['sources']) == 2
        assert db.execute('SELECT count(*) FROM alerts').fetchone()[0] == 1


def test_half_open_custom_window_and_routine_retention():
    start = datetime(2026, 11, 8, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    ingest_event('TCS', start.isoformat(), 'Newspaper publication', 'a', '')
    ingest_event('TCS', end.isoformat(), 'Quarterly results', 'b', '')
    result = window_digest(start, end)
    assert result['total'] == 1 and len(result['routine']) == 1
    with pytest.raises(ValueError):
        window_digest(start.replace(tzinfo=None), end)


def test_prior_volume_excludes_signal_session():
    data = bars(); data[-1]['volume'] = 300
    assert eod_signal(data)['volume_ratio'] == 3


def test_insufficient_or_zero_volume_and_split():
    assert eod_signal(bars()[:20])['volume_ratio'] is None
    data = bars(); data[0]['volume'] = 0
    assert eod_signal(data)['volume_ratio'] is None
    data = bars(); data[-1]['split'] = 2
    assert eod_signal(data)['volume_ratio'] is None


def test_duplicate_session_rejected():
    data = bars(); data[-1]['date'] = data[-2]['date']
    with pytest.raises(ValueError):
        eod_signal(data)


def test_price_and_volume_trigger_independently_once():
    data = bars(); data[-1].update(volume=250., adjusted_close=90.)
    for _ in range(2):
        store_eod('TCS', '2026-01-21T13:00:00+00:00', data)
    with connect() as db:
        assert {r['rule'] for r in db.execute('SELECT rule FROM alerts')} == {'price','volume'}
        assert db.execute('SELECT count(*) FROM alerts').fetchone()[0] == 2


def test_exact_volume_threshold_is_not_a_crossing():
    data = bars(); data[-1]['volume'] = 200
    store_eod('TCS', '2026-01-21T13:00:00+00:00', data)
    with connect() as db:
        assert db.execute('SELECT count(*) FROM alerts').fetchone()[0] == 0


def test_category_preferences_are_respected():
    set_state('rules', {**DEFAULT_RULES, 'categories': []})
    ingest_event('TCS', datetime.now(timezone.utc).isoformat(), 'Financial results', 'Results', '')
    with connect() as db:
        assert db.execute('SELECT count(*) FROM alerts').fetchone()[0] == 0


def test_results_title_wins_over_acquisition_and_newspaper_mentions_in_notes():
    result=classify('Financial Results', 'Results include acquisition costs. Newspaper publication will follow. Trading window remains closed.')
    assert result['category']=='results' and result['priority']>=70
