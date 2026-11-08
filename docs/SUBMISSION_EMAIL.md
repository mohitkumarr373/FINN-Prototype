To: assessment@enam.com
Subject: IIT Delhi – AI Engineer - 2027

Hi ENAM team,

I built FINN around two decisions an investor makes repeatedly: what deserves attention now, and what the underlying company disclosures actually support.

The mobile inbox covers an 18-company mock portfolio, with selectable digest windows, collapsed routine filings and configurable filing, price and volume alerts. Research starts from an NSE ticker and a question, retrieves annual reports and earnings-call transcripts, and exposes the source pages behind the answer.

The main trade-off was scope. I used a small Python service, SQLite and a responsive web interface. Rules handle filtering and calculations; a smaller model handles extraction and a stronger model is reserved for requested analysis. Screener locates documents, but its summary tables do not become research evidence. Missing or incomparable numbers stay missing.

I left out native notifications, biography enrichment, voice and automated four-quarter promise scoring. Alerts currently arrive in-app. The illustrative inbox is labelled separately from live data; research uses actual company documents. BSE returned 403 during testing, so source gaps remain visible and research has an NSE fallback. Live model-provider evaluation still needs credentials.

Next I would prioritise extraction/relevance evaluation, filing revisions and background notification delivery.

Prototype: [deployed mobile URL]
Repository: [GitHub URL]
AI build history: [conversation export or share URL]

The repository includes setup instructions, tests and a record of the checks performed.

Regards,
Mohit Kumar
