#!/usr/bin/env bash
# Bonsai 2 27B — chat server with web UI, vision and tool calling.
# Usage: ./run-server.sh [extra llama-server flags]
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$DIR/bin:${LD_LIBRARY_PATH:-}"

CTX="${BONSAI_CTX:-32768}"
PORT="${BONSAI_PORT:-8080}"

exec "$DIR/bin/llama-server" \
  -m "$DIR/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf" \
  --mmproj "$DIR/models/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf" \
  -ngl 99 -fa on -c "$CTX" --jinja \
  --temp 1.0 --top-p 0.95 --top-k 20 \
  --host 127.0.0.1 --port "$PORT" \
  "$@"
