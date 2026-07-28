# Embedded MCP Server Design

## Summary

Ship a stable MCP (Model Context Protocol) server inside FableGear so every user
gets an AI agent endpoint that manages their own library. The server starts with
the app (if configured), binds to localhost by default, and optionally exposes
to the network with token auth for cloud agents.

Dev mode unlocks diagnostic tools for the developer without shipping a separate
server.

## Architecture

```
┌─────────────────────────────────────────────┐
│  main.py                                    │
│  ├─ Flask/Waitress  :5001  (UI + API)       │
│  └─ MCP SSE server  :5002  (agent endpoint) │
│       └─ daemon thread, dies with app       │
└─────────────────────────────────────────────┘
         │ localhost (default)
         │ 0.0.0.0 + token (exposed mode)
         │
    ┌────┴────┐     ┌──────────────┐
    │ Claude  │     │ Cloud agents │
    │ Desktop │     │ (Kimi, etc.) │
    │ Cursor  │     │ via Tailscale│
    └─────────┘     │ or tunnel    │
                    └──────────────┘
```

### Transport

SSE (Server-Sent Events) — the HTTP-based MCP transport. Endpoints:
- `GET /sse` — event stream (server → client)
- `POST /messages?session_id=...` — client → server

SSE is the most widely supported transport across MCP clients today. The server
uses FastMCP (Python MCP SDK) which handles the protocol internally via
Starlette/uvicorn.

### Port Selection

1. Default: 5002
2. On startup, check if 5002 is available
3. If taken, probe 5003–5010 for an open port
4. Persist the chosen port to `~/.fablegear/config.json` → `mcp_port`
5. If a persisted port exists and is available, use it (stable across restarts)
6. Flask exposes `/api/mcp-info` so the UI always knows the active port

### Authentication

- **Localhost binding** (`127.0.0.1`): no auth required. Only local processes
  can connect.
- **Exposed binding** (`0.0.0.0`): requires a Bearer token. The token is a
  32-byte hex string generated on first enable, saved to
  `~/.fablegear/config.json` → `mcp_token`. Cloud agents include it as
  `Authorization: Bearer <token>` or `?token=<token>` query param.

### Dev Mode

Activates when `.dev` file exists in repo root OR `FABLEGEAR_DEV=1` env var.

Extra tools registered only in dev mode:
- `get_health_report` — tool chain status, checkpoint integrity, config validation
- `run_test_suite` — execute tests, return results to agent
- `get_tool_manifest` — structured dump of all tool signatures and states
- `replay_job` — re-run a job from history with verbose tracing
- `get_logs` — tail app log with filtering

Same server, same port, same auth. Tools simply aren't registered when the
sentinel is absent.

## User Config Schema Additions

New keys in `~/.fablegear/config.json`:

```json
{
  "mcp_enabled": false,
  "mcp_autostart": false,
  "mcp_port": 5002,
  "mcp_token": "",
  "mcp_expose": false
}
```

## UX Flow

### Install / Onboarding

The onboarding wizard gets a new optional step: "AI Agent Access (MCP Server)"

- Toggle: Enable AI agent access
- If enabled:
  - Port config (auto-detected, editable)
  - Autostart toggle: "Start automatically when FableGear opens"
  - Token display + copy button (for cloud agent config)
- "Skip for now" always available

### Welcome Window (every launch)

Three states in the pill row:

1. **MCP enabled + autostart**: MCP starts automatically. Green status dot +
   port shown in pill. Click opens status panel with connection URL + config
   copy buttons.

2. **MCP enabled + manual start**: Pill shows "Start MCP" button. Click starts
   the server and transitions to state 1.

3. **MCP not enabled**: Pill shows "AI Agents" with a subtle learn-more icon.
   Click opens explanation panel + setup wizard.

### Setup Wizard (in-app)

For users who skipped MCP at install or want to reconfigure:

1. Explanation: what it is, what it does, privacy reassurance (localhost only
   by default, nothing leaves your machine)
2. Enable toggle
3. Port config
4. Autostart toggle
5. Agent config snippets with copy buttons:
   - Claude Desktop (`claude_desktop_config.json`)
   - Cursor (`.cursor/mcp.json`)
   - Generic MCP client (URL + token)

### Runtime Controls

Flask API endpoints:
- `GET  /api/mcp/status` — running, port, host, connections, dev mode
- `POST /api/mcp/start` — start the server if not running
- `POST /api/mcp/stop` — stop the server
- `GET  /api/mcp/config-snippet?client=claude-desktop` — ready-to-paste config

## File Changes

| File | Change |
|------|--------|
| `user_config.py` | Add MCP defaults, token generation |
| `mcp_server.py` | Add `start_embedded()`, smart port, token auth middleware, dev tools |
| `main.py` | Call `start_embedded()` based on config |
| `app.py` | Add `/api/mcp/*` routes |
| `templates/index.html` | Welcome window MCP pill + status panel |
| `templates/onboarding.html` | MCP setup step |
| `.mcp.json` | URL-based config for local dev |

## Security

- Localhost binding is the default — zero network exposure
- Token auth required for exposed mode (0.0.0.0)
- Write tools still enforce Rekordbox-closed gate regardless of transport
- All writes still create timestamped DB backups
- Token is never logged or included in error messages
