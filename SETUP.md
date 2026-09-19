# Setup guide — Ternary Bonsai 2 27B, and the Pagoda Garden scene

End to end: a 27B vision-language model running locally on a 12 GB card, wired up so
it can write a Three.js scene, open it in a real browser, look at the screenshot and
fix its own mistakes.

Everything below was verified on the machine this repo lives on. Where something is
*not* verified, it says so.

---

## 1. What you need

### Hardware

| | Minimum | This machine |
|---|---|---|
| GPU | NVIDIA, 8 GB VRAM | RTX 3060, 12 GB |
| Disk | ~10 GB for model + runtime | 6.2 GB models, 1.1 GB runtime |
| RAM | 16 GB | — |

The model is 5.5 GiB of weights at 1.75 bits/weight. With a 32K context and the
vision tower loaded it sits at **9.2 GB of 12 GB**, leaving about 2.7 GB headroom.
An 8 GB card will need a shorter context or `--no-mmproj-offload`.

Add roughly **3 GB** more if you use agent mode — Playwright caches browser builds
under `~/.npm/_npx`.

### Software

Verified working versions:

| Component | Version here | Notes |
|---|---|---|
| OS | Ubuntu 26.04.1 LTS, kernel 7.0.0-31 | any modern Linux x86-64 |
| NVIDIA driver | 610.57.04 | must support CUDA 12.x |
| Python | 3.14.4 (system) | 3.9+ is fine; no venv needed |
| Pillow | 12.1.1 | only for screenshot downscaling |
| Node | v24.16.0 | agent mode only |
| Google Chrome | 152.0.7977.82 | agent mode only |

No CUDA *toolkit* is required — the runtime libraries ship in `bin/`.

Check your driver:

```bash
nvidia-smi
```

If that fails but `lsmod | grep nvidia` shows the module loaded, the driver and the
userspace tools have drifted apart — usually a kernel update without a matching DKMS
rebuild. Fix that before going further, or everything silently falls back to CPU and
runs roughly ten times slower.

---

## 2. Get the runtime

`bin/` holds a **PrismML fork of llama.cpp**, not stock llama.cpp. This matters more
than it sounds.

Bonsai 2 stores its weights in a rotated (Hadamard) basis. Stock llama.cpp rejects
`PTQ1_0` as an unknown type, so it will not run these files at all. Worse, a `Q2_0`
band of this model exists in the wild: stock llama.cpp **loads it without any warning
and produces fluent gibberish**, because `Q2_0` is a type it already knows.

> If output ever turns to nonsense, check which binary is running before anything else.

Do **not** `apt install` or `brew install` llama.cpp.

Download the build matching your platform from
<https://github.com/PrismML-Eng/llama.cpp> — the one installed here is
`prism-b10685-7dffb15`, `linux-cuda-12.8-x64` (Apache 2.0), reporting itself as
`version: 0.2.0-dev (build 10685, commit 7dffb158d)`. Unpack it into `bin/`.

If the machine has no CUDA toolkit, add the two runtime libraries from NVIDIA's pip
wheels — `libcudart.so.12` from `nvidia-cuda-runtime-cu12` and `libcublas.so.12`
(plus `libcublasLt.so.12`) from `nvidia-cublas-cu12` — and drop them in `bin/`
alongside the binaries. The launcher scripts set `LD_LIBRARY_PATH` for you.

Use the **CUDA 12.8** build rather than 13.x: the 13.x runtime libraries are not
published as wheels yet, and a 12.x runtime works fine on a 13-capable driver.

Verify nothing is missing:

```bash
LD_LIBRARY_PATH=bin ldd bin/llama-server | grep "not found"   # should print nothing
```

---

## 3. Get the models

Two files, into `models/`:

```bash
mkdir -p models
curl -L -o models/Ternary-Bonsai-2-27B-PTQ1_0.gguf \
  https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/main/Ternary-Bonsai-2-27B-PTQ1_0.gguf

curl -L -o models/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf \
  https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/main/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf
```

| File | Size | Purpose |
|---|---|---|
| `Ternary-Bonsai-2-27B-PTQ1_0.gguf` | 5.5 GiB | language model |
| `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` | 601 MiB | vision tower |

