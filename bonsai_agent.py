#!/usr/bin/env python3
"""
Bonsai agent loop: file tools scoped to ./mount + a real browser the model can see.

Why this exists instead of llama-server's built-in --tools/--mcp-servers-config:
that path executes MCP tools fine, but its /tools endpoint returns only
{"plain_text_response": ...} -- image content parts from an MCP result are
dropped, so a screenshot never reaches the vision tower. This harness runs the
tool loop itself and feeds screenshots back as image_url parts, which works.

Usage:
    ./run-server.sh                      # plain server, no --tools needed
    python3 bonsai_agent.py "Build a pricing page in pricing.html and check it looks right"
"""
import argparse, base64, io, json, os, queue, subprocess, sys, threading, time, urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
MOUNT = os.path.join(ROOT, "mount")
API = os.environ.get("BONSAI_API", "http://127.0.0.1:8080/v1/chat/completions")
PREVIEW_PORT = int(os.environ.get("BONSAI_PREVIEW_PORT", "8081"))
MAX_IMG_W = 768          # vision cost grows fast; 768px keeps text legible
MAX_STEPS = 24
PW_OUT = os.path.join(os.environ.get("TMPDIR", "/tmp"), "bonsai-playwright")


# ---------------------------------------------------------------- mount sandbox
def safe_path(rel):
    """Resolve rel inside MOUNT, refusing anything that escapes it."""
    full = os.path.realpath(os.path.join(MOUNT, rel))
    if full != os.path.realpath(MOUNT) and not full.startswith(os.path.realpath(MOUNT) + os.sep):
        raise ValueError(f"path escapes mount/: {rel}")
    return full


# ---------------------------------------------------------------- MCP (browser)
class MCPStdio:
    """Minimal MCP stdio client -- just enough to drive @playwright/mcp."""

    def __init__(self, cmd, env=None):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                  env=env or os.environ, cwd=PW_OUT)
        self.q, self._id = queue.Queue(), 0
        threading.Thread(target=self._reader, daemon=True).start()
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "bonsai-agent", "version": "1"}})
        self._notify("notifications/initialized", {})

    def _reader(self):
        for line in self.p.stdout:
            try:
                self.q.put(json.loads(line))
            except Exception:
                pass

    def _send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _rpc(self, method, params, timeout=180):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        while True:
            m = self.q.get(timeout=timeout)
            if m.get("id") == self._id:
                return m

    def call(self, name, args):
        """Returns (text, [png_bytes, ...])."""
        r = self._rpc("tools/call", {"name": name, "arguments": args})
        res = r.get("result", {})
        if "error" in r:
            return f"error: {r['error']}", []
        texts, images = [], []
        for c in res.get("content", []):
            if c.get("type") == "image":
                images.append(base64.b64decode(c["data"]))
            elif c.get("type") == "text":
                texts.append(c.get("text", ""))
        return "\n".join(texts), images

    def close(self):
        try:
            self.p.terminate()
        except Exception:
            pass


