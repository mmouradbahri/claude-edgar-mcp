"""claude-edgar-mcp — an MCP server exposing SEC EDGAR data as tools for Claude."""

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("claude-edgar-mcp")

# SEC requires a real User-Agent identifying the requester
SEC_HEADERS = {"User-Agent": "Mourad Bahri mmourad.bahri@gmail.com"}

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


def _get_cik(ticker: str) -> dict:
    """Internal helper: resolve ticker to CIK info. Shared by all tools."""
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
    return _get_cik(ticker)


@mcp.tool()
def get_recent_filings(
    ticker: str,
    filing_type: str = "10-K",
    limit: int = 10,
) -> list[dict]:
    """Get a US company's most recent SEC filings of a specific type.

    Use this when a user wants to know when a company last filed a 10-K/10-Q/8-K,
    or wants a list of recent filings to review or fetch further.

    Args:
        ticker: US stock ticker (e.g. 'META', 'WING', 'AAPL').
        filing_type: Filing type — '10-K' (annual), '10-Q' (quarterly), '8-K' (current event), 'DEF 14A' (proxy). Default '10-K'.
        limit: Maximum number of results to return. Default 10.

    Returns:
        List of dicts with `form`, `filed_date`, `period_of_report`,
        `accession_number`, and `primary_document_url`.
    """
    cik_info = _get_cik(ticker)
    cik = cik_info["cik"]

    r = httpx.get(
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        headers=SEC_HEADERS,
        timeout=15.0,
    )
    r.raise_for_status()
    data = r.json()

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    reports = recent.get("reportDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    results = []
    cik_int = int(cik)
    for i, form in enumerate(forms):
        if form != filing_type:
            continue
        acc_no_dashes = accessions[i].replace("-", "")
        results.append({
            "form": form,
            "filed_date": dates[i] if i < len(dates) else None,
            "period_of_report": reports[i] if i < len(reports) else None,
            "accession_number": accessions[i],
            "primary_document_url": (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik_int}/{acc_no_dashes}/{primary_docs[i]}"
                if i < len(primary_docs) else None
            ),
        })
        if len(results) >= limit:
            break

    return results


if __name__ == "__main__":
    mcp.run()
