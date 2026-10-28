/** Decode the nearest-neighbour cluster PNG onto its own display grid.
 * Both preview PNGs share this grid; transparent cells are always NoData.
 */
export function decodeClusterLabels(rgba: Uint8ClampedArray | Uint8Array, palette: string[]): Int16Array {
  if (rgba.length % 4) throw new Error("聚类预览的像素长度无效");
  const colors = new Map<number, number>();
  palette.forEach((color, label) => {
    if (!/^#[0-9a-f]{6}$/i.test(color)) throw new Error("聚类调色板格式无效");
    const rgb = Number.parseInt(color.slice(1), 16);
    if (colors.has(rgb)) throw new Error("聚类调色板包含重复颜色");
    colors.set(rgb, label);
  });
  const labels = new Int16Array(rgba.length / 4).fill(-1);
  for (let pixel = 0; pixel < labels.length; pixel++) {
    const offset = pixel * 4;
    if (!rgba[offset + 3]) continue;
    const label = colors.get((rgba[offset] << 16) | (rgba[offset + 1] << 8) | rgba[offset + 2]);
    if (label === undefined) throw new Error("聚类预览颜色与调色板不一致，请重新生成缓存");
    labels[pixel] = label;
  }
  return labels;
}

/** Preserve the selected preview's RGB values while hiding excluded classes. */
export function applyClusterVisibility(rgba: Uint8ClampedArray | Uint8Array, labels: Int16Array, visible: boolean[]): Uint8ClampedArray {
  if (rgba.length !== labels.length * 4) throw new Error("栅格预览与类别掩膜尺寸不一致");
  const masked = new Uint8ClampedArray(rgba);
  for (let pixel = 0; pixel < labels.length; pixel++) {
    const label = labels[pixel];
    if (label < 0 || !visible[label]) masked[pixel * 4 + 3] = 0;
  }
  return masked;
}
