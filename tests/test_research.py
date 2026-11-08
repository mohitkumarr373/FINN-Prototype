import pytest
from finn.research import validated_claims, research, store_document, chunk_pages
from finn.results import compare, validate_facts, quoted_number
from finn.sources import parse_screener, ticker, check_url


def test_pull_rejects_news_or_results_document_types():
    for kind in ('news','broker_note','quarterly_results'):
        with pytest.raises(ValueError):
            store_document('TCS', kind, 'bad', 'https://example.com', b'', 'test')


def test_absent_evidence_produces_gap_not_fabricated_profile():
    result = research('INFY', 'What drives growth?')
    assert result['answer'] == [] and result['sources'] == []
    assert result['gaps']


def test_fabricated_quote_or_unknown_citation_is_removed():
    chunk = {'id':'c1','text':'Management expects growth of 5% this year.', 'page':2,
             'title':'Annual report', 'url':'https://issuer.example/ar.pdf', 'published':None}
    raw = {'claims':[
        {'text':'x','kind':'guidance','chunk_id':'c1','quote':'Management expects growth of 5% this year.'},
        {'text':'x','kind':'guidance','chunk_id':'c1','quote':'Management expects growth of 50% this year.'},
        {'text':'x','kind':'guidance','chunk_id':'made_up','quote':chunk['text']}]}
    assert len(validated_claims(raw, [chunk])) == 1


def fact(value, period, scope='consolidated', unit='INR crore', metric='Revenue'):
    return {'metric':metric,'period_end':period,'months':3,'scope':scope,'unit':unit,'value':value}


def test_qoq_yoy_arithmetic_and_scope_matching():
    rows = compare([fact(120,'2026-09-30'),fact(100,'2026-06-30'),fact(80,'2025-09-30')])
    assert rows[0]['qoq_pct'] == 20 and rows[0]['yoy_pct'] == 50
    rows = compare([fact(120,'2026-09-30'),fact(100,'2026-06-30',scope='standalone')])
    assert rows[0]['qoq_pct'] is None


def test_units_and_nonpositive_denominators_are_not_compared():
    assert compare([fact(120,'2026-09-30'),fact(100,'2026-06-30',unit='INR lakh')])[0]['qoq_pct'] is None
    for base in (0,-100):
        assert compare([fact(120,'2026-09-30'),fact(base,'2026-06-30')])[0]['qoq_pct'] is None


def test_ambiguous_financial_value_withheld_and_ebitda_not_derived():
    rows = compare([fact(120,'2026-09-30'),fact(140,'2026-09-30')])
    assert rows[0]['value'] is None
    assert next(x for x in rows if x['metric']=='EBITDA')['value'] is None


def test_annual_value_and_unquoted_number_rejected():
    quote = 'Consolidated Revenue 120 INR crore for quarter ended 2026-09-30'
    chunk = {'id':'c1','text':quote,'page':1}
    good = {**fact(120,'2026-09-30'), 'chunk_id':'c1', 'quote':quote}
    assert len(validate_facts({'facts':[good]},[chunk])) == 1
    assert not validate_facts({'facts':[{**good,'months':12}]},[chunk])
    assert not validate_facts({'facts':[{**good,'value':900}]},[chunk])
    assert quoted_number(-250, 'PAT (250)')


def test_chunks_keep_pdf_page():
    chunks = chunk_pages([(7,'a'*6500)])
    assert len(chunks)==3 and {c['page'] for c in chunks}=={7}


def test_screener_only_approved_primary_links_no_ai_summaries():
    html='''<h1>Infosys</h1><a href="https://www.nseindia.com/get-quotes/equity?symbol=INFY">NSE: INFY</a>
    <a href="https://www.infosys.com">Website</a><a href="https://www.bseindia.com">BSE: 500209</a>
    <div class="documents"><h3>Annual reports</h3><li><a href="https://www.infosys.com/ar.pdf">Annual Report 2026</a></li>
    <a href="/summary/">AI Summary</a><a href="https://broker.example/report.pdf">Annual Report 2025</a></div>
    <div class="documents"><h3>Concalls</h3><li>Oct 2026 <a href="https://www.bseindia.com/call.pdf">Transcript</a>
    <a href="https://broker.example/note.pdf">Transcript</a><a href="https://www.infosys.com/deck.pdf">PPT</a></li></div>'''
    result=parse_screener(html,'INFY','https://www.screener.in/company/INFY/')
    assert len(result['links'])==2
    assert result['bse_code']=='500209'
    with pytest.raises(ValueError):
        parse_screener(html,'TCS','https://www.screener.in/company/TCS/')


