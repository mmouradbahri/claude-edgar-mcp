"""claude-edgar-mcp — an MCP server exposing SEC EDGAR data as tools for Claude."""

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("claude-edgar-mcp")

# SEC requires a real User-Agent identifying the requester
SEC_HEADERS = {"User-Agent": "Mourad Bahri mourad@modllabs.com"}

_ticker_cache: dict = {}


def _load_ticker_cache() -> None:
    """Fetch SEC's canonical ticker → CIK map once, cache in memory."""
    global _ticker_cache
    if _ticker_cache:
        return
    r = httpx.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=SEC_HEADERS,
        timeout=10.0,
    )
    r.raise_for_status()
    data = r.json()
    _ticker_cache = {v["ticker"].upper(): v for v in data.values()}


@mcp.tool()
def ticker_to_cik(ticker: str) -> dict:
    """Resolve a US stock ticker to its SEC Central Index Key (CIK).

    Use this whenever a user gives you a ticker (like 'AAPL' or 'META') and
    you need to look up SEC filings for that company. Every other EDGAR tool
    starts with a CIK.

    Args:
        ticker: The stock ticker (case-insensitive), e.g. 'AAPL', 'META', 'WING'.

    Returns:
        Dict with `ticker`, `cik` (10-digit zero-padded string), and `company_name`.
    """
    _load_ticker_cache()
    t = ticker.upper()
    if t not in _ticker_cache:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR database.")
    entry = _ticker_cache[t]
    return {
        "ticker": entry["ticker"],
        "cik": str(entry["cik_str"]).zfill(10),
        "company_name": entry["title"],
    }


if __name__ == "__main__":
    mcp.run()
