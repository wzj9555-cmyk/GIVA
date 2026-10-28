"""Cache lifecycle regressions using a real, small georeferenced TIF."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

import prepare_raster
from raster_common import (
    CACHE_VERSION, REQUIRED_FILES, VARIANCE_STEM, cache_directory, discover_sources,
    matches_source, read_cache_metadata, source_signature,
)


class RasterCacheLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="GIVA 测试 ")
        cls.root = Path(cls.workspace.name)
        cls.template_input = cls.root / "原始 tif"
        cls.template_input.mkdir()
        cls.template_source = cls.template_input / "测试 Dataset.TIF"
        # More than the former 32 PCA dimensions, with distinct offsets/scales so
        # the regression detects dimensionality reduction and any normalization.
        values = np.random.default_rng(42).normal(size=(48, 32, 40)).astype("float32")
        values *= np.linspace(.2, 4, 48, dtype=np.float32)[:, None, None]
        values += np.linspace(-3, 8, 48, dtype=np.float32)[:, None, None]
        values[:, :2, :] = -9999
        values[:, 4, 3] = np.nan
        with rasterio.open(cls.template_source, "w", driver="GTiff", width=40, height=32,
                           count=48, dtype="float32", nodata=-9999, crs="EPSG:32651",
                           transform=from_origin(350000, 3450000, 2.5, 2.5)) as target:
            target.write(values)
        cls.template_output = cache_directory(cls.template_source, cls.root / "模板cache")
        with contextlib.redirect_stdout(io.StringIO()):
            prepare_raster.prepare(cls.template_source, cls.template_output, input_dir=cls.template_input)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(prefix="case ", dir=self.root)
        self.input_dir = Path(self.case.name) / "tif"
        self.input_dir.mkdir()
        self.source = self.input_dir / self.template_source.name
        shutil.copy2(self.template_source, self.source)
        self.output = cache_directory(self.source, Path(self.case.name) / "cache")
        shutil.copytree(self.template_output, self.output)
        # The fixture source is copied to an independent test root.
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        metadata["source_signature"] = source_signature(self.source, self.input_dir)
        (self.output / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    def tearDown(self):
        self.case.cleanup()

    def prepare(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return prepare_raster.prepare(self.source, self.output, input_dir=self.input_dir, **kwargs)

    def test_complete_products_and_unchanged_reuse(self):
        metadata = read_cache_metadata(self.output)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["valid_count"], 40 * 30 - 1)
        self.assertEqual(set(metadata["products"]), set(REQUIRED_FILES))
        self.assertFalse(Path(metadata["source_signature"]["path"]).is_absolute())
        before = (self.output / "metadata.json").stat().st_mtime_ns
        with patch.object(prepare_raster, "_build_dataset", side_effect=AssertionError("must reuse")):
            self.prepare()
        self.assertEqual((self.output / "metadata.json").stat().st_mtime_ns, before)

    def test_force_really_rebuilds_a_complete_cache(self):
        before = (self.output / "metadata.json").stat().st_mtime_ns
        with patch.object(prepare_raster, "_build_dataset", wraps=prepare_raster._build_dataset) as build:
            self.prepare(force_rebuild=True)
        self.assertEqual(build.call_count, 1)
        self.assertNotEqual((self.output / "metadata.json").stat().st_mtime_ns, before)
        self.assertIsNotNone(read_cache_metadata(self.output))

    def test_clustering_fits_and_predicts_every_raw_original_dimension(self):
        real_constructor = prepare_raster.MiniBatchKMeans
        fitted, predicted = [], []

        def record_model(**kwargs):
            model = real_constructor(**kwargs)
            real_fit, real_predict = model.partial_fit, model.predict

            def record_fit(values, *args, **kwargs):
                fitted.append(values.copy())
                return real_fit(values, *args, **kwargs)

            def record_predict(values, *args, **kwargs):
                predicted.append(values.copy())
                return real_predict(values, *args, **kwargs)

            model.partial_fit = record_fit
            model.predict = record_predict
            return model

        with patch.object(prepare_raster, "MiniBatchKMeans", side_effect=record_model) as constructor:
            metadata = self.prepare(force_rebuild=True)
        constructor.assert_called_once_with(n_clusters=8, random_state=42, batch_size=8192, n_init=3)
        pixel_ids = np.fromfile(self.output / "pixel_ids.u32", dtype="<u4")
        with rasterio.open(self.source) as image:
            original = image.read().reshape(image.count, -1).T[pixel_ids]
        self.assertGreater(original.shape[1], 32)
        self.assertEqual(len(fitted), 3)
        self.assertEqual(len(predicted), 1)
        for values in fitted + predicted:
            self.assertEqual(values.dtype, np.dtype("float32"))
            np.testing.assert_array_equal(values, original)
        self.assertEqual(metadata["clustering_components"], original.shape[1])
        self.assertEqual(metadata["clustering_space"], "original")
        self.assertEqual(metadata["clustering_metric"], "euclidean")
        self.assertEqual(metadata["clustering_normalization"], "none")
        self.assertIs(metadata["clustering_whiten"], False)
        self.assertEqual(metadata["parameters"]["pca_components"], 3)
        self.assertEqual(np.fromfile(self.output / "positions.f32", dtype="<f4").size, len(original) * 3)
        spectrum = json.loads((self.output / f"{VARIANCE_STEM}.json").read_text(encoding="utf-8"))
        self.assertEqual(len(spectrum["eigenvalue"]), original.shape[1])

    def test_old_or_misdeclared_clustering_cache_is_invalid(self):
        path = self.output / "metadata.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        invalid_fields = {
            "version": 3, "clustering_components": 32, "clustering_space": "pca",
            "clustering_normalization": "l2", "clustering_whiten": True,
        }
        for key, value in invalid_fields.items():
            with self.subTest(field=key):
                metadata = dict(original, **{key: value})
                path.write_text(json.dumps(metadata), encoding="utf-8")
                self.assertIsNone(read_cache_metadata(self.output))
        path.write_text(json.dumps(original), encoding="utf-8")

    def test_missing_plot_is_regenerated(self):
        (self.output / f"{VARIANCE_STEM}.png").unlink()
        self.assertIsNone(read_cache_metadata(self.output))
        self.prepare()
        self.assertIsNotNone(read_cache_metadata(self.output))

    def test_binary_size_mismatch_is_regenerated(self):
        with (self.output / "positions.f32").open("ab") as handle:
            handle.write(b"extra")
        self.assertIsNone(read_cache_metadata(self.output))
        self.prepare()
        self.assertIsNotNone(read_cache_metadata(self.output))

    def test_same_name_size_and_mtime_with_different_content_rebuilds(self):
        old = read_cache_metadata(self.output)
        info = self.source.stat()
        with rasterio.open(self.source, "r+") as image:
            block = image.read(1)
            block[8, 8] += 3
            image.write(block, 1)
        os.utime(self.source, ns=(info.st_atime_ns, info.st_mtime_ns))
        new_signature = source_signature(self.source, self.input_dir)
        self.assertEqual(new_signature["size"], old["source_signature"]["size"])
        self.assertEqual(new_signature["mtime_ns"], old["source_signature"]["mtime_ns"])
        self.assertNotEqual(new_signature["sha256"], old["source_signature"]["sha256"])
        self.assertFalse(matches_source(old, new_signature))
        self.prepare()
        self.assertEqual(read_cache_metadata(self.output)["source_signature"]["sha256"], new_signature["sha256"])

    def test_failed_rebuild_retains_complete_previous_cache(self):
        before = (self.output / "metadata.json").read_bytes()
        with patch.object(prepare_raster, "write_explained_variance", side_effect=RuntimeError("test render failure")):
            with self.assertRaisesRegex(RuntimeError, "render failure"):
                self.prepare(force_rebuild=True)
        self.assertEqual((self.output / "metadata.json").read_bytes(), before)
        self.assertIsNotNone(read_cache_metadata(self.output))
        self.assertFalse(list(self.output.parent.glob(".*.building-*")))

    def test_failed_new_build_has_no_manifest(self):
        shutil.rmtree(self.output)
        with patch.object(prepare_raster, "render_overview", side_effect=RuntimeError("test overview failure")):
            with self.assertRaisesRegex(RuntimeError, "overview failure"):
                self.prepare()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob(".*.building-*")))

    def test_source_changes_during_build_are_not_published(self):
        before = (self.output / "metadata.json").read_bytes()
        real_build = prepare_raster._build_dataset

        def change_during_build(*args):
            result = real_build(*args)
            info = self.source.stat()
            os.utime(self.source, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
            return result

        with patch.object(prepare_raster, "_build_dataset", side_effect=change_during_build):
            with self.assertRaisesRegex(ValueError, "处理期间"):
                self.prepare(force_rebuild=True)
        self.assertEqual((self.output / "metadata.json").read_bytes(), before)

    def test_case_insensitive_extensions_and_normalized_id_collision(self):
        self.assertEqual(discover_sources(self.input_dir), [self.source.resolve()])
        shutil.copy2(self.source, self.input_dir / "测试 dataset.tiff")
        with self.assertRaisesRegex(ValueError, "名称冲突"):
            discover_sources(self.input_dir)

    def test_nan_nodata_is_json_null_with_unchanged_valid_mask(self):
        with rasterio.open(self.source, "r+") as image:
            image.nodata = float("nan")
            values = image.read()
            values[:, :2, :] = np.nan
            image.write(values)
        self.prepare()
        metadata = read_cache_metadata(self.output)
        self.assertIsNone(metadata["nodata"])
        self.assertEqual(metadata["valid_count"], 40 * 30 - 1)
        content = (self.output / "metadata.json").read_text(encoding="utf-8")
        self.assertIn('"nodata": null', content)
        self.assertNotIn("NaN", content)
        self.assertNotIn("Infinity", content)
        self.assertEqual(json.loads(content)["version"], CACHE_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
