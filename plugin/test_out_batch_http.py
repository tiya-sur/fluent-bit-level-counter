
import importlib
import json
import sys
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch


def _load_plugin():
    """Import (or reload) the plugin and return the module."""
    import importlib.util, pathlib, os

    plugin_path = pathlib.Path(__file__).parent / "my_plugin" / "out_batch_http.py"
    if not plugin_path.exists():
        plugin_path = pathlib.Path(__file__).parent / "out_batch_http.py"

    spec   = importlib.util.spec_from_file_location("out_batch_http", plugin_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def _make_record(message, level, file_, line,
                 timestamp="2026-03-29T10:00:00Z",
                 source_file="/logs/test.log"):
    return {
        "message":     message,
        "level":       level,
        "file":        file_,
        "line":        line,
        "timestamp":   timestamp,
        "source_file": source_file,
    }


class _FakeResponse:
  
    def __init__(self, status=200):
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass




class TestTimeoutFlush(unittest.TestCase):
   

    def test_timeout_flush(self):
        plugin = _load_plugin()
        sent_batches = []

        def fake_urlopen(req, timeout=None):
            body = req.data.decode()
            sent_batches.append(json.loads(body))
            return _FakeResponse(200)

        plugin.plugin_init({
            "batch_size":        "5",
            "batch_timeout_sec": "2",
            "collapse_alerts":   "false",
            "host":              "127.0.0.1",
            "port":              "9999",
            "path":              "/test",
            "retry_limit":       "0",
            "retry_delay_sec":   "0",
        })

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            plugin.output_cb("tag", time.time(), _make_record("A", "ERROR", "a.py", 1))
            plugin.output_cb("tag", time.time(), _make_record("B", "INFO",  "b.py", 2))

          
            self.assertEqual(len(sent_batches), 0, "Should not flush before timeout")

          
            time.sleep(2.5)

        plugin.plugin_exit({})

        self.assertEqual(len(sent_batches), 1, "Expected exactly 1 HTTP request")
        self.assertEqual(len(sent_batches[0]), 2, "Expected 2 records in the batch")
        print("  PASS Test 1: timeout flush – 2 records sent in 1 request after ~2s")


class TestCountFlush(unittest.TestCase):
   

    def test_count_flush(self):
        plugin = _load_plugin()
        sent_batches = []

        def fake_urlopen(req, timeout=None):
            sent_batches.append(json.loads(req.data.decode()))
            return _FakeResponse(200)

        plugin.plugin_init({
            "batch_size":        "5",
            "batch_timeout_sec": "10",
            "collapse_alerts":   "false",
            "host":              "127.0.0.1",
            "port":              "9999",
            "path":              "/test",
            "retry_limit":       "0",
            "retry_delay_sec":   "0",
        })

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            for i in range(5):
                plugin.output_cb("tag", time.time(),
                                 _make_record(f"Msg {i}", "ERROR", "x.py", i))

        plugin.plugin_exit({})

        self.assertGreaterEqual(len(sent_batches), 1, "Expected at least 1 HTTP request")
        total_records = sum(len(b) for b in sent_batches)
        self.assertEqual(total_records, 5, "Expected all 5 records to be sent")
        print(f"  PASS Test 2: count flush – 5 records sent in {len(sent_batches)} request(s)")


class TestAlertCollapsing(unittest.TestCase):
    
    def test_alert_collapsing(self):
        plugin = _load_plugin()
        sent_batches = []

        def fake_urlopen(req, timeout=None):
            sent_batches.append(json.loads(req.data.decode()))
            return _FakeResponse(200)

        plugin.plugin_init({
            "batch_size":        "200",
            "batch_timeout_sec": "2",
            "collapse_alerts":   "true",
            "host":              "127.0.0.1",
            "port":              "9999",
            "path":              "/test",
            "retry_limit":       "0",
            "retry_delay_sec":   "0",
        })

        rec1 = _make_record("Order reject count 12", "ERROR", "order_handler.py", 88,
                             timestamp="2026-03-29T10:00:00Z")
        rec2 = _make_record("Order reject count 13", "ERROR", "order_handler.py", 88,
                             timestamp="2026-03-29T10:00:01Z")
        rec3 = _make_record("Socket timeout 7",      "ERROR", "network.py",       41,
                             timestamp="2026-03-29T10:00:02Z")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            plugin.output_cb("tag", time.time(), rec1)
            plugin.output_cb("tag", time.time(), rec2)
            plugin.output_cb("tag", time.time(), rec3)
            
            time.sleep(2.5)

        plugin.plugin_exit({})

        all_records = [r for batch in sent_batches for r in batch]

        self.assertEqual(
            len(all_records), 2,
            f"Expected 2 records after collapsing, got {len(all_records)}: {all_records}"
        )

       
        order_records = [r for r in all_records if "order" in r["file"].lower()]
        self.assertEqual(len(order_records), 1)
        self.assertIn("13", order_records[0]["message"],
                      "Latest record (count 13) should survive, not count 12")

      
        socket_records = [r for r in all_records if "network" in r["file"].lower()]
        self.assertEqual(len(socket_records), 1)

        print(
            f"  PASS Test 3: alert collapsing – 3 records -> {len(all_records)} record(s) sent "
            f"(duplicate key collapsed, latest survives)"
        )

    def test_composite_key_building(self):
        
        plugin = _load_plugin()
        plugin.plugin_init({"collapse_alerts": "true"})

        key1 = plugin._make_alert_key(
            {"level": "ERROR", "message": "Order reject count 12", "file": "order_handler.py", "line": 88}
        )
        key2 = plugin._make_alert_key(
            {"level": "ERROR", "message": "Order reject count 13", "file": "order_handler.py", "line": 88}
        )
        self.assertEqual(key1, key2, "Normalised keys must match for same logical alert")

        key3 = plugin._make_alert_key(
            {"level": "ERROR", "message": "Socket timeout 7", "file": "network.py", "line": 41}
        )
        self.assertNotEqual(key1, key3, "Different alerts must produce different keys")

        print("  PASS Test 3b: composite key building – same logical alert -> same key")


class TestFieldPreservation(unittest.TestCase):
   
    def test_field_preservation(self):
        plugin = _load_plugin()
        sent_batches = []

        def fake_urlopen(req, timeout=None):
            sent_batches.append(json.loads(req.data.decode()))
            return _FakeResponse(200)

        plugin.plugin_init({
            "batch_size":        "200",
            "batch_timeout_sec": "2",
            "collapse_alerts":   "false",
            "host":              "127.0.0.1",
            "port":              "9999",
            "path":              "/test",
            "retry_limit":       "0",
            "retry_delay_sec":   "0",
        })

        original = [
            _make_record("A", "ERROR",   "module_a.py", 123,
                         "2026-03-29T10:00:00Z", "/logs/a.log"),
            _make_record("B", "WARNING", "module_b.py", 456,
                         "2026-03-29T10:00:01Z", "/logs/b.log"),
        ]

        expected_fields = {"message", "level", "file", "line", "timestamp", "source_file"}

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            for rec in original:
                plugin.output_cb("tag", time.time(), rec)
            time.sleep(2.5)

        plugin.plugin_exit({})

        all_records = [r for batch in sent_batches for r in batch]
        self.assertEqual(len(all_records), 2)

        for i, (sent, orig) in enumerate(zip(all_records, original)):
            self.assertEqual(set(sent.keys()), expected_fields,
                             f"Record {i}: unexpected fields {set(sent.keys())}")
            for field in expected_fields:
                self.assertEqual(sent[field], orig[field],
                                 f"Record {i}: field '{field}' was mutated")

        print("  PASS Test 4: field preservation – all fields sent unchanged, no wrappers added")


class TestFailureHandling(unittest.TestCase):
   
    def test_failure_is_logged_and_retried(self):
        plugin = _load_plugin()
        attempt_count = [0]
        log_messages  = []

        original_print = __builtins__["print"] if isinstance(__builtins__, dict) else print

        def capturing_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            log_messages.append(msg)
            original_print(*args, **kwargs)

        import builtins
        builtins.print = capturing_print

        def always_fail(req, timeout=None):
            attempt_count[0] += 1
            raise urllib.error.URLError("Connection refused")

        import urllib.error

        plugin.plugin_init({
            "batch_size":        "200",
            "batch_timeout_sec": "2",
            "collapse_alerts":   "false",
            "host":              "127.0.0.1",
            "port":              "9999",
            "path":              "/test",
            "retry_limit":       "2",
            "retry_delay_sec":   "0.1",
        })

        try:
            with patch("urllib.request.urlopen", side_effect=always_fail):
                plugin.output_cb("tag", time.time(),
                                 _make_record("disk error", "ERROR", "disk.py", 10))
                time.sleep(2.5)
            plugin.plugin_exit({})
        finally:
            builtins.print = original_print

        
        self.assertEqual(attempt_count[0], 3,
                         f"Expected 3 send attempts, got {attempt_count[0]}")

        failure_logs = [m for m in log_messages if "FAILED" in m or "GIVING UP" in m]
        self.assertGreater(len(failure_logs), 0,
                           "Expected failure messages to be logged")

        print(
            f"  PASS Test 5: failure handling – "
            f"{attempt_count[0]} attempt(s) made, failure logged, no crash"
        )



class TestKeyNormalisation(unittest.TestCase):


    def setUp(self):
        self.plugin = _load_plugin()
        self.plugin.plugin_init({"collapse_alerts": "true"})

    def test_semicolons_split(self):
        brief = self.plugin._derive_alert_brief("Socket timeout;;;downstream host")
        self.assertEqual(brief, "Socket timeout")

    def test_no_semicolons(self):
        brief = self.plugin._derive_alert_brief("Socket timeout")
        self.assertEqual(brief, "Socket timeout")

    def test_digits_stripped(self):
        self.assertEqual(
            self.plugin._clean_alert_brief("Order reject count 12"),
            self.plugin._clean_alert_brief("Order reject count 13"),
        )

    def test_hex_stripped(self):
        c1 = self.plugin._clean_alert_brief("error at 0x7f9abc123")
        c2 = self.plugin._clean_alert_brief("error at 0x000000000")
        self.assertEqual(c1, c2)

    def test_check_the_file_stripped(self):
        c = self.plugin._clean_alert_brief("some error...check the file: foo.py line 12")
        self.assertNotIn("check the file", c)
        self.assertNotIn("foo.py", c)

    def test_different_levels_different_keys(self):
        k_error   = self.plugin._make_alert_key(
            {"level": "ERROR",   "message": "disk full", "file": "x.py", "line": 1})
        k_warning = self.plugin._make_alert_key(
            {"level": "WARNING", "message": "disk full", "file": "x.py", "line": 1})
        self.assertNotEqual(k_error, k_warning)

    def test_different_lines_different_keys(self):
        k1 = self.plugin._make_alert_key(
            {"level": "ERROR", "message": "msg", "file": "x.py", "line": 1})
        k2 = self.plugin._make_alert_key(
            {"level": "ERROR", "message": "msg", "file": "x.py", "line": 2})
        self.assertNotEqual(k1, k2)



if __name__ == "__main__":
    print("=" * 60)
    print("out_batch_http.py  –  test suite")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

   
    for cls in [
        TestKeyNormalisation,
        TestFieldPreservation,
        TestAlertCollapsing,
        TestCountFlush,
        TestTimeoutFlush,
        TestFailureHandling,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
