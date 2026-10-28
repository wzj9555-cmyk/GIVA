"""Verify copied-image georeferencing and transparent Web Mercator tiles."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from rasterio.transform import from_origin
import rasterio

import aerial
from app import raster


class AerialTests(unittest.TestCase):
    def test_prepare_reuse_and_tile_georeference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_dir, cache_root = root / "imagery", root / "cache"
            image_dir.mkdir()
            # Four distinct regions let us detect misplaced or mirrored tiles.
            pixels = np.zeros((512, 512, 3), dtype=np.uint8)
            pixels[:256, :256] = [220, 30, 30]
            pixels[:256, 256:] = [30, 220, 30]
            pixels[256:, :256] = [30, 30, 220]
            pixels[256:, 256:] = [220, 220, 30]
            image = image_dir / "aerial.jpeg"
            Image.fromarray(pixels).save(image, quality=98)
            affine = from_origin(121.600, 31.190, .00001, .00001)
            meta = {"image": image.name, "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                    "width": 512, "height": 512, "bands": 3, "crs": "EPSG:4326",
                    "coordinates": "GCJ-02", "transform": list(affine),
                    "bounds": [121.6, 31.18488, 121.60512, 31.19]}
            (image_dir / "georeference.json").write_text(json.dumps(meta), encoding="utf-8")
            with patch.object(aerial, "IMAGE_DIR", image_dir), patch.object(raster, "RESULT_DIR", cache_root):
                product = aerial.prepare_aerial(cache_root / ".aerial-Air2025")
                with rasterio.open(product) as ds:
                    self.assertEqual(ds.crs.to_string(), "EPSG:4326")
                    self.assertEqual(ds.transform, affine)
                    self.assertEqual((ds.width, ds.height), (512, 512))
                stamp = product.stat().st_mtime_ns
                self.assertEqual(aerial.prepare_aerial(cache_root / ".aerial-Air2025").stat().st_mtime_ns, stamp)
                self.assertTrue(raster.aerial_status()["available"])
                lon, lat, zoom = 121.601, 31.189, 18
                n = 2**zoom
                xf = (lon + 180) / 360 * n
                yf = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
                response = raster.aerial_tile(zoom, int(xf), int(yf))
                import io
                tile = np.asarray(Image.open(io.BytesIO(response.body)))
                x, y = int((xf % 1) * 256), int((yf % 1) * 256)
                self.assertEqual(tile[y, x, 3], 255)
                self.assertGreater(int(tile[y, x, 0]), 150)
                self.assertLess(int(tile[y, x, 1]), 80)
                outside = raster.aerial_tile(8, 0, 0)
                self.assertEqual(np.asarray(Image.open(io.BytesIO(outside.body)))[:, :, 3].max(), 0)


if __name__ == "__main__":
    unittest.main()
