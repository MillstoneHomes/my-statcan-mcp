# my-statcan-mcp

MCP server for Statistics Canada's Web Data Service (WDS). Claude can search tables, read their dimensions, and pull data. It doesn't need you to know product IDs or coordinates.

## Tools
| Tool | What it does |
|---|---|
| `search_tables` | Keyword search over table titles, e.g. "building permits", "new housing price index" |
| `get_table_metadata` | Lists a table's dimensions and member IDs. `member_filter` narrows long lists such as geography |
| `get_table_data` | Latest N periods for one or more coordinates. Short coordinates like `2.2` are padded to 10 positions |
| `get_vector_data` | Latest N periods by vector ID, e.g. `v41690973` |

## Option A: local (Claude Desktop / Claude Code). No hosting needed.
```bash
pip install -r requirements.txt
claude mcp add statcan -- python /path/to/server.py          # Claude Code
```
For Claude Desktop, add this to `claude_desktop_config.json`:
```json
{ "mcpServers": { "statcan": { "command": "python", "args": ["/path/to/server.py"] } } }
```

## Option B: hosted (claude.ai custom connector)
Deploy the Dockerfile to any container host (Cloud Run, Render, Fly). It listens on `$PORT`.
Set `STATCAN_MCP_SECRET` to a long random string. The endpoint then becomes
`https://<host>/mcp/<secret>`. Paste that URL into claude.ai under Settings → Connectors → Add custom connector.
claude.ai connectors can't send custom headers, so a secret URL is the access gate.
