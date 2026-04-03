# Fluent Bit Log Processing Pipeline with Batching Middleware

## Overview

This project implements a complete log processing pipeline using Fluent Bit. It demonstrates parsing, filtering, enrichment, forwarding, and server-side load reduction via batching and alert collapsing.

The pipeline:

```
Log File → Parser → Custom Filter Plugin → HTTP Output → Middleware → HTTP Server
```

The middleware sits between Fluent Bit and the server. It buffers incoming records, collapses repeated same-key alerts into a single latest record, and forwards one batched JSON array per flush window — reducing both HTTP request count and payload object count on the server.

---

## Features

- Parses structured and JSON logs
- Supports multiple log formats
- Custom C filter plugin for log level counting
- Batches many small HTTP requests into one larger request
- Collapses repeated same-key alerts using a composite key (latest-state wins)
- Sends enriched, reduced logs to a local HTTP server
- Maintains running count per log level

---

## Phases Implemented

- **Phase 1:** Basic pipeline with parsing and stdout
- **Phase 2:** Multiple parsers
- **Phase 3:** Config-level filtering
- **Phase 4:** Custom filter plugin
- **Phase 5:** Validation using stdout
- **Phase 6:** Local HTTP server
- **Phase 7:** HTTP output integration
- **Phase 8:** Bonus feature (global/stateful counting)
- **Phase 9:** Middleware batching — many Fluent Bit HTTP calls become one
- **Phase 10:** Alert collapsing — same-key alerts reduced to latest record before forwarding

---

## Architecture

```
logs.txt  ──┐
            ├──► Fluent Bit (tail + parser) ──► HTTP OUT (port 9000)
json_logs.txt ──┘                                        │
                                                         ▼
                                              middleware.py (port 9000)
                                              • buffers records
                                              • collapses same-key alerts
                                              • flushes as one JSON array
                                                         │
                                                         ▼
                                              server.py (port 8080)
                                              • receives batched payload
                                              • prints records
```

---

## Components

### 1. Parser (`parsers.conf`)
Extracts structured fields from raw logs using regex and JSON parsers.
Fields extracted: `level`, `file`, `line`, `message`, `time`

### 2. Filter Plugin (`my_plugin/filter_count.c`)
Custom C plugin. Adds a `count` field to each record based on log level frequency.
Counts are maintained as global state across the pipeline run.

### 3. Middleware (`middleware.py`)
Python HTTP server that sits between Fluent Bit and the real server.

**Batching:** Accumulates records in memory. Flushes when either:
- Buffered record count reaches `BATCH_SIZE` (default: 200)
- Oldest buffered record is older than `BATCH_TIMEOUT_SEC` (default: 2s)

**Alert collapsing:** When `COLLAPSE_ALERTS = True`, records are keyed by:
```
severity | cleaned_alert_brief | file | line
```
If two records share the same composite key, only the latest is kept. This mirrors the Python server's own deduplication logic.

**Alert brief normalisation** (same rules as server logic):
- Text before `;;;` in message, or full message if no `;;;`
- Hex addresses removed (`0x7f9a...`)
- Quoted 24-char object IDs removed
- Digit runs removed
- Suffix after `...check the file:` removed

**Failure handling:** Retries up to `RETRY_LIMIT` times with `RETRY_DELAY_SEC` delay. Logs every failure. Prints `GIVING UP` with record count if all retries exhausted. Flushes remaining buffer on shutdown.

### 4. Output (`fluent-bit.conf`)
Built-in Fluent Bit HTTP output. Points at middleware on port 9000.

### 5. Server (`server.py`)
Local Python HTTP server. Receives the final batched payload and prints it.
Runs at `http://0.0.0.0:8080`.

---

## Configuration

Edit the `CONFIG` block at the top of `middleware.py`:

| Setting | Default | Description |
|---|---|---|
| `LISTEN_PORT` | `9000` | Port middleware listens on (Fluent Bit points here) |
| `FORWARD_HOST` | `127.0.0.1` | Host of the real server |
| `FORWARD_PORT` | `8080` | Port of the real server |
| `FORWARD_PATH` | `/` | Path on the real server |
| `BATCH_SIZE` | `200` | Flush after this many records |
| `BATCH_TIMEOUT_SEC` | `2.0` | Flush after this many seconds |
| `COLLAPSE_ALERTS` | `True` | Enable same-key alert collapsing |
| `RETRY_LIMIT` | `3` | Retries on forward failure |
| `RETRY_DELAY_SEC` | `1.0` | Seconds between retries |

