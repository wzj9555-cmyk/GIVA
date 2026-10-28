"""Prepare every valid raster vector for the full-resolution GIVA workspace."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from rasterio.warp import calculate_default_transform, reproject, Resampling, transform_bounds
from sklearn.cluster import MiniBatchKMeans
from threadpoolctl import threadpool_limits

from plot_explained_variance import write_explained_variance
from raster_common import (
    CACHE_VERSION, DEFAULT_CACHE_DIR, DEFAULT_INPUT_DIR, OVERVIEW_NAME,
    cache_directory, chinese_font_path, dataset_id_for_source, discover_sources,
    matches_source, processing_parameters, product_manifest, read_cache_metadata,
    source_signature, validate_products,
)

PALETTE = ["#52c9b0", "#f2b86b", "#7795ed", "#e784a6", "#a5cc73", "#b59ae5", "#65bedc", "#e88d67", "#dbc86c", "#8aa5b5", "#bbcfdf", "#c689c1"]
VERSION = CACHE_VERSION


def colors() -> np.ndarray:
    return np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in PALETTE], dtype=np.uint8)


def prepare(source: Path, output: Path | None = None, n_clusters: int = 8,
            force_rebuild: bool = False, input_dir: Path = DEFAULT_INPUT_DIR) -> dict:
    """Build in isolation, then publish only a complete dataset directory."""
    source = Path(source).resolve()
    input_dir = Path(input_dir).resolve()
    # Check the complete source directory even when preparing one file.
    discover_sources(input_dir)
    identity = dataset_id_for_source(source)
    output = Path(output).resolve() if output is not None else cache_directory(source)
    if output.name != identity:
        raise ValueError(f"缓存目录必须命名为规范化数据集名称: {identity}")
    if not 2 <= n_clusters <= 12:
        raise ValueError("聚类数量必须在 2–12 之间")
    signature = source_signature(source, input_dir)
    current = read_cache_metadata(output)
    if not force_rebuild and current is not None and matches_source(current, signature, n_clusters):
        print("缓存完整且源文件 SHA-256 一致，复用: " + str(output), flush=True)
        return current
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{identity}.building-", dir=output.parent))
    backup: Path | None = None
    try:
        metadata = _build_dataset(source, staging, n_clusters, signature)
        if source_signature(source, input_dir) != signature:
            raise ValueError(f"处理期间原始 TIF 已发生变化，未发布缓存: {source.name}")
        metadata["products"] = product_manifest(staging)
        validate_products(staging, metadata)
        # A manifest is a completion marker. No partial build ever has one.
        (staging / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
        if output.exists():
            backup = output.with_name(f".{identity}.previous-{uuid4().hex}")
            output.rename(backup)
        try:
            staging.rename(output)
        except BaseException:
            if backup is not None:
                backup.rename(output)
                backup = None
            raise
        print(f"已发布全量缓存: {output} ({metadata['valid_count']:,} 个有效像元)", flush=True)
        return metadata
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)


def _build_dataset(source: Path, output: Path, n_clusters: int, signature: dict) -> dict:
    started = time.perf_counter()
    with rasterio.open(source) as src, threadpool_limits(limits=4):
        if src.crs is None:
            raise ValueError("原始 TIF 必须包含坐标参考系（CRS）")
        total = src.width * src.height
        if total > 2 ** 32:
            raise ValueError("栅格总像元数超过 uint32 索引范围")
        vectors = np.lib.format.open_memmap(output / "vectors.npy", mode="w+", dtype="float32", shape=(total, src.count))
        pixel_ids = np.empty(total, dtype=np.uint32)
        covariance = np.zeros((src.count, src.count), dtype=np.float64)
        sums = np.zeros(src.count, dtype=np.float64)
        count = 0
        blocks = list(src.block_windows(1))
        for step, (_, window) in enumerate(blocks):
            block = src.read(window=window)
            valid = np.isfinite(block).all(axis=0)
            if src.nodata is not None:
                valid &= (block != src.nodata).all(axis=0)
            valid &= src.dataset_mask(window=window) > 0
            rows, cols = np.nonzero(valid)
            values = np.ascontiguousarray(block[:, valid].T, dtype=np.float32)
            if not np.isfinite(values).all():
                raise ValueError("原始向量数值超出 Float32 可表示范围")
            end = count + len(values)
            vectors[count:end] = values
            pixel_ids[count:end] = (rows + int(window.row_off)) * src.width + cols + int(window.col_off)
            for start in range(0, len(values), 8192):
                batch = values[start:start + 8192].astype(np.float64)
                sums += batch.sum(axis=0)
                covariance += batch.T @ batch
            count = end
            print(f"Read / covariance {step + 1}/{len(blocks)}, valid={count:,}", flush=True)
        if count < n_clusters:
            raise ValueError(f"有效像元数 {count} 少于聚类数量 {n_clusters}")
        vectors.flush()
        # The on-disk matrix is reserved to the raster extent; valid_count controls its used rows.
        mean = sums / count
        covariance -= np.outer(sums, sums) / count
        covariance /= count - 1
        eigenvalues, components = np.linalg.eigh(covariance)
        order = np.argsort(eigenvalues)[::-1]
        eigenvalues = np.maximum(eigenvalues[order], 0)
        # PCA supplies display coordinates and RGB only; clustering uses every original band.
        components = components[:, order[:min(3, src.count)]]
        # Fix PCA sign for reproducible colors and orientation.
        for column in range(components.shape[1]):
            if components[np.argmax(np.abs(components[:, column])), column] < 0:
                components[:, column] *= -1
        reduced = np.empty((count, components.shape[1]), dtype=np.float32)
        norms = np.empty(count, dtype=np.float32)
        for start in range(0, count, 8192):
            block = np.asarray(vectors[start:min(count, start + 8192)])
            reduced[start:start + len(block)] = (block - mean) @ components
            norms[start:start + len(block)] = np.linalg.norm(block, axis=1)
        print(f"PCA display complete; fitting clusters on ALL valid pixels in original {src.count}D space", flush=True)
        # Original Float32 values, without centering, scaling, normalization or whitening.
        # Every valid vector participates; mini-batches bound memory, with no subset selection.
        model = MiniBatchKMeans(n_clusters=n_clusters, random_state=42, batch_size=8192, n_init=3)
        for epoch in range(3):
            for start in range(0, count, 8192):
                model.partial_fit(np.asarray(vectors[start:min(count, start + 8192)]))
        labels = np.empty(count, dtype=np.uint8)
        for start in range(0, count, 8192):
            stop = min(count, start + 8192)
            labels[start:stop] = model.predict(np.asarray(vectors[start:stop])).astype(np.uint8)
        pca = reduced[:, :3]
        if pca.shape[1] < 3:
            pca = np.pad(pca, ((0, 0), (0, 3 - pca.shape[1])))
        low, high = np.percentile(pca, [2, 98], axis=0)
        rgb = (np.clip((pca - low) / np.maximum(high - low, 1e-9), 0, 1) * 255).astype(np.uint8)
        center = (low + high) / 2
        positions = ((pca - center) / max(float((high - low).max()), 1e-9) * 24).astype("<f4")
        positions.tofile(output / "positions.f32")
        rgb.tofile(output / "colors.u8")
        labels.tofile(output / "labels.u8")
        pixel_ids[:count].astype("<u4").tofile(output / "pixel_ids.u32")
        np.save(output / "norms.npy", norms)
        lookup = np.full(total, -1, dtype=np.int32)
        lookup[pixel_ids[:count]] = np.arange(count, dtype=np.int32)
        np.save(output / "lookup.npy", lookup.reshape(src.height, src.width))
        cluster_grid = np.full(total, 255, dtype=np.uint8)
        cluster_grid[pixel_ids[:count]] = labels
        profile = dict(driver="GTiff", width=src.width, height=src.height, crs=src.crs, transform=src.transform, compress="deflate", tiled=True)
        with rasterio.open(output / "clusters.tif", "w", **profile, count=1, dtype="uint8", nodata=255) as dst:
            dst.write(cluster_grid.reshape(src.height, src.width), 1)
            dst.write_colormap(1, {i: tuple(c) + (255,) for i, c in enumerate(colors().tolist())} | {255: (0, 0, 0, 0)})
        full_rgb = np.zeros((total, 3), dtype=np.uint8)
        full_rgb[pixel_ids[:count]] = rgb
        mask = (lookup >= 0).astype(np.uint8) * 255
        # Keep the validity mask inside the downloadable TIFF, without a .msk sidecar.
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(output / "pca_rgb.tif", "w", **profile, count=3, dtype="uint8") as dst:
            dst.write(full_rgb.reshape(src.height, src.width, 3).transpose(2, 0, 1))
            dst.write_mask(mask.reshape(src.height, src.width))
        (output / "pca_rgb.tif.msk").unlink(missing_ok=True)
        mercator, mw, mh = calculate_default_transform(src.crs, "EPSG:3857", src.width, src.height, *src.bounds)
        # Oversample the display grid so reprojection does not skip source cells.
        mercator = mercator * rasterio.Affine.scale(.5, .5)
        mw, mh = mw * 2, mh * 2
        def preview(name: str, pixel_colors: np.ndarray) -> None:
            rgba = np.zeros((total, 4), dtype=np.uint8)
            rgba[pixel_ids[:count], :3] = pixel_colors
            rgba[:, 3] = mask
            destination = np.zeros((4, mh, mw), dtype=np.uint8)
            reproject(rgba.reshape(src.height, src.width, 4).transpose(2, 0, 1), destination, src_transform=src.transform, src_crs=src.crs, dst_transform=mercator, dst_crs="EPSG:3857", resampling=Resampling.nearest)
            Image.fromarray(destination.transpose(1, 2, 0)).save(output / name)
        preview("pca.png", rgb)
        preview("clusters.png", colors()[labels])
        left, bottom, right, top = rasterio.transform.array_bounds(mh, mw, mercator)
        west, south, east, north = transform_bounds("EPSG:3857", "EPSG:4326", left, bottom, right, top)
        metadata = {
            "version": VERSION, "dataset_id": dataset_id_for_source(source), "source_signature": signature, "name": source.name,
            "parameters": processing_parameters(n_clusters),
            "width": src.width, "height": src.height, "bands": src.count, "valid_count": count,
            "total_pixels": total,
            "nodata": float(src.nodata) if src.nodata is not None and np.isfinite(src.nodata) else None,
            "resolution": list(src.res), "crs": str(src.crs),
            "transform": list(src.transform), "bounds": [west, south, east, north],
            "image_coordinates": [[west, north], [east, north], [east, south], [west, south]],
            "preview_size": [mw, mh],
            "source_bounds": list(src.bounds), "cluster_count": n_clusters, "palette": PALETTE[:n_clusters],
            "cluster_counts": np.bincount(labels, minlength=n_clusters).tolist(),
            "pca_explained_ratio": (eigenvalues[:3] / max(eigenvalues.sum(), 1e-12)).tolist(),
            "pca_fit_count": count, "clustering_fit_count": count, "clustering_epochs": 3,
            "clustering_components": src.count, "clustering_space": "original",
            "clustering_metric": "euclidean", "clustering_normalization": "none",
            "clustering_whiten": False, "sampled": False,
            "position_bounds": [positions.min(axis=0).tolist(), positions.max(axis=0).tolist()],
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "description": f"{src.count}D raster embedding; unsupervised clusters, not semantic land-use labels",
        }
        render_overview(output, metadata)
        write_explained_variance(eigenvalues, output, count)
        metadata["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        return metadata


def render_overview(output: Path, meta: dict) -> None:
    """Offline overview for sharing without a running web server."""
    canvas = Image.new("RGB", (1800, 1080), "#101b29")
    draw = ImageDraw.Draw(canvas)
    font_path = str(chinese_font_path())
    title = ImageFont.truetype(font_path, 34)
    body = ImageFont.truetype(font_path, 21)
    small = ImageFont.truetype(font_path, 17)
    draw.text((55, 32), "GIVA  /  全量栅格表征可视化", font=title, fill="#edf3f7")
    draw.text((55, 91), f"{meta['valid_count']:,} 个有效像元  ·  {meta['bands']} 维  ·  分辨率 {meta['resolution'][0]:g}  ·  全量参与 PCA 与聚类", font=body, fill="#9bb6ca")
    for i, (filename, label) in enumerate([("pca.png", "地理空间 / PCA 三通道配色"), ("clusters.png", f"地理空间 / {meta['cluster_count']} 类无监督聚类")]):
        x = 55 + i * 870
        draw.rounded_rectangle((x, 160, x + 815, 890), radius=18, fill="#182737")
        draw.text((x + 25, 179), label, font=body, fill="#e1ebf1")
        image = Image.open(output / filename).convert("RGBA")
        image.thumbnail((765, 625))
        canvas.paste(image, (x + (815 - image.width) // 2, 240 + (625 - image.height) // 2), image)
    for i, (color, count) in enumerate(zip(meta["palette"], meta["cluster_counts"])):
        columns = 6 if meta["cluster_count"] > 8 else 4
        x = 60 + (i % columns) * (1720 // columns)
        y = 925 + (i // columns) * 43
        draw.rounded_rectangle((x, y + 3, x + 17, y + 20), radius=4, fill=color)
        draw.text((x + 29, y), f"C{i + 1:02d}   {count:,} 像元", font=small, fill="#c6d6df")
    draw.text((55, 1025), "类别仅表示表征相似性；PCA 颜色为融合特征映射，不是真彩色影像。", font=small, fill="#8ba2b4")
    canvas.save(output / OVERVIEW_NAME)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, help="只准备一个原始 TIF；省略时扫描整个 tif 目录")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, help="单数据集缓存目录；必须与规范化数据集名称一致")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--clusters", type=int, default=8, choices=range(2, 13))
    parser.add_argument("--force-rebuild", action="store_true", help="忽略完整缓存，重新计算全部产品")
    args = parser.parse_args()
    if args.output and not args.source:
        parser.error("--output 必须与 --source 一起使用；批量处理请用 --cache-dir")
    sources = [args.source] if args.source else discover_sources(args.input_dir)
    if not sources:
        parser.error(f"未找到原始 TIF: {args.input_dir}")
    for source in sources:
        prepare(source, args.output or cache_directory(source, args.cache_dir), args.clusters,
                force_rebuild=args.force_rebuild, input_dir=args.input_dir)
