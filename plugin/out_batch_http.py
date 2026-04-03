
import json
import re
import time
import threading
import urllib.request
import urllib.error
from collections import OrderedDict


_lock        = threading.Lock()
_buffer      = OrderedDict()  
_buffer_list = []             
_oldest_ts   = None           
_cfg         = {}
_timer       = None
_shutdown    = False




def _as_bool(val) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes")


def _defaults() -> dict:
    return {
        "batch_size":        200,
        "batch_timeout_sec": 2.0,
        "collapse_alerts":   False,
        "host":              "127.0.0.1",
        "port":              8080,
        "path":              "/",
        "retry_limit":       3,
        "retry_delay_sec":   1.0,
    }




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
  
    severity    = str(record.get("level", "")).strip().upper()
    message     = str(record.get("message", ""))
    alert_brief = _derive_alert_brief(message)
    cleaned     = _clean_alert_brief(alert_brief)
    file_       = str(record.get("file", ""))
    line        = str(record.get("line", ""))
    return f"{severity}|{cleaned}|{file_}|{line}"



def _send(records: list) -> bool:
    
    host        = _cfg.get("host", "127.0.0.1")
    port        = int(_cfg.get("port", 8080))
    path        = _cfg.get("path", "/")
    retry_limit = int(_cfg.get("retry_limit", 3))
    retry_delay = float(_cfg.get("retry_delay_sec", 1.0))

    url     = f"http://{host}:{port}{path}"
    payload = json.dumps(records).encode("utf-8")

    for attempt in range(1, retry_limit + 2):   
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
                f"[out_batch_http] SENT {len(records)} record(s) -> "
                f"{url}  HTTP {status}"
            )
            return True

        except urllib.error.URLError as exc:
            print(
                f"[out_batch_http] SEND FAILED attempt {attempt}/{retry_limit + 1} "
                f"-> {url} : {exc}"
            )
            if attempt <= retry_limit:
                time.sleep(retry_delay)

        except Exception as exc:
            print(f"[out_batch_http] Unexpected send error (attempt {attempt}): {exc}")
            if attempt <= retry_limit:
                time.sleep(retry_delay)

    print(
        f"[out_batch_http] GIVING UP after {retry_limit + 1} attempt(s). "
        f"{len(records)} record(s) dropped."
    )
    return False



def _flush_locked():
    """
    Collect buffered records, clear the buffer, then send.
    Must be called while _lock is held.
    Releases _lock around the network call to avoid blocking ingest.
    """
    global _buffer, _buffer_list, _oldest_ts

    collapse = _cfg.get("collapse_alerts", False)
    if collapse:
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
        _send(records)
    finally:
        _lock.acquire()


def _maybe_flush():

    batch_size = int(_cfg.get("batch_size", 200))
    timeout    = float(_cfg.get("batch_timeout_sec", 2.0))
    collapse   = _cfg.get("collapse_alerts", False)

    count = len(_buffer) if collapse else len(_buffer_list)
    if count == 0:
        return

    age = (time.monotonic() - _oldest_ts) if _oldest_ts is not None else 0.0

    if count >= batch_size:
        print(
            f"[out_batch_http] Count flush: {count} record(s) "
            f"(threshold={batch_size})"
        )
        _flush_locked()
    elif age >= timeout:
        print(
            f"[out_batch_http] Timeout flush: {count} record(s) "
            f"(age={age:.2f}s >= {timeout}s)"
        )
        _flush_locked()



def _timer_tick():
    global _timer, _shutdown
    if _shutdown:
        return
    with _lock:
        _maybe_flush()
    # Re-arm at half the timeout interval for responsiveness
    interval = max(0.1, float(_cfg.get("batch_timeout_sec", 2.0)) / 2)
    _timer = threading.Timer(interval, _timer_tick)
    _timer.daemon = True
    _timer.start()


def _start_timer():
    global _timer
    interval = max(0.1, float(_cfg.get("batch_timeout_sec", 2.0)) / 2)
    _timer = threading.Timer(interval, _timer_tick)
    _timer.daemon = True
    _timer.start()



def plugin_init(config: dict):
   
    global _cfg
    merged = _defaults()
    merged.update({k: v for k, v in config.items() if v is not None})

    merged["collapse_alerts"]   = _as_bool(merged["collapse_alerts"])
    merged["batch_size"]        = int(merged["batch_size"])
    merged["batch_timeout_sec"] = float(merged["batch_timeout_sec"])
    merged["port"]              = int(merged["port"])
    merged["retry_limit"]       = int(merged["retry_limit"])
    merged["retry_delay_sec"]   = float(merged["retry_delay_sec"])

    _cfg.update(merged)
    print(f"[out_batch_http] Initialized: {_cfg}")
    _start_timer()
    return 0


def output_cb(tag: str, timestamp: float, record: dict):
  
    global _oldest_ts

    with _lock:
        if _oldest_ts is None:
            _oldest_ts = time.monotonic()

        if _cfg.get("collapse_alerts", False):
            key = _make_alert_key(record)
            _buffer[key] = record          
        else:
            _buffer_list.append(record)

        _maybe_flush()

    return 0


def plugin_exit(config: dict):
   
    global _shutdown, _timer
    _shutdown = True
    if _timer:
        _timer.cancel()

    with _lock:
        collapse = _cfg.get("collapse_alerts", False)
        count    = len(_buffer) if collapse else len(_buffer_list)
        if count:
            print(f"[out_batch_http] Shutdown flush: {count} record(s) remaining")
            _flush_locked()
        else:
            print("[out_batch_http] Shutdown: buffer empty, nothing to flush")
    return 0
