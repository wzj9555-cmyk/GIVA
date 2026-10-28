"""Export one complete cached raster as public, static browser data."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT.parent / "cache" / "joint_2p5m_512d"
OUTPUT = ROOT / "site-data" / "joint_2p5m_512d"
ROWS = 16384  # 32 MiB at 512 Float32 dimensions; under GitHub's file limit.

def main():
    if OUTPUT.exists():
        raise SystemExit(f"输出目录已存在，请先手动移走：{OUTPUT}")
    sys.path.insert(0, str(ROOT / "backend"))
    from raster_common import read_cache_metadata
    meta = read_cache_metadata(CACHE)
    if not meta or meta['dataset_id'] != 'joint_2p5m_512d' or meta['bands'] != 512:
        raise SystemExit('joint 缓存不完整或不是预期的 512 维数据')
    vectors = np.load(CACHE / 'vectors.npy', mmap_mode='r')
    count, bands = meta['valid_count'], meta['bands']
    OUTPUT.mkdir(parents=True)
    for name in ['positions.f32', 'colors.u8', 'labels.u8', 'pixel_ids.u32', 'clusters.png', 'pca.png', 'clusters.tif', 'pca_rgb.tif', '全量栅格可视化总览.png']:
        shutil.copyfile(CACHE / name, OUTPUT / name)
    for source, target, dtype in [('norms.npy', 'norms.f32', '<f4'), ('lookup.npy', 'lookup.i32', '<i4')]:
        np.asarray(np.load(CACHE / source, mmap_mode='r'), dtype=dtype).tofile(OUTPUT / target)
    np.asarray(vectors[:count, :12], dtype='<f4').tofile(OUTPUT / 'vector_preview.f32')
    chunks = []
    for start in range(0, count, ROWS):
        end = min(count, start + ROWS)
        name = f'vectors-{start // ROWS:04d}.f32'
        data = np.asarray(vectors[start:end], dtype='<f4').tobytes(order='C')
        (OUTPUT / name).write_bytes(data)
        chunks.append({'name': name, 'start': start, 'rows': end-start, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        print(f'导出向量 {end:,}/{count:,}', flush=True)
    meta['web_vector_chunks'] = chunks
    (OUTPUT / 'metadata.json').write_text(json.dumps(meta, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
    print(f'公开数据已生成：{OUTPUT}', flush=True)

if __name__ == '__main__':
    main()