**The mmproj file is not optional here.** Without it the model cannot see images, and
the whole point of the Pagoda Garden workflow is that it looks at its own output.

There is also a `PQ2_0` band (7.21 GB, 2.13 bits/weight) that processes prompts
faster. On a 12 GB card `PTQ1_0` is the right pick — on Ada-class and
memory-constrained cards it actually decodes faster too.

---

## 4. Check the base setup

```bash
chmod +x run-server.sh run-cli.sh run-agent.sh
./run-server.sh
```

Cold load takes about 20 seconds. Open <http://localhost:8080> and ask it anything.

Expect roughly **27.6 tok/s** generation and **272 tok/s** prompt processing on a
3060. If you are seeing 2–3 tok/s, it is running on CPU — go back and fix the driver.

Ctrl+C to stop.

---

## 5. Agent prerequisites

Agent mode needs Node and Chrome:

```bash
node --version            # v24.16.0 here
google-chrome --version   # 152.0.7977.82 here
python3 -c "import PIL"   # Pillow, for downscaling screenshots
```

Playwright itself is fetched by `npx` on first run — no install step, but the first
run pauses a while to download it.

Two things worth knowing about how the sandbox works:

- **`mount/` is the only folder the model can touch.** Paths are resolved and checked
  against it, so `../`, absolute paths and nested traversal are all rejected.
- **Pages are served over HTTP, not `file://`.** Playwright blocks the `file:`
  protocol outright, and ES module import maps need a real origin anyway. The harness
  starts a static server on port 8081 for the duration of the run.

---

## 6. Run the Pagoda Garden scene

The task lives in `task.txt`: a voxel Pagoda Garden in Three.js — grass island,
5-tier pagoda, torii, cherry trees, pond with a bridge, stone lanterns, sunset light
with shadows, auto-rotating camera.

Two terminals.

```bash
# terminal 1 — screenshots eat context, so give it room
BONSAI_CTX=65536 ./run-server.sh
```

```bash
# terminal 2
python3 bonsai_agent.py -f task.txt
```

The loop: write `mount/index.html` → open it in headless Chrome → screenshot →
look at the render → fix what is wrong → screenshot again. Up to 24 steps.

### What you will see

```
[task] Make a small voxel Pagoda Garden scene in Three.js...
[preview] serving mount/ at http://127.0.0.1:8081
[browser] playwright mcp ready

[step 1/24] generating   (total 0s)
    processing prompt … 15s

  [thinking] Planning the island, then the pagoda tiers, then lanterns...
  [thought for 2m16s]

  [writing] write_file … 7.4 KB · 1m41s
  [writing] done — 11.2 KB in 3m04s
    step done in 5m20s (4100 tok @ 12.0 tok/s)
[tool] write_file({"path": "index.html", ...
[tool] screenshot({"path": "index.html"})
[tool] -> screenshot attached (48213 bytes)

[step 2/24] generating   (total 5m20s)
```

Reasoning streams live. The file-writing stretch is the longest part of each step,
so it shows a running byte count and timer — that line is your proof it has not hung.

The `tok/s` figure is also your fastest sanity check on the GPU: **~27 tok/s means
the GPU is working, ~2 tok/s means it is not.**

> The block above is illustrative. It comes from simulated-stream tests, not a
> completed GPU run — the timings are plausible, not measured.

### Options

| Want | Do |
|---|---|
| Task from a file | `python3 bonsai_agent.py -f task.txt` |
| Task inline | `python3 bonsai_agent.py "Make a landing page in index.html"` |
| Task on stdin | `echo "Fix the header" \| python3 bonsai_agent.py` |
| Quieter output | `--hide-thinking` |
| Deeper thinking | `--effort xhigh` (default `medium`, also `low`) |
| Bigger files | `--max-tokens 24576` (default 16384) |
| No live streaming | `--no-stream` |

---

## 7. View the result

The preview server only lives as long as the agent run. To open the scene afterwards,
serve `mount/` again:

```bash
python3 -m http.server 8081 --directory mount
```

Then open <http://127.0.0.1:8081/index.html>.

