# claude-edgar-mcp

A [Model Context Protocol](https://modelcontextprotocol.io/) server that gives Claude, GPT, or any MCP-compatible LLM direct access to SEC EDGAR — the US Securities and Exchange Commission filings database used by every equity analyst on Wall Street.

## Tools

| Tool | What it does |
|------|--------------|
| \`ticker_to_cik\` | Resolves a US stock ticker to its SEC Central Index Key (CIK) |

More tools coming — see roadmap.

## Example

Ask Claude:

> What is Meta's SEC CIK?

Claude calls \`ticker_to_cik("META")\` and returns:

\`\`\`json
{
  "ticker": "META",
  "cik": "0001326801",
  "company_name": "Meta Platforms, Inc."
}
\`\`\`

## Install

\`\`\`bash
git clone https://github.com/mmouradbahri/claude-edgar-mcp.git
cd claude-edgar-mcp
uv sync
\`\`\`

## Wire into any MCP-compatible client

**Claude Desktop:** edit \`~/Library/Application Support/Claude/claude_desktop_config.json\`:

\`\`\`json
{
  "mcpServers": {
    "claude-edgar-mcp": {
      "command": "/Users/YOU/.local/bin/uv",
      "args": ["--directory", "/PATH/TO/claude-edgar-mcp", "run", "main.py"]
    }
  }
}
\`\`\`

**OpenAI Codex CLI:** edit \`~/.codex/config.toml\`:

\`\`\`toml
[mcp_servers.claude-edgar-mcp]
command = "/Users/YOU/.local/bin/uv"
args = ["--directory", "/PATH/TO/claude-edgar-mcp", "run", "main.py"]
\`\`\`

Restart your client. Works with Claude Desktop, Claude Code, OpenAI Codex, Cursor, VS Code + Copilot, and Gemini CLI.

## Roadmap

- **v0.2** — \`get_recent_filings(ticker, filing_type, limit)\` — return list of a company's recent 10-K/10-Q/8-K filings
- **v0.3** — \`get_10k_url(ticker)\` — direct URL to a company's latest 10-K
- **v0.4** — \`get_10k_section(accession, section)\` — extract Business, Risk Factors, MD&A from a specific filing
- **v0.5** — \`get_financials(ticker, years)\` — pull revenue, net income, FCF from XBRL

Built by [Mourad Bahri](https://github.com/mmouradbahri) — part of a broader AI × financial-services project.
