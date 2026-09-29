"""Statistics Canada MCP server.

Wraps the StatCan Web Data Service (WDS) so Claude can find a table, read its
dimensions, and pull data without the user knowing product IDs or coordinates.

Run modes:
  python server.py            -> stdio (Claude Desktop / Claude Code, local)
  python server.py --http     -> streamable HTTP on $PORT (default 8080)
  (HTTP is also used automatically when $PORT is set, e.g. Cloud Run / Render)

Remote access control: set STATCAN_MCP_SECRET and the endpoint becomes
/mcp/<secret>. claude.ai custom connectors cannot send custom headers, so a
secret URL is the simplest gate that actually works with them.
"""

import os
import sys
import time

import httpx
from mcp.server.mcpserver import MCPServer

WDS = "https://www150.statcan.gc.ca/t1/wds/rest"
TIMEOUT = httpx.Timeout(30.0)
CUBE_LIST_TTL = 24 * 3600

SCALAR = {0: "units", 1: "tens", 2: "hundreds", 3: "thousands", 4: "tens of thousands",
          5: "hundreds of thousands", 6: "millions", 7: "tens of millions",
          8: "hundreds of millions", 9: "billions"}
FREQ = {1: "daily", 2: "weekly", 4: "biweekly", 6: "monthly", 7: "bimonthly", 9: "quarterly",
        10: "tri-annual", 11: "semi-annual", 12: "annual", 13: "every 2 years",
        14: "every 3 years", 15: "every 4 years", 16: "every 5 years", 17: "every 10 years",
        18: "occasional", 19: "occasional quarterly", 20: "occasional monthly", 21: "occasional daily"}

mcp = MCPServer(
    name="statcan",
    instructions=(
        "Statistics Canada data. Typical flow: search_tables -> get_table_metadata "
        "-> get_table_data. If you already have a vector ID (e.g. v41690973), use "
        "get_vector_data directly. Coordinates are member IDs per dimension joined "
        "by dots, in dimension order; missing trailing positions are padded with 0."
    ),
)

_cube_cache: dict = {"at": 0.0, "cubes": []}


async def _get(path: str):
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{WDS}/{path}")
        r.raise_for_status()
        return r.json()


async def _post(path: str, body):
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{WDS}/{path}", json=body)
        r.raise_for_status()
        return r.json()


def _unwrap(resp) -> dict:
    """WDS returns [{status, object}]; raise on anything but SUCCESS."""
    item = resp[0] if isinstance(resp, list) and resp else resp
    if not isinstance(item, dict) or item.get("status") != "SUCCESS":
        raise ValueError(f"StatCan returned: {item}")
    return item["object"]


def _pid(product_id: int | str) -> int:
    """Accept 18100004, '18-10-0004-01', '1810000401' and return the 8-digit PID."""
    digits = "".join(ch for ch in str(product_id) if ch.isdigit())
    if len(digits) == 10:
        digits = digits[:8]
    if len(digits) != 8:
        raise ValueError(f"Expected an 8-digit product ID (e.g. 18100004), got {product_id!r}")
    return int(digits)


def _coord(coordinate: str) -> str:
    parts = [p.strip() or "0" for p in coordinate.split(".")]
    if len(parts) > 10:
        raise ValueError("Coordinate has more than 10 positions")
    return ".".join(parts + ["0"] * (10 - len(parts)))


def _series(obj: dict) -> dict:
    points = obj.get("vectorDataPoint") or []
    scalar = points[0].get("scalarFactorCode") if points else None
    return {
        "product_id": obj.get("productId"),
        "coordinate": obj.get("coordinate"),
        "vector_id": obj.get("vectorId"),
        "scale": SCALAR.get(scalar, scalar),
        # statusCode/symbolCode flag preliminary, revised or suppressed values; 0 means none.
        "points": [
            {"period": p.get("refPer"), "value": p.get("value")}
            | ({"status": p["statusCode"]} if p.get("statusCode") else {})
            | ({"symbol": p["symbolCode"]} if p.get("symbolCode") else {})
            for p in points
        ],
    }


async def _series_info(items: list[dict]) -> list[dict]:
    try:
        resp = await _post("getSeriesInfoFromCubePidCoord" if "productId" in items[0]
                           else "getSeriesInfoFromVector", items)
    except Exception:
        return [{} for _ in items]
    out = []
    for it in resp:
        o = it.get("object") if it.get("status") == "SUCCESS" else None
        out.append({"title": o.get("SeriesTitleEn"), "frequency": FREQ.get(o.get("frequencyCode"))}
                   if o else {})
    return out


