import unittest
from collections import OrderedDict
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app.rate_limit import limit
from app.records import parse_import
from app.schemas import RecordInput


class ValidationTests(unittest.TestCase):
    def test_csv_header_and_invalid_row(self):
        with self.assertRaises(HTTPException) as error:
            parse_import(b"title,value,category_id,timestamp\nx,invalid,1,2026-10-28T00:00:00Z", "data.csv")
        self.assertEqual(error.exception.detail["rows"][0]["row"], 1)
        for content in [b"{}", b"[NaN]", b"[]", b"\xff"]:
            with self.assertRaises(HTTPException):
                parse_import(content, "data.json")

    def test_timezone_and_precision(self):
        result = RecordInput(title=" test ", value="1.234567", category_id=1,
                             timestamp="2026-10-28T10:00:00+08:00")
        self.assertEqual(result.title, "test")
        self.assertEqual(result.timestamp.hour, 2)
        self.assertIsNone(result.timestamp.tzinfo)

    def test_rate_limit_window_and_ip_isolation(self):
        one = Request({"type": "http", "client": ("127.0.0.1", 1)})
        two = Request({"type": "http", "client": ("127.0.0.2", 1)})
        with patch("app.rate_limit.windows", OrderedDict()), patch("app.rate_limit.monotonic", return_value=0):
            limit(one, "register", 1)
            with self.assertRaises(HTTPException) as error:
                limit(one, "register", 1)
            self.assertEqual(error.exception.status_code, 429)
            limit(two, "register", 1)
            with patch("app.rate_limit.monotonic", return_value=61):
                limit(one, "register", 1)


class RequestLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_request_is_rejected_before_parser(self):
        from unittest.mock import AsyncMock
        from app.request_limits import RequestBodyLimit
        downstream = AsyncMock()
        receive = AsyncMock(side_effect=[
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False}])
        send = AsyncMock()
        await RequestBodyLimit(downstream, maximum=5)({"type": "http", "headers": []}, receive, send)
        downstream.assert_not_called()
        self.assertEqual(send.call_args_list[0].args[0]["status"], 413)
