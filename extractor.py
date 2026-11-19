"""Fetch HTML in one thread and extract hyperlinks in another."""

import argparse
import json
import math
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_PAGE_BYTES = 2_000_000


@dataclass(frozen=True)
class Page:
    url: str
    html: str = ""
    error: str | None = None


class LinkParser(HTMLParser):
    """Collect anchor href values in document order, including duplicates."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value is not None:
                    self.links.append(value)
                    break


def extract_links(html: str) -> list[str]:
    parser = LinkParser()
    parser.feed(html)
    parser.close()
    return parser.links


def fetch_html(url: str, timeout: float = 10.0) -> str:
    """Fetch HTTP(S) HTML with a socket timeout and a response-size limit."""
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS URLs are supported")
    request = Request(url, headers={"User-Agent": "web-link-extractor/0.1"})
    with urlopen(request, timeout=timeout) as response:
        if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("Response is not HTML")
        body = response.read(MAX_PAGE_BYTES + 1)
        if len(body) > MAX_PAGE_BYTES:
            raise ValueError(f"Page exceeds {MAX_PAGE_BYTES} bytes")
        encoding = response.headers.get_content_charset() or "utf-8"
        return body.decode(encoding, errors="replace")


def produce(urls: list[str], pages: Queue, fetch: Callable[[str], str]) -> None:
    try:
        for url in urls:
            try:
                page = Page(url=url, html=fetch(url))
            except Exception as error:
                # A failed URL becomes an explicit result, not a stopped pipeline.
                page = Page(url=url, error=f"{type(error).__name__}: {error}")
            pages.put(page)
    finally:
        # None is the end marker: it follows every queued page, even on failure.
        pages.put(None)


def consume(pages: Queue, results: list[dict], parse: Callable[[str], list[str]]) -> None:
    while True:
        page = pages.get()  # Wait for work; an empty queue is not completion.
        if page is None:
            return
        if page.error is not None:
            results.append({"url": page.url, "stage": "fetch", "error": page.error})
            continue
        try:
            links = parse(page.html)
        except Exception as error:
            results.append({"url": page.url, "stage": "parse",
                            "error": f"{type(error).__name__}: {error}"})
        else:
            results.append({"url": page.url, "links": links})


def run_pipeline(
    urls: list[str],
    queue_size: int = 100,
    fetch: Callable[[str], str] = fetch_html,
    parse: Callable[[str], list[str]] = extract_links,
) -> list[dict]:
    if queue_size < 1:
        raise ValueError("queue_size must be positive")
    pages: Queue = Queue(maxsize=queue_size)
    results: list[dict] = []  # Only the consumer writes; main reads after join.
    producer = Thread(target=produce, args=(urls, pages, fetch), name="producer")
    consumer = Thread(target=consume, args=(pages, results, parse), name="consumer")
    consumer.start()
    producer.start()
    producer.join()
    consumer.join()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="*", help="HTTP(S) page URLs")
    parser.add_argument("--file", type=Path, help="One URL per line; blank lines ignored")
    parser.add_argument("--queue-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=10.0, help="Socket timeout in seconds")
    args = parser.parse_args()
    if args.queue_size < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("queue size and timeout must be positive finite values")
    urls = list(args.urls)
    if args.file:
        try:
            urls.extend(line.strip() for line in args.file.read_text(encoding="utf-8-sig").splitlines()
                        if line.strip())
        except (OSError, UnicodeError) as error:
            parser.error(str(error))
    if not urls:
        parser.error("provide URLs or --file")
    results = run_pipeline(urls, args.queue_size, fetch=lambda url: fetch_html(url, args.timeout))
    for result in results:
        print(json.dumps(result))
    return 1 if any("error" in result for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
