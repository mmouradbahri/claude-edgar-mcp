"""claude-edgar-mcp — an MCP server exposing SEC EDGAR data as tools for Claude."""

import re

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("claude-edgar-mcp")

SEC_HEADERS = {"User-Agent": "Mourad Bahri mmourad.bahri@gmail.com"}

_ticker_cache: dict = {}


def _load_ticker_cache() -> None:
    global _ticker_cache
    if _ticker_cache:
        return
    r = httpx.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=10.0)
    r.raise_for_status()
    data = r.json()
    _ticker_cache = {v["ticker"].upper(): v for v in data.values()}


def _get_cik(ticker: str) -> dict:
    _load_ticker_cache()
    t = ticker.upper()
    if t not in _ticker_cache:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR database.")
    entry = _ticker_cache[t]
    return {"ticker": entry["ticker"], "cik": str(entry["cik_str"]).zfill(10), "company_name": entry["title"]}


def _get_recent_filings_raw(ticker: str, filing_type: str, limit: int) -> list[dict]:
    cik_info = _get_cik(ticker)
    cik = cik_info["cik"]
    r = httpx.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=SEC_HEADERS, timeout=15.0)
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
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dashes}/{primary_docs[i]}"
                if i < len(primary_docs) else None
            ),
        })
        if len(results) >= limit:
            break
    return results


def _extract_snippets(url: str, query: str, max_snippets: int = 3, context_chars: int = 250) -> list[str]:
    """Fetch a filing document and extract short text snippets around keyword matches."""
    try:
        r = httpx.get(url, headers=SEC_HEADERS, timeout=20.0)
        r.raise_for_status()
    except Exception:
        return []
    try:
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)
    except Exception:
        return []
    clean_query = query.strip('"').strip()
    if not clean_query:
        return []
    matches = list(re.finditer(re.escape(clean_query), text, re.IGNORECASE))
    if not matches:
        return []
    snippets = []
    last_end = -1
    for m in matches:
        if m.start() < last_end + context_chars:
            continue
        start = max(0, m.start() - context_chars)
        end = min(len(text), m.end() + context_chars)
        last_end = end
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."
        snippets.append(snippet)
        if len(snippets) >= max_snippets:
            break
    return snippets


@mcp.tool()
def ticker_to_cik(ticker: str) -> dict:
    """Resolve a US stock ticker to its SEC Central Index Key (CIK)."""
    return _get_cik(ticker)


@mcp.tool()
def get_recent_filings(ticker: str, filing_type: str = "10-K", limit: int = 10) -> list[dict]:
    """Get a US company's most recent SEC filings of a specific type."""
    return _get_recent_filings_raw(ticker, filing_type, limit)


DEFAULT_XBRL_CONCEPTS = [
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss", "OperatingIncomeLoss", "Assets",
    "CashAndCashEquivalentsAtCarryingValue", "StockholdersEquity",
]


@mcp.tool()
def get_company_facts(ticker: str, concepts: list[str] | None = None, years: int = 5) -> dict:
    """Fetch reported financial data (revenue, net income, assets, cash) from SEC's XBRL API."""
    cik_info = _get_cik(ticker)
    cik = cik_info["cik"]
    r = httpx.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers=SEC_HEADERS, timeout=30.0)
    r.raise_for_status()
    data = r.json()
    us_gaap = data.get("facts", {}).get("us-gaap", {})
    concepts_to_use = concepts if concepts else DEFAULT_XBRL_CONCEPTS
    result = {"ticker": ticker.upper(), "company_name": data.get("entityName", cik_info["company_name"]), "facts": {}}
    for concept in concepts_to_use:
        concept_data = us_gaap.get(concept)
        if not concept_data:
            result["facts"][concept] = {"available": False, "note": "Not reported by this company"}
            continue
        usd_values = concept_data.get("units", {}).get("USD", [])
        annual_values = [v for v in usd_values if v.get("form") == "10-K" and v.get("fp") == "FY"]
        by_end: dict = {}
        for v in annual_values:
            end = v.get("end")
            if end is None:
                continue
            if end not in by_end or v.get("filed", "") > by_end[end].get("filed", ""):
                by_end[end] = v
        deduped = sorted(by_end.values(), key=lambda v: v.get("end", ""), reverse=True)[:years]
        result["facts"][concept] = {
            "available": True, "label": concept_data.get("label"),
            "values": [{
                "period_end": v.get("end"), "value_usd": v.get("val"),
                "form": v.get("form"), "filed_date": v.get("filed"), "fiscal_year": v.get("fy"),
            } for v in deduped],
        }
    return result


ITEM_START_PATTERNS = {
    "business": r"item\s*1\.\s*business",
    "risk_factors": r"item\s*1a\.?\s*risk\s*factors",
    "mda": r"item\s*7\.?\s*management",
    "financial_statements": r"item\s*8\.?\s*financial\s*statements",
}

NEXT_SECTION_PATTERNS = {
    "business": r"item\s*1a\b",
    "risk_factors": r"item\s*(1b|2)\b",
    "mda": r"item\s*(7a|8)\b",
    "financial_statements": r"item\s*9\b",
}


