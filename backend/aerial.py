"""Prepare a tiled local aerial image without changing the source JPEG."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.windows import Window

from raster_common import PACKAGE_ROOT, DEFAULT_CACHE_DIR

IMAGE_DIR = PACKAGE_ROOT / "imagery" / "Air2025"
CACHE_DIR = DEFAULT_CACHE_DIR / ".aerial-Air2025"
PRODUCT = "aerial.tif"
MANIFEST = "aerial-cache.json"
VERSION = 1


def read_reference():
    path = IMAGE_DIR / "georeference.json"
    if not path.is_file():
        return None
    meta = json.loads(path.read_text(encoding="utf-8"))
    if meta["coordinates"] != "GCJ-02" or meta["crs"] != "EPSG:4326":
        raise ValueError("航空影像必须明确标注 GCJ-02 坐标及经纬度参考")
    image = IMAGE_DIR / meta["image"]
    if image.resolve().parent != IMAGE_DIR.resolve() or not image.is_file():
        raise ValueError("航空影像 JPEG 不存在")
    with image.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != meta["sha256"]:
        raise ValueError("航空影像 JPEG 哈希与空间参考记录不一致")
    return meta


def prepare_aerial(cache_dir: Path = CACHE_DIR):
    meta = read_reference()
    if meta is None:
        return None
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target, manifest = cache_dir / PRODUCT, cache_dir / MANIFEST
    signature = {"version": VERSION, "reference": meta}
    if target.is_file() and manifest.is_file():
        try:
            existing = json.loads(manifest.read_text(encoding="utf-8"))
            with rasterio.open(target) as source:
                if (existing == {**signature, "bytes": target.stat().st_size}
                    and source.width == meta["width"] and source.height == meta["height"]
                    and source.crs.to_string() == meta["crs"]
                    and tuple(source.transform) == tuple(meta["transform"])):
                    return target
        except (OSError, ValueError, KeyError, AttributeError, rasterio.errors.RasterioError):
            pass
    fd, name = tempfile.mkstemp(prefix=".aerial-building-", suffix=".tif", dir=cache_dir)
    os.close(fd)
    staging = Path(name)
    try:
        with rasterio.open(IMAGE_DIR / meta["image"]) as source:
            if (source.width, source.height, source.count) != (meta["width"], meta["height"], meta["bands"]):
                raise ValueError("航空 JPEG 尺寸与空间参考不匹配")
            profile = {"driver": "GTiff", "width": source.width, "height": source.height,
                       "count": 3, "dtype": "uint8", "crs": meta["crs"],
                       "transform": Affine(*meta["transform"][:6]), "tiled": True,
                       "blockxsize": 512, "blockysize": 512, "compress": "jpeg",
                       "photometric": "ycbcr", "jpeg_quality": 95, "bigtiff": "IF_SAFER"}
            with rasterio.open(staging, "w", **profile) as dest:
                for y in range(0, source.height, 512):
                    height = min(512, source.height - y)
                    window = Window(0, y, source.width, height)
                    dest.write(source.read(window=window), window=window)
                    print(f"航空底图缓存：{min(y + height, source.height)}/{source.height} 行", flush=True)
                dest.build_overviews([2, 4, 8, 16, 32, 64, 128], Resampling.average)
                dest.update_tags(ns="rio_overview", resampling="average")
        staging.replace(target)
        manifest.write_text(json.dumps({**signature, "bytes": target.stat().st_size}, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
    finally:
        staging.unlink(missing_ok=True)


if __name__ == "__main__":
    print(prepare_aerial() or "没有配置航空影像")
