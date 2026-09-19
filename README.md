# Ternary Bonsai 2 27B — local setup (RTX 3060 12 GB)

A 27B vision-language reasoning model running entirely on your GPU, in 5.5 GiB of
weights. Text, image input and OpenAI-style tool calling all work. Everything here
is installed and tested on this machine.

## TL;DR

```bash
cd /home/jack/LLM-Ternary-Bonsai
./run-server.sh          # then open http://localhost:8080
```

That's it. Ctrl+C to stop.

Setting this up from scratch on another machine, or running the Pagoda Garden scene?
See **[SETUP.md](SETUP.md)** for the full walkthrough.

## Measured on this machine (RTX 3060, 12 GB)

| Metric | Result |
|---|---|
| Generation (tg128) | **27.6 tok/s** |
| Prompt processing (pp512) | **272 tok/s** |
| VRAM, 32K context + vision | **9.2 GB / 12 GB** |
| Model load time | ~20 s from cold |

Comfortably fits. About 2.7 GB of VRAM headroom at the default settings.

## What's installed

```
LLM-Ternary-Bonsai/
├── bin/                                       PrismML llama.cpp fork (CUDA), + CUDA 12.8 runtime libs
├── models/
│   ├── Ternary-Bonsai-2-27B-PTQ1_0.gguf       5.5 GiB  language model (1.75 bits/weight)
│   └── Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf  601 MiB  vision tower (images/PDFs)
├── mount/                                     sandbox the agent reads and writes
├── run-server.sh                              chat server + web UI
├── run-cli.sh                                 terminal chat
├── run-agent.sh                               server + file/browser tools (text-only checking)
├── bonsai_agent.py                            agent loop that can *see* its own pages
├── mcp.json                                   Playwright browser config for run-agent.sh
├── task.txt                                   example task (the Pagoda Garden scene)
└── README.md
```

### Why this specific build

Bonsai 2 stores its weights in a **rotated (Hadamard) basis**. Stock llama.cpp cannot
run these files — it rejects `PTQ1_0` as an unknown type. Do not `apt install` or
`brew install` llama.cpp and point it at these weights.

One thing to actively avoid: there is a `Q2_0` band of this model floating around.
Stock llama.cpp **loads it without any warning and produces gibberish**, because
`Q2_0` is a type it already knows. If output ever turns to nonsense, check which
binary is running before anything else.

Note this machine had no CUDA toolkit, so `bin/` includes `libcudart.so.12` and
`libcublas.so.12` extracted from NVIDIA's pip wheels. The launcher scripts set
`LD_LIBRARY_PATH` for you. The CUDA 12.8 build is used rather than 13.3 because the
13.x runtime libraries aren't published yet — your driver runs 12.x fine.

## Usage

### Web UI (recommended)

```bash
./run-server.sh
```

Open <http://localhost:8080>. You get chat, image upload, PDF attachments, and a
**Reasoning effort** picker (the lightbulb icon in the message box) — Off / Low /
Medium / High / Max, per conversation.

### Terminal

```bash
./run-cli.sh                                    # interactive chat
./run-cli.sh -st -p "Explain CRDTs briefly."    # one-shot, then exit
```

### As an API

The server speaks the OpenAI chat-completions format, so anything that talks to
OpenAI can point at it:

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Reverse a string in Python."}]}'
```

Base URL `http://127.0.0.1:8080/v1`, API key can be any non-empty string.

Tool calling works natively — send an OpenAI `tools` array and you get back
`finish_reason: "tool_calls"` with proper `tool_calls` objects. Vision works by
sending an `image_url` content part; a `data:image/png;base64,...` URI is fine.

### Agent mode — build a web page and look at it

The model can write files into `mount/`, open them in a real headless Chrome, and
screenshot the result to check its own work. `mount/` is the only folder it can
touch.

Needs `node`/`npx` (for Playwright) and Google Chrome, both already on this machine.
The browser package is fetched by `npx` on first run.

The worked example in `task.txt` is a voxel Pagoda Garden scene in Three.js:

```bash
# terminal 1 — screenshots eat context, so give it room
BONSAI_CTX=65536 ./run-server.sh

# terminal 2
python3 bonsai_agent.py -f task.txt
```

It writes `mount/index.html`, screenshots it, looks at the render, fixes what is
wrong and screenshots again — up to 24 steps. Progress is printed as it goes:
thinking tokens stream live, and the long file-writing stretch shows a running
byte count and timer.