def test_url_and_symbol_boundaries():
    for url in ('http://www.nseindia.com/a', 'https://nseindia.com.evil.com/a',
                'https://user:pass@nseindia.com/a', 'https://127.0.0.1/a'):
        with pytest.raises(ValueError):
            check_url(url, {'nseindia.com'})
    assert ticker(' m&m ')=='M&M'
    with pytest.raises(ValueError):
        ticker('../secrets')


def test_model_routing_and_citation_gate(monkeypatch):
    import finn.research as module
    chunk = {'id':'c1','text':'Management expects growth of 5% this year.', 'page':2,
             'title':'Annual report', 'url':'https://issuer.example/ar.pdf', 'published':'2026-05-01'}
    monkeypatch.setattr(module, 'retrieve', lambda *a: [chunk])
    monkeypatch.setattr(module.llm, 'enabled', lambda *a: True)
    lanes = []
    def fake(lane, prompt, payload):
        lanes.append(lane)
        if prompt == module.prompts.EXTRACT:
            return {'claims':[{'text':'Growth outlook','kind':'guidance','chunk_id':'c1','quote':chunk['text']}]}
        if prompt == module.prompts.REASON:
            return {'answer':[
                {'claim':'Management expects 5% growth.', 'implication':'Execution needs monitoring.', 'evidence_ids':['E1']},
                {'claim':'A made-up fact.', 'implication':'Unsupported.', 'evidence_ids':['E999']}], 'gaps':['No outcome reported.']}
        return {'accepted':[0]}
    monkeypatch.setattr(module.llm, 'call', fake)
    result=module.research('INFY','What is guidance?')
    assert lanes==['extract','reason','extract']
    assert len(result['answer'])==1 and result['mode']=='cited_analysis'


def test_verifier_failure_falls_back_to_sources(monkeypatch):
    import finn.research as module
    chunk={'id':'c1','text':'Management expects growth of 5% this year.', 'page':2,
           'title':'Annual report','url':'https://issuer.example/ar.pdf','published':None}
    monkeypatch.setattr(module,'retrieve',lambda *a:[chunk])
    monkeypatch.setattr(module.llm,'enabled',lambda *a:True)
    def fake(lane,prompt,payload):
        if prompt==module.prompts.EXTRACT:
            return {'claims':[{'text':'Guidance','kind':'guidance','chunk_id':'c1','quote':chunk['text']}]}
        if prompt==module.prompts.REASON:
            return {'answer':[{'claim':'Growth is expected.', 'implication':'Watch delivery.', 'evidence_ids':['E1']}]}
        raise RuntimeError('Provider timeout')
    monkeypatch.setattr(module.llm,'call',fake)
    result=module.research('INFY','What is guidance?')
    assert not result['answer'] and result['mode']=='source_passages'


def test_real_pdf_ingestion_and_retrieval_keep_primary_provenance():
    import pymupdf
    from finn.research import retrieve
    document=pymupdf.open()
    page=document.new_page()
    page.insert_text((72,72),'Illustrative test company: management expects revenue growth of five percent.')
    content=document.tobytes()
    result=store_document('TESTCO','transcript','Test transcript','https://issuer.example/call.pdf',content,'test fixture')
    assert result['pages']==1 and result['chunks']==1
    evidence=retrieve('TESTCO','revenue growth guidance')
    assert evidence and evidence[0]['page']==1
    assert evidence[0]['kind']=='transcript' and evidence[0]['sha']
    store_document('TESTCO','transcript','Test transcript','https://issuer.example/call.pdf',content,'test fixture')
    assert len(retrieve('TESTCO','revenue growth'))==1
