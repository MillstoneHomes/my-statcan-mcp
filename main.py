import os
import requests
from fastapi import FastAPI, HTTPException, Header
from fastapi_mcp import FastApiMCP

app = FastAPI(title="StatCan MCP Server")
mcp = FastApiMCP(app, name="statcan_fetcher", description="Fetches live economic cubes directly from Statistics Canada")

SECRET_TOKEN = os.getenv("CLAUDE_SECRET_TOKEN", "super-secret-passphrase")

@app.post("/fetch_statcan_data", operation_id="fetch_statcan_data")
def fetch_statcan_data(
    product_id: int, 
    coordinate: str, 
    latest_n_periods: int = 12,
    x_project_token: str = Header(None, alias="X-Project-Token")
):
    """
    Fetch data slices from a specific Statistics Canada Product ID (Cube) and coordinate.
    """
    if x_project_token != SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized token.")

    url = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromCubePidCoordAndLatestNPeriods"
    payload = [{"productId": product_id, "coordinate": coordinate, "latestNPeriods": latest_n_periods}]
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code != 200:
            return {"error": f"StatCan API error: {response.status_code}"}
        return response.json()
    except Exception as e:
        return {"error": str(e)}

mcp.mount_http()