```
[step 1/24] generating   (total 0s)
  [thinking] Planning the island, then the pagoda tiers...
  [thought for 2m16s]
  [writing] write_file … 7.4 KB · 1m41s
  [writing] done — 11.2 KB in 3m04s
[tool] screenshot({"path": "index.html"})
[tool] -> screenshot attached (48213 bytes)
```

**Viewing the result.** The preview server only runs while the agent runs, so to
open the scene afterwards serve `mount/` again — `file://` will not work, because
ES module import maps need a real origin:

```bash
python3 -m http.server 8081 --directory mount   # then open http://127.0.0.1:8081/index.html
```

Ways to give it a task, and the flags worth knowing:

| Want | Do |
|---|---|
| Task from a file | `python3 bonsai_agent.py -f task.txt` |
| Task inline | `python3 bonsai_agent.py "Make a landing page in index.html"` |
| Task on stdin | `echo "Fix the header" \| python3 bonsai_agent.py` |
| Hide the reasoning | `--hide-thinking` |
| Deeper thinking | `--effort xhigh` (default `medium`) |
| Bigger files | `--max-tokens 24576` (default 16384) |

If a response is cut off mid-file you get `[!] hit the N-token cap`; raise
`--max-tokens`, and make sure the server has context to match.

### Agent mode without vision

`./run-agent.sh` is the other option: it enables llama-server's own built-in file
tools (scoped to `mount/` by running the server from there) plus the same browser
over MCP, all inside the normal web UI at localhost:8080 — no Python harness.

The catch is that it **cannot see screenshots**. llama-server's `/tools` endpoint
returns only `{"plain_text_response": ...}`, so image content from an MCP result is
dropped before it reaches the vision tower. What does survive is text, and that is
still a lot: `browser_snapshot` gives the accessibility tree, `browser_evaluate`
returns real computed styles and layout numbers, and console messages catch JS
errors. Good enough to verify structure and catch breakage; no use for judging
whether something *looks* right.

Use `bonsai_agent.py` when appearance matters, `run-agent.sh` when it does not.

## Tuning

Both scripts pass extra flags straight through to llama.cpp.

| Want | Do |
|---|---|
| Longer context | `BONSAI_CTX=65536 ./run-server.sh` |
| Shorter, faster answers | `./run-server.sh --reasoning-budget 2048` |
| Different port | `BONSAI_PORT=9090 ./run-server.sh` |
| Free VRAM for context | `./run-server.sh --no-mmproj-offload` (vision encoder to system RAM) |
| Reachable from your LAN | `./run-server.sh --host 0.0.0.0` |

**Context and VRAM.** The KV cache costs 64 KiB/token at FP16, so 32K context ≈ 2 GB.
Your card can take roughly 64K before it gets tight. The model supports up to 262144
tokens, but that's far beyond 12 GB — for very long contexts you'd want `-ctk q4_0
-ctv q4_0` to shrink the KV cache about 3.5x.

**This is a reasoning model and it thinks by default**, at `xhigh` effort. If a
response feels slow, most of that time is thinking tokens, not the hardware. Cap it
rather than disabling it — `--reasoning-budget 2048` keeps most of the quality.
`low` effort is not supported; it behaves like `xhigh`.

**Sampling defaults** are already set in the scripts to the values PrismML used for
their benchmarks (thinking mode): `temp 1.0, top-p 0.95, top-k 20`. For non-thinking
use, `--temp 0.7 --top-p 0.80 --presence-penalty 1.5` is their recommendation.

## The other packing

There is a second build, `PQ2_0` (7.21 GB, 2.13 bits/weight), which processes prompts
faster. It is not installed here — `PTQ1_0` is the right pick on a 12 GB card, and on
Ada-class and memory-constrained cards it actually decodes *faster* too. If you want
to try it anyway (needs ~7.3 GB free disk):

```bash
curl -L -o models/Ternary-Bonsai-2-27B-PQ2_0.gguf \
  https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/main/Ternary-Bonsai-2-27B-PQ2_0.gguf
```

Then edit the `-m` path in `run-server.sh`.

## Caveat on the benchmark claims

The "98.2% retention" figure in the announcement is PrismML's own reported number,
not an independent evaluation. The quality here is genuinely good for 5.5 GiB, but
treat the headline as a vendor claim until third-party evals land.

## Upstream

- Model: <https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf>
- Demo repo (source of truth for setup): <https://github.com/PrismML-Eng/Bonsai-demo>
- llama.cpp fork: <https://github.com/PrismML-Eng/llama.cpp>

Installed build: `prism-b10685-7dffb15` (linux-cuda-12.8-x64), Apache 2.0.
