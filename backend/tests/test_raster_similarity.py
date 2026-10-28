"""Exercise similarity query limits and ordering through the actual ASGI API."""
import asyncio
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import FastAPI
import numpy as np
from rasterio.transform import from_origin

from app import raster
from verify_raster import independent_cosine_scores


class RasterSimilarityApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = FastAPI()
        cls.app.include_router(raster.router)
        # Real 512-dimensional data, with enough candidates to distinguish
        # Top 200 and Top 500, plus an undefined zero-vector similarity.
        cls.dataset = raster.RasterDataset.__new__(raster.RasterDataset)
        cls.dataset.count = 641
        cls.dataset.vectors = np.random.default_rng(827).normal(size=(641, 512)).astype(np.float32)
        cls.dataset.vectors[4] = 0
        cls.dataset.norms = np.linalg.norm(cls.dataset.vectors, axis=1)
        cls.dataset.pixel_ids = np.arange(641, dtype=np.uint32)
        cls.dataset.labels = np.arange(641, dtype=np.uint16) % 8
        cls.dataset.affine = from_origin(121, 32, .0001, .0001)
        cls.dataset.meta = {"width": 641, "crs": "EPSG:4326", "bands": 512}
        cls.reference = independent_cosine_scores(cls.dataset.vectors, 0)

    def request(self, **params):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": "/raster/similar",
            "raw_path": b"/raster/similar", "root_path": "", "headers": [],
            "query_string": urlencode(params).encode(),
            "server": ("test", 80), "client": ("127.0.0.1", 1),
        }
        asyncio.run(self.app(scope, receive, send))
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return status, json.loads(body)

    def test_top_200_and_500_return_exact_sorted_original_vector_results(self):
        with patch.object(raster, "get_raster", return_value=self.dataset):
            for k in (200, 500):
                with self.subTest(top=k):
                    status, result = self.request(index=0, k=k, dataset_id="fixture")
                    self.assertEqual(status, 200)
                    self.assertEqual(result["query"]["index"], 0)
                    self.assertEqual(result["searched_count"], 641)
                    self.assertEqual(result["method"], "exact_cosine_512d")
                    items = result["items"]
                    self.assertEqual(len(items), k)
                    returned = [item["index"] for item in items]
                    expected = np.argsort(self.reference)[-k:][::-1]
                    np.testing.assert_array_equal(returned, expected)
                    self.assertNotIn(0, returned)
                    self.assertNotIn(4, returned)
                    np.testing.assert_allclose([item["score"] for item in items], self.reference[expected], atol=2e-6)
                    self.assertTrue(all(a["score"] >= b["score"] for a, b in zip(items, items[1:])))

    def test_out_of_range_query_limits_are_rejected_before_loading_data(self):
        with patch.object(raster, "get_raster") as load:
            for k in (0, 501):
                with self.subTest(top=k):
                    status, result = self.request(index=0, k=k)
                    self.assertEqual(status, 422)
                    self.assertEqual(result["detail"][0]["loc"], ["query", "k"])
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
