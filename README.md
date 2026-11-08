# FINN

A mobile portfolio inbox and a primary-source research desk. Built for the ENAM AI Engineer challenge, released 8 November 2026, due 15 November 2026.

The default inbox is explicitly illustrative. It uses seven labelled scenarios to demonstrate ranking across an 18-company portfolio. Research never uses synthetic company documents. Live sources and illustrative events live in separate query scopes.

## Run locally

Python 3.12 is the tested runtime. Run from this directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn finn.app:app --host 127.0.0.1 --port 8000
```

Windows PowerShell activation: `.venv\Scripts\Activate.ps1`.

Open http://127.0.0.1:8000. No API key is needed for the illustrative inbox, watch rules, or research source passages. Select **Live sources** to see real ingested data. In a second activated terminal:

```bash
python -m finn.worker once
python -m finn.worker watch
```

`once` imports the recent filing window and EOD prices. `watch` then polls filings and runs EOD checks after 18:30 Asia/Kolkata. Keep one worker process and one application process. The loop pauses five minutes after each cycle; actual detection latency includes the cycle runtime and upstream delays.

## Research

Enter any NSE ticker and tap **Research company**. A missing corpus is loaded automatically. **Load documents** refreshes it explicitly. The app discovers company-specific annual-report and transcript links on Screener, tries approved issuer/exchange PDF hosts, and uses NSE's own reports and announcement feed when links fail. It selects up to two annual reports and six earnings-call transcripts. It does not fetch Screener AI summaries, news, broker reports or investor decks into the research corpus.

Try INFY, PERSISTENT and TATACONSUM; these are outside the mock portfolio. The pipeline is parameterized by ticker, rather than branching on those names. Availability and PDF extraction quality still determine coverage. You can also preload:

```bash
python -m finn.worker research --ticker INFY
```

Then ask about growth guidance, cash conversion or the business mix. Without model credentials, the app returns ranked passages with document and PDF-page links. That is deliberately labelled **Source passages**, not an AI analysis. Data is downloaded when you load a company; company PDFs are not redistributed in this repository.

## Enable analysis

Copy `.env.example` to `.env` and configure your provider key plus `EXTRACT_MODEL` and `REASON_MODEL`. The models must support the OpenAI-compatible Chat Completions JSON response format. Use a small extraction model and a stronger reasoning model available in your account. The exact model IDs are configuration, not a dependency on a soon-to-be-retired model.

The plain Python commands do not load `.env` automatically. Export its variables in your shell, use the example below on Bash, or run Docker Compose, which reads it:

```bash
set -a
source .env
set +a
uvicorn finn.app:app --host 127.0.0.1 --port 8000
```

Extraction creates a quote ledger. The server rejects unknown citation IDs and quotes that do not occur in the source. The reasoning model sees the validated ledger, not the internet. A small-model second pass checks support before returning an answer. This reduces unsupported claims; it is not a proof that every inference is correct. Failed verification returns source passages.

Costs are bounded by maximum context/output size, a daily call limit and a persistent content/prompt/model cache. `/api/usage` reports model calls and tokens. No paid model calls were needed for the included deterministic tests. Live provider behavior must be checked with the selected models before presenting the generated-analysis mode to an assessor.

## Container run

```bash
cp .env.example .env
docker compose up --build
```

The app and worker share a SQLite volume. Compose binds the app to localhost. For a phone-accessible deployment, run behind HTTPS with a persistent disk, set `FINN_ACCESS_TOKEN`, and use the gear control in the UI to enter that token. The token guards API requests; the static shell remains public. This is a single-user prototype, not a team-authentication system. No API keys are sent to the browser.

Do not use ephemeral serverless disk for this version. Do not launch multiple application workers: research job status is held in memory. A process restart loses active jobs; completed documents, alerts, rules and model cache remain in SQLite.

If your environment uses a trusted outbound proxy and cannot resolve public DNS locally, `FINN_TRUST_PROXY_DNS=1` delegates resolution to that proxy. Use it only when the proxy enforces public destination IPs. Normal deployments should leave it unset.

## What to inspect

| File | Responsibility |
| --- | --- |
| `finn/sources.py` | Yahoo EOD, NSE/BSE feeds, Screener parsing, primary-source allowlist, bounded PDF fetching, NSE research fallback |
| `finn/push.py` | Category/materiality split, ranked windows, deduplication, configurable price/volume/filing alerts |
| `finn/research.py` | Document hashes, page-aware chunks, lexical retrieval, quote validation, cited answers |
| `finn/results.py` | Reported Revenue/EBITDA/PBT/PAT extraction and guarded QoQ/YoY calculations |
| `finn/prompts.py` | Versioned extraction, classification, reasoning and verification prompts |
| `finn/llm.py` | Model routing, persistent caching, token accounting and daily budget |
| `finn/worker.py` | Source polling, EOD timing and persistent ingestion status |
| `finn/app.py` | API, bounded research jobs, service-token checks and static serving |
| `static/` | Mobile UI, custom periods, watch rules, source links, app manifest and shell service worker |
| `tests/` | Financial edge cases, deduplication, source constraints, model routing and API behavior |
| `docs/ARCHITECTURE.md` | Product decisions, source contracts and production changes |
| `docs/VALIDATION.md` | Actual test evidence and limits |
| `docs/SUBMISSION_EMAIL.md` | Covering note with publication-link fields |
| `docs/BUILD_HISTORY.md` | Build record and instructions for the required conversation export |

## Verify

```bash
python -m pytest -q
```

See `docs/VALIDATION.md` for the exact checks performed. In particular, BSE may return HTTP 403; that is reported as incomplete coverage rather than an empty successful feed. NSE fallback is independent primary-source access. No proxy rotation or CAPTCHA bypass is implemented.

## Deliberate boundaries

- Alerts are durable in-app events, delivered while the page is open by polling. Closed-app Web Push/APNs/FCM is not implemented.
- Numerical results require a configured extraction model. Missing EBITDA stays missing; banks are not forced into an industrial-company template. Standalone/consolidated or unit mismatches do not produce growth rates.
- The result extractor is conservative and may withhold valid figures when a PDF table cannot be supported by a contiguous quote. Always retain the filing link.
- Exact-text hashes collapse identical cross-exchange documents. Different cover pages or reworded filings can still produce duplicates. Revision/supersession handling needs a stronger filing identity model before production.
- No biography scraping, OCR, voice, trade execution, price targets, portfolio optimizer or automated four-quarter promise scorecard.
- No deployed URL, GitHub repository or AI conversation share URL has been created by this package. Those are submission steps; do not invent the links.
