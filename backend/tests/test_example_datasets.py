"""Verify bundled file validation and exact-name matching."""
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from app.dataset_rules import DatasetDefinition
from app.example_datasets import EXAMPLES, has_exact_name, read_example


class ExampleDatasetTests(unittest.IsolatedAsyncioTestCase):
    async def test_name_match_is_exact(self):
        session = MagicMock()
        session.scalars = AsyncMock()
        result = MagicMock()
        session.scalars.return_value = result
        result.all.return_value = ["Example-Iris"]
        self.assertFalse(await has_exact_name(session, "example-iris"))
        result.all.return_value = ["example-iris"]
        self.assertTrue(await has_exact_name(session, "example-iris"))

    async def test_bundled_csv_matches_schema(self):
        for name, filename, fields in EXAMPLES:
            payloads = read_example(Path("data") / filename, DatasetDefinition(fields=list(fields)),
                                    name == "example-vega-stocks")
            self.assertEqual(len(payloads), 150 if name == "example-iris" else 560)
            if name == "example-iris":
                self.assertEqual(payloads[0]["Id"], {"kind": "integer", "value": "1"})
                self.assertTrue(fields[0].unique)
            else:
                self.assertEqual(payloads[0]["date"]["kind"], "datetime")
                self.assertEqual(payloads[0]["date"]["value"], "2000-01-01T00:00:00+00:00")
