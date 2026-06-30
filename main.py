import os
import requests
from fastapi import FastAPI
from mcp.server.fastapi import FastApiServer
from mcp.types import Tool, TextContent

app = FastAPI(title="StatCan MCP Server")

SECRET_TOKEN = os.getenv("CLAUDE_SECRET_TOKEN", "super-secret-passphrase")
mcp_server = FastApiServer(
    name="statcan-fetcher",
    version="1.0.0",
    description="Fetches live economic cubes directly from Statistics Canada"
)

@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="fetch_statcan_data",
            description="Fetch data slices from a specific Statistics Canada Product ID (Cube) and coordinate.",
            input_schema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "8-digit StatCan table Product ID (e.g., 18100004 for CPI)"},
                    "coordinate": {"type": "string", "description": "Dimension coordinate string (e.g., '1.1.1.1.0.0.0.0.0')"},
                    "latest_n_periods": {"type": "integer", "description": "Number of recent time periods to pull", "default": 12}
                },
                "required": ["product_id", "coordinate"]
            }
        )
    ]

@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "fetch_statcan_data":
        raise ValueError(f"Unknown tool: {name}")
        
    pid = arguments["product_id"]
    coord = arguments["coordinate"]
    periods = arguments.get("latest_n_periods", 12)
    
    url = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromCubePidCoordAndLatestNPeriods"
    payload = [{"productId": pid, "coordinate": coord, "latestNPeriods": periods}]
    
    response = requests.post(url, json=payload, timeout=15)
    if response.status_code != 200:
        return [TextContent(type="text", text=f"StatCan API error: {response.status_code}")]
        
    return [TextContent(type="text", text=str(response.json()))]

app.mount("/mcp", mcp_server.app)
