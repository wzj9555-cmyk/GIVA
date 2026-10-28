import test from "node:test";
import assert from "node:assert/strict";
import { decodeClusterLabels, applyClusterVisibility } from "../src/raster/previewMask.ts";

const palette = ["#52c9b0", "#f2b86b", "#7795ed"];
const cluster = new Uint8ClampedArray([
  82, 201, 176, 255, 242, 184, 107, 255, 119, 149, 237, 255, 123, 45, 67, 0,
]);
const pca = new Uint8ClampedArray([
  10, 20, 30, 255, 40, 50, 60, 255, 70, 80, 90, 255, 0, 0, 0, 0,
]);

test("class labels come from cluster colors and preserve NoData", () => {
  assert.deepEqual([...decodeClusterLabels(cluster, palette)], [0, 1, 2, -1]);
});

for (const [mode, rgba] of [["cluster", cluster], ["pca", pca]]) {
  test(`${mode}: hiding a class clears only its alpha`, () => {
    const result = applyClusterVisibility(rgba, decodeClusterLabels(cluster, palette), [true, false, true]);
    const expected = new Uint8ClampedArray(rgba);
    expected[7] = 0;
    assert.deepEqual(result, expected);
    assert.notEqual(result, rgba);
  });
  test(`${mode}: all classes off is transparent, all restored matches original`, () => {
    const labels = decodeClusterLabels(cluster, palette);
    const empty = applyClusterVisibility(rgba, labels, [false, false, false]);
    assert.deepEqual([...empty].filter((_, i) => i % 4 === 3), [0, 0, 0, 0]);
    assert.deepEqual(applyClusterVisibility(rgba, labels, [true, true, true]), rgba);
  });
}

test("PCA pixels use cluster labels even when PCA colors match another class", () => {
  const pcaColors = new Uint8ClampedArray(cluster);
  pcaColors.set(cluster.slice(0, 4), 4);
  const result = applyClusterVisibility(pcaColors, decodeClusterLabels(cluster, palette), [true, false, true]);
  assert.equal(result[3], 255);
  assert.equal(result[7], 0);
});

test("mismatched previews and unknown/duplicate palette colors fail explicitly", () => {
  assert.throws(() => decodeClusterLabels(new Uint8Array([1, 2, 3]), palette));
  assert.throws(() => decodeClusterLabels(new Uint8Array([1, 2, 3, 255]), palette));
  assert.throws(() => decodeClusterLabels(cluster, [palette[0], palette[0]]));
  assert.throws(() => applyClusterVisibility(pca, new Int16Array(1), [true]));
});
