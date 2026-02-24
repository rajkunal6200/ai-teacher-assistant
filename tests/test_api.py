import pytest
from httpx import AsyncClient
from src.main import app

@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get('/health')
        assert r.status_code == 200
        j = r.json()
        assert j.get('status') == 'healthy'

@pytest.mark.asyncio
async def test_chat_fallback():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post('/chat', json={"message":"Hi","language":"english"})
        assert r.status_code == 200
        j = r.json()
        assert 'reply' in j
