#!/usr/bin/env bash
# 初始化本地环境
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
cp -n .env.example .env || true
mkdir -p data/blobs
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  for c in python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
if [[ -z "$PY" ]]; then
  echo "需要 Python 3.12+，请先安装或设置 PYTHON=/path/to/python3.12" >&2
  exit 1
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "检测到 $PY ($("$PY" -V))，本项目需要 Python >=3.12（与 Docker 一致）" >&2
  exit 1
fi
"$PY" -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e packages/shared -e packages/llm-gateway -e packages/rag \
  -e packages/agent-core -e packages/eval -e apps/api -e apps/rag-worker
echo "bootstrap done. activate: source .venv/bin/activate"
