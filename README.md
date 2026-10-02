# AI Equity Research

Type a ticker, confirm a peer group, and get a sell-side style research report: a buy/hold/sell
call with a thesis, valuation against peers, seven charts, management guidance pulled from earnings
releases, and the risks and catalysts behind it.

It runs on institutional data from WRDS (Compustat, CRSP, I/B/E/S) rather than free APIs, and uses
Claude for the analysis and FinBERT for sentiment.

## How it works

1. **Data.** Five years of fundamentals from Compustat, daily prices from CRSP (topped up with
   recent days from yfinance), analyst estimates, price targets and ratings from I/B/E/S, and 8-K
   earnings releases from WRDS SEC and EDGAR.
2. **Peers.** Suggested from the same GICS industry within a market-cap band, US-headquartered companies first.
   You confirm or edit the list before the report runs.
3. **Signals.** A rules-based score across valuation, growth trend, sentiment, analyst views,
   momentum, EPS surprises and ROIC trend. FinBERT scores each earnings release.
4. **Two passes of Claude.**
   - **Pass 1 (Sonnet)** builds a thesis using data tools, with the mechanical score kept out of
     its context, so it can't anchor on it.
   - **Pass 2 (Opus)** reconciles that thesis with the signals and has to say where it agrees and
     where it overrides them. Those agreements and disagreements are shown in the report.

The point of the split: if the model sees the score first, it tends to write a story around it.
The design reasoning is in [`docs/specs`](docs/specs).

## Running it

You need your own credentials. Nothing here ships with keys.

- A **WRDS** account with Compustat, CRSP and I/B/E/S access (most universities provide one)
- An **Anthropic API key**

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your own values
python app.py                 # http://localhost:5000
```

Or from the command line, with a quality check on the output:

```bash
python run_report.py MSFT
python check_report.py MSFT
```

WRDS asks for multi-factor approval on each new connection. Set `DISABLE_FINBERT=1` to skip the
local FinBERT model if you don't want to download it.

## Tests

```bash
pip install pytest
pytest tests/
```

The tests run without WRDS or API access. They cover SQL parameter binding (every query binds its
values, none are formatted into the SQL), session isolation between users, the CAGR window maths,
and parsing of model output.

## Limitations

- Single-process, single-user design. Report data is held in memory between the two steps.
- CRSP coverage on the account it was built with ends in 2024, so recent prices come from yfinance.
- No sample reports are included, because WRDS data can't be redistributed.
- `audit_outcomes.py` compares past recommendations with what the stock did since, but it needs a
  history of reports to say anything useful.
