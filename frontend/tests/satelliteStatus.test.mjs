import test from "node:test";
import assert from "node:assert/strict";
import { initialSatelliteStatus, updateSatelliteStatus } from "../src/raster/satelliteStatus.ts";

test("satellite is ready only after an image tile has loaded", () => {
  let status = initialSatelliteStatus(true);
  status = updateSatelliteStatus(status, { type: "data", tileLoaded: false, complete: true });
  assert.equal(status.phase, "loading");
  status = updateSatelliteStatus(status, { type: "data", tileLoaded: true, complete: true });
  assert.equal(status.phase, "ready");
});

test("remaining successful tiles cannot conceal a failed satellite request", () => {
  let status = updateSatelliteStatus(initialSatelliteStatus(true), { type: "error" });
  status = updateSatelliteStatus(status, { type: "data", tileLoaded: true, complete: true });
  assert.equal(status.phase, "error");
  status = updateSatelliteStatus(status, { type: "loading" });
  assert.equal(status.phase, "error");
});

test("late events remain hidden while the basemap is switched off", () => {
  let status = updateSatelliteStatus(initialSatelliteStatus(true), { type: "visibility", enabled: false });
  status = updateSatelliteStatus(status, { type: "loading" });
  status = updateSatelliteStatus(status, { type: "error" });
  status = updateSatelliteStatus(status, { type: "data", tileLoaded: true, complete: true });
  assert.equal(status.phase, "off");
  assert.equal(status.failed, true);
});

test("enabling a failed source starts a new attempt with no stale success or error", () => {
  let status = updateSatelliteStatus(initialSatelliteStatus(true), { type: "error" });
  status = updateSatelliteStatus(status, { type: "visibility", enabled: false });
  status = updateSatelliteStatus(status, { type: "visibility", enabled: true });
  assert.deepEqual(status, initialSatelliteStatus(true));
  status = updateSatelliteStatus(status, { type: "data", tileLoaded: true, complete: true });
  assert.equal(status.phase, "ready");
});

test("a healthy cached satellite source can be shown again without reloading", () => {
  let status = updateSatelliteStatus(initialSatelliteStatus(true), { type: "data", tileLoaded: true, complete: true });
  status = updateSatelliteStatus(status, { type: "visibility", enabled: false });
  status = updateSatelliteStatus(status, { type: "visibility", enabled: true });
  assert.equal(status.phase, "ready");
});
