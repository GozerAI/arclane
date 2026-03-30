"""Arclane Sandbox Proxy — routes LLM requests through the local Claude CLI.

Sits between the Arclane container and your Claude Max account. Exposes an
OpenAI-compatible /v1/chat/completions endpoint that translates to Claude CLI calls.

Usage:
    # Start the proxy (runs on port 8099)
    python tests/sandbox_proxy.py

    # Then configure Arclane to use it:
    ARCLANE_LLM_BASE_URL=http://host.docker.internal:8099
    ARCLANE_LLM_MODEL=claude-sonnet-4-6
    ARCLANE_LLM_API_KEY=sandbox

    # Or for local dev (not in Docker):
    ARCLANE_LLM_BASE_URL=http://localhost:8099
"""

import argparse
import json
import subprocess
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

CLAUDE_CLI = r"C:\Users\chrisfromarose\.local\bin\claude.exe"
PORT = 8099

# Model routing — maps requested model to Claude CLI model flag
MODEL_MAP = {
    # Explicit model IDs
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
    # Short aliases
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    # Default fallback
    "default": "claude-sonnet-4-6",
}

# Stats tracking
stats = {
    "requests": 0,
    "total_tokens_approx": 0,
    "total_time_s": 0,
    "errors": 0,
    "by_model": {},
}


def call_claude(model: str, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> tuple[str, float]:
    """Call Claude CLI and return (output, elapsed_seconds)."""
    cli_model = MODEL_MAP.get(model, MODEL_MAP["default"])

    # Combine system + user prompt for CLI
    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    start = time.time()
    try:
        result = subprocess.run(
            [
                CLAUDE_CLI,
                "-p", full_prompt,
                "--model", cli_model,
                "--output-format", "text",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        elapsed = time.time() - start

        if result.returncode != 0:
            stderr = result.stderr[:500] if result.stderr else "Unknown error"
            return f"Error: {stderr}", elapsed

        output = (result.stdout or "").strip()
        if not output:
            return "Error: Claude CLI returned empty output", elapsed
        return output, elapsed

    except subprocess.TimeoutExpired:
        return "Error: Request timed out after 180s", time.time() - start
    except Exception as e:
        return f"Error: {e}", time.time() - start


class SandboxHandler(BaseHTTPRequestHandler):
    """Handles OpenAI-compatible chat completion requests."""

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            self._handle_completion()
        else:
            self.send_error(404, f"Not found: {self.path}")

    def do_GET(self):
        if self.path == "/health":
            self._respond_json(200, {"status": "ok", "proxy": "sandbox", "stats": stats})
        elif self.path == "/v1/models":
            self._respond_json(200, {
                "data": [
                    {"id": k, "object": "model"} for k in MODEL_MAP if k != "default"
                ]
            })
        elif self.path == "/stats":
            self._respond_json(200, stats)
        elif self.path == "/reset":
            stats.update(requests=0, total_tokens_approx=0, total_time_s=0, errors=0, by_model={})
            self._respond_json(200, {"status": "reset"})
        else:
            self.send_error(404)

    def _handle_completion(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body)
        except Exception as e:
            self.send_error(400, f"Bad request: {e}")
            return

        model = payload.get("model", "default")
        messages = payload.get("messages", [])
        max_tokens = payload.get("max_tokens", 4096)

        # Extract system and user messages
        system_prompt = ""
        user_prompt = ""
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_prompt = content
            elif role == "user":
                user_prompt = content

        if not user_prompt:
            self.send_error(400, "No user message provided")
            return

        cli_model = MODEL_MAP.get(model, MODEL_MAP["default"])
        prompt_words = len(system_prompt.split()) + len(user_prompt.split())

        print(f"\n{'='*60}")
        print(f"  REQUEST #{stats['requests']+1}")
        print(f"  Model: {model} -> {cli_model}")
        print(f"  Prompt: ~{prompt_words} words")
        print(f"  System: {system_prompt[:80]}...")
        print(f"  User: {user_prompt[:80]}...")
        print(f"{'='*60}")
        print(f"  Generating...", end="", flush=True)

        output, elapsed = call_claude(model, system_prompt, user_prompt, max_tokens)
        output_words = len(output.split())
        approx_tokens = int(prompt_words * 1.3 + output_words * 1.3)

        # Update stats
        stats["requests"] += 1
        stats["total_tokens_approx"] += approx_tokens
        stats["total_time_s"] = round(stats["total_time_s"] + elapsed, 1)
        model_stats = stats["by_model"].setdefault(cli_model, {"requests": 0, "time_s": 0, "tokens": 0})
        model_stats["requests"] += 1
        model_stats["time_s"] = round(model_stats["time_s"] + elapsed, 1)
        model_stats["tokens"] += approx_tokens

        if output.startswith("Error:"):
            stats["errors"] += 1
            print(f" ERROR ({elapsed:.1f}s)")
            print(f"  {output[:200]}")
            self.send_error(500, output[:200])
            return

        print(f" done ({elapsed:.1f}s, ~{output_words} words)")

        # Respond in OpenAI format
        response = {
            "id": f"sandbox-{stats['requests']}",
            "object": "chat.completion",
            "model": cli_model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": output,
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": int(prompt_words * 1.3),
                "completion_tokens": int(output_words * 1.3),
                "total_tokens": approx_tokens,
            },
        }

        self._respond_json(200, response)

    def _respond_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Suppress default access logs — we have our own
        pass


def main():
    parser = argparse.ArgumentParser(description="Arclane Sandbox Proxy")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port (default: {PORT})")
    parser.add_argument("--model", default="sonnet", help="Default model (default: sonnet)")
    args = parser.parse_args()

    if args.model in MODEL_MAP:
        MODEL_MAP["default"] = MODEL_MAP[args.model]

    server = HTTPServer(("0.0.0.0", args.port), SandboxHandler)
    print(f"Arclane Sandbox Proxy")
    print(f"  Port: {args.port}")
    print(f"  Default model: {MODEL_MAP['default']}")
    print(f"  Claude CLI: {CLAUDE_CLI}")
    print(f"  Endpoints:")
    print(f"    POST /v1/chat/completions  (OpenAI-compatible)")
    print(f"    GET  /health               (health check)")
    print(f"    GET  /v1/models            (list models)")
    print(f"    GET  /stats                (request stats)")
    print(f"    GET  /reset                (reset stats)")
    print(f"")
    print(f"  Configure Arclane:")
    print(f"    ARCLANE_LLM_BASE_URL=http://host.docker.internal:{args.port}")
    print(f"    ARCLANE_LLM_MODEL=claude-sonnet-4-6")
    print(f"    ARCLANE_LLM_API_KEY=sandbox")
    print(f"")
    print(f"  Waiting for requests...")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n\nFinal stats: {json.dumps(stats, indent=2)}")
        server.server_close()


if __name__ == "__main__":
    main()
