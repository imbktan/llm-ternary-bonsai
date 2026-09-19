#!/usr/bin/env bash
# Bonsai 2 27B — agent mode: file tools scoped to ./mount + a real Chrome via MCP.
#
# The model can write HTML into mount/ and then inspect it in a real browser using
# browser_snapshot (accessibility tree), browser_evaluate (computed styles, layout
# numbers) and browser_console_messages (JS errors).
#
# NOTE: it cannot *see* screenshots on this path. llama-server's /tools endpoint
# returns only {"plain_text_response": ...}, so image parts from an MCP result are
# dropped before they reach the vision tower. For visual checking use bonsai_agent.py.
#
# Usage: ./run-agent.sh [extra llama-server flags]
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$DIR/bin:${LD_LIBRARY_PATH:-}"
export PATH="$(dirname "$(command -v npx)"):$PATH"   # llama-server spawns npx for MCP

CTX="${BONSAI_CTX:-32768}"
PORT="${BONSAI_PORT:-8080}"
PREVIEW_PORT="${BONSAI_PREVIEW_PORT:-8081}"

mkdir -p "$DIR/mount"

# Playwright blocks the file: protocol, so mount/ has to be served over HTTP.
python3 -m http.server "$PREVIEW_PORT" --bind 127.0.0.1 --directory "$DIR/mount" \
  >/dev/null 2>&1 &
PREVIEW_PID=$!
trap 'kill $PREVIEW_PID 2>/dev/null || true' EXIT
echo "preview: http://127.0.0.1:$PREVIEW_PORT/  (serving mount/)"

# Built-in file tools resolve against the server's CWD — this is what scopes them.
cd "$DIR/mount"

"$DIR/bin/llama-server" \
  -m "$DIR/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf" \
  --mmproj "$DIR/models/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf" \
  -ngl 99 -fa on -c "$CTX" --jinja \
  --temp 1.0 --top-p 0.95 --top-k 20 \
  --host 127.0.0.1 --port "$PORT" \
  --tools read_file,write_file,edit_file,file_glob_search,grep_search \
  --mcp-servers-config "$DIR/mcp.json" \
  "$@"
