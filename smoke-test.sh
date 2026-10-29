#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
[ "$#" -eq 1 ] || { echo '用法: ./smoke-test.sh /绝对路径/audio.wav'; exit 1; }
source .env
BASE="http://127.0.0.1:${PORT:-8000}"
response=$(curl --fail-with-body "$BASE/v1/transcription-jobs" -F "file=@$1")
echo "$response"
job_id=$(printf '%s' "$response" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
for ((attempt=0; attempt<600; attempt++)); do
  result=$(curl --fail-with-body "$BASE/v1/transcription-jobs/$job_id")
  status=$(printf '%s' "$result" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  case "$status" in
    succeeded) echo "$result"; exit 0 ;;
    failed) echo "$result"; exit 1 ;;
  esac
  sleep 2
done
echo "等待超时，任务可能仍在执行：$job_id"
exit 1
