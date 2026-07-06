"""claude-edgar-mcp — an MCP server exposing SEC EDGAR data as tools for Claude."""

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("claude-edgar-mcp")

SEC_HEADERS = {"User-Agent": "Mourad Bahri mmourad.bahri@gmail.com"}

_ticker_cache: dict = {}


def _load_ticker_cache() -> None:
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
    """Resolve a US stock ticker to its SEC Central Index Key (CIK)."""
    return _get_cik(ticker)


@mcp.tool()
def get_recent_filings(
    ticker: str,
    filing_type: str = "10-K",
    limit: int = 10,
) -> list[dict]:
    """Get a US company's most recent SEC filings of a specific type."""
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


DEFAULT_XBRL_CONCEPTS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "Assets",
    "CashAndCashEquivalentsAtCarryingValue",
    "StockholdersEquity",
]


@mcp.tool()
def get_company_facts(
    ticker: str,
    concepts: list[str] | None = None,
    years: int = 5,
) -> dict:
    """Fetch reported financial data (revenue, net income, assets, cash, etc.) from a US company's SEC filings via XBRL.

    Use this to get real, GAAP-reported numbers for a company across multiple years.
    All values are as-reported in the company's 10-K annual filings, deduplicated
    by fiscal period-end and preferring the most recently filed restatement.

    Args:
        ticker: US stock ticker (e.g. 'META', 'WING').
        concepts: List of XBRL concept names. If None, returns a default set covering
            revenue, net income, operating income, assets, cash, and stockholders equity.
        years: Number of most recent annual fiscal periods to return per concept. Default 5.

    Returns:
        Dict with `ticker`, `company_name`, and `facts` — a dict keyed by concept name.
    """
    cik_info = _get_cik(ticker)
    cik = cik_info["cik"]

    r = httpx.get(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        headers=SEC_HEADERS,
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()

    us_gaap = data.get("facts", {}).get("us-gaap", {})
    concepts_to_use = concepts if concepts else DEFAULT_XBRL_CONCEPTS

    result = {
        "ticker": ticker.upper(),
        "company_name": data.get("entityName", cik_info["company_name"]),
        "facts": {},
    }

    for concept in concepts_to_use:
        concept_data = us_gaap.get(concept)
        if not concept_data:
            result["facts"][concept] = {
                "available": False,
                "note": "Not reported by this company (concept may use a different name)",
            }
            continue

        usd_values = concept_data.get("units", {}).get("USD", [])
        # Full-year 10-K data only (not quarterly, not 10-Q, not 8-K)
        annual_values = [
            v for v in usd_values
            if v.get("form") == "10-K" and v.get("fp") == "FY"
        ]

        # Dedup by period-end date, keeping the most recently filed restatement
        by_end: dict = {}
        for v in annual_values:
            end = v.get("end")
            if end is None:
                continue
            if end not in by_end or v.get("filed", "") > by_end[end].get("filed", ""):
                by_end[end] = v

        deduped = sorted(by_end.values(), key=lambda v: v.get("end", ""), reverse=True)[:years]

        result["facts"][concept] = {
            "available": True,
            "label": concept_data.get("label"),
            "values": [
                {
                    "period_end": v.get("end"),
                    "value_usd": v.get("val"),
                    "form": v.get("form"),
                    "filed_date": v.get("filed"),
                    "fiscal_year": v.get("fy"),
                }
                for v in deduped
            ],
        }

    return result


if __name__ == "__main__":
    mcp.run()
