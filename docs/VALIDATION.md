# Validation record

Checked on 8 November 2026. This records actual outcomes, not intended behavior.

## Automated checks

`python -m pytest -q`: **30 passed**. The suite covers results versus publication notices, explicit auditor risk, subject precedence over incidental PDF mentions, unknown-category visibility, cross-exchange content deduplication, retained source URLs, custom half-open time windows, configurable alert categories, exact-threshold behavior, prior-session volume baselines, insufficient history, duplicate sessions, split suppression, negative and zero comparison bases, unit/scope mismatches, ambiguous financial values, unsupported figures, source-type restrictions, quote rejection, citation validation, model routing, verifier failure, PDF ingestion/retrieval, API-token enforcement, watch-rule persistence, and automatic document loading for a new research ticker.

`node --check static/app.js`: passed.

One non-blocking Starlette TestClient deprecation warning was emitted for its httpx integration. Runtime API calls and tests succeeded with the pinned dependencies.

## Browser checks

Ran Chromium through Playwright against the real FastAPI application:

- Inbox rendered and routine items remained collapsible.
- Saving a new price threshold succeeded.
- Changing from illustrative to live data displayed source coverage status.
- An empty corpus returned an explicit evidence gap during the initial UI test.
- The final Research action returned six page-linked passages from the loaded real Infosys corpus.
- No uncaught browser JavaScript errors were recorded.
- No horizontal overflow at widths of 320, 390 or 1440 CSS pixels in the research view; the inbox also passed the 390-pixel check.
- Mobile inbox and research screenshots were visually reviewed. Internal screenshots are not shipped in the source archive.

## Live source checks

| Path | Observed result |
| --- | --- |
| Screener company discovery | INFY symbol confirmed; 15 annual-report links, 38 transcript links, BSE code and standardized table sections discovered |
| Yahoo / yfinance | INFY returned 128 completed sessions; latest session was 6 November 2026 |
| NSE announcements | Public adapter returned records and the ticker-filtered query returned only INFY |
| BSE announcements/PDFs | HTTP 403 in this environment; failure was retained and reported |
| NSE annual-report fallback | Retrieved report metadata including revised submissions |
| INFY research corpus | 2 annual reports and 4 earnings-call transcripts loaded; 6 passages returned for a guidance question |
| PERSISTENT research corpus | 2 annual reports and 5 transcripts loaded through the same engine |
| TATACONSUM research corpus | 2 annual reports and 4 transcripts loaded through the same engine |
| Filing attachment pipeline | A real TCS PDF was fetched and ingested with its source link; the live check exposed a category-precedence error, which was corrected and covered by a regression test |

The environment resolves public sites through an outbound proxy; live fetch checks used the documented trusted-proxy DNS setting. This is an execution-environment setting, not a requirement for normal direct-network deployments.

## Not verified here

- No commercial LLM credentials were supplied. The routing, quote gates and failure behavior were tested with controlled model responses; live extraction accuracy, reasoning quality, latency and spend were not measured.
- The complete 18-company worker run and sustained multi-day scheduling were not soak-tested.
- BSE successful responses could not be validated live because access was denied. Its adapter is implemented but remains a deployment check.
- Native/background push is not implemented. Alerts are durable in-app events and the browser polls while open.
- Docker Compose configuration is included; a Docker image build was not run here.
- No public deployment, GitHub repository or conversation-share URL was created.

The downloaded company PDFs/text cache are not included in the deliverable. The app recreates its research corpus from primary sources when a ticker is loaded.