@mcp.tool()
def get_10k_section(
    ticker: str,
    section: str = "risk_factors",
    accession_number: str | None = None,
    max_chars: int = 25000,
) -> dict:
    """Extract a specific text section from a company's 10-K filing.

    Sections: "business" (Item 1), "risk_factors" (Item 1A),
    "mda" (Item 7), "financial_statements" (Item 8).
    """
    if section not in ITEM_START_PATTERNS:
        raise ValueError(f"Section '{section}' not supported. Options: {list(ITEM_START_PATTERNS.keys())}")

    filings = _get_recent_filings_raw(ticker, "10-K", 20)
    if not filings:
        raise ValueError(f"No 10-K filings found for {ticker}")
    if accession_number:
        matching = [f for f in filings if f["accession_number"] == accession_number]
        if not matching:
            raise ValueError(f"Accession {accession_number} not found in recent 10-Ks for {ticker}")
        document_url = matching[0]["primary_document_url"]
    else:
        document_url = filings[0]["primary_document_url"]

    r = httpx.get(document_url, headers=SEC_HEADERS, timeout=30.0)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n\s*\n+", "\n\n", text)

    start_pat = ITEM_START_PATTERNS[section]
    matches = list(re.finditer(start_pat, text, re.IGNORECASE))
    if not matches:
        raise ValueError(f"Section '{section}' marker not found in filing")
    start_match = matches[-1]
    start = start_match.end()

    end_pat = NEXT_SECTION_PATTERNS.get(section)
    if end_pat:
        end_match = re.search(end_pat, text[start:], re.IGNORECASE)
        end = start + end_match.start() if end_match else start + max_chars * 2
    else:
        end = start + max_chars * 2

    extracted = text[start:end].strip()
    if len(extracted) > max_chars:
        extracted = extracted[:max_chars] + f"\n\n[... truncated to {max_chars} chars]"

    return {
        "ticker": ticker.upper(),
        "section": section,
        "text": extracted,
        "char_count": len(extracted),
        "document_url": document_url,
    }


@mcp.tool()
def search_full_text(
    query: str,
    ticker: str | None = None,
    forms: list[str] | None = None,
    limit: int = 10,
    with_snippets: bool = True,
) -> dict:
    """Full-text search across all SEC EDGAR filings.

    Powered by SEC's EDGAR search index. Supports exact phrases (double quotes)
    and boolean operators (AND, OR, NOT).

    When with_snippets=True (default), fetches the top 3 filings to extract
    text snippets around keyword matches — slower (~10-20s) but shows context.
    Set with_snippets=False for fast metadata-only search.

    Args:
        query: Search string. Examples: 'generative AI', '"AI capex"', 'TikTok AND competition'.
        ticker: Optional US stock ticker to limit search to one company.
        forms: Optional list of filing forms (e.g. ["10-K", "10-Q"]).
        limit: Maximum number of results. Default 10.
        with_snippets: If True, fetch top 3 filings for snippet extraction.

    Returns:
        Dict with `query`, `total_hits`, and `results`.
    """
    params: dict = {"q": query}
    if forms:
        params["forms"] = ",".join(forms)
    if ticker:
        cik_info = _get_cik(ticker)
        params["ciks"] = str(int(cik_info["cik"]))

    r = httpx.get(
        "https://efts.sec.gov/LATEST/search-index",
        params=params,
        headers=SEC_HEADERS,
        timeout=20.0,
    )
    r.raise_for_status()
    data = r.json()

    hits = data.get("hits", {})
    total_field = hits.get("total")
    if isinstance(total_field, dict):
        total = total_field.get("value", 0)
    else:
        total = total_field or 0
    results_raw = hits.get("hits", [])[:limit]

    snippet_budget = 3
    results = []
    for idx, hit in enumerate(results_raw):
        src = hit.get("_source", {})
        adsh = src.get("adsh", "") or ""
        ciks = src.get("ciks", []) or []
        cik_int = int(ciks[0]) if ciks else None
        acc_no_dashes = adsh.replace("-", "") if adsh else ""

        hit_id = src.get("id", "") or hit.get("_id", "")
        primary_doc_url = None
        if ":" in hit_id and cik_int and acc_no_dashes:
            filename = hit_id.split(":", 1)[1]
            primary_doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dashes}/{filename}"

        filing_index_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dashes}/"
            if cik_int and acc_no_dashes else None
        )

        snippets: list[str] = []
        if with_snippets and primary_doc_url and idx < snippet_budget:
            snippets = _extract_snippets(primary_doc_url, query)

        results.append({
            "form": src.get("form"),
            "filed_date": src.get("file_date"),
            "accession_number": adsh,
            "company_name": (src.get("display_names") or [None])[0],
            "cik": ciks[0] if ciks else None,
            "filing_index_url": filing_index_url,
            "primary_document_url": primary_doc_url,
            "snippets": snippets,
            "relevance_score": hit.get("_score"),
        })

    return {
        "query": query,
        "total_hits": total,
        "results": results,
    }


if __name__ == "__main__":
    mcp.run()
