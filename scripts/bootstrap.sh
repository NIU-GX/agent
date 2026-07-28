#!/usr/bin/env bash
# 初始化本地环境
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
cp -n .env.example .env || true
mkdir -p data/blobs
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e packages/shared -e packages/llm-gateway -e packages/rag \
  -e packages/agent-core -e packages/eval -e apps/api -e apps/rag-worker
echo "bootstrap done. activate: source .venv/bin/activate"
