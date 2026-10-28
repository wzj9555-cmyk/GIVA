"""Shared paths, cache contracts and macOS fonts for the raster workspace.

This module deliberately imports the scientific packages only while validating
products, so the launcher can discover paths before checking its Conda runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = PROJECT_ROOT.parent
DEFAULT_INPUT_DIR = PACKAGE_ROOT / "tif"
DEFAULT_CACHE_DIR = PACKAGE_ROOT / "cache"
CACHE_VERSION = 4
PROCESSING_VERSION = "full-raster-macos-4-original-clustering"
VARIANCE_STEM = "PCA累计解释方差曲线"
OVERVIEW_NAME = "全量栅格可视化总览.png"
REQUIRED_FILES = (
    "vectors.npy", "norms.npy", "lookup.npy", "positions.f32", "colors.u8",
    "labels.u8", "pixel_ids.u32", "clusters.tif", "pca_rgb.tif", "pca.png",
    "clusters.png", OVERVIEW_NAME, f"{VARIANCE_STEM}.png",
    f"{VARIANCE_STEM}.csv", f"{VARIANCE_STEM}.json",
)
PUBLIC_FILES = frozenset(REQUIRED_FILES) - {"vectors.npy", "norms.npy", "lookup.npy"}


def dataset_id_for_source(source: Path) -> str:
    value = unicodedata.normalize("NFKC", Path(source).stem).casefold()
    value = re.sub(r"[^\w-]+", "_", value, flags=re.UNICODE).strip("_-")
    if not value or value in {".", ".."}:
        raise ValueError(f"TIF 文件名无法生成数据集名称: {source.name}")
    return value


def discover_sources(input_dir: Path = DEFAULT_INPUT_DIR) -> list[Path]:
    input_dir = Path(input_dir).resolve()
    if not input_dir.exists():
        return []
    if not input_dir.is_dir():
        raise ValueError(f"TIF 目录不存在: {input_dir}")
    sources = sorted(
        (path for path in input_dir.iterdir() if path.is_file() and path.suffix.casefold() in {".tif", ".tiff"}),
        key=lambda path: path.name.casefold(),
    )
    used: dict[str, Path] = {}
    for source in sources:
        identity = dataset_id_for_source(source)
        if identity in used:
            raise ValueError(f"TIF 数据集名称冲突: {used[identity].name} / {source.name} -> {identity}")
        used[identity] = source
    return sources


def cache_directory(source: Path, cache_root: Path = DEFAULT_CACHE_DIR) -> Path:
    return Path(cache_root).resolve() / dataset_id_for_source(source)


def source_signature(source: Path, input_dir: Path = DEFAULT_INPUT_DIR) -> dict:
    source = Path(source).resolve()
    input_dir = Path(input_dir).resolve()
    if source.parent != input_dir or source.suffix.casefold() not in {".tif", ".tiff"}:
        raise ValueError(f"原始 TIF 必须直接放在 {input_dir}: {source}")
    before = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"读取校验期间原始 TIF 已发生变化: {source.name}")
    return {
        "path": Path(os.path.relpath(source, PACKAGE_ROOT)).as_posix(),
        "sha256": digest.hexdigest(), "size": after.st_size, "mtime_ns": after.st_mtime_ns,
    }


def resolve_source_path(metadata: dict) -> Path:
    relative = Path(metadata["source_signature"]["path"])
    if relative.is_absolute():
        raise ValueError("缓存源路径必须使用相对路径，请重建缓存")
    return (PACKAGE_ROOT / relative).resolve()


def processing_parameters(n_clusters: int = 8) -> dict:
    return {
        "processing_version": PROCESSING_VERSION, "clusters": n_clusters,
        "pca_components": 3, "clustering_epochs": 3, "batch_size": 8192,
        "clustering_space": "original", "clustering_metric": "euclidean",
        "clustering_normalization": "none", "clustering_whiten": False,
        "random_state": 42, "n_init": 3, "preview_oversampling": 2,
        "color_percentiles": [2, 98], "vector_dtype": "float32",
    }


def matches_source(metadata: dict, signature: dict, n_clusters: int = 8) -> bool:
    old = metadata.get("source_signature", {})
    return (
        metadata.get("version") == CACHE_VERSION
        and metadata.get("parameters") == processing_parameters(n_clusters)
        and all(old.get(key) == signature.get(key) for key in ("path", "size", "sha256"))
    )


def chinese_font_path() -> Path:
    """One explicit macOS CJK font shared by Pillow and Matplotlib."""
    configured = os.environ.get("GIVA_FONT_PATH")
    if configured:
        selected = Path(configured).expanduser().resolve()
        if not selected.is_file():
            raise RuntimeError(f"GIVA_FONT_PATH 指定的字体文件不存在: {selected}")
        return selected
    candidates = (
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("未找到 macOS 中文字体（STHeiti / PingFang / Hiragino Sans GB）")


def product_manifest(directory: Path) -> dict:
    return {name: {"bytes": (directory / name).stat().st_size} for name in REQUIRED_FILES}


def validate_products(directory: Path, metadata: dict) -> None:
    """Validate headers and file lengths without reading the full vector matrix."""
    import numpy as np
    import rasterio
    from PIL import Image

    count, width, height, bands = (int(metadata[key]) for key in ("valid_count", "width", "height", "bands"))
    total = width * height
    if not (2 <= count <= total <= 2 ** 32 and width > 0 and height > 0 and bands > 0):
        raise ValueError("缓存栅格尺寸或有效像元数异常")
    if metadata.get("total_pixels") != total or metadata.get("sampled") is not False:
        raise ValueError("缓存全量像元信息异常")
    clusters = metadata["cluster_count"]
    if metadata.get("parameters") != processing_parameters(clusters):
        raise ValueError("缓存处理参数已经过期")
    if (metadata.get("pca_fit_count") != count or metadata.get("clustering_fit_count") != count
            or sum(metadata["cluster_counts"]) != count or len(metadata["cluster_counts"]) != clusters):
        raise ValueError("缓存分析数量不一致")
    if (metadata.get("clustering_components") != bands
            or metadata.get("clustering_space") != "original"
            or metadata.get("clustering_metric") != "euclidean"
            or metadata.get("clustering_normalization") != "none"
            or metadata.get("clustering_whiten") is not False):
        raise ValueError("缓存聚类必须使用未经归一化或白化的全部原始维度")
    products = metadata.get("products", {})
    if set(products) != set(REQUIRED_FILES):
        raise ValueError("缓存产品清单不完整")
    for name in REQUIRED_FILES:
        product = directory / name
        size = product.stat().st_size
        if size <= 0 or size != products[name].get("bytes"):
            raise ValueError(f"缓存文件缺失或长度异常: {name}")
    for name, stride in (("positions.f32", 12), ("colors.u8", 3), ("labels.u8", 1), ("pixel_ids.u32", 4)):
        if (directory / name).stat().st_size != count * stride:
            raise ValueError(f"缓存二进制长度异常: {name}")
    arrays = {
        "vectors.npy": ((total, bands), np.dtype("float32")),
        "norms.npy": ((count,), np.dtype("float32")),
        "lookup.npy": ((height, width), np.dtype("int32")),
    }
    for name, (shape, dtype) in arrays.items():
        array = np.load(directory / name, mmap_mode="r", allow_pickle=False)
        try:
            if array.shape != shape or array.dtype != dtype:
                raise ValueError(f"缓存数组形状异常: {name}")
            if array.offset + array.nbytes != (directory / name).stat().st_size:
                raise ValueError(f"缓存数组长度异常: {name}")
        finally:
            array._mmap.close()
    for name in ("pca.png", "clusters.png", OVERVIEW_NAME, f"{VARIANCE_STEM}.png"):
        with Image.open(directory / name) as image:
            if name in {"pca.png", "clusters.png"} and list(image.size) != metadata["preview_size"]:
                raise ValueError(f"缓存图像尺寸异常: {name}")
            image.verify()
    for name, expected_count in (("clusters.tif", 1), ("pca_rgb.tif", 3)):
        with rasterio.open(directory / name) as image:
            if (image.width, image.height, image.count) != (width, height, expected_count):
                raise ValueError(f"缓存 GeoTIFF 尺寸异常: {name}")
            if str(image.crs) != metadata["crs"] or tuple(image.transform) != tuple(metadata["transform"]):
                raise ValueError(f"缓存 GeoTIFF 投影异常: {name}")
    spectrum = json.loads((directory / f"{VARIANCE_STEM}.json").read_text(encoding="utf-8"))
    if len(spectrum["eigenvalue"]) != bands or len(spectrum["cumulative_ratio"]) != bands:
        raise ValueError("解释方差维度异常")


def read_cache_metadata(directory: Path) -> dict | None:
    directory = Path(directory)
    try:
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        signature = metadata["source_signature"]
        if (metadata.get("version") != CACHE_VERSION or metadata.get("dataset_id") != directory.name
                or directory.name.startswith(".") or Path(signature["path"]).is_absolute()
                or not re.fullmatch(r"[0-9a-f]{64}", signature.get("sha256", ""))):
            return None
        validate_products(directory, metadata)
        return metadata
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return None
