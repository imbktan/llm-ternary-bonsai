#!/usr/bin/env bash
# Bonsai 2 27B — terminal chat, no server needed.
# Usage: ./run-cli.sh                          (interactive)
#        ./run-cli.sh -st -p "your question"   (one-shot)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$DIR/bin:${LD_LIBRARY_PATH:-}"

CTX="${BONSAI_CTX:-32768}"

exec "$DIR/bin/llama-cli" \
  -m "$DIR/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf" \
  -ngl 99 -fa on -c "$CTX" \
  --temp 1.0 --top-p 0.95 --top-k 20 \
  "$@"
