#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
[ -x .venv/bin/python ] && [ -f .env ] || { echo '请先运行 bash install.sh'; exit 1; }
umask 077
set -a
source .env
set +a
mkdir -p "${DATA_DIR:-$PWD/data}"
export TMPDIR="${DATA_DIR:-$PWD/data}"
export PYTHONPATH="$PWD/vendor/FireRedASR2S${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -m uvicorn server:create_app --factory \
  --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --workers 1 \
  --limit-concurrency 32 --timeout-keep-alive 5 --timeout-graceful-shutdown 15
