# Design Note

## Parser Design

The parser uses regex to extract structured fields from log lines. It identifies:

* timestamp → `time`
* log level → `level`
* source file → `file`
* line number → `line`
* message → `message`

A second parser handles JSON logs directly.

---

## What Fields Are Extracted

Each log record contains:

* time
* level
* file
* line
* message

---

## Plugin Input

The plugin receives records in MessagePack format from Fluent Bit after parsing.

Each record:

```
[ timestamp, { key-value map } ]
```

---

## Counting Logic

The plugin:

* reads the `level` field
* maps it to a counter
* maintains static/global counters for:

  * INFO
  * ERROR
  * WARNING
  * DEBUG
  * CRITICAL
* increments the corresponding counter
* adds `count` field to the record

---

## Why stdout First?

stdout was used to:

* validate parsing correctness
* debug plugin logic
* confirm count increments

This isolates issues before introducing networking complexity.

---

## Final Data Flow

```
Log file → tail input → parser → filter plugin → HTTP output → Python server
```

---

## Config vs Plugin Responsibility

### Config Handles:

* parsing
* routing
* selecting logs

### Plugin Handles:

* stateful counting
* enrichment logic

---

## Bonus Feature

Stateful counting across multiple inputs and log formats.
