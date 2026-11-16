"""Small Civil NX API client and offline parameter planner (Python 3.9+)."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class MidasAPIError(RuntimeError):
    """Transport, application-level, or unrecognized command failure."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A custom authentication header must not follow an unexpected redirect.
        return None


def _redact(value: Any, *private_values: str) -> str:
    text = str(value)
    values = set(private_values)
    values.add(os.environ.get("MIDAS_API_KEY", "").strip())
    values.add(os.environ.get("MIDAS_BASE_URL", "").strip().rstrip("/"))
    variants = set()
    for item in values:
        if item:
            # JSON may escape quotes and backslashes in an echoed credential.
            variants.update((item, json.dumps(item, ensure_ascii=False)[1:-1]))
    for item in sorted(variants, key=len, reverse=True):
        text = text.replace(item, "[REDACTED]")
    return text


def _failure(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            if name == "error" and item:
                return "error: " + str(item)
            if name == "success" and item is False:
                return "success=false"
            if name in ("status", "result") and isinstance(item, str):
                if item.lower() in ("error", "failed", "failure", "denied", "aborted"):
                    return name + "=" + item
            if name == "message" and isinstance(item, str) and re.search(
                    r"\b(not allowed|not permitted|failed|failure|cannot|aborted|cancelled|canceled|license error|no license)\b",
                    item, re.IGNORECASE):
                return "message: " + item
            if isinstance(item, (dict, list)):
                found = _failure(item)
                if found:
                    return found
    elif isinstance(value, list):
        for item in value:
            found = _failure(item)
            if found:
                return found
    return None


class MidasClient:
    """Only GET is retried; no network activity occurs during construction."""

    def __init__(self, *, timeout: float = 30, get_retries: int = 1):
        self.base = os.environ.get("MIDAS_BASE_URL", "").strip().rstrip("/")
        self._key = os.environ.get("MIDAS_API_KEY", "").strip()
        if not self.base or not self._key:
            raise MidasAPIError("Set MIDAS_BASE_URL and MIDAS_API_KEY in the environment.")
        if any(ord(char) < 33 or ord(char) > 126 for char in self._key):
            raise MidasAPIError("API key contains unsupported whitespace, control or non-ASCII characters.")
        parsed = urlsplit(self.base)
        loopback = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query
                or parsed.fragment or (parsed.scheme != "https"
                                      and not (parsed.scheme == "http" and loopback))):
            raise MidasAPIError("Use the software Base URL, HTTPS except on loopback, without credentials or query.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if isinstance(get_retries, bool) or not isinstance(get_retries, int) or get_retries < 0:
            raise ValueError("get_retries must be a non-negative integer")
        self.timeout, self.get_retries = timeout, get_retries
        self._opener = build_opener(_NoRedirect())
        self.calls: list[dict[str, Any]] = []

    def _safe(self, text: Any) -> str:
        return _redact(text, self._key, self.base)[:600]

    def request(self, method: str, endpoint: str, body: Any = None,
                *, timeout: Optional[float] = None) -> Any:
        method = method.upper()
        location = urlsplit(endpoint)
        if (method not in ("GET", "POST", "PUT", "DELETE") or not endpoint.startswith("/")
                or endpoint.startswith("//") or location.scheme or location.netloc
                or location.query or location.fragment):
            raise ValueError("Use GET/POST/PUT/DELETE and a relative API endpoint without query.")
        if method == "GET" and body is not None:
            raise ValueError("GET must not contain a JSON body")
        deadline = self.timeout if timeout is None else timeout
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError("timeout must be positive and finite")
        data = None if body is None else json.dumps(
            body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        retries = self.get_retries if method == "GET" else 0
        for attempt in range(retries + 1):
            started, status, ok = time.monotonic(), None, False
            request = Request(self.base + endpoint, data=data, method=method, headers={
                "MAPI-Key": self._key, "Content-Type": "application/json"})
            try:
                with self._opener.open(request, timeout=deadline) as response:
                    status = response.status
                    raw = response.read()
                if not 200 <= status < 300:
                    raise MidasAPIError("Unexpected HTTP status " + str(status))
                try:
                    result = json.loads(raw.decode("utf-8-sig"))
                except (ValueError, UnicodeError):
                    raise MidasAPIError("Response is not UTF-8 JSON; inspect the connection.") from None
                failure = _failure(result)
                if failure:
                    raise MidasAPIError(self._safe(failure))
                ok = True
                return result
            except HTTPError as exc:
                status = exc.code
                exc.close()
                if method != "GET" or attempt == retries or status not in (429, 502, 503, 504):
                    suffix = " Inspect actual state before retrying." if method != "GET" else ""
                    raise MidasAPIError("HTTP " + str(status) + " at " + endpoint + suffix) from None
            except (URLError, TimeoutError, OSError):
                if method != "GET" or attempt == retries:
                    suffix = " Outcome unknown; inspect model and results before retrying." if method != "GET" else ""
                    raise MidasAPIError("Connection/timeout failure at " + endpoint + suffix) from None
            finally:
                self.calls.append({"method": method, "endpoint": endpoint, "attempt": attempt + 1,
                                   "status": status, "ok": ok,
                                   "seconds": round(time.monotonic() - started, 3)})
            time.sleep(min(attempt + 1, 2))
        raise MidasAPIError("GET retry budget exhausted")


def require_analysis_complete(response: Any) -> None:
    """Check the recognized completion message; this is not a freshness check."""
    if _failure(response):
        raise MidasAPIError("Analysis response reports a failure; inspect a sanitized archive.")
    if (not isinstance(response, dict)
            or str(response.get("message", "")).strip().casefold()
            != "midas civil nx command complete"):
        raise MidasAPIError("Unrecognized analysis completion response; do not mark the run complete.")


def table_rows(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Pass the actual HEAD/DATA block, including a selected modal subtable."""
    head, data = block.get("HEAD"), block.get("DATA")
    if (not isinstance(head, list) or not head or any(not isinstance(x, str) for x in head)
            or len(set(head)) != len(head)):
        raise ValueError("Missing or ambiguous HEAD")
    if not isinstance(data, list) or not data:
        raise ValueError("Missing DATA; check selected nodes, elements and load cases")
    if any(not isinstance(row, list) or len(row) != len(head) for row in data):
        raise ValueError("DATA row width differs from HEAD")
    return [dict(zip(head, row)) for row in data]


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Parameter values and target fields must be numeric, excluding bool")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError("Number is outside finite float range") from None
    if not math.isfinite(result):
        raise ValueError("Non-finite number")
    return result


def _field_parent(record: Any, path: list[Any]) -> tuple[Any, Any]:
    if not isinstance(path, list) or not path:
        raise ValueError("Target path must be a non-empty list")
    current = record
    for position, key in enumerate(path):
        if isinstance(current, dict) and isinstance(key, str) and key in current:
            pass
        elif (isinstance(current, list) and isinstance(key, int) and not isinstance(key, bool)
              and 0 <= key < len(current)):
            pass
        else:
            raise ValueError("Target path does not exist in the frozen baseline")
        if position == len(path) - 1:
            return current, key
        current = current[key]
    raise ValueError("Invalid path")


def build_parameter_updates(baseline: dict[str, Any], spec: dict[str, Any],
                            values: dict[str, Any]) -> dict[str, Any]:
    """Build Assign blocks from an immutable baseline, never from previous updates."""
    if (not isinstance(baseline, dict) or not isinstance(spec, dict)
            or not isinstance(values, dict) or not spec or set(values) != set(spec)):
        raise ValueError("Provide a baseline and exactly the declared parameter names")
    updates: dict[str, Any] = {}
    used = set()
    for name, definition in spec.items():
        if not isinstance(definition, dict):
            raise ValueError("Each parameter definition must be an object")
        value = _number(values[name])
        bounds, targets = definition.get("bounds"), definition.get("targets")
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError("Each parameter needs two numeric bounds")
        lower, upper = map(_number, bounds)
        if lower > upper or not lower <= value <= upper:
            raise ValueError("Parameter outside its declared bounds: " + name)
        if not isinstance(targets, list) or not targets:
            raise ValueError("Each parameter needs at least one target")
        for target in targets:
            if not isinstance(target, dict):
                raise ValueError("Each target must be an object")
            table, key = target["table"], target["id"]
            path, mode = target["path"], target["mode"]
            if not isinstance(table, str) or not isinstance(key, str) or mode not in ("scale", "set"):
                raise ValueError("Targets require string table/id and scale or set mode")
            if table not in baseline or not isinstance(baseline[table], dict) or key not in baseline[table]:
                raise ValueError("Target table/ID absent from baseline")
            source = baseline[table][key]
            parent, leaf = _field_parent(source, path)
            original = _number(parent[leaf])
            identity = table, key, tuple(path)
            if identity in used:
                raise ValueError("Multiple parameters target the same field")
            used.add(identity)
            record = updates.setdefault(table, {"Assign": {}})["Assign"].setdefault(key, deepcopy(source))
            destination, leaf = _field_parent(record, path)
            destination[leaf] = _number(original * value if mode == "scale" else value)
    return {"baseline_sha256": canonical_hash(baseline), "spec_sha256": canonical_hash(spec),
            "parameters": deepcopy(values), "updates": updates}


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            safe_key = _redact(key) if isinstance(key, str) else key
            if safe_key in result:
                raise ValueError("Redaction would create duplicate JSON keys")
            result[safe_key] = _redact_json(item)
        return result
    return value


def _output(value: Any, path: Optional[str]) -> None:
    text = json.dumps(_redact_json(value), ensure_ascii=False, indent=2, allow_nan=False)
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    else:
        print(text)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe", help="Read-only GET /db/UNIT")
    probe.add_argument("--output")
    get = commands.add_parser("get", help="Read one database table; no model mutation")
    get.add_argument("table")
    get.add_argument("--output")
    plan = commands.add_parser("plan", help="Offline parameter updates; no credentials/network needed")
    for name in ("baseline", "spec", "values", "output"):
        plan.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            value = build_parameter_updates(_read_json(args.baseline), _read_json(args.spec),
                                            _read_json(args.values))
        else:
            table = "UNIT" if args.command == "probe" else args.table
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", table):
                raise ValueError("Use a database table name, not a path")
            value = MidasClient().request("GET", "/db/" + table.upper())
        _output(value, args.output)
    except (MidasAPIError, ValueError, KeyError, OSError) as exc:
        parser.exit(2, _redact(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
