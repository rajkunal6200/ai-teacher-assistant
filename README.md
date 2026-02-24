# AI Teacher Assistant

FastAPI-based teaching app with web UI, offline fallback mode, and optional AI providers.

## Best Option Without API Keys

You do not need any API key to run this app.

The app already supports no-key mode with:
- Rule-based educational responses
- Free public knowledge fallback (DuckDuckGo/Wikipedia when reachable)
- Full UI and core features without paid provider keys

If you later add `OPENAI_API_KEY`, `GEMINI_API_KEY`, or `DEEPSEEK_API_KEY`, it will automatically use those.

## Quick Start (Local)

1. Run the launcher script:

```bash
./run_app.sh
```

2. Open:

- Web app: `http://127.0.0.1:8003/app`
- API docs: `http://127.0.0.1:8003/docs`
- Health: `http://127.0.0.1:8003/health`

## Manual Start

```bash
python3 -m venv venv
source ./venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn src.main:app --host 0.0.0.0 --port 8003
```

## Docker (Easy Deployment)

Build and run:

```bash
docker build -t ai-teacher-assistant .
docker run --env-file .env -p 8003:8003 ai-teacher-assistant
```

Or with Compose:

```bash
docker compose up --build
```

## Share So Others Can Download

1. Push this project to GitHub.
2. Share the repo URL so anyone can clone and run `./run_app.sh`.
3. For one-click deployment, use Docker on any VPS or PaaS that supports containers.

## Notes

- Frontend is now served by backend at `/app` (same-origin API calls, deployment-safe).
- If port `8003` is busy, run with another port (example: `--port 8004`).
- Voice features depend on system audio support and installed libraries.
