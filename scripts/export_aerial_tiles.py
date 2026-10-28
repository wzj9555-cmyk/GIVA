"""Export the local GCJ-02 aerial image as static JPEG tiles for Pages."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

from PIL import Image
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from aerial import prepare_aerial, read_reference

OUTPUT = ROOT / "site-data" / "aerial"
HALF_WORLD = 20037508.342789244


def tile_xy(lng: float, lat: float, zoom: int) -> tuple[int, int]:
    scale = 2**zoom
    return (int((lng + 180) / 360 * scale),
            int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale))


def export_tile(args):
    source_path, destination, zoom, x, y = args
    size = 2 * HALF_WORLD / 2**zoom
    left, top = -HALF_WORLD + x * size, HALF_WORLD - y * size
    affine = from_bounds(left, top - size, left + size, top, 256, 256)
    with rasterio.open(source_path) as source:
        with WarpedVRT(source, crs="EPSG:3857", transform=affine, width=256, height=256,
                       resampling=Resampling.bilinear) as warped:
            pixels = warped.read([1, 2, 3]).transpose(1, 2, 0)
    output = destination / str(zoom) / str(x) / f"{y}.jpg"
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels).save(output, format="JPEG", quality=85, optimize=True)
    return output.stat().st_size


def main():
    reference = read_reference()
    if reference is None:
        raise RuntimeError("没有找到航空影像及空间参考")
    source = prepare_aerial()
    if source is None:
        raise RuntimeError("航空影像缓存未生成")
    if OUTPUT.exists():
        raise RuntimeError(f"导出目录已存在，请先移走：{OUTPUT}")
    west, south, east, north = reference["bounds"]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".aerial-export-", dir=OUTPUT.parent))
    try:
        jobs = []
        counts = {}
        for zoom in range(12, 21):
            min_x, min_y = tile_xy(west, north, zoom)
            max_x, max_y = tile_xy(east, south, zoom)
            counts[str(zoom)] = (max_x - min_x + 1) * (max_y - min_y + 1)
            jobs.extend((source, temporary, zoom, x, y)
                        for x in range(min_x, max_x + 1) for y in range(min_y, max_y + 1))
        print(f"导出 {len(jobs):,} 个航空 JPEG 瓦片", flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            total = 0
            for i, size in enumerate(pool.map(export_tile, jobs), 1):
                total += size
                if i % 250 == 0 or i == len(jobs):
                    print(f"航空瓦片 {i:,}/{len(jobs):,}", flush=True)
        metadata = {"available": True, "bounds": reference["bounds"], "coordinates": "GCJ-02",
                    "maxzoom": 20, "minzoom": 12, "version": reference["sha256"],
                    "image_sha256": reference["sha256"], "tile_counts": counts,
                    "tile_count": len(jobs), "tile_bytes": total, "format": "jpeg"}
        (temporary / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.rename(temporary, OUTPUT)
        print(f"航空瓦片完成：{total / 1024**2:.1f} MiB，{OUTPUT}", flush=True)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