---

## Setup Instructions

### 1. Build Fluent Bit with Plugin

```bash
cd fluent-bit/build
cmake ..
make
```

### 2. Run HTTP Server (Window 1)

```bash
python server.py
```

Server runs at `http://0.0.0.0:8080`

### 3. Run Middleware (Window 2)

```bash
python middleware.py
```

Middleware listens at `http://0.0.0.0:9000` and forwards to server on port 8080.

### 4. Run Fluent Bit (Window 3)

```bash
cd C:\path\to\fluent-bit-4.2.3-win64
bin\fluent-bit.exe -c fluent-bit.conf
```

---

## Sample Logs

**logs.txt**
```
2026-03-21 10:15:01,123 : INFO : [engine.cpp : 42] : Order received
2026-03-21 10:15:02,456 : ERROR : [risk.cpp : 88] : Position limit exceeded
```

**json_logs.txt**
```json
{"time":"2026-03-21","level":"ERROR","file":"test.cpp","line":10,"message":"Failure A"}
```

---

## How to Verify

### Verify batching is working
Watch the server terminal. Instead of one `POST` per log line, you should see one `POST` every ~2 seconds containing all records buffered in that window:

```
===== RECEIVED LOG =====
[
  {"time": "...", "level": "ERROR", "file": "test.cpp", ...},
  {"time": "...", "level": "INFO",  "file": "main.cpp", ...},
  ...
]
127.0.0.1 - - [03/Apr/2026 13:51:52] "POST / HTTP/1.1" 200 -
```

One `POST` line = one batched request = batching is working.

### Verify alert collapsing is working
Add two lines with the same `level`, `file`, `line` and similar `message` (digits only differ) to `json_logs.txt`:

```json
{"time":"2026-03-21","level":"ERROR","file":"test.cpp","line":10,"message":"Failure count 1"}
{"time":"2026-03-21","level":"ERROR","file":"test.cpp","line":10,"message":"Failure count 2"}
```

Only `Failure count 2` will appear in the next batch — the first is collapsed because both normalise to the same composite key.

### Verify field preservation
- `level`, `file`, `line`, `message`, `time` fields are all present and unchanged
- No `date` field (stripped by middleware)
- No wrapper object — payload is a bare JSON array

---

## Running Tests

```bash
cd my_plugin
python test_out_batch_http.py
```

All 13 tests should pass:

```
test_check_the_file_stripped          ok
test_different_levels_different_keys  ok
test_different_lines_different_keys   ok
test_digits_stripped                  ok
test_hex_stripped                     ok
test_no_semicolons                    ok
test_semicolons_split                 ok
test_field_preservation               ok
test_alert_collapsing                 ok
test_composite_key_building           ok
test_count_flush                      ok
test_timeout_flush                    ok
test_failure_is_logged_and_retried    ok

Ran 13 tests in 10.063s  OK
```

---

## File Structure

```
fluent-bit-level-counter/
├── my_plugin/
│   ├── filter_count.c          # Custom C filter — adds count field per log level
│   ├── out_batch_http.py       # Python output plugin (for builds with Python support)
│   └── test_out_batch_http.py  # Test suite — 13 tests covering all brief test cases
├── fluent-bit.conf             # Fluent Bit config — points HTTP output at middleware
├── parsers.conf                # Log parsers — regex + JSON
├── middleware.py               # Batching + alert collapsing middleware
├── server.py                   # Local test HTTP server
├── logs.txt                    # Sample structured logs
└── json_logs.txt               # Sample JSON logs
```

---

## Technologies Used

- Fluent Bit 4.2.3 (Windows)
- Custom C Plugin (filter)
- Python 3 (middleware + server)
- Standard library only — no external dependencies

## Screenshots

### Fluent Bit Running
![Fluent Bit](screenshots/fluent_bit_running.jpeg)

---

### Stdout Validation 
![Stdout Output](screenshots/filter_working.jpeg)

---

### HTTP Server Receiving Logs
![HTTP Output](screenshots/http_server_output.jpeg)

---

### Server
![Server](screenshots/http_server_running.png)

## Global Variable
![Global](screenshots/global.jpeg)

## Global Variable status in server
![Global Variable](screenshots/global_variable_added.jpeg)

