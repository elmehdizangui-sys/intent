#!/usr/bin/env python3
"""
MCP stdio server for intent — wraps intent_blocks.py as tools.

Transport: newline-delimited JSON-RPC 2.0 over stdin/stdout.
No dependencies beyond stdlib — the IDE spawns this automatically.

Tools exposed:
  intent_find     — scan tree for @intent:/@for: markers
  intent_verify   — re-hash regions, report drift
  intent_list     — markers in a file with receipt ids
  intent_explain  — full receipt for a file:line
  intent_ack      — acknowledge drift, update hash
  intent_finalize — write receipt after edit + mark
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "intent_blocks.py"

TOOLS = [
    {
        "name": "intent_find",
        "description": "Scan the project tree for all @intent:/@for: markers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to scan. Defaults to repo root."
                }
            }
        }
    },
    {
        "name": "intent_verify",
        "description": "Re-hash annotated regions and report drift against stored receipts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "strict": {
                    "type": "boolean",
                    "description": "Exit non-zero on any warning (for CI use)."
                }
            }
        }
    },
    {
        "name": "intent_list",
        "description": "Show all @intent:/@for: markers in a file with linked receipt ids.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string"}
            },
            "required": ["file"]
        }
    },
    {
        "name": "intent_explain",
        "description": "Show the full receipt for the intent region at a given file:line.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "file:line — e.g. src/main.py:42"
                }
            },
            "required": ["location"]
        }
    },
    {
        "name": "intent_ack",
        "description": "Acknowledge hash drift and update the stored receipt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id":     {"type": "string", "description": "Receipt id (prefix ok)"},
                "region": {"type": "integer", "description": "Region index (default 0)"},
                "reason": {"type": "string"}
            },
            "required": ["id", "reason"]
        }
    },
    {
        "name": "intent_finalize",
        "description": "Write a receipt after code has been edited and marked with @intent:/@for:.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan":    {"type": "string", "description": "Numbered plan shown to dev before editing"},
                "regions": {"type": "string", "description": "JSON array of region objects"},
                "model":   {"type": "string", "description": "Model id that made the edits"},
                "prompt":  {"type": "string", "description": "Verbatim original dev request"}
            },
            "required": ["plan", "regions"]
        }
    }
]


def run_intent(args: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
    )
    out = result.stdout.strip()
    err = result.stderr.strip()
    if err:
        out = f"{out}\n{err}".strip()
    return out


def dispatch_tool(name: str, arguments: dict) -> str:
    if name == "intent_find":
        args = ["find"]
        if arguments.get("path"):
            args.append(arguments["path"])
        return run_intent(args)

    if name == "intent_verify":
        args = ["verify"]
        if arguments.get("path"):
            args.append(arguments["path"])
        if arguments.get("strict"):
            args.append("--strict")
        return run_intent(args)

    if name == "intent_list":
        return run_intent(["list", arguments["file"]])

    if name == "intent_explain":
        return run_intent(["explain", arguments["location"]])

    if name == "intent_ack":
        args = ["ack", arguments["id"], "--reason", arguments["reason"]]
        if arguments.get("region") is not None:
            args += ["--region", str(arguments["region"])]
        return run_intent(args)

    if name == "intent_finalize":
        args = [
            "finalize",
            "--plan", arguments["plan"],
            "--regions", arguments["regions"],
        ]
        if arguments.get("model"):
            args += ["--model", arguments["model"]]
        if arguments.get("prompt"):
            args += ["--prompt", arguments["prompt"]]
        return run_intent(args)

    return f"Unknown tool: {name}"


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(request: dict) -> None:
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    # Notifications have no id — do not respond.
    if req_id is None and method != "initialize":
        return

    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "intent", "version": "1.0"}
            }
        })

    elif method == "tools/list":
        send({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS}
        })

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            output = dispatch_tool(tool_name, arguments)
            send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": output}]
                }
            })
        except Exception as exc:
            send({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)}
            })

    else:
        send({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        })


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            handle(json.loads(raw))
        except json.JSONDecodeError:
            pass


if __name__ == "__main__":
    main()
