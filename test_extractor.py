import functools
import json
import re
import subprocess
import sys
import tempfile
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from demo import generate_site
from extractor import extract_links, fetch_html, run_pipeline


class ParserTests(unittest.TestCase):
    def test_href_contract(self):
        html = '<A HREF="/a?x=1&amp;y=2">A</A><a href="/a">B</a><a href="/a">C</a>'
        html += '<a>No href</a><a href="">Empty</a><a href="#market">Market</a>'
        html += '<img src="ignored.png"><a href="mailto:a@example.test">Email</a>'
        self.assertEqual(extract_links(html),
                         ["/a?x=1&y=2", "/a", "/a", "", "#market", "mailto:a@example.test"])

    def test_empty_and_imperfect_html(self):
        self.assertEqual(extract_links(""), [])
        self.assertEqual(extract_links('<p><a href="/match">Unclosed'), ["/match"])


class PipelineTests(unittest.TestCase):
    def test_empty_input_finishes(self):
        self.assertEqual(run_pipeline([]), [])

    def test_fetch_and_parse_failures_do_not_stop_later_pages(self):
        def fetch(url):
            if url == "fetch-bad":
                raise TimeoutError("timed out")
            return url

        def parse(html):
            if html == "parse-bad":
                raise ValueError("cannot parse")
            return [html]

        results = run_pipeline(["first", "fetch-bad", "parse-bad", "last"],
                               queue_size=1, fetch=fetch, parse=parse)
        self.assertEqual(results[0], {"url": "first", "links": ["first"]})
        self.assertEqual(results[1]["stage"], "fetch")
        self.assertEqual(results[2]["stage"], "parse")
        self.assertEqual(results[3], {"url": "last", "links": ["last"]})

    def test_consumer_runs_before_producer_finishes(self):
        first_parsed = Event()

        def fetch(url):
            if url == "second" and not first_parsed.wait(timeout=2):
                raise TimeoutError("consumer did not run during production")
            return url

        def parse(html):
            first_parsed.set()
            return [html]

        results = run_pipeline(["first", "second"], fetch=fetch, parse=parse)
        self.assertEqual(results, [{"url": "first", "links": ["first"]},
                                   {"url": "second", "links": ["second"]}])

    def test_small_queue_preserves_all_work_including_duplicates(self):
        urls = [str(index) for index in range(200)] + ["0"]
        results = run_pipeline(urls, queue_size=1, fetch=lambda url: url, parse=lambda html: [html])
        self.assertEqual([result["url"] for result in results], urls)

    def test_invalid_queue_size(self):
        with self.assertRaises(ValueError):
            run_pipeline([], queue_size=0)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


class LocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        handler = functools.partial(QuietHandler, directory=cls.temp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_port
        generate_site(cls.directory, count=100, port=cls.port)
        cls.thread = Thread(target=cls.server.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()
        cls.temp.cleanup()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}/{path}"

    def test_real_http_and_missing_page_isolation(self):
        results = run_pipeline([self.url("missing.html"), self.url("match-001.html")])
        self.assertEqual(results[0]["stage"], "fetch")
        self.assertIn("404", results[0]["error"])
        self.assertEqual(results[1]["links"],
                         ["match-002.html", "#match-winner", "#total-goals"])

    def test_non_html_and_unsupported_scheme(self):
        with self.assertRaisesRegex(ValueError, "not HTML"):
            fetch_html(self.url("urls.txt"))
        with self.assertRaisesRegex(ValueError, "Only HTTP"):
            fetch_html("file:///example.html")

    def test_page_size_limit(self):
        with patch("extractor.MAX_PAGE_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "exceeds"):
                fetch_html(self.url("match-001.html"))

    def test_socket_timeout_passed_to_transport(self):
        with patch("extractor.urlopen", side_effect=TimeoutError("slow")) as open_url:
            with self.assertRaises(TimeoutError):
                fetch_html(self.url("match-001.html"), timeout=0.5)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 0.5)

    def test_cli_extracts_all_100_pages(self):
        command = [sys.executable, str(Path(__file__).with_name("extractor.py")),
                   "--file", str(self.directory / "urls.txt"), "--queue-size", "2"]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        results = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(len(results), 100)
        self.assertTrue(all(len(result["links"]) == 3 for result in results))

    def test_cli_reports_partial_failure_with_nonzero_exit(self):
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("extractor.py")),
             self.url("missing.html"), self.url("match-001.html")],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 1)
        results = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(results[0]["stage"], "fetch")
        self.assertIn("links", results[1])

    def test_generated_market_constraints(self):
        for path in self.directory.glob("*.html"):
            prices = [float(value) for value in re.findall(r"<li>\w+: ([\d.]+)</li>",
                                                          path.read_text(encoding="utf-8"))]
            self.assertEqual(len(prices), 5)
            self.assertTrue(all(1.5 <= price <= 3 for price in prices))
            self.assertAlmostEqual(sum(1 / price for price in prices[:3]), 1.04, places=6)


if __name__ == "__main__":
    unittest.main()
