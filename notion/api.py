"""Official HTTPS API only; credentials stay in the injected process environment."""
from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VERSION = "2026-03-11"


class NotionError(RuntimeError):
    pass


class Client:
    def __init__(self, token=None, transport=urlopen, sleeper=time.sleep):
        self._token = token or os.environ.get("NOTION_TOKEN")
        if not self._token:
            raise NotionError("NOTION_TOKEN is missing. Add it securely in environment settings.")
        self.transport = transport
        self.sleeper = sleeper

    def request(self, method, path, payload=None, query=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Use an API-relative path")
        url = "https://api.notion.com/v1" + path
        if query:
            url += "?" + urlencode(query)
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self._token,
            "Notion-Version": VERSION, "Content-Type": "application/json",
            "User-Agent": "Notion-Fitness-Tracker/1.0",
        })
        # A full snapshot is small, so use a conservative <= 2 req/sec rate.
        retry_safe = method in {"GET", "PATCH"} or path == "/search" or path.endswith("/query")
        for attempt in range(5):
            self.sleeper(0.5)
            try:
                with self.transport(request, timeout=30) as response:
                    return json.loads(response.read())
            except HTTPError as error:
                status = error.code
                if status == 429 and attempt < 4:
                    delay = float(error.headers.get("Retry-After", "2"))
                    if not 0 <= delay <= 60:
                        raise NotionError("Rate limited; retry later at the server's Retry-After interval") from None
                    self.sleeper(delay)
                    continue
                if status in {500, 502, 503, 504} and retry_safe and attempt < 4:
                    self.sleeper(2 ** attempt)
                    continue
                # Include only the API's structured diagnosis, with credentials redacted.
                detail = "check access and schema"
                try:
                    diagnosis = json.loads(error.read())
                    message = diagnosis.get("message", "")
                    if isinstance(message, str):
                        detail = message.replace(self._token, "[redacted]")[:1200]
                except (ValueError, OSError):
                    pass
                raise NotionError(f"Notion HTTP {status} at {method} {path}; {detail}") from None
            except (URLError, TimeoutError, OSError):
                if retry_safe and attempt < 4:
                    self.sleeper(2 ** attempt)
                    continue
                raise NotionError(f"Unconfirmed {method} {path}; inspect remote state before retrying a create") from None
        raise NotionError("Notion request did not complete")

    def pages(self, path, payload=None, query=None, method="POST"):
        cursor = None
        seen = set()
        while True:
            body = dict(payload or {})
            params = dict(query or {})
            target = params if method == "GET" else body
            target["page_size"] = 100
            if cursor:
                target["start_cursor"] = cursor
            result = self.request(method, path, body if method != "GET" else None, params)
            if result.get("request_status", {}).get("type") == "incomplete":
                raise NotionError("Incomplete API query; do not replace a complete projection")
            yield from result["results"]
            if not result.get("has_more"):
                break
            cursor = result.get("next_cursor")
            if not cursor or cursor in seen:
                raise NotionError("Invalid or repeated pagination cursor")
            seen.add(cursor)

    def data(self, data_source_id):
        records = list(self.pages(f"/data_sources/{data_source_id}/query"))
        for record in records:
            for prop in record.get("properties", {}).values():
                if prop.get("type") == "relation" and prop.get("has_more"):
                    values = list(self.pages(
                        f"/pages/{record['id']}/properties/{prop['id']}", method="GET"))
                    prop["relation"] = [item["relation"] for item in values]
                    prop["has_more"] = False
        return records

    def search(self, title):
        return list(self.pages("/search", {"query": title}))