def shrink(png):
    """Downscale a screenshot so prompt processing stays cheap."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        if im.width > MAX_IMG_W:
            im = im.resize((MAX_IMG_W, round(im.height * MAX_IMG_W / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return png


# ---------------------------------------------------------------- tool schemas
TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List files in the mount folder.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file from the mount folder.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string", "description": "Path relative to mount/"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Write a text file into the mount folder, creating or overwriting it.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string", "description": "Path relative to mount/"},
                                      "content": {"type": "string", "description": "Full file contents"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "screenshot",
        "description": ("Open an HTML file from the mount folder in a real Chrome browser and "
                        "return a screenshot of it, so you can see how it actually renders. "
                        "Use this to check your work after writing HTML."),
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string", "description": "HTML file relative to mount/, e.g. index.html"},
                                      "full_page": {"type": "boolean", "description": "Capture the whole scrollable page (default false)"},
                                      "wait_ms": {"type": "integer", "description": "Wait this long before capturing, for pages that load scripts from a CDN or animate (default 2000)"}},
                       "required": ["path"]}}},
]


def run_tool(mcp, name, args):
    """Returns (text_for_model, [png_bytes, ...])."""
    if name == "list_files":
        out = []
        for d, dirs, fs in os.walk(MOUNT):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            for f in fs:
                if not f.startswith("."):
                    out.append(os.path.relpath(os.path.join(d, f), MOUNT))
        return ("\n".join(sorted(out)) or "(empty)"), []
    if name == "read_file":
        return open(safe_path(args["path"]), encoding="utf-8").read(), []
    if name == "write_file":
        p = safe_path(args["path"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(args["content"])
        return f"Wrote {len(args['content'])} bytes to {args['path']}", []
    if name == "screenshot":
        safe_path(args["path"])                       # reject escapes before we navigate
        url = f"http://127.0.0.1:{PREVIEW_PORT}/{args['path'].lstrip('/')}"
        t1, _ = mcp.call("browser_navigate", {"url": url})
        if "### Error" in t1:
            return f"Could not open {url}:\n{t1}", []
        # CDN imports and the first WebGL frame need a moment; without this the
        # screenshot catches an empty page.
        time.sleep(max(0, int(args.get("wait_ms", 2000))) / 1000.0)
        t2, imgs = mcp.call("browser_take_screenshot",
                            {"type": "png", "fullPage": bool(args.get("full_page"))})
        errs, _ = mcp.call("browser_console_messages", {"onlyErrors": True})
        note = ""
        if errs and errs.strip() and "no console messages" not in errs.lower():
            note = f"\nConsole errors:\n{errs.split('### Ran')[0].strip()[:800]}"
        return f"Screenshot of {args['path']} taken.{note}", [shrink(i) for i in imgs]
    return f"unknown tool {name}", []


# ---------------------------------------------------------------- the loop
class Heartbeat:
    """Print an elapsed-time tick while a blocking request is in flight."""

    def __init__(self, label, every=15):
        self.label, self.every, self.done = label, every, threading.Event()

    def __enter__(self):
        self.t0 = time.time()
        def tick():
            while not self.done.wait(self.every):
                print(f"    {self.label} … {dur(time.time() - self.t0)}", flush=True)
        threading.Thread(target=tick, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.done.set()
        self.elapsed = time.time() - self.t0


DIM, RESET = "\033[2m", "\033[0m"


def dur(sec):
    sec = int(sec)
    return f"{sec}s" if sec < 60 else f"{sec // 60}m{sec % 60:02d}s"


def human(n):
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB"


def chat(messages, effort, stream=True, show_thinking=True, on_token=None,
         max_tokens=16384):
    """Streams by default, printing reasoning tokens as they arrive.

    Returns the same shape as the non-streaming endpoint, so the caller does
    not care which mode was used.
    """
    payload = {"messages": messages, "tools": TOOLS, "max_tokens": max_tokens,
               "chat_template_kwargs": {"reasoning_effort": effort}}
    if stream:
        payload["stream"] = True
    req = urllib.request.Request(API, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    if not stream:
        with urllib.request.urlopen(req, timeout=3600) as r:
            return json.load(r)

    content, reasoning, calls, timings, finish, mode = [], [], {}, {}, None, None
    tool_bytes, shown_kb, t0, think_t0 = 0, -1, time.time(), None
    colour = sys.stdout.isatty()
    with urllib.request.urlopen(req, timeout=3600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ch = json.loads(data)
            except json.JSONDecodeError:
                continue
            if ch.get("timings"):
                timings = ch["timings"]
            if ch.get("error"):
                return {"error": ch["error"]}
            for c in ch.get("choices", []):
                if c.get("finish_reason"):
                    finish = c["finish_reason"]
                d = c.get("delta") or {}

                rc = d.get("reasoning_content")
                if rc:
                    if on_token:
                        on_token()
                    reasoning.append(rc)
                    if show_thinking:
                        if mode != "think":
                            print(f"\n  {DIM if colour else ''}[thinking] ", end="", flush=True)
                            mode, think_t0 = "think", time.time()
                        print(rc, end="", flush=True)

                tc = d.get("content")
                if tc:
                    if on_token:
                        on_token()
                    content.append(tc)
                    if mode == "think":
                        print((RESET if colour else "")
                              + f"\n  [thought for {dur(time.time() - think_t0)}]", flush=True)
                    if mode != "say":
                        print("\n--- assistant ---", flush=True)
                        mode = "say"
                    print(tc, end="", flush=True)

                for t in d.get("tool_calls") or []:
                    if on_token:
                        on_token()
                    e = calls.setdefault(t.get("index", 0),
                                         {"id": None, "type": "function",
                                          "function": {"name": "", "arguments": ""}})
                    if t.get("id"):
                        e["id"] = t["id"]
                    f = t.get("function") or {}
                    if f.get("name"):
                        e["function"]["name"] = f["name"]
                    if f.get("arguments"):
                        e["function"]["arguments"] += f["arguments"]
                        tool_bytes += len(f["arguments"])

                    # Writing a whole HTML file can take minutes; without this
                    # the run looks hung for its longest stretch.
                    if mode != "tool":
                        if mode == "think":
                            print((RESET if colour else "")
                                  + f"\n  [thought for {dur(time.time() - think_t0)}]", flush=True)
                        print(f"\n  [writing] {e['function']['name']}", end="", flush=True)
                        mode = "tool"
                    if tool_bytes // 256 != shown_kb:
                        shown_kb = tool_bytes // 256
                        if colour:
                            print(f"\r  [writing] {e['function']['name']} … "
                                  f"{human(tool_bytes)} · {dur(time.time() - t0)}",
                                  end="", flush=True)
                        else:
                            print(".", end="", flush=True)

    if mode == "think":
        print((RESET if colour else "")
              + f"\n  [thought for {dur(time.time() - think_t0)}]", flush=True)
    if mode == "tool":
        print(f"\r  [writing] done — {human(tool_bytes)} in {dur(time.time() - t0)}"
              + " " * 24, flush=True)
    elif mode:
        print(flush=True)
    if finish == "length":
        print(f"  [!] hit the {max_tokens}-token cap mid-response; "
              f"raise it with --max-tokens", flush=True)

    msg = {"role": "assistant", "content": "".join(content)}
    if reasoning:
        msg["reasoning_content"] = "".join(reasoning)
    if calls:
        msg["tool_calls"] = [calls[k] for k in sorted(calls) if calls[k]["function"]["name"]]
    return {"choices": [{"message": msg, "finish_reason": finish}], "timings": timings}


SYSTEM = (
    "You are a web developer with a sandboxed folder and a real browser.\n"
    "Write HTML/CSS/JS files with write_file, then ALWAYS call screenshot to look at the "
    "rendered result before telling the user you are done. If the screenshot shows a layout "
    "problem, fix the file and screenshot again. All paths are relative to the mount folder."
)


def main():
    ap = argparse.ArgumentParser(
        description="Build HTML in mount/ and visually check it in a real browser.",
        epilog="The task can be an argument, -f FILE, or piped on stdin.")
    ap.add_argument("task", nargs="?", help="what to build (omit to read stdin)")
    ap.add_argument("-f", "--task-file", help="read the task from a file")
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "xhigh"])
    ap.add_argument("--hide-thinking", action="store_true",
                    help="don't print the model's reasoning tokens")
    ap.add_argument("--max-tokens", type=int, default=16384,
                    help="cap on one response (default 16384; a big scene needs room)")
    ap.add_argument("--no-stream", action="store_true",
                    help="wait for each full response instead of streaming it")
    a = ap.parse_args()

    if a.task_file:
        a.task = open(a.task_file, encoding="utf-8").read().strip()
    elif not a.task and not sys.stdin.isatty():
        a.task = sys.stdin.read().strip()
    if not a.task:
        ap.error("no task given -- pass it as an argument, with -f FILE, or on stdin")
    print(f"[task] {a.task[:120]}{'...' if len(a.task) > 120 else ''}", flush=True)

    os.makedirs(MOUNT, exist_ok=True)
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/favicon.ico":       # otherwise every page logs a 404
                self.send_response(204); self.end_headers(); return
            super().do_GET()

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", PREVIEW_PORT),
                                partial(Handler, directory=MOUNT))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"[preview] serving mount/ at http://127.0.0.1:{PREVIEW_PORT}", flush=True)

    os.makedirs(PW_OUT, exist_ok=True)
    env = dict(os.environ)
    mcp = MCPStdio(["npx", "-y", "@playwright/mcp@latest", "--browser", "chrome",
                    "--headless", "--isolated", "--output-dir", PW_OUT], env)
    print("[browser] playwright mcp ready", flush=True)

    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": a.task}]
    run_t0 = time.time()
    try:
        for step in range(MAX_STEPS):
            print(f"\n[step {step + 1}/{MAX_STEPS}] generating"
                  f"   (total {dur(time.time() - run_t0)})", flush=True)
            try:
                # The heartbeat covers prompt processing (no tokens yet); the
                # first token silences it and the stream takes over.
                with Heartbeat("processing prompt") as hb:
                    resp = chat(messages, a.effort,
                                stream=not a.no_stream,
                                show_thinking=not a.hide_thinking,
                                on_token=hb.done.set,
                                max_tokens=a.max_tokens)
            except urllib.error.URLError as e:
                print(f"[!] cannot reach llama-server at {API} ({e.reason}).\n"
                      f"    Start it first:  ./run-server.sh")
                return 1
            if "error" in resp:
                print("[server error]", resp["error"].get("message")); return 1
            t = resp.get("timings") or {}
            print(f"    step done in {dur(hb.elapsed)}"
                  + (f" ({t['predicted_n']} tok @ {t['predicted_per_second']:.1f} tok/s)"
                     if t.get("predicted_per_second") else ""), flush=True)

            msg = resp["choices"][0]["message"]
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if a.no_stream:      # streaming already printed these live
                if msg.get("reasoning_content") and not a.hide_thinking:
                    print(f"\n  [thinking] {msg['reasoning_content'].strip()}", flush=True)
                if msg.get("content"):
                    print(f"\n--- assistant ---\n{msg['content']}\n", flush=True)
            if not calls:
                print(f"\n[done] {step + 1} steps in {dur(time.time() - run_t0)}", flush=True)
                return 0
            for c in calls:
                fn = c["function"]["name"]
                raw = c["function"]["arguments"] or "{}"
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError as e:
                    # Usually means the response was cut off mid-argument.
                    print(f"[tool] {fn}: arguments truncated after {len(raw)} bytes "
                          f"({e}) -- asking the model to retry", flush=True)
                    messages.append({"role": "tool", "tool_call_id": c["id"],
                                     "content": ("Your tool call was cut off before the "
                                                 "arguments finished. Write the file in "
                                                 "smaller pieces, or shorten it.")})
                    continue
                print(f"[tool] {fn}({json.dumps(args)[:100]})", flush=True)
                try:
                    text, imgs = run_tool(mcp, fn, args)
                except Exception as e:
                    text, imgs = f"error: {e}", []
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": text})
                # Tool-role messages can't carry images, so hand the screenshot
                # over as a user turn -- that is the part the vision tower reads.
                for png in imgs:
                    b = base64.b64encode(png).decode()
                    messages.append({"role": "user", "content": [
                        {"type": "text", "text": "Here is the screenshot you requested."},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b}}]})
                    print(f"[tool] -> screenshot attached ({len(png)} bytes)", flush=True)
        print("[!] hit step limit")
        return 1
    finally:
        mcp.close()
        httpd.shutdown()


if __name__ == "__main__":
    sys.exit(main())
