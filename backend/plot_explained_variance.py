"""Create a scree plot and cumulative explained-variance table for the raster embedding."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

from raster_common import (
    DEFAULT_CACHE_DIR, VARIANCE_STEM,
    chinese_font_path, read_cache_metadata, resolve_source_path,
)


def compute_spectrum(vectors: np.ndarray, valid_count: int, block_size: int = 8192) -> np.ndarray:
    dimension = vectors.shape[1]
    sums = np.zeros(dimension, dtype=np.float64)
    second = np.zeros((dimension, dimension), dtype=np.float64)
    for start in range(0, valid_count, block_size):
        block = np.asarray(vectors[start:min(valid_count, start + block_size)], dtype=np.float64)
        sums += block.sum(axis=0)
        second += block.T @ block
    covariance = (second - np.outer(sums, sums) / valid_count) / (valid_count - 1)
    eigenvalues = np.linalg.eigvalsh(covariance)[::-1]
    return np.maximum(eigenvalues, 0.0)


def compute_raster_spectrum(path: Path) -> tuple[np.ndarray, int]:
    import rasterio

    with rasterio.open(path) as source:
        dimension = source.count
        sums = np.zeros(dimension, dtype=np.float64)
        second = np.zeros((dimension, dimension), dtype=np.float64)
        valid_count = 0
        for _, window in source.block_windows(1):
            data = source.read(window=window, masked=True)
            mask = np.ma.getmaskarray(data).any(axis=0)
            values = np.asarray(data.data[:, ~mask].T, dtype=np.float64)
            finite = np.isfinite(values).all(axis=1)
            values = values[finite]
            if not len(values):
                continue
            sums += values.sum(axis=0)
            second += values.T @ values
            valid_count += len(values)
    if valid_count < 2:
        raise ValueError("Raster must contain at least two valid pixels")
    covariance = (second - np.outer(sums, sums) / valid_count) / (valid_count - 1)
    eigenvalues = np.linalg.eigvalsh(covariance)[::-1]
    return np.maximum(eigenvalues, 0.0), valid_count


def make_plot(eigenvalues: np.ndarray, output: Path, title: str, valid_count: int) -> dict:
    ratios = eigenvalues / max(float(eigenvalues.sum()), 1e-15)
    cumulative = np.cumsum(ratios)
    dimensions = np.arange(1, len(ratios) + 1)
    thresholds = {}
    for threshold in (0.8, 0.9, 0.95):
        index = int(np.searchsorted(cumulative, threshold))
        thresholds[str(int(threshold * 100))] = index + 1 if index < len(ratios) else None

    font_path = str(chinese_font_path())
    font_manager.fontManager.addfont(font_path)
    font_name = font_manager.FontProperties(fname=font_path).get_name()
    plt.rcParams.update({"font.family": font_name, "axes.unicode_minus": False})
    figure, (axis_ratio, axis_cumulative) = plt.subplots(1, 2, figsize=(15, 6), dpi=180)
    figure.patch.set_facecolor("#101b28")
    for axis in (axis_ratio, axis_cumulative):
        axis.set_facecolor("#152433")
        axis.tick_params(colors="#b8cbd8", labelsize=9)
        for spine in axis.spines.values():
            spine.set_color("#385064")
        axis.grid(color="#385064", alpha=0.35, linewidth=0.7)
        axis.set_xlabel("主成分维度", color="#dce8ef")
    axis_ratio.bar(dimensions, ratios * 100, color="#63cdb4", width=1.0, alpha=0.85)
    axis_ratio.set_title("单个主成分解释率", color="#edf5f7", loc="left", pad=12)
    axis_ratio.set_ylabel("解释率 (%)", color="#dce8ef")
    axis_ratio.set_xlim(0, len(ratios) + 1)
    axis_ratio.set_ylim(0, max(float(ratios[0] * 100 * 1.12), 1))
    leading = "\n".join(f"PC{i + 1}: {ratio * 100:.2f}%" for i, ratio in enumerate(ratios[:3]))
    axis_ratio.text(0.02, 0.96, leading, transform=axis_ratio.transAxes, va="top", color="#c8dce6", fontsize=10)
    axis_cumulative.plot(dimensions, cumulative * 100, color="#f2b86b", linewidth=2.2)
    axis_cumulative.fill_between(dimensions, cumulative * 100, color="#f2b86b", alpha=0.12)
    axis_cumulative.set_title("累计解释率", color="#edf5f7", loc="left", pad=12)
    axis_cumulative.set_ylabel("累计解释率 (%)", color="#dce8ef")
    axis_cumulative.set_xlim(0.5, max(len(ratios), 1.5))
    axis_cumulative.set_ylim(0, 102)
    for threshold, color in ((80, "#63cdb4"), (90, "#7795ed"), (95, "#e784a6")):
        k = thresholds[str(threshold)]
        if k is None:
            continue
        value = cumulative[k - 1] * 100
        axis_cumulative.axhline(threshold, color=color, linewidth=1, alpha=0.7)
        axis_cumulative.axvline(k, color=color, linewidth=1, alpha=0.6, linestyle="--")
        axis_cumulative.scatter([k], [value], color=color, s=30, zorder=4)
        axis_cumulative.annotate(f"{threshold}%: {k} 维", (k, value), xytext=(8, -16 if threshold == 95 else 8), textcoords="offset points", color=color, fontsize=9)
    first = min(3, len(ratios))
    clustering = min(32, len(ratios))
    axis_cumulative.text(0.02, 0.04, f"前 {first} 维累计: {cumulative[first - 1] * 100:.2f}%\n前 {clustering} 维累计: {cumulative[clustering - 1] * 100:.2f}%", transform=axis_cumulative.transAxes, color="#c8dce6", fontsize=10)
    figure.suptitle(title, color="#f0f6fa", fontsize=16, x=0.06, ha="left", va="top", y=0.99)
    figure.text(0.06, 0.92, f"基于全部 {valid_count:,} 个有效像元的 {len(ratios)} 维融合表征", color="#9db8c9", fontsize=10, va="top")
    figure.tight_layout(rect=(0, 0.02, 1, 0.84))
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, facecolor=figure.get_facecolor(), bbox_inches="tight")
    plt.close(figure)
    return {"dimension": dimensions.tolist(), "eigenvalue": eigenvalues.tolist(), "explained_ratio": ratios.tolist(), "cumulative_ratio": cumulative.tolist(), "threshold_dimensions": thresholds}


def write_explained_variance(eigenvalues: np.ndarray, directory: Path, valid_count: int,
                             output: Path | None = None) -> dict:
    output = Path(output) if output is not None else Path(directory) / f"{VARIANCE_STEM}.png"
    result = make_plot(eigenvalues, output, f"{len(eigenvalues)} 维表征的解释方差曲线", valid_count)
    with output.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["dimension", "eigenvalue", "explained_ratio", "cumulative_ratio"])
        writer.writerows(zip(result["dimension"], result["eigenvalue"], result["explained_ratio"], result["cumulative_ratio"]))
    output.with_suffix(".json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--input", type=Path, help="Numpy array containing valid pixel vectors")
    inputs.add_argument("--raster", type=Path, help="Multi-band GeoTIFF read directly in blocks")
    inputs.add_argument("--dataset-id", help="读取 cache/<dataset_id>，输出到 --output")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--valid-count", type=int, help="Required when --input is used")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.dataset_id:
        if Path(args.dataset_id).name != args.dataset_id or args.dataset_id.startswith("."):
            parser.error("--dataset-id 必须是缓存子目录名称")
        directory = args.cache_dir / args.dataset_id
        metadata = read_cache_metadata(directory)
        if metadata is None:
            parser.error(f"缓存缺失或不完整: {directory}")
        vectors = np.load(directory / "vectors.npy", mmap_mode="r")
        valid_count = metadata["valid_count"]
        spectrum = compute_spectrum(vectors, valid_count)
    elif args.raster:
        spectrum, valid_count = compute_raster_spectrum(args.raster)
    elif args.input:
        if args.valid_count is None:
            parser.error("--valid-count is required when --input is used")
        vectors = np.load(args.input, mmap_mode="r")
        valid_count = args.valid_count
        if not 2 <= valid_count <= len(vectors):
            parser.error("--valid-count 必须在 2 和向量行数之间")
        spectrum = compute_spectrum(vectors, valid_count)
    else:
        candidates = [path for path in args.cache_dir.iterdir() if path.is_dir() and read_cache_metadata(path)] if args.cache_dir.exists() else []
        if len(candidates) != 1:
            parser.error("请用 --dataset-id、--raster 或 --input 选择数据；缓存中有多个数据集时必须明确选择")
        metadata = read_cache_metadata(candidates[0])
        spectrum, valid_count = compute_raster_spectrum(resolve_source_path(metadata))
    result = write_explained_variance(spectrum, args.output.parent, valid_count, args.output)
    csv_path = args.output.with_suffix(".csv")
    json_path = args.output.with_suffix(".json")
    print(json.dumps({"plot": str(args.output), "csv": str(csv_path), "json": str(json_path), "valid_count": valid_count, "threshold_dimensions": result["threshold_dimensions"], "pc1_to_pc3_percent": sum(result["explained_ratio"][:3]) * 100, "pc1_to_pc32_percent": sum(result["explained_ratio"][:32]) * 100}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
