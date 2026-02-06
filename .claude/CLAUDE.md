# 3dsmax-mcp

MCP server bridging Claude to Autodesk 3ds Max via TCP socket (port 8765).

## Flags
- [x] **learn-from-mistakes** — When enabled, Claude MUST update `.claude/skills/3dsmax-mcp-dev/SKILL.md` whenever it encounters a bug, unexpected behavior, or discovers a MAXScript/3ds Max gotcha during a session. This keeps the skill file growing with real-world lessons so future sessions (and anyone who clones this repo) benefit immediately.

### How learn-from-mistakes works
1. Claude hits an error or unexpected result
2. Claude fixes the issue
3. Claude appends the lesson to the relevant section in `.claude/skills/3dsmax-mcp-dev/SKILL.md`
4. Keep entries concise — one line per lesson, with the pattern or fix
5. Don't duplicate — check if the lesson is already recorded before adding

## Project Structure
- `src/server.py` — FastMCP server entry point
- `src/max_client.py` — TCP socket client (connects to 127.0.0.1:8765)
- `src/tools/` — MCP tool implementations (one file per category)
- `maxscript/mcp_server.ms` — MAXScript listener (runs inside 3ds Max)
- `maxscript/startup/mcp_autostart.ms` — auto-start loader for 3ds Max

## Skills
- `.claude/skills/3dsmax-mcp-dev/` — development conventions and gotchas (grows iteratively via learn-from-mistakes)

## Key Patterns
- Tools are registered via `@mcp.tool()` decorators in `src/tools/*.py`
- All tools send MAXScript strings to 3ds Max via `client.send_command()`
- MAXScript results are returned as JSON strings built with manual concatenation
- Viewport capture: `gw.getViewportDib()` → save to temp → `Read` tool to view
