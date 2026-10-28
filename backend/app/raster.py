"""Binary raster data and exact, on-demand similarity queries."""
from __future__ import annotations

import os
import io
import json
import threading
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from PIL import Image
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.transform import Affine
from rasterio.warp import transform
from threadpoolctl import threadpool_limits

from raster_common import DEFAULT_CACHE_DIR, PUBLIC_FILES, read_cache_metadata

router = APIRouter(prefix="/raster", tags=["full raster"])
RESULT_DIR = Path(os.getenv("GIVA_RASTER_DIR", str(DEFAULT_CACHE_DIR))).resolve()
_datasets: dict[str, "RasterDataset"] = {}
_load_lock = threading.Lock()
_query_lock = threading.Lock()


class RasterDataset:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.meta = read_cache_metadata(self.directory)
        if self.meta is None:
            raise HTTPException(503, f"栅格数据集文件不完整，请重建缓存: {directory.name}")
        manifest = (self.directory / "metadata.json").stat()
        self.cache_token = (self.directory, manifest.st_mtime_ns, manifest.st_size)
        self.count = self.meta["valid_count"]
        self.vectors = np.load(directory / "vectors.npy", mmap_mode="r")
        self.norms = np.load(directory / "norms.npy", mmap_mode="r")
        self.lookup = np.load(directory / "lookup.npy", mmap_mode="r")
        self.pixel_ids = np.memmap(directory / "pixel_ids.u32", mode="r", dtype="<u4", shape=(self.count,))
        self.labels = np.memmap(directory / "labels.u8", mode="r", dtype="uint8", shape=(self.count,))
        self.affine = Affine(*self.meta["transform"][:6])

    def pixel(self, index: int) -> dict:
        if index < 0 or index >= self.count:
            raise HTTPException(404, "像元不在有效区内")
        row, col = divmod(int(self.pixel_ids[index]), self.meta["width"])
        x, y = self.affine * (col + .5, row + .5)
        lng, lat = transform(self.meta["crs"], "EPSG:4326", [x], [y])
        vector = self.vectors[index]
        return {"index": index, "row": row, "col": col, "pixel_id": int(self.pixel_ids[index]), "lng": lng[0], "lat": lat[0], "cluster": int(self.labels[index]), "norm": float(self.norms[index]), "vector_preview": vector[:12].tolist(), "bands": self.meta["bands"]}

    def locate(self, lng: float, lat: float) -> dict:
        x, y = transform("EPSG:4326", self.meta["crs"], [lng], [lat])
        col, row = (~self.affine) * (x[0], y[0])
        row, col = int(np.floor(row)), int(np.floor(col))
        if not (0 <= row < self.lookup.shape[0] and 0 <= col < self.lookup.shape[1]):
            raise HTTPException(404, "点击位置超出栅格范围")
        return self.pixel(int(self.lookup[row, col]))

    def similar(self, index: int, k: int) -> dict:
        query = self.pixel(index)
        k = min(k, self.count - 1)
        if k < 1:
            raise HTTPException(422, "相似像元数量必须至少为 1")
        q = np.asarray(self.vectors[index], dtype=np.float32)
        if self.norms[index] <= 0:
            raise HTTPException(422, "零向量没有余弦相似度")
        scores = np.empty(self.count, dtype=np.float32)
        with _query_lock, threadpool_limits(limits=4):
            for start in range(0, self.count, 8192):
                end = min(self.count, start + 8192)
                denominator = self.norms[start:end] * self.norms[index]
                scores[start:end] = (self.vectors[start:end] @ q) / np.maximum(denominator, 1e-12)
        scores[index] = -np.inf
        scores[self.norms <= 0] = -np.inf
        top = np.argpartition(scores, -k)[-k:]
        top = top[np.argsort(scores[top])[::-1]]
        items = [{**self.pixel(int(i)), "score": float(np.clip(scores[i], -1, 1))} for i in top if np.isfinite(scores[i])]
        return {"query": query, "searched_count": self.count, "method": f"exact_cosine_{self.meta['bands']}d", "items": items}


def _dataset_directories() -> list[Path]:
    if not RESULT_DIR.is_dir():
        return []
    return sorted(path for path in RESULT_DIR.iterdir()
                  if path.is_dir() and not path.name.startswith(".") and (path / "metadata.json").is_file())


def _directory_for_entry(entry: dict) -> Path:
    directory = RESULT_DIR / entry["directory"]
    if not (directory / "metadata.json").is_file():
        raise HTTPException(404, "栅格数据集文件不完整")
    return directory


