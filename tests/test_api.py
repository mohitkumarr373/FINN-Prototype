from fastapi.testclient import TestClient
from finn.app import app


def test_mobile_shell_and_demo_are_usable_without_api_keys():
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        meta=client.get('/api/meta').json()
        assert len(meta['portfolio'])==18
        digest=client.get('/api/digest').json()
        assert digest['total']==7
        assert len(digest['attention'])==3
        assert len(digest['routine'])==3
        assert client.get('/api/digest?mode=live').json()['total']==0


def test_rule_validation_and_persistence():
    with TestClient(app) as client:
        rules=client.get('/api/meta').json()['rules']
        assert client.put('/api/rules',json={**rules,'volume_multiple':0}).status_code==422
        assert client.put('/api/rules',json={**rules,'categories':['made_up']}).status_code==422
        assert client.put('/api/rules',json={**rules,'price_pct':6}).status_code==200
        assert client.get('/api/meta').json()['rules']['price_pct']==6


def test_public_instance_api_requires_configured_token(monkeypatch):
    monkeypatch.setenv('FINN_ACCESS_TOKEN','test-secret')
    with TestClient(app) as client:
        assert client.get('/api/meta').status_code==401
        assert client.get('/api/meta',headers={'Authorization':'Bearer test-secret'}).status_code==200
        assert client.get('/').status_code==200


def test_research_autoloads_missing_company(monkeypatch):
    import importlib,time
    module=importlib.import_module('finn.app')
    loaded=[]
    monkeypatch.setattr(module,'sync_company',lambda s:(loaded.append(s) or {'errors':[]}))
    monkeypatch.setattr(module,'research',lambda *a:{'mode':'source_passages','gaps':[]})
    with TestClient(app) as client:
        response=client.post('/api/research',json={'ticker':'INFY','question':'What drives growth?','deep':False})
        assert response.status_code==202
        job_id=response.json()['job_id']
        for _ in range(100):
            result=client.get('/api/jobs/'+job_id).json()
            if result['status']=='done':break
            time.sleep(.01)
        assert result['status']=='done' and loaded==['INFY']
