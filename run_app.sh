#!/usr/bin/env bash
set -euo pipefail

if [ ! -d "venv" ]; then
  python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

if curl -fsS http://127.0.0.1:8003/health >/dev/null 2>&1; then
  echo "AI Teacher Assistant is already running."
  echo "Web app: http://127.0.0.1:8003/app"
  echo "API docs: http://127.0.0.1:8003/docs"
  exit 0
fi

uvicorn src.main:app --host 0.0.0.0 --port 8003
