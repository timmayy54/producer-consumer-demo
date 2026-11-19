# Producer/consumer web link extractor

A small Python command-line program: one producer fetches HTML, a bounded
in-memory queue passes pages to one concurrent consumer, and the consumer extracts
hyperlinks. Python 3.10+; no third-party dependencies. Tested on Python 3.12.

## Run

```powershell
python extractor.py https://example.com
python extractor.py --file urls.txt --queue-size 100 --timeout 10
python -m unittest -v
```

Input files contain one URL per line; empty lines are ignored. Positional URLs
and file entries can be combined. Output is one JSON object per input occurrence
on stdout, in input order, after processing completes. Redirect to save it:

```powershell
python extractor.py --file demo_site/urls.txt > results.jsonl
```

Success: `{"url": "...", "links": ["/next", "#market"]}`.
Failure: `{"url": "...", "stage": "fetch", "error": "..."}` (or stage `parse`).
Exit codes: 0 means all pages processed successfully, 1 means one or more page
failures, and 2 means invalid command-line input. An empty HTML page succeeds
with an empty link list.

## Local demonstration

Generate 100 repeatable fictional football pages, then serve them in terminal 1:

```powershell
python demo.py
python -m http.server 8000 --bind 127.0.0.1 --directory demo_site
```

In terminal 2, run the extractor command above. Stop the server with Ctrl+C.
The generator supports `--count` and `--port`; match the port in the server command.
Generated files are ignored by Git and can be regenerated. The generator overwrites
matching generated filenames; use the dedicated demo_site directory only.

Each page has a fictional match-winner market, over/under 2.5 prices, and three
anchors. The extractor reads anchors, not betting odds, and does not follow links.
Teams are familiar English clubs, not a current-season fixture list.

Match-winner implied probabilities sum to 1.04 before display rounding. Six decimal
places keep the displayed book close to 104%. With every price capped at 3.0, the
three-way market necessarily has relatively similar prices: approximately
2.419355 to 3.0. Over/under prices are independently sampled between 1.5 and 3.0;
no book-percentage constraint is imposed on that two-way market.

## Walk through the code

1. `main` reads URLs and settings, calls `run_pipeline`, then prints JSON results.
2. `run_pipeline` creates a `Queue(maxsize=...)` and starts two threads. Only the
   consumer changes the result list; main reads it after joining both threads.
3. `produce` calls `fetch_html` once per URL and queues a `Page` containing its URL
   and HTML, or an explicit fetch error. A full queue blocks the producer.
4. `consume` blocks on `get`, parses each page, and records a result or parse error.
5. The producer's `finally` block queues `None`. FIFO ordering means the consumer
   sees this marker after all preceding pages and exits. Temporary emptiness is
   never mistaken for completion. Thread joins wait for the workers to finish.

`extract_links` uses the standard-library HTML parser, not a regular expression.
It collects `a[href]` values in document order, preserving duplicate, empty,
relative, fragment and non-HTTP hrefs. HTML entities are decoded. It does not resolve
relative URLs, honour `base` tags or execute JavaScript. The parser tolerates many
malformed HTML documents, so broken markup does not necessarily cause an error.

The per-page `Exception` catches deliberately isolate fetch/parser failures;
process termination and resource exhaustion are not recoverable guarantees.
HTTP errors are surfaced by urllib. Requests use a socket timeout (not a strict
whole-request deadline), follow normal urllib redirects, require an HTML content
type and cap each response at 2 MB. Declared encoding is used, with UTF-8 as the
fallback and replacement for invalid byte sequences.

## Scope and tradeoffs

- Producer and consumer overlap, but URL fetches are sequential. A fixed fetcher
  pool is a possible extension, with completion signalled only after all fetchers finish.
- The queue limits pending page count and applies backpressure. **Oldest-entry
  trimming is not implemented.** That alternative would discard pending work and
  should explicitly report which inputs were dropped.
- Input URLs and extracted results remain in memory. This is a finite-batch demo,
  not a fully streaming or durable system. There is no queue persistence, automatic
  acknowledgement, retry, dead-letter handling or recovery after process failure.
- Adding consumers requires a completion marker per consumer and a decision about
  output ordering. More parsing threads do not guarantee greater CPU throughput.
- No crawling, browser automation, odds validation, scheduling or external broker.
- Tests use local HTTP and injected failures, not external website availability.
  They cover parsing, concurrent progress, shutdown, error isolation, queue capacity,
  HTTP/content/size errors, CLI output and generated-market constraints. This is
  targeted MVP coverage, not a claim of exhaustive coverage.

## Development process

Built with AI assistance following a human-selected one-producer/one-consumer
design. The implementation and deterministic tests are available for review;
no claim of independent unaided authorship is made. Future changes should preserve
the small explainable design and demonstrate their behaviour with tests.