def list_rasters() -> list[dict]:
    entries: list[dict] = []
    for directory in _dataset_directories():
        meta = read_cache_metadata(directory)
        if meta is None:
            continue
        dataset_id = meta["dataset_id"]
        entries.append({"dataset_id": dataset_id, "name": meta.get("name", directory.name), "valid_count": meta.get("valid_count", 0), "bands": meta.get("bands", 0), "width": meta.get("width", 0), "height": meta.get("height", 0), "crs": meta.get("crs", ""), "sampled": bool(meta.get("sampled", False)), "directory": directory.name})
    return entries

def get_raster(dataset_id: str | None = None) -> RasterDataset:
    entries = list_rasters()
    if not entries:
        raise HTTPException(503, "请先运行 prepare_raster.py 生成全量可视化数据")
    wanted = dataset_id or entries[0]["dataset_id"]
    entry = next((item for item in entries if item["dataset_id"] == wanted), None)
    if entry is None:
        raise HTTPException(404, f"栅格数据集不存在: {wanted}")
    directory = _directory_for_entry(entry)
    manifest = (directory / "metadata.json").stat()
    token = (directory.resolve(), manifest.st_mtime_ns, manifest.st_size)
    with _load_lock:
        if wanted not in _datasets or _datasets[wanted].cache_token != token:
            _datasets[wanted] = RasterDataset(directory)
        return _datasets[wanted]


@router.get("/datasets")
def datasets():
    return {"items": list_rasters()}


@router.get("/metadata")
def metadata(dataset_id: str | None = Query(None)):
    return get_raster(dataset_id).meta


def aerial_cache():
    directory = RESULT_DIR / ".aerial-Air2025"
    image, manifest = directory / "aerial.tif", directory / "aerial-cache.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data["version"] != 1 or data["bytes"] != image.stat().st_size:
            return None
        return image, data["reference"]
    except (OSError, KeyError, ValueError, TypeError):
        return None


@router.get("/aerial")
def aerial_status():
    cached = aerial_cache()
    if cached is None:
        return {"available": False}
    path, meta = cached
    return {"available": True, "bounds": meta["bounds"], "coordinates": meta["coordinates"],
            "version": str(path.stat().st_mtime_ns), "maxzoom": 20}


@router.get("/aerial/tiles/{zoom}/{x}/{y}.png")
def aerial_tile(zoom: int, x: int, y: int):
    if zoom < 0 or zoom > 22 or x < 0 or y < 0 or x >= 2**zoom or y >= 2**zoom:
        raise HTTPException(404, "瓦片坐标无效")
    cached = aerial_cache()
    if cached is None:
        raise HTTPException(404, "航空底图未准备")
    half = 20037508.342789244
    size = 2 * half / 2**zoom
    left, top = -half + x * size, half - y * size
    affine = from_bounds(left, top - size, left + size, top, 256, 256)
    with rasterio.open(cached[0]) as source:
        with WarpedVRT(source, crs="EPSG:3857", transform=affine, width=256, height=256,
                       resampling=Resampling.bilinear, add_alpha=True) as warped:
            pixels = warped.read([1, 2, 3, 4]).transpose(1, 2, 0)
    output = io.BytesIO()
    Image.fromarray(pixels, mode="RGBA").save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/files/{name}")
def file(name: str, dataset_id: str | None = Query(None)):
    if name not in PUBLIC_FILES:
        raise HTTPException(404, "File not found")
    dataset = get_raster(dataset_id)
    path = dataset.directory / name
    if not path.is_file():
        raise HTTPException(404, "File not found")
    media = {".png": "image/png", ".tif": "image/tiff", ".json": "application/json", ".csv": "text/csv"}.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=media)


@router.get("/pixel")
def pixel(index: int = Query(..., ge=0), dataset_id: str | None = Query(None)):
    return get_raster(dataset_id).pixel(index)


@router.get("/locate")
def locate(lng: float = Query(..., ge=-180, le=180), lat: float = Query(..., ge=-90, le=90), dataset_id: str | None = Query(None)):
    return get_raster(dataset_id).locate(lng, lat)


@router.get("/similar")
def similar(index: int = Query(..., ge=0), k: int = Query(30, ge=1, le=500), dataset_id: str | None = Query(None)):
    dataset = get_raster(dataset_id)
    return dataset.similar(index, min(k, dataset.count - 1))
