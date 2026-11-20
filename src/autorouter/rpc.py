"""Synchronous JSON-RPC client for the installed official Codex App Server."""

import json, subprocess, queue, threading, time, sys


class RPC:
    def __init__(self, event=None, timeout=180, binary="codex", env=None):
        self.event = event or (lambda x: None)
        self.timeout, self.seq, self.messages = timeout, 0, queue.Queue()
        self.p = subprocess.Popen(
            [binary, "app-server", "--stdio"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

        def reader():
            for line in self.p.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    pass
            self.messages.put(None)

        threading.Thread(target=reader, daemon=True).start()
        try:
            self.call(
                "initialize",
                {
                    "clientInfo": {"name": "autorouter", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def send(self, value):
        self.p.stdin.write(json.dumps(value) + "\n")
        self.p.stdin.flush()

    def receive(self, deadline):
        try:
            m = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError(
                "Codex request timed out; inspect persisted task before resuming"
            )
        if m is None:
            raise RuntimeError("Codex App Server disconnected")
        if "method" in m and "id" in m:
            self.event({"method": "approval/pending", "params": m})
            # Explicit interactive decision; unattended clients fail closed.
            if sys.stdin.isatty() and m["method"].endswith("/requestApproval"):
                print("\nCodex approval:", json.dumps(m), file=sys.stderr)
                yes = (
                    input("Approve this operation for this request? [y/N] ").lower()
                    == "y"
                )
            else:
                yes = False
            if m["method"].endswith("/requestApproval") and (
                "commandExecution" in m["method"] or "fileChange" in m["method"]
            ):
                self.send(
                    {
                        "id": m["id"],
                        "result": {"decision": "accept" if yes else "decline"},
                    }
                )
            elif m["method"] == "item/tool/requestUserInput":
                answers = {}
                for question in m["params"].get("questions", []):
                    if sys.stdin.isatty():
                        print(question.get("question", ""), file=sys.stderr)
                        options = question.get("options") or []
                        for i, option in enumerate(options):
                            print(f"{i + 1}. {option['label']}", file=sys.stderr)
                        answer = input("Answer (or option number): ")
                        if answer.isdigit() and 1 <= int(answer) <= len(options):
                            answer = options[int(answer) - 1]["label"]
                        answers[question["id"]] = {"answers": [answer]}
                    else:
                        answers[question["id"]] = {"answers": ["Decline"]}
                self.send({"id": m["id"], "result": {"answers": answers}})
            else:
                self.send(
                    {
                        "id": m["id"],
                        "error": {
                            "code": -32601,
                            "message": "Unsupported interactive request; use standard Codex",
                        },
                    }
                )
            self.event(
                {
                    "method": "approval/resolved",
                    "params": {"id": m["id"], "interactive": sys.stdin.isatty()},
                }
            )
        elif "method" in m:
            self.event(m)
        return m

    def call(self, method, params=None):
        self.seq += 1
        rid = self.seq
        self.send({"id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while True:
            m = self.receive(deadline)
            if m.get("id") == rid and "method" not in m:
                if "error" in m:
                    raise RuntimeError(json.dumps(m["error"]))
                return m.get("result", {})

    def models(self):
        out = []
        cursor = None
        seen = set()
        while True:
            r = self.call(
                "model/list", {"cursor": cursor, "limit": 100, "includeHidden": False}
            )
            out += r["data"]
            cursor = r.get("nextCursor")
            if not cursor:
                return out
            if cursor in seen:
                raise RuntimeError("Repeated model pagination cursor")
            seen.add(cursor)

    def turn(
        self,
        thread,
        model,
        effort,
        prompt,
        schema=None,
        disabled_plugins=None,
        images=None,
    ):
        p = {
            "threadId": thread,
            "model": model,
            "effort": effort,
            "input": [{"type": "text", "text": prompt}],
        }
        if images:
            p["input"].extend(
                {"type": "localImage", "path": str(path)} for path in images
            )
        if schema:
            p["outputSchema"] = schema
        if disabled_plugins is not None:
            p["disabledPluginIds"] = disabled_plugins
        r = self.call("turn/start", p)
        tid = r["turn"]["id"]
        text = []
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                m = self.receive(deadline)
                q = m.get("params", {})
                if q.get("threadId") != thread:
                    continue
                if (
                    m.get("method") == "item/completed"
                    and q.get("item", {}).get("type") == "agentMessage"
                    and q["item"].get("phase") != "commentary"
                ):
                    text.append(q["item"]["text"])
                if m.get("method") == "turn/completed" and q["turn"]["id"] == tid:
                    if q["turn"]["status"] != "completed":
                        raise RuntimeError(json.dumps(q["turn"]))
                    return "\n".join(text)
        except (KeyboardInterrupt, TimeoutError):
            self.send(
                {
                    "id": self.seq + 1,
                    "method": "turn/interrupt",
                    "params": {"threadId": thread, "turnId": tid},
                }
            )
            raise

    def close(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
