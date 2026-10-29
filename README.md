# FireRedASR2-AED HTTP Queue

面向单 NVIDIA GPU 的轻量 HTTP 转写服务：有界 FIFO 队列、SQLite 任务持久化、上传保护、隔离推理进程与硬超时。

本项目是独立服务封装，**不是 FireRedTeam 官方项目**。不包含模型权重或上游源码。当前接口处理 **0～60 秒**音频；完整会议需在外部通过 VAD 切段，并合并时间偏移。它不负责说话人区分、标点恢复或生成会议纪要。

## 下载并启动

环境：Ubuntu 22.04、Python 3.10、NVIDIA GPU、兼容 CUDA 11.8 的驱动。显存与吞吐需用实际负载验证；本项目不承诺最低显存容量。

```bash
sudo apt-get update
sudo apt-get install -y git ffmpeg python3.10-venv curl unzip

git clone https://github.com/frankxiaofei/fireredasr-http.git
cd fireredasr-http
bash install.sh
bash start.sh
```

安装脚本固定上游源码提交，创建独立虚拟环境，安装依赖、检查 CUDA、从 ModelScope 下载权重，生成随机 API Key。需要网络访问 GitHub、PyPI、PyTorch 下载站、ModelScope。下载失败时可以重新执行；不会覆盖已有 `.env`。

首次启动加载模型完成后才提供接口。默认绑定 `127.0.0.1:8000`。内网使用可修改 `.env` 中的 `HOST=0.0.0.0`。公网需放在 TLS 反向代理后，不要直接暴露明文密钥。

