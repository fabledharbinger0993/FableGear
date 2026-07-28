#!/usr/bin/env bash
# FableGear MCP — start SSE server + Cloudflare tunnel
# Usage: ./start_remote.sh [PORT]
#
# Starts the MCP server in SSE mode on localhost, then opens a
# Cloudflare Quick Tunnel so any agent with the URL can connect.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:-8765}"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"

if ! command -v cloudflared &>/dev/null; then
    echo "cloudflared not found. Install with: brew install cloudflared" >&2
    exit 1
fi

cleanup() {
    echo ""
    echo "Shutting down..."
    kill "$MCP_PID" 2>/dev/null || true
    kill "$TUNNEL_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

echo "Starting FableGear MCP server on port $PORT..."
"$VENV_PYTHON" "$SCRIPT_DIR/mcp_server.py" --transport sse --port "$PORT" &
MCP_PID=$!
sleep 3

if ! kill -0 "$MCP_PID" 2>/dev/null; then
    echo "MCP server failed to start." >&2
    exit 1
fi

echo "MCP server running. Starting Cloudflare tunnel..."
echo ""
echo "Look for the https://<random>.trycloudflare.com URL below."
echo "Remote agents connect to that URL + /sse"
echo "Local agents connect to http://localhost:$PORT/sse"
echo ""
echo "Press Ctrl+C to stop both."
echo "────────────────────────────────────────────────────────────────"

cloudflared tunnel --url "http://localhost:$PORT" &
TUNNEL_PID=$!

wait
