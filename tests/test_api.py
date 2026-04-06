from fastapi.testclient import TestClient
import asyncio
from src.main import app
from src.main import get_ai_response

client = TestClient(app)


def test_health():
    r = client.get('/health')
    assert r.status_code == 200
    j = r.json()
    assert j.get('status') == 'healthy'


def test_chat_fallback():
    r = client.post('/chat', json={"message": "Hi", "language": "english"})
    assert r.status_code == 200
    j = r.json()
    assert 'reply' in j


def test_unsupervised_learning_normalization():
    reply = asyncio.run(get_ai_response("what is unsuper vised learning", "english"))
    assert "unsupervised learning" in reply.lower() or "unlabeled data" in reply.lower()


def test_generic_fallback_no_old_placeholder_text():
    reply = asyncio.run(get_ai_response("what is sustainable design", "english"))
    assert "can be explained in simple words" not in reply.lower()
    assert "summary:" in reply.lower()


def test_neural_networks_do_not_collapse_to_generic_ai_answer():
    reply = asyncio.run(get_ai_response("explain neural networks for class 8", "english"))
    assert "layers" in reply.lower()
    assert "neural network" in reply.lower()
