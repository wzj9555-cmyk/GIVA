"""Verify every prepared raster offline; opt in to live API checks explicitly.

Run from the Conda ``macos`` environment::

    python backend/verify_raster.py
    python backend/verify_raster.py --dataset-id joint_2p5m_512d --port 8000

This command only reads sources and caches. It never rebuilds or repairs them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import unittest
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import calculate_default_transform, reproject, Resampling, transform
from threadpoolctl import threadpool_limits

from app.raster import RasterDataset
from raster_common import DEFAULT_CACHE_DIR, read_cache_metadata, resolve_source_path


DOWNLOAD_FILES = frozenset({
    "positions.f32", "colors.u8", "labels.u8", "pixel_ids.u32",
    "clusters.png", "pca.png", "clusters.tif", "pca_rgb.tif",
    "全量栅格可视化总览.png", "PCA累计解释方差曲线.png",
    "PCA累计解释方差曲线.csv", "PCA累计解释方差曲线.json",
})


def select_directories(cache_dir: Path, dataset_id: str | None = None) -> list[Path]:
    """Select direct child datasets, failing visibly on incomplete caches."""
    if not cache_dir.is_dir():
        raise ValueError(f"缓存目录不存在：{cache_dir}；请先运行 start.command")
    directories = sorted(path for path in cache_dir.iterdir() if path.is_dir() and not path.name.startswith("."))
    if dataset_id is not None:
        directories = [path for path in directories if path.name == dataset_id]
        if not directories:
            raise ValueError(f"缓存数据集不存在：{dataset_id}")
    if not directories:
        raise ValueError(f"没有待验证的数据集：{cache_dir}；请先运行 start.command")
    for directory in directories:
        if read_cache_metadata(directory) is None:
            raise ValueError(f"缓存不完整或已失效：{directory}；请先重新生成缓存")
    return directories


def independent_cosine_scores(vectors: np.ndarray, index: int) -> np.ndarray:
    """Float64 reference, independent of the API's Float32 matrix product."""
    query = np.asarray(vectors[index], dtype=np.float64)
    query_norm = np.linalg.norm(query)
    if query_norm <= 0:
        raise ValueError("参考查询必须是非零向量")
    scores = np.full(len(vectors), -np.inf, dtype=np.float64)
    with threadpool_limits(limits=4):
        for start in range(0, len(vectors), 4096):
            matrix = np.asarray(vectors[start:start + 4096], dtype=np.float64)
            denominator = np.linalg.norm(matrix, axis=1) * query_norm
            numerator = np.einsum("ij,j->i", matrix, query)
            np.divide(numerator, denominator, out=scores[start:start + len(matrix)], where=denominator > 0)
    scores[index] = -np.inf
    return scores