**Do not open it with `file://`.** Chrome blocks ES module import maps without a real
origin, so Three.js never loads and you get a blank page with nothing in the console
to explain why.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Fluent nonsense | Stock llama.cpp on a `Q2_0` file | Check which binary is running |
| `unknown type PTQ1_0` | Stock llama.cpp | Use the PrismML fork in `bin/` |
| 2–3 tok/s | Running on CPU | `nvidia-smi`; rebuild DKMS after kernel updates |
| Long silence after `[thinking]` | Normal — writing the file | Watch the `[writing]` byte counter |
| `[!] hit the N-token cap` | Response cut off mid-file | Raise `--max-tokens`, and `BONSAI_CTX` to match |
| `arguments truncated` | Same, caught and retried | Harmless; raise the cap if it repeats |
| Blank page when opened | Opened via `file://` | Serve over HTTP (section 7) |
| `cannot reach llama-server` | Server not started | `./run-server.sh` in another terminal |
| `Unexpected reasoning effort` | Only `xhigh`/`medium`/`low` accepted | Use one of those |
| Port already in use | Stale server | `BONSAI_PORT=9090`, `BONSAI_PREVIEW_PORT=8082` |

---

## 9. File reference

| Path | What it is |
|---|---|
| `bin/` | PrismML llama.cpp fork + CUDA 12.8 runtime libraries |
| `models/` | Language model and vision tower |
| `mount/` | The sandbox — the only folder the agent can read or write |
| `run-server.sh` | Chat server + web UI on :8080 |
| `run-cli.sh` | Terminal chat, no server |
| `run-agent.sh` | Server with built-in file/browser tools (text-only checking) |
| `bonsai_agent.py` | Agent loop that can *see* its own pages |
| `mcp.json` | Playwright browser config, used by `run-agent.sh` |
| `task.txt` | The Pagoda Garden task |

### Why there are two agent modes

`run-agent.sh` uses llama-server's own built-in tools and MCP support, all inside the
normal web UI — no Python harness. It is the more convenient option, and it **cannot
see screenshots**.

That is a limitation of the server, not a configuration mistake. Its `/tools` endpoint
returns only `{"plain_text_response": ...}`, so image content from an MCP result is
dropped before it reaches the vision tower. Measured directly: a screenshot that
arrived from Playwright as ~19 KB of base64 PNG came back out as 365 bytes of text.

What *does* survive is text, and that is still useful — `browser_snapshot` returns the
accessibility tree, `browser_evaluate` returns real computed styles and layout
numbers, `browser_console_messages` catches JS errors. Enough to verify structure and
catch breakage; no use for judging whether something looks right.

`bonsai_agent.py` exists to close that gap: it runs the tool loop itself and feeds
screenshots back as `image_url` content parts, which the vision tower does read.

**Use `bonsai_agent.py` when appearance matters, `run-agent.sh` when it does not.**

---

## 10. Tuning

| Want | Do |
|---|---|
| Longer context | `BONSAI_CTX=65536 ./run-server.sh` |
| Shorter, faster answers | `./run-server.sh --reasoning-budget 2048` |
| Different port | `BONSAI_PORT=9090 ./run-server.sh` |
| Free VRAM for context | `./run-server.sh --no-mmproj-offload` |
| Reachable from your LAN | `./run-server.sh --host 0.0.0.0` |

The KV cache costs 64 KiB/token at FP16, so 32K context ≈ 2 GB. A 12 GB card takes
roughly 64K before it gets tight. For very long contexts, `-ctk q4_0 -ctv q4_0`
shrinks the KV cache about 3.5×.

This is a reasoning model and it thinks by default, at `xhigh` effort. If a response
feels slow, most of that time is thinking tokens, not the hardware. Cap it rather than
disabling it.

Sampling defaults in the scripts match PrismML's benchmark settings for thinking mode
(`temp 1.0, top-p 0.95, top-k 20`). For non-thinking use they recommend
`--temp 0.7 --top-p 0.80 --presence-penalty 1.5`.

---

## Upstream

- Model: <https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf>
- Demo repo (source of truth for setup): <https://github.com/PrismML-Eng/Bonsai-demo>
- llama.cpp fork: <https://github.com/PrismML-Eng/llama.cpp>

The "98.2% retention" figure in the announcement is PrismML's own reported number, not
an independent evaluation. The quality is genuinely good for 5.5 GiB, but treat the
headline as a vendor claim until third-party evals land.
