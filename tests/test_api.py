import io
import json
import unittest
from urllib.error import HTTPError, URLError

from notion.api import Client, NotionError
from notion.source import value


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


class ApiTests(unittest.TestCase):
    def test_paginates_past_first_hundred_rows(self):
        requests = []
        def transport(req, timeout):
            body = json.loads(req.data)
            requests.append(body)
            if len(requests) == 1:
                return response({"results": list(range(100)), "has_more": True, "next_cursor": "second"})
            return response({"results": [100, 101], "has_more": False, "next_cursor": None})
        client = Client("fixture-token", transport=transport, sleeper=lambda _: None)
        rows = list(client.pages("/data_sources/source/query"))
        self.assertEqual(len(rows), 102)
        self.assertEqual(requests[1]["start_cursor"], "second")

    def test_incomplete_scan_cannot_be_treated_as_complete(self):
        client = Client("fixture-token", transport=lambda *args, **kwargs: response({
            "results": [], "has_more": False, "request_status": {"type": "incomplete"}}), sleeper=lambda _: None)
        with self.assertRaisesRegex(NotionError, "Incomplete"):
            list(client.pages("/data_sources/source/query"))

    def test_repeated_cursor_is_rejected(self):
        client = Client("fixture-token", transport=lambda *args, **kwargs: response({
            "results": [], "has_more": True, "next_cursor": "same"}), sleeper=lambda _: None)
        with self.assertRaisesRegex(NotionError, "pagination cursor"):
            list(client.pages("/data_sources/source/query"))

    def test_ambiguous_page_create_is_not_retried(self):
        calls = []
        def transport(req, timeout):
            calls.append(req)
            raise URLError("unknown outcome")
        client = Client("fixture-token", transport=transport, sleeper=lambda _: None)
        with self.assertRaisesRegex(NotionError, "Unconfirmed POST"):
            client.request("POST", "/pages", {"properties": {}})
        self.assertEqual(len(calls), 1)

    def test_retry_after_is_respected(self):
        waits, calls = [], []
        def transport(req, timeout):
            calls.append(req)
            if len(calls) == 1:
                raise HTTPError(req.full_url, 429, "rate limited", {"Retry-After": "5.5"}, None)
            return response({"ok": True})
        client = Client("fixture-token", transport=transport, sleeper=waits.append)
        self.assertTrue(client.request("GET", "/users/me")["ok"])
        self.assertIn(5.5, waits)

    def test_relation_properties_are_fully_paginated(self):
        def transport(req, timeout):
            if req.full_url.endswith("/query"):
                return response({"results": [{"id": "page", "properties": {
                    "Related": {"id": "rel", "type": "relation", "has_more": True,
                                "relation": [{"id": "initial"}]}}}], "has_more": False})
            return response({"results": [{"relation": {"id": "one"}}, {"relation": {"id": "two"}}], "has_more": False})
        client = Client("fixture-token", transport=transport, sleeper=lambda _: None)
        rows = client.data("source")
        self.assertEqual(value(rows[0]["properties"]["Related"]), ["one", "two"])

    def test_formula_string_is_a_single_value(self):
        self.assertEqual(value({"type": "formula", "formula": {"type": "string", "string": "same-condition"}}), "same-condition")

    def test_unhydrated_relation_is_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, "paginated"):
            value({"type": "relation", "relation": [{"id": "one"}], "has_more": True})

    def test_error_does_not_expose_token_or_raw_body(self):
        secret = "fixture-private-value"
        def transport(req, timeout):
            raise HTTPError(req.full_url, 401, secret, {}, io.BytesIO(secret.encode()))
        client = Client(secret, transport=transport, sleeper=lambda _: None)
        with self.assertRaises(NotionError) as caught:
            client.request("GET", "/users/me")
        self.assertNotIn(secret, str(caught.exception))

    def test_structured_api_diagnosis_redacts_token(self):
        secret = 'fixture-private-value'
        def transport(req, timeout):
            body = json.dumps({'message':'Invalid value ' + secret}).encode()
            raise HTTPError(req.full_url, 400, 'validation', {}, io.BytesIO(body))
        client = Client(secret, transport=transport, sleeper=lambda _: None)
        with self.assertRaises(NotionError) as caught:
            client.request('PATCH', '/data_sources/example', {})
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn('[redacted]', str(caught.exception))


if __name__ == "__main__":
    unittest.main()