class FullRasterChecks(unittest.TestCase):
    directory: Path
    base_url: str | None = None

    @classmethod
    def setUpClass(cls):
        cls.data = RasterDataset(cls.directory)
        cls.source = resolve_source_path(cls.data.meta)
        cls.source_valid = np.zeros((cls.data.meta["height"], cls.data.meta["width"]), dtype=bool)
        with rasterio.open(cls.source) as source:
            # Independently enumerate all cells, including per-band invalid values.
            for _, window in source.block_windows(1):
                block = source.read(window=window)
                valid = np.isfinite(block).all(axis=0)
                if source.nodata is not None:
                    valid &= (block != source.nodata).all(axis=0)
                valid &= source.dataset_mask(window=window) > 0
                row, col = int(window.row_off), int(window.col_off)
                cls.source_valid[row:row + int(window.height), col:col + int(window.width)] = valid
        nonzero = np.flatnonzero(cls.data.norms > 0)
        if not len(nonzero):
            raise AssertionError("数据集没有可用于余弦检索的非零向量")
        cls.query_index = int(nonzero[len(nonzero) // 2])
        cls.reference_scores = independent_cosine_scores(cls.data.vectors[:cls.data.count], cls.query_index)

    def test_01_full_coverage_and_binary_mapping(self):
        d = self.data
        self.assertFalse(d.meta["sampled"])
        self.assertEqual(d.count, int(self.source_valid.sum()))
        self.assertEqual(d.count, d.meta["pca_fit_count"])
        self.assertEqual(d.count, d.meta["clustering_fit_count"])
        self.assertEqual(d.meta["clustering_components"], d.meta["bands"])
        self.assertEqual(d.meta["clustering_space"], "original")
        self.assertEqual(d.meta["clustering_metric"], "euclidean")
        self.assertEqual(d.meta["clustering_normalization"], "none")
        self.assertIs(d.meta["clustering_whiten"], False)
        self.assertEqual(d.count, sum(d.meta["cluster_counts"]))
        self.assertEqual(d.lookup.shape, self.source_valid.shape)
        np.testing.assert_array_equal(d.lookup >= 0, self.source_valid)
        np.testing.assert_array_equal(np.sort(d.pixel_ids), np.flatnonzero(self.source_valid))
        np.testing.assert_array_equal(d.lookup.ravel()[d.pixel_ids], np.arange(d.count))
        np.testing.assert_array_equal(np.bincount(d.labels, minlength=d.meta["cluster_count"]), d.meta["cluster_counts"])
        self.assertTrue(np.all(d.labels < d.meta["cluster_count"]))
        self.assertEqual(d.vectors.shape[1], d.meta["bands"])
        self.assertGreaterEqual(d.vectors.shape[0], d.count)
        self.assertEqual(d.norms.shape, (d.count,))
        for name, dtype, shape in [
            ("positions.f32", "<f4", (d.count, 3)),
            ("colors.u8", "uint8", (d.count, 3)),
            ("labels.u8", "uint8", (d.count,)),
            ("pixel_ids.u32", "<u4", (d.count,)),
        ]:
            with self.subTest(file=name):
                self.assertEqual((self.directory / name).stat().st_size, int(np.prod(shape)) * np.dtype(dtype).itemsize)
                values = np.memmap(self.directory / name, mode="r", dtype=dtype, shape=shape)
                self.assertTrue(np.isfinite(values).all())
        self.assertFalse(Path(d.meta["source_signature"]["path"]).is_absolute())
        digest = hashlib.sha256()
        with self.source.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
        self.assertEqual(digest.hexdigest(), d.meta["source_signature"]["sha256"])

    def test_02_exported_tiff_projection_values_and_masks(self):
        d = self.data
        expected_mask = self.source_valid.astype(np.uint8) * 255
        with rasterio.open(self.source) as source:
            for name in ("clusters.tif", "pca_rgb.tif"):
                with self.subTest(file=name), rasterio.open(self.directory / name) as export:
                    self.assertEqual(export.crs, source.crs)
                    self.assertEqual(export.transform, source.transform)
                    self.assertEqual((export.width, export.height), (source.width, source.height))
                    np.testing.assert_array_equal(export.dataset_mask(), expected_mask)
                    self.assertEqual(len(export.files), 1, "下载的 TIFF 必须包含内部有效区掩膜")
                    if name == "clusters.tif":
                        grid = export.read(1).ravel()
                        np.testing.assert_array_equal(grid[d.pixel_ids], d.labels)
                        self.assertTrue(np.all(grid[~self.source_valid.ravel()] == 255))
                    else:
                        colors = np.memmap(self.directory / "colors.u8", mode="r", dtype="uint8", shape=(d.count, 3))
                        np.testing.assert_array_equal(export.read().reshape(3, -1)[:, d.pixel_ids].T, colors)

    def test_03_reprojected_previews_retain_every_valid_source_cell(self):
        d = self.data
        with rasterio.open(self.source) as source:
            affine, width, height = calculate_default_transform(source.crs, "EPSG:3857", source.width, source.height, *source.bounds)
            ids = np.where(self.source_valid, np.arange(source.width * source.height, dtype=np.uint32).reshape(self.source_valid.shape) + 1, 0).astype(np.uint32)
            projected = np.zeros((height * 2, width * 2), dtype=np.uint32)
            reproject(ids, projected, src_transform=source.transform, src_crs=source.crs, dst_transform=affine * rasterio.Affine.scale(.5, .5), dst_crs="EPSG:3857", resampling=Resampling.nearest)
        np.testing.assert_array_equal(np.unique(projected[projected > 0]) - 1, np.sort(d.pixel_ids))
        for name in ("clusters.png", "pca.png"):
            with self.subTest(file=name), Image.open(self.directory / name) as image:
                self.assertEqual(image.size, (width * 2, height * 2))
                self.assertEqual(image.mode, "RGBA")
                np.testing.assert_array_equal(np.asarray(image)[:, :, 3] > 0, projected > 0)

    def test_04_source_vectors_and_coordinate_round_trip(self):
        d = self.data
        with rasterio.open(self.source) as source:
            self.assertEqual(d.meta["crs"], str(source.crs))
            self.assertEqual(tuple(d.meta["transform"]), tuple(source.transform))
            for index in np.unique(np.linspace(0, d.count - 1, min(17, d.count), dtype=int)):
                with self.subTest(index=int(index)):
                    pixel = d.pixel(int(index))
                    actual = source.read(window=rasterio.windows.Window(pixel["col"], pixel["row"], 1, 1)).ravel()
                    np.testing.assert_array_equal(actual, d.vectors[index])
                    self.assertAlmostEqual(float(np.linalg.norm(actual.astype(np.float64))), float(d.norms[index]), delta=max(1e-5, float(d.norms[index]) * 1e-6))
                    x, y = source.xy(pixel["row"], pixel["col"])
                    lng, lat = transform(source.crs, "EPSG:4326", [x], [y])
                    self.assertAlmostEqual(pixel["lng"], lng[0], places=10)
                    self.assertAlmostEqual(pixel["lat"], lat[0], places=10)
                    self.assertEqual(d.locate(pixel["lng"], pixel["lat"])["index"], index)

    def assert_similarity(self, result: dict, k: int):
        self.assertEqual(result["searched_count"], self.data.count)
        self.assertEqual(result["query"]["index"], self.query_index)
        scores = self.reference_scores
        k = min(k, int(np.isfinite(scores).sum()))
        returned = [item["index"] for item in result["items"]]
        self.assertEqual(len(returned), k)
        self.assertEqual(len(set(returned)), k)
        self.assertNotIn(self.query_index, returned)
        if not k:
            return
        expected = np.argsort(scores)[-k:][::-1]
        threshold = scores[expected[-1]]
        # Permit numerical ties at the boundary without accepting lower-ranked
        # vectors. Every unambiguous top-k member must still be returned.
        tolerance = 2e-6
        required = set(np.flatnonzero(scores > threshold + tolerance).tolist())
        self.assertTrue(required.issubset(returned))
        self.assertTrue(np.all(scores[returned] >= threshold - tolerance))
        if len(np.flatnonzero(np.abs(scores - threshold) <= tolerance)) == 1:
            self.assertEqual(set(expected.tolist()), set(returned))
        for item in result["items"]:
            self.assertAlmostEqual(item["score"], float(np.clip(scores[item["index"]], -1, 1)), delta=tolerance)
        self.assertTrue(all(a["score"] >= b["score"] for a, b in zip(result["items"], result["items"][1:])))

    def test_05_exact_similarity_against_independent_float64_reference(self):
        for requested in (30, 200, 500):
            with self.subTest(top=requested):
                k = min(requested, self.data.count - 1)
                self.assert_similarity(self.data.similar(self.query_index, k), k)

    def request(self, path: str, params: dict | None = None):
        query = urllib.parse.urlencode({"dataset_id": self.data.meta["dataset_id"], **(params or {})})
        return urllib.request.urlopen(f"{self.base_url}{path}?{query}", timeout=120)

    def get_json(self, path: str, params: dict | None = None) -> dict:
        with self.request(path, params) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def test_06_live_api_dataset_endpoints_and_validation(self):
        if self.base_url is None:
            self.skipTest("离线验证；使用 --port 或 --base-url 开启 API 检查")
        d = self.data
        with urllib.request.urlopen(self.base_url + "/health", timeout=30) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response)["status"], "ok")
        for suffix in ("/", "/?raster=1"):
            with urllib.request.urlopen(self.base_url + suffix, timeout=30) as response:
                self.assertEqual(response.status, 200)
        listed = self.get_json("/raster/datasets")["items"]
        self.assertIn(d.meta["dataset_id"], [item["dataset_id"] for item in listed])
        metadata = self.get_json("/raster/metadata")
        self.assertEqual(metadata["dataset_id"], d.meta["dataset_id"])
        self.assertEqual(metadata["valid_count"], d.count)
        pixel = self.get_json("/raster/pixel", {"index": self.query_index})
        self.assertEqual(pixel["pixel_id"], int(d.pixel_ids[self.query_index]))
        located = self.get_json("/raster/locate", {"lng": pixel["lng"], "lat": pixel["lat"]})
        self.assertEqual(located["index"], self.query_index)
        for requested in (30, 200, 500):
            with self.subTest(top=requested):
                k = min(requested, d.count - 1)
                self.assert_similarity(self.get_json("/raster/similar", {"index": self.query_index, "k": k}), k)
        for path, params, status in [
            ("/raster/metadata", {"dataset_id": "__missing_dataset__"}, 404),
            ("/raster/pixel", {"index": -1}, 422),
            ("/raster/pixel", {"index": d.count}, 404),
            ("/raster/pixel", {"index": "invalid"}, 422),
            ("/raster/similar", {"index": 0, "k": 0}, 422),
            ("/raster/similar", {"index": 0, "k": 501}, 422),
            ("/raster/similar", {"index": d.count}, 404),
            ("/raster/locate", {"lng": 181, "lat": 0}, 422),
            ("/raster/locate", {"lng": 0, "lat": -91}, 422),
        ]:
            with self.subTest(path=path, params=params):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self.request(path, params)
                self.assertEqual(raised.exception.code, status)

    def test_07_live_download_allowlist(self):
        if self.base_url is None:
            self.skipTest("离线验证；使用 --port 或 --base-url 开启 API 检查")
        for name in sorted(DOWNLOAD_FILES):
            with self.subTest(file=name), self.request("/raster/files/" + urllib.parse.quote(name, safe="")) as response:
                self.assertEqual(response.status, 200)
                size = 0
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    size += len(block)
                self.assertEqual(size, (self.directory / name).stat().st_size)
        for name in ("vectors.npy", "norms.npy", "lookup.npy", "metadata.json", self.source.name, "../vectors.npy"):
            with self.subTest(private_file=name):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self.request("/raster/files/" + urllib.parse.quote(name, safe=""))
                self.assertEqual(raised.exception.code, 404)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-id", help="只检查 cache 中的指定数据集；默认检查全部")
    live = parser.add_mutually_exclusive_group()
    live.add_argument("--base-url", help="显式开启在线 API 检查，例如 http://127.0.0.1:8000")
    live.add_argument("--port", type=int, help="在指定本机端口显式开启在线 API 检查")
    args = parser.parse_args(argv)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")
    base_url = args.base_url.rstrip("/") if args.base_url else (f"http://127.0.0.1:{args.port}" if args.port else None)
    if base_url and (urllib.parse.urlparse(base_url).scheme not in {"http", "https"} or not urllib.parse.urlparse(base_url).netloc):
        parser.error("--base-url 必须是有效的 HTTP 或 HTTPS 地址")
    try:
        directories = select_directories(DEFAULT_CACHE_DIR, args.dataset_id)
    except ValueError as error:
        parser.exit(2, f"{error}\n")
    suite = unittest.TestSuite()
    for directory in directories:
        checks = type(f"RasterChecks_{directory.name}", (FullRasterChecks,), {"directory": directory, "base_url": base_url})
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(checks))
    print(f"验证 {len(directories)} 个数据集（{'离线 + API: ' + base_url if base_url else '离线，无需启动服务'}）", flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
