

import json
import re
import time
import threading
import urllib.request
import urllib.error
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer


LISTEN_HOST       = "0.0.0.0"   
LISTEN_PORT       = 9000         

FORWARD_HOST      = "127.0.0.1" 
FORWARD_PORT      = 8080         
FORWARD_PATH      = "/"         

BATCH_SIZE        = 200          
BATCH_TIMEOUT_SEC = 2.0         
COLLAPSE_ALERTS   = True         

RETRY_LIMIT       = 3            
RETRY_DELAY_SEC   = 1.0         

_lock        = threading.Lock()
_buffer      = OrderedDict()   
_buffer_list = []              
_oldest_ts   = None            
_timer       = None
_shutdown    = False


_RE_HEX    = re.compile(r"0x[0-9a-fA-F]+")
_RE_OID    = re.compile(r"'[a-f0-9]{24}'")
_RE_DIGITS = re.compile(r"[0-9]+")
_RE_CHECK  = re.compile(r"\.\.\.check the file:.*", re.IGNORECASE)


def _derive_alert_brief(message: str) -> str:
    if ";;;" in message:
        return message.split(";;;", 1)[0]
    return message


def _clean_alert_brief(brief: str) -> str:
    s = _RE_CHECK.sub("", brief)
    s = _RE_HEX.sub("", s)
    s = _RE_OID.sub("", s)
    s = _RE_DIGITS.sub("", s)
    return " ".join(s.split()).strip().lower()


def _make_alert_key(record: dict) -> str:
    severity = str(record.get("level", "")).strip().upper()
    message  = str(record.get("message", ""))
    cleaned  = _clean_alert_brief(_derive_alert_brief(message))
    file_    = str(record.get("file", ""))
    line     = str(record.get("line", ""))
    return f"{severity}|{cleaned}|{file_}|{line}"


def _forward(records: list):
    url     = f"http://{FORWARD_HOST}:{FORWARD_PORT}{FORWARD_PATH}"
    payload = json.dumps(records).encode("utf-8")

    for attempt in range(1, RETRY_LIMIT + 2):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
            print(
                f"[middleware] FORWARDED {len(records)} record(s) "
                f"-> {url}  HTTP {status}"
            )
            return
        except urllib.error.URLError as exc:
            print(f"[middleware] FORWARD FAILED attempt {attempt} -> {url} : {exc}")
            if attempt <= RETRY_LIMIT:
                time.sleep(RETRY_DELAY_SEC)
        except Exception as exc:
            print(f"[middleware] Unexpected error attempt {attempt}: {exc}")
            if attempt <= RETRY_LIMIT:
                time.sleep(RETRY_DELAY_SEC)

    print(f"[middleware] GIVING UP. {len(records)} record(s) dropped.")



def _flush_locked():
    global _buffer, _buffer_list, _oldest_ts

    if COLLAPSE_ALERTS:
        records = list(_buffer.values())
        _buffer.clear()
    else:
        records = list(_buffer_list)
        _buffer_list.clear()

    _oldest_ts = None

    if not records:
        return

    _lock.release()
    try:
        _forward(records)
    finally:
        _lock.acquire()


def _maybe_flush():
    count = len(_buffer) if COLLAPSE_ALERTS else len(_buffer_list)
    if count == 0:
        return

    age = (time.monotonic() - _oldest_ts) if _oldest_ts is not None else 0.0

    if count >= BATCH_SIZE:
        print(f"[middleware] Count flush: {count} record(s) (threshold={BATCH_SIZE})")
        _flush_locked()
    elif age >= BATCH_TIMEOUT_SEC:
        print(f"[middleware] Timeout flush: {count} record(s) (age={age:.2f}s)")
        _flush_locked()



def _timer_tick():
    global _timer, _shutdown
    if _shutdown:
        return
    with _lock:
        _maybe_flush()
    interval = max(0.1, BATCH_TIMEOUT_SEC / 2)
    _timer = threading.Timer(interval, _timer_tick)
    _timer.daemon = True
    _timer.start()


def _start_timer():
    global _timer
    interval = max(0.1, BATCH_TIMEOUT_SEC / 2)
    _timer = threading.Timer(interval, _timer_tick)
    _timer.daemon = True
    _timer.start()


class MiddlewareHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        global _oldest_ts

        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            data   = json.loads(body)
        except Exception as exc:
            print(f"[middleware] Bad request: {exc}")
            self._respond(400)
            return

        if isinstance(data, dict):
            records = [data]
        elif isinstance(data, list):
            records = data
        else:
            records = [data]

        with _lock:
            for record in records:
                record.pop("date", None)

                if _oldest_ts is None:
                    _oldest_ts = time.monotonic()

                if COLLAPSE_ALERTS:
                    key = _make_alert_key(record)
                    _buffer[key] = record        
                else:
                    _buffer_list.append(record)

            _maybe_flush()

        self._respond(200)

    def _respond(self, code: int):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, fmt, *args):
      
        pass



def run():
    global _shutdown, _timer

    _start_timer()

    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), MiddlewareHandler)
    print(f"[middleware] Listening on {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"[middleware] Forwarding to http://{FORWARD_HOST}:{FORWARD_PORT}{FORWARD_PATH}")
    print(f"[middleware] batch_size={BATCH_SIZE}  timeout={BATCH_TIMEOUT_SEC}s  collapse_alerts={COLLAPSE_ALERTS}")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[middleware] Shutting down...")
        _shutdown = True
        if _timer:
            _timer.cancel()
        with _lock:
            count = len(_buffer) if COLLAPSE_ALERTS else len(_buffer_list)
            if count:
                print(f"[middleware] Flushing {count} remaining record(s) before exit...")
                _flush_locked()
        server.server_close()
        print("[middleware] Done.")


if __name__ == "__main__":
    run()
