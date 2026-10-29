#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "${BASH_SOURCE[0]}")"
PYTHON_BIN="${PYTHON_BIN:-python3.10}"
command -v "$PYTHON_BIN" >/dev/null || { echo '请先安装 Python 3.10，或设置 PYTHON_BIN'; exit 1; }
"$PYTHON_BIN" -c 'import sys; assert sys.version_info[:2] == (3,10), "需要 Python 3.10"'
for cmd in git ffmpeg; do command -v "$cmd" >/dev/null || { echo "缺少 $cmd；Ubuntu: sudo apt-get install git ffmpeg python3.10-venv"; exit 1; }; done
command -v nvidia-smi >/dev/null || { echo '需要安装 NVIDIA 驱动'; exit 1; }
nvidia-smi
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade 'pip<26'
REV=4e7d9aaf4482a47cec1724807026b9b151926eb5
mkdir -p vendor
if [ ! -d vendor/FireRedASR2S/.git ]; then
  git clone https://github.com/FireRedTeam/FireRedASR2S.git vendor/FireRedASR2S
fi
git -C vendor/FireRedASR2S fetch origin "$REV"
git -C vendor/FireRedASR2S checkout --detach "$REV"
.venv/bin/python -m pip install -r vendor/FireRedASR2S/requirements.txt -c constraints.txt
.venv/bin/python -m pip install -r requirements-api.txt -c constraints.txt
.venv/bin/python -m pip install modelscope==1.25.0 -c constraints.txt
.venv/bin/python -c 'import torch; assert torch.cuda.is_available(), "CUDA 不可用，请检查驱动"; print(torch.cuda.get_device_name(0))'
export MODEL_ID="${MODEL_ID:-xukaituo/FireRedASR2-AED}"
export MODEL_DIR="${MODEL_DIR:-$PWD/models/FireRedASR2-AED}"
.venv/bin/python - <<'PYTHON'
import os
from modelscope import snapshot_download
snapshot_download(os.environ['MODEL_ID'], local_dir=os.environ['MODEL_DIR'])
PYTHON
if [ ! -f .env ]; then
  .venv/bin/python - <<'PYTHON'
import os, shlex
from pathlib import Path
path=Path('.env')
path.write_text('HOST=127.0.0.1\nPORT=8000\nCUDA_VISIBLE_DEVICES=0\nMODEL_DIR='+shlex.quote(os.environ['MODEL_DIR'])+'\n')
path.chmod(0o600)
PYTHON
fi
.venv/bin/python -m pip freeze > installed-versions.txt
echo '安装完成。执行 ./start.sh。'
