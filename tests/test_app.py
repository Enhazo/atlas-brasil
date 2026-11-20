import pytest
from fastapi.testclient import TestClient

import main


SAMPLE = [
    {'ibge_id': 100, 'name': 'Aurora', 'state_code': 1, 'state': 'Estado Um', 'region': 'Norte', 'immediate_region': 'Centro Um', 'intermediate_region': 'Norte Um'},
    {'ibge_id': 101, 'name': 'Bela Vista', 'state_code': 1, 'state': 'Estado Um', 'region': 'Norte', 'immediate_region': 'Centro Dois', 'intermediate_region': 'Norte Um'},
    {'ibge_id': 200, 'name': 'Campo Novo', 'state_code': 2, 'state': 'Estado Dois', 'region': 'Sul', 'immediate_region': 'Centro Três', 'intermediate_region': 'Sul Um'},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = main.create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(main, 'engine', engine)
    main.SessionLocal.configure(bind=engine)
    main.Base.metadata.create_all(engine)
    async def fake_fetch(): return SAMPLE
    monkeypatch.setattr(main, 'fetch_municipalities', fake_fetch)
    with TestClient(main.app) as test_client:
        yield test_client
    main.Base.metadata.drop_all(engine)
    engine.dispose()


def test_health(client):
    assert client.get('/health').json() == {'status': 'ok'}


def test_sync_and_filter_by_region(client):
    result = client.get('/api/municipalities?region=Norte').json()
    assert result['total'] == 2
    assert {item['name'] for item in result['items']} == {'Aurora', 'Bela Vista'}


def test_filter_by_name_and_state(client):
    result = client.get('/api/municipalities?q=campo&state=Estado%20Dois').json()
    assert result['total'] == 1
    assert result['items'][0]['ibge_id'] == 200
    assert result['items'][0]['intermediate_region'] == 'Sul Um'


def test_states_unique(client):
    result = client.get('/api/states').json()
    assert len(result) == 2


def test_compare_two_municipalities(client):
    result = client.get('/api/municipalities/compare?ids=100&ids=200')
    assert result.status_code == 200
    assert len(result.json()['items']) == 2
    assert result.json()['items'][0]['immediate_region']


def test_compare_requires_unique_ids(client):
    assert client.get('/api/municipalities/compare?ids=100&ids=100').status_code == 422


def test_compare_unknown_id_returns_404(client):
    assert client.get('/api/municipalities/compare?ids=100&ids=999').status_code == 404


def test_pagination_and_query_validation(client):
    assert client.get('/api/municipalities?limit=1&offset=1').json()['items'][0]['name'] == 'Bela Vista'
    assert client.get('/api/municipalities?limit=0').status_code == 422


def test_home_page_in_portuguese(client):
    assert 'Encontre seu' in client.get('/').text


def test_new_municipality_with_null_microregion_uses_immediate_region(monkeypatch):
    payload = [{'id': 5101837, 'nome': 'Boa Esperança do Norte', 'microrregiao': None, 'regiao-imediata': {'id': 510008, 'nome': 'Sorriso', 'regiao-intermediaria': {'id': 5103, 'nome': 'Sinop', 'UF': {'id': 51, 'sigla': 'MT', 'nome': 'Mato Grosso', 'regiao': {'id': 5, 'sigla': 'CO', 'nome': 'Centro-Oeste'}}}}}]
    class Response:
        def raise_for_status(self): pass
        def json(self): return payload
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(main.httpx, 'AsyncClient', Client)
    import asyncio
    rows = asyncio.run(main.fetch_municipalities())
    assert rows[0]['state_code'] == 51
    assert rows[0]['region'] == 'Centro-Oeste'
    assert rows[0]['immediate_region'] == 'Sorriso'
    assert rows[0]['intermediate_region'] == 'Sinop'


def test_full_ibge_sync_includes_new_municipality_with_null_microregion(client):
    response = client.post('/api/sync')
    assert response.status_code == 200
    assert response.json()['municipalities'] == 3
    states = client.get('/api/states').json()
    assert {item['name'] for item in states} == {'Estado Um', 'Estado Dois'}


def test_ibge_wildcards_are_searched_literally(client):
    result = client.get('/api/municipalities?q=%25').json()
    assert result['total'] == 0


def test_rejects_municipality_with_no_resolvable_state(monkeypatch):
    payload = [{'id': 999, 'nome': 'Desconhecido', 'microrregiao': None, 'regiao-imediata': None}]
    class Response:
        def raise_for_status(self): pass
        def json(self): return payload
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(main.httpx, 'AsyncClient', Client)
    import asyncio
    with pytest.raises(main.HTTPException) as exc:
        asyncio.run(main.fetch_municipalities())
    assert exc.value.status_code == 502


def test_ibge_region_works_with_current_immediate_region_shape(monkeypatch):
    payload = [{'id': 1, 'nome': 'Município Teste', 'microrregiao': None, 'regiao-imediata': {'regiao-intermediaria': {'UF': {'id': 35, 'nome': 'São Paulo', 'regiao': {'nome': 'Sudeste'}}}}}]
    class Response:
        def raise_for_status(self): pass
        def json(self): return payload
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(main.httpx, 'AsyncClient', Client)
    import asyncio
    rows = asyncio.run(main.fetch_municipalities())
    assert rows[0]['region'] == 'Sudeste'
