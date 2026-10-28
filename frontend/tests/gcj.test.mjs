import test from "node:test";
import assert from "node:assert/strict";
import { wgsToGcj, gcjToWgs } from "../src/raster/gcj.ts";

test("Shanghai display coordinates round-trip to the source coordinates", () => {
  const source = [121.606589, 31.189153];
  const displayed = wgsToGcj(...source);
  assert.ok(Math.abs(displayed[0] - source[0]) > .001);
  const returned = gcjToWgs(...displayed);
  assert.ok(Math.abs(returned[0] - source[0]) < 1e-8);
  assert.ok(Math.abs(returned[1] - source[1]) < 1e-8);
});

test("coordinates outside mainland China are unchanged", () => {
  assert.deepEqual(wgsToGcj(2.35, 48.85), [2.35, 48.85]);
});