可下载 [发布包](https://github.com/frankxiaofei/fireredasr-http/releases/latest)，解压后同样运行 `bash install.sh`、`bash start.sh`。

## 调用

同目录另开终端：

```bash
bash smoke-test.sh /absolute/path/test.wav
```

手工调用（不要把真实密钥写进共享脚本或提交到 Git）：

```bash
source .env
curl --fail-with-body http://127.0.0.1:8000/v1/transcription-jobs \
  -H "Authorization: Bearer $API_KEY" -F 'file=@test.wav'
```

返回 `202 Accepted`：

```json
{"id":"任务ID","status":"queued","status_url":"/v1/transcription-jobs/任务ID"}
```

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/transcription-jobs/任务ID \
  -H "Authorization: Bearer $API_KEY"
```

状态：`queued` → `running` → `succeeded` 或 `failed`。成功结果位于 `result`，含 `text`、`duration`（秒）、`confidence`、`timestamp`（官方字词级秒时间戳）。失败信息位于 `error`。

| 接口 | 行为 |
|---|---|
| `POST /v1/transcription-jobs` | 异步提交，推荐使用 |
| `GET /v1/transcription-jobs/{id}` | 查询状态和结果 |
| `DELETE /v1/transcription-jobs/{id}` | 取消尚未执行的任务；执行中返回 409 |
| `POST /v1/audio/transcriptions` | 兼容同步调用，最多等待 30 秒；未完成返回 202 与查询地址 |
| `GET /v1/status` | 鉴权的工作进程状态与队列用量 |
| `GET /health` | 无需鉴权，仅用于 HTTP 存活检查，不代表 GPU 可用 |

429 响应带 `Retry-After: 5`，客户端应退避加随机抖动；网络不确定时先查询已取得的任务 ID，不要盲目重复提交。当前不提供幂等键去重，未收到提交响应时重复请求可能创建多个任务。

## 保护机制与边界

- 在 multipart 解析之前校验 Bearer Token，不接收未授权请求体。
- 原始 HTTP 请求体限制为 20 MiB + 64 KiB multipart 开销；音频文件上限 20 MiB。Content-Length 和分块传输均受限制。
- 上传总时间最多 60 秒；最多两个请求处于上传/提交处理中。鉴权请求共享 120 次/分钟令牌桶。反向代理还应限制连接数和未授权请求速率。
- 默认最多 16 个持久任务（含应用内保存上传、排队、执行）；原始网络上传另受两个槽位限制。按上传完成后的入队顺序执行，始终只有一个 GPU 推理任务。容量满在读取请求体前尽早拒绝，并在实际入队前再次检查。
- 默认保留至少 1 GiB 可用空间，上传前另预留请求体和副本空间。数据目录、原始上传临时文件和 SQLite 默认位于同一磁盘。
- 推理运行在独立常驻子进程，模型只加载一次。单次处理默认 180 秒；超时或异常会回收整个进程组，下一任务重载模型。ffmpeg 单次转换最多 90 秒；模型启动最多 300 秒。
- ffmpeg 禁用网络协议，仅允许常用音频容器；服务应以专用低权限用户运行。此措施不替代操作系统/容器隔离，也不保证抵御编解码器漏洞。
- SQLite 保存任务状态和结果；重启后恢复未执行的 queued 任务。被中断的 uploading/running 任务标为 failed，避免默默重复推理。
- 原始音频在完成、失败或取消后删除。结果默认保留一小时，最多 1000 条，后台每 30 秒清理。删除不等同于介质安全擦除，备份和文件系统快照由运维管理。
- 数据目录使用文件锁，拒绝多进程共用，避免重复推理。必须保持 `--workers 1`；需要多 GPU / 多机时升级任务调度方案。
- 客户端断开不取消已入队任务；正常停机保留排队任务，中断正在执行的任务。崩溃后自动清理服务自身遗留的任务目录。
- 所有请求使用一个共享 API Key，适合受信任的内部调用，**不提供租户隔离**。数据与转写结果以明文存储在私有数据目录，主机加密和备份策略需单独配置。
- 模型加载失败时服务启动失败；推理异常只记录任务 ID 和异常类型，不记录密钥、音频或转写文本。

## 配置

安装自动生成 `.env`，修改后重启：

```bash
HOST=127.0.0.1
PORT=8000
CUDA_VISIBLE_DEVICES=0
MODEL_DIR=models/FireRedASR2-AED
DATA_DIR=data
MAX_ACTIVE_JOBS=16
JOB_TIMEOUT_SECONDS=180
RESULT_TTL_SECONDS=3600
MIN_FREE_BYTES=1073741824
```

API_KEY 至少 24 个 ASCII 字符，建议使用安装生成值。默认模型来源是官方 README 的 `xukaituo/FireRedASR2-AED`，可在安装时设置 `MODEL_ID` 和 `MODEL_DIR`。已有 `.env` 不会被重写，修改下载位置后请同步修改它。

提供 `deploy/nginx.conf` 和 `deploy/fireredasr.service` 运维示例，使用前调整路径、服务用户和 TLS 配置；systemd 示例要求提前创建并授权 data 目录。

CPU Web 服务依赖和 GPU 模型依赖分开；安装后的完整版本保存到 `installed-versions.txt`。模型默认下载最新版本，正式部署应自行记录模型修订和哈希。安装脚本依赖上游固定提交，不宣称已验证所有新驱动和硬件组合。

## 测试

CPU 环境即可运行保护机制、队列和 HTTP 测试，无需下载模型：

```bash
python3 -m venv .venv-test
. .venv-test/bin/activate
pip install -r requirements-test.txt
python -m pytest -q
```

测试使用假推理器验证 HTTP 保护与任务行为，并用真实子进程验证超时强杀。GitHub Actions 在 Python 3.10 / 3.11 上运行相同测试。

**GPU 模型加载、识别质量、实际显存占用与吞吐未在开发机器上验证。** 部署后用 `smoke-test.sh` 完成 GPU 验收；生产前至少验证短音频、60 秒边界、静音、损坏文件和长时间排队。

## 上游与许可证

服务封装代码采用 MIT License。模型及上游代码各自遵循其许可证，不包含在本仓库，也不因本仓库的 MIT 许可而重新授权。

- [FireRedASR2S 官方项目](https://github.com/FireRedTeam/FireRedASR2S)
- 固定源码提交：`4e7d9aaf4482a47cec1724807026b9b151926eb5`
- [高吞吐 Triton / TensorRT 部署](https://github.com/FireRedTeam/FireRedASR2S/tree/main/runtime/triton_tensorrt)
