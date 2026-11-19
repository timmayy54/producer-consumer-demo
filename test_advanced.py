import time
import unittest
from importlib.util import find_spec
from threading import Barrier, Lock

HAS_FLASK = find_spec("flask") is not None
if HAS_FLASK:
    from app import DemoRun, app
from advanced import DroppingQueue, run_advanced
from extractor import Page


def new_run():
    run = DemoRun()
    run.started = time.monotonic()
    return run


@unittest.skipUnless(HAS_FLASK, "Install requirements-ui.txt for advanced demo tests")
class AdvancedTests(unittest.TestCase):
    def test_oldest_evicted_and_fifth_drop_triggers_once(self):
        run = new_run()
        triggers = []
        pages = DroppingQueue(run, lambda: triggers.append(len(run.data["dropped"])))
        for index in range(20):
            pages.put(Page(str(index)))
        self.assertEqual(triggers, [5])
        self.assertEqual([item["url"] for item in run.data["dropped"]],
                         [str(index) for index in range(10)])
        self.assertEqual(run.data["peak"], 10)
        self.assertEqual([pages.get().url for _ in range(10)],
                         [str(index) for index in range(10, 20)])

    def test_shutdown_markers_never_evict_pending_pages(self):
        run = new_run()
        pages = DroppingQueue(run, lambda: None)
        for index in range(10):
            pages.put(Page(str(index)))
        pages.put(None)
        pages.put(None)
        self.assertEqual(run.data["dropped"], [])
        self.assertEqual(len(run.data["pending"]), 10)
        for index in range(10):
            self.assertEqual(pages.get().url, str(index))
        self.assertIsNone(pages.get())
        self.assertIsNone(pages.get())
        with self.assertRaises(ValueError):
            pages.put(Page("late"))

    def test_four_fetches_overlap_scale_out_and_all_inputs_accounted_for(self):
        run = new_run()
        barrier = Barrier(4)
        lock = Lock()
        active = peak = 0

        def fetch(url):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                if int(url) < 4:
                    barrier.wait(timeout=2)
                return f'<title>Match {url}</title><a href="/next">Next</a>'
            finally:
                with lock:
                    active -= 1

        urls = [str(index) for index in range(100)]
        run_advanced(run, urls, fetch=fetch, fetch_interval=0.001, parse_interval=0.02)
        state = run.snapshot()
        processed = [result["url"] for result in state["results"]]
        dropped = [result["url"] for result in state["dropped"]]
        self.assertEqual(peak, 4)
        self.assertEqual(len(processed) + len(dropped), 100)
        self.assertEqual(set(processed) | set(dropped), set(urls))
        self.assertFalse(set(processed) & set(dropped))
        self.assertEqual(state["intervention"]["dropped_at_trigger"], 5)
        self.assertEqual(state["consumer_count"], 2)
        self.assertEqual(set(result["consumer"] for result in state["results"]),
                         {"Consumer 1", "Consumer 2"})
        self.assertEqual(state["peak"], 10)
        self.assertEqual(state["pending"], [])
        self.assertTrue(state["producer_done"] and state["consumer_done"])

    def test_failures_and_no_scale_out_with_small_input(self):
        run = new_run()

        def fetch(url):
            if url == "bad-fetch":
                raise TimeoutError("test timeout")
            return url

        def parse(html):
            if html == "bad-parse":
                raise ValueError("test parser failure")
            return ["/next"]

        run_advanced(run, ["bad-fetch", "bad-parse", "good"], fetch=fetch, parse=parse,
                     fetch_interval=0, parse_interval=0)
        self.assertEqual(run.data["completed"], 3)
        self.assertEqual(run.data["errors"], 2)
        self.assertEqual(run.data["consumer_count"], 1)
        self.assertIsNone(run.data["intervention"])

    def test_invalid_mode_is_rejected(self):
        with app.test_client() as client:
            self.assertEqual(client.post("/api/start", json={"mode": "other"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
