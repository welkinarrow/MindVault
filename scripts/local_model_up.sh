#!/usr/bin/env bash
# 一键启动并预热本地模型（A3）。演示前、排练前各跑一次；重复运行是安全的。
#   1. 检查 Ollama 已安装；没在运行就启动它
#   2. 没有 qwen3:8b 就下载（约 5 GB）
#   3. 没有 mv-qwen3 就按 config/Modelfile.mv-qwen3 创建（8K 上下文、温度 0）
#   4. 预热：把模型载入内存，保持 24 小时，并测一次短请求的耗时
#   5. 打印模型占用的内存（ollama ps）
#
# 用法（仓库根目录）：
#   scripts/local_model_up.sh
set -euo pipefail
cd "$(dirname "$0")/.."

URL="${LOCAL_LLM_URL:-http://localhost:11434}"
MODEL="${LOCAL_LLM_MODEL:-mv-qwen3}"
BASE_MODEL="qwen3:8b"
MODELFILE="config/Modelfile.mv-qwen3"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

if ! command -v ollama >/dev/null 2>&1; then
  echo "没找到 ollama。请先安装：https://ollama.com/download（或运行 curl -fsSL https://ollama.com/install.sh | sh）" >&2
  exit 1
fi

if ! curl -sf "$URL/api/version" >/dev/null 2>&1; then
  echo "==> 启动 Ollama"
  if [ -d /Applications/Ollama.app ]; then
    open -a Ollama
  else
    mkdir -p logs
    nohup ollama serve > logs/ollama.log 2>&1 &
  fi
  for _ in $(seq 1 30); do
    curl -sf "$URL/api/version" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf "$URL/api/version" >/dev/null 2>&1 || { echo "Ollama 30 秒内没有启动，请手动打开 Ollama 应用后重试" >&2; exit 1; }
fi
echo "==> Ollama $(curl -sf "$URL/api/version" | sed -E 's/.*"version": *"([^"]+)".*/\1/') 正在运行"

# 在 awk 里判断，不用 grep -q：pipefail 下 grep 提前退出会让上游收到 SIGPIPE，误判为“没有”
has_model() { ollama list | awk -v a="$1" -v b="$1:latest" 'NR>1 && ($1==a || $1==b) {found=1} END {exit !found}'; }

if ! has_model "$BASE_MODEL"; then
  echo "==> 下载 $BASE_MODEL（约 5 GB，只需一次）"
  ollama pull "$BASE_MODEL"
fi

if ! has_model "$MODEL"; then
  echo "==> 创建 $MODEL"
  ollama create "$MODEL" -f "$MODELFILE"
fi

echo "==> 预热"
LOCAL_LLM_URL="$URL" LOCAL_LLM_MODEL="$MODEL" "$PY" scripts/local_llm.py --check --warmup

echo "==> 内存占用"
ollama ps