@mcp.tool()
async def search_tables(query: str, limit: int = 15, include_archived: bool = False) -> list[dict]:
    """Search Statistics Canada tables by keywords in the title (all words must match).

    Examples: "consumer price index", "building permits", "housing starts",
    "new housing price index", "population estimates quarterly".
    Returns product IDs to use with get_table_metadata / get_table_data.
    """
    if time.time() - _cube_cache["at"] > CUBE_LIST_TTL or not _cube_cache["cubes"]:
        _cube_cache["cubes"] = await _get("getAllCubesListLite")
        _cube_cache["at"] = time.time()

    words = [w for w in query.lower().split() if w]
    hits = []
    for c in _cube_cache["cubes"]:
        if c.get("archived") in ("1", 1, True) and not include_archived:
            continue
        title = (c.get("cubeTitleEn") or "").lower()
        if all(w in title for w in words):
            hits.append(c)
    # Shorter titles first: they are usually the headline table, not a niche breakdown.
    hits.sort(key=lambda c: (len(c.get("cubeTitleEn") or ""), -int(str(c.get("cubeEndDate", "0"))[:4] or 0)))
    return [
        {
            "product_id": c.get("productId"),
            "title": c.get("cubeTitleEn"),
            "start": c.get("cubeStartDate"),
            "end": c.get("cubeEndDate"),
            "frequency": FREQ.get(c.get("frequencyCode"), c.get("frequencyCode")),
            "archived": c.get("archived") in ("1", 1, True),
        }
        for c in hits[:limit]
    ] or [{"message": f"No tables match all of: {words}. Try fewer or different words."}]


@mcp.tool()
async def get_table_metadata(product_id: str, max_members_per_dimension: int = 150,
                             member_filter: str = "") -> dict:
    """Get a table's dimensions and their members, needed to build a coordinate.

    product_id: 8-digit ID (18100004) or the dashed form (18-10-0004-01).
    member_filter: optional case-insensitive text to only list matching members
    (e.g. "Toronto"), useful for tables with long geography lists.
    A coordinate is one member ID per dimension, in dimension order, joined by dots.
    """
    obj = _unwrap(await _post("getCubeMetadata", [{"productId": _pid(product_id)}]))
    f = member_filter.lower().strip()
    dims = []
    for d in obj.get("dimension", []):
        members = d.get("member", [])
        if f:
            members = [m for m in members if f in (m.get("memberNameEn") or "").lower()]
        dims.append({
            "position": d.get("dimensionPositionId"),
            "name": d.get("dimensionNameEn"),
            "member_count": len(d.get("member", [])),
            "members": [
                {"id": m.get("memberId"), "name": m.get("memberNameEn"),
                 **({"parent": m["parentMemberId"]} if m.get("parentMemberId") else {})}
                for m in members[:max_members_per_dimension]
            ],
            "truncated": len(members) > max_members_per_dimension,
        })
    return {
        "product_id": obj.get("productId"),
        "title": obj.get("cubeTitleEn"),
        "start": obj.get("cubeStartDate"),
        "end": obj.get("cubeEndDate"),
        "frequency": FREQ.get(obj.get("frequencyCode"), obj.get("frequencyCode")),
        "dimensions": dims,
    }


@mcp.tool()
async def get_table_data(product_id: str, coordinates: list[str], latest_n_periods: int = 12) -> list[dict]:
    """Fetch the latest N periods for one or more series in a table.

    coordinates: e.g. ["2.2"] or ["2.2.0.0.0.0.0.0.0.0"]; short forms are padded
    with zeros to 10 positions. Up to 50 series per call.
    """
    pid = _pid(product_id)
    items = [{"productId": pid, "coordinate": _coord(c)} for c in coordinates[:50]]
    resp = await _post("getDataFromCubePidCoordAndLatestNPeriods",
                       [i | {"latestN": latest_n_periods} for i in items])
    infos = await _series_info(items)
    out = []
    for it, info in zip(resp, infos):
        if it.get("status") != "SUCCESS":
            out.append({"error": it.get("object") or it})
        else:
            out.append(info | _series(it["object"]))
    return out


@mcp.tool()
async def get_vector_data(vector_ids: list[str], latest_n_periods: int = 12) -> list[dict]:
    """Fetch the latest N periods for StatCan vector IDs (e.g. "v41690973" or 41690973)."""
    ids = [int(str(v).lower().lstrip("v")) for v in vector_ids[:50]]
    resp = await _post("getDataFromVectorsAndLatestNPeriods",
                       [{"vectorId": v, "latestN": latest_n_periods} for v in ids])
    infos = await _series_info([{"vectorId": v} for v in ids])
    return [
        (info | _series(it["object"])) if it.get("status") == "SUCCESS" else {"error": it.get("object") or it}
        for it, info in zip(resp, infos)
    ]


def main() -> None:
    if "--http" in sys.argv or os.getenv("PORT"):
        import uvicorn

        secret = os.getenv("STATCAN_MCP_SECRET", "").strip("/")
        path = f"/mcp/{secret}" if secret else "/mcp"
        # host="0.0.0.0" also stops the SDK from enabling localhost-only Host checks,
        # which would otherwise reject requests arriving on a public hostname.
        app = mcp.streamable_http_app(streamable_http_path=path, host="0.0.0.0", stateless_http=True)
        uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
