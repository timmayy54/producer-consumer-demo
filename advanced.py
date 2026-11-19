"""Advanced demo orchestration: concurrent fetching, oldest eviction and scale-out."""

import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from queue import Queue
from threading import Thread, current_thread

from extractor import Page, consume, extract_links, fetch_html


class TitleParser(HTMLParser):
    """Read a title for display only, independently of hyperlink extraction."""

    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


class DroppingQueue(Queue):
    """Limit pending pages without blocking producers; retain completion markers."""

    def __init__(self, run, on_fifth_drop, capacity=10):
        # Public put must not wait for room. Enforce the page limit atomically in _put.
        super().__init__(maxsize=0)
        self.run = run
        self.capacity = capacity
        self.on_fifth_drop = on_fifth_drop
        self.closing = False
        self.titles = {}

    def _put(self, page):
        # Queue holds its mutex throughout this hook: concurrent writers cannot
        # both test a slot as free or evict the same page.
        with self.run.lock:
            state = self.run.data
            if page is None:
                self.closing = True
                super()._put(page)
                return
            if self.closing:
                raise ValueError("Cannot enqueue pages after completion markers")
            if len(state["pending"]) == self.capacity:
                oldest = super()._get()
                self.unfinished_tasks -= 1  # Eviction settles this queue item.
                state["pending"].pop(0)
                state["dropped"].append({"url": oldest.url,
                    "filename": oldest.url.rsplit("/", 1)[-1],
                    "reason": "Queue at capacity: replaced oldest pending page",
                    "time": round(time.monotonic() - self.run.started, 1),
                    "consumers": state["consumer_count"]})
                self.run.log("producer", f"Dropped oldest: {oldest.url.rsplit('/', 1)[-1]}")
                if len(state["dropped"]) == 5:
                    self.on_fifth_drop()
            super()._put(page)
            filename = page.url.rsplit("/", 1)[-1]
            title_parser = TitleParser()
            title_parser.feed(page.html)
            self.titles[page.url] = title_parser.title or filename
            state["pending"].append(filename)
            state["produced"] += 1
            state["peak"] = max(state["peak"], len(state["pending"]))
            self.run.log("producer", f"Queued {filename}" + (" (fetch error)" if page.error else ""))

    def _get(self):
        page = super()._get()
        if page is not None:
            with self.run.lock:
                state = self.run.data
                state["pending"].pop(0)
                name = current_thread().name
                state["consumers"][name] = f"Processing {page.url.rsplit('/', 1)[-1]}"
                self.run.log("consumer", f"{name} took {page.url.rsplit('/', 1)[-1]}")
        return page


class AdvancedResults(list):
    def __init__(self, run, pages):
        super().__init__()
        self.run = run
        self.pages = pages

    def append(self, result):
        name = current_thread().name
        with self.run.lock:
            super().append(result)
            state = self.run.data
            state["completed"] += 1
            state["errors"] += int("error" in result)
            state["links"] += len(result.get("links", []))
            state["results"].append({**result, "consumer": name,
                "filename": result["url"].rsplit("/", 1)[-1],
                "title": self.pages.titles[result["url"]]})
            state["consumers"][name] = "Waiting for a page"
            self.run.log("consumer", f"{name} completed {result['url'].rsplit('/', 1)[-1]}")


def run_advanced(run, urls, fetch=fetch_html, parse=extract_links,
                 fetch_interval=0.4, parse_interval=0.5):
    """Four fetch threads; add consumer 2 exactly when the fifth page is evicted."""
    consumers = []

    def paced_parse(html):
        started = time.monotonic()
        try:
            return parse(html)
        finally:
            time.sleep(max(0, parse_interval - (time.monotonic() - started)))

    def consumer_work():
        consume(pages, results, paced_parse)
        with run.lock:
            run.data["consumers"][current_thread().name] = "Finished"

    def add_consumer():
        # First call is before production. Second call is inside the fifth eviction.
        with run.lock:
            name = f"Consumer {len(consumers) + 1}"
            worker = Thread(target=consumer_work, name=name, daemon=True)
            consumers.append(worker)
            run.data["consumer_count"] = len(consumers)
            run.data["consumers"][name] = "Waiting for a page"
            if len(consumers) == 2:
                run.data["intervention"] = {"time": round(time.monotonic() - run.started, 1),
                    "dropped_at_trigger": len(run.data["dropped"]),
                    "message": "Simulated DevOps intervention: Consumer 2 started after 5 drops"}
                run.log("consumer", run.data["intervention"]["message"])
            worker.start()

    pages = DroppingQueue(run, add_consumer)
    results = AdvancedResults(run, pages)
    add_consumer()

    def fetch_and_enqueue(url):
        name = current_thread().name.replace("fetch_", "Fetcher ")
        started = time.monotonic()
        with run.lock:
            run.data["fetch_workers"][name] = f"Fetching {url.rsplit('/', 1)[-1]}"
        try:
            page = Page(url=url, html=fetch(url))
        except Exception as error:
            page = Page(url=url, error=f"{type(error).__name__}: {error}")
        time.sleep(max(0, fetch_interval - (time.monotonic() - started)))
        pages.put(page)
        with run.lock:
            run.data["fetch_workers"][name] = "Ready for next URL"

    try:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="fetch") as pool:
            futures = [pool.submit(fetch_and_enqueue, url) for url in urls]
            for future in futures:
                future.result()  # Results are enqueued by workers, not in this input order.
    finally:
        # Pool has finished: no further eviction/scale-out can race with shutdown.
        with run.lock:
            run.data["producer_done"] = True
            run.data["producer"] = "All four fetch workers finished"
            for name in run.data["fetch_workers"]:
                run.data["fetch_workers"][name] = "Finished"
        for _ in consumers:
            pages.put(None)  # Reserved control entries do not evict pending pages.
        for worker in consumers:
            worker.join()
        with run.lock:
            run.data["consumer_done"] = True
            run.data["consumer"] = "All consumers finished"
