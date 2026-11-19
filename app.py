"""Local visual demo. Run with .venv/Scripts/python app.py."""

import copy
import re
import time
from pathlib import Path
from queue import Queue
from threading import RLock, Thread

from flask import Flask, jsonify, render_template, request, send_from_directory

from demo import generate_site
from extractor import consume, extract_links, fetch_html, produce
from advanced import run_advanced


ROOT = Path(__file__).resolve().parent
SITE = ROOT / "demo_site"
app = Flask(__name__)


class DemoRun:
    """Display state only; extraction remains in extractor.py."""

    def __init__(self):
        self.lock = RLock()
        self.reset()

    def reset(self):
        self.data = {
            "status": "ready", "total": 100, "produced": 0, "completed": 0,
            "errors": 0, "links": 0, "pending": [], "peak": 0,
            "producer": "Ready to fetch", "consumer": "Waiting for pages",
            "producer_done": False, "consumer_done": False,
            "producer_log": [], "consumer_log": [], "results": [], "elapsed": 0,
            "mode": "basic", "capacity": 100, "dropped": [], "intervention": None,
            "fetch_workers": {}, "consumers": {}, "consumer_count": 1,
        }
        self.started = None

    def log(self, worker, message):
        # Callers hold the state lock. Keep the display log deliberately short.
        entries = self.data[f"{worker}_log"]
        entries.append({"time": round(time.monotonic() - self.started, 1), "message": message})
        del entries[:-12]

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.data)
            if result["status"] == "running":
                result["elapsed"] = round(time.monotonic() - self.started, 1)
            return result

    def start(self, base_url, mode="basic"):
        with self.lock:
            if self.data["status"] == "running":
                return False
            self.reset()
            self.data["mode"] = mode
            self.data["capacity"] = 10 if mode == "advanced" else 100
            self.started = time.monotonic()
            self.data["status"] = "running"
            Thread(target=self.run, args=(base_url,), name="demo-run", daemon=True).start()
            return True

    def run(self, base_url):
        try:
            generate_site(SITE, count=100)
            urls = [f"{base_url}/pages/match-{index:03}.html" for index in range(1, 101)]
            if self.data["mode"] == "advanced":
                run_advanced(self, urls)
                with self.lock:
                    self.data["status"] = "finished"
                return
            pages = DisplayQueue(self)
            results = DisplayResults(self)

            def paced_fetch(url):
                started = time.monotonic()
                with self.lock:
                    self.data["producer"] = f"Fetching {url.rsplit('/', 1)[-1]}"
                try:
                    return fetch_html(url)
                finally:
                    # Include fetch time: at most five fetch attempts per second.
                    time.sleep(max(0, 0.2 - (time.monotonic() - started)))

            def paced_parse(html):
                started = time.monotonic()
                try:
                    return extract_links(html)
                finally:
                    time.sleep(max(0, 0.25 - (time.monotonic() - started)))

            def producer_work():
                try:
                    produce(urls, pages, paced_fetch)
                finally:
                    with self.lock:
                        self.data["producer_done"] = True
                        self.data["producer"] = "Finished fetching"
                        self.log("producer", "All URLs attempted; completion marker queued")

            def consumer_work():
                consume(pages, results, paced_parse)
                with self.lock:
                    self.data["consumer_done"] = True
                    self.data["consumer"] = "Finished processing"
                    self.log("consumer", "Completion marker received")

            producer = Thread(target=producer_work, name="producer", daemon=True)
            consumer = Thread(target=consumer_work, name="consumer", daemon=True)
            consumer.start()
            producer.start()
            producer.join()
            consumer.join()
            with self.lock:
                self.data["status"] = "finished"
        except Exception as error:
            with self.lock:
                self.data["status"] = "failed"
                self.log("producer", f"Demo failed: {error}")
        finally:
            with self.lock:
                self.data["elapsed"] = round(time.monotonic() - self.started, 1)


class DisplayQueue(Queue):
    """Queue's storage hooks observe real enqueue/dequeue operations under its lock."""

    def __init__(self, run):
        super().__init__(maxsize=100)
        self.run = run

    def _put(self, page):
        super()._put(page)
        if page is None:
            return  # Completion marker is not an HTML message in the display.
        filename = page.url.rsplit("/", 1)[-1]
        with self.run.lock:
            state = self.run.data
            state["pending"].append(filename)
            state["produced"] += 1
            state["peak"] = max(state["peak"], len(state["pending"]))
            self.run.log("producer", f"Queued {filename}" + (" (fetch error)" if page.error else ""))

    def _get(self):
        page = super()._get()
        if page is not None:
            filename = page.url.rsplit("/", 1)[-1]
            with self.run.lock:
                self.run.data["pending"].pop(0)
                self.run.data["consumer"] = f"Parsing {filename}"
                self.run.log("consumer", f"Dequeued {filename}")
        return page


class DisplayResults(list):
    """Publish each result when the unchanged consumer appends it."""

    def __init__(self, run):
        super().__init__()
        self.run = run

    def append(self, result):
        super().append(result)
        filename = result["url"].rsplit("/", 1)[-1]
        with self.run.lock:
            state = self.run.data
            state["completed"] += 1
            state["errors"] += int("error" in result)
            state["links"] += len(result.get("links", []))
            # Titles are demo metadata, not a new responsibility for the extractor.
            title = filename
            try:
                html = (SITE / filename).read_text(encoding="utf-8")
                match = re.search(r"<title>(.*?)</title>", html)
                if match:
                    title = match.group(1)
            except OSError:
                pass
            state["results"].append({**result, "title": title, "filename": filename})
            self.run.log("consumer", f"{'Failed' if 'error' in result else 'Completed'} {filename}")
            state["consumer"] = "Waiting for next page"


demo_run = DemoRun()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/pages/<path:filename>")
def demo_page(filename):
    return send_from_directory(SITE, filename)


@app.get("/api/state")
def state():
    response = jsonify(demo_run.snapshot())
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/start")
def start():
    # Use the listening port, not user-supplied URLs or the Host header.
    port = request.environ["SERVER_PORT"]
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "basic") if isinstance(body, dict) else None
    if mode not in {"basic", "advanced"}:
        return jsonify(error="Mode must be basic or advanced"), 400
    if not demo_run.start(f"http://127.0.0.1:{port}", mode):
        return jsonify(error="A demonstration is already running"), 409
    return jsonify(started=True), 202


if __name__ == "__main__":
    # Threaded HTTP serving lets the producer fetch pages while the UI polls state.
    app.run(host="127.0.0.1", port=5000, threaded=True, debug=False, use_reloader=False)
