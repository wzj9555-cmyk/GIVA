export interface SatelliteStatus {
  enabled: boolean;
  phase: "off" | "loading" | "ready" | "error";
  hasTiles: boolean;
  failed: boolean;
}

type SatelliteEvent =
  | { type: "visibility"; enabled: boolean }
  | { type: "loading" }
  | { type: "data"; tileLoaded: boolean; complete: boolean }
  | { type: "error" };

export const initialSatelliteStatus = (enabled: boolean): SatelliteStatus => ({
  enabled, phase: enabled ? "loading" : "off", hasTiles: false, failed: false,
});

export function updateSatelliteStatus(state: SatelliteStatus, event: SatelliteEvent): SatelliteStatus {
  if (event.type === "visibility") {
    if (!event.enabled) return { ...state, enabled: false, phase: "off" };
    // A failed source is explicitly retried when it is enabled again.
    if (state.failed) return initialSatelliteStatus(true);
    return { ...state, enabled: true, phase: state.hasTiles ? "ready" : "loading" };
  }
  if (event.type === "error") return { ...state, failed: true, phase: state.enabled ? "error" : "off" };
  if (event.type === "loading") return state.enabled && !state.failed ? { ...state, phase: "loading" } : state;
  const hasTiles = state.hasTiles || event.tileLoaded;
  return {
    ...state, hasTiles,
    // MapLibre also considers errored tiles complete. A completion event alone
    // must never turn an access/network failure into a successful load.
    phase: !state.enabled ? "off" : state.failed ? "error" : event.complete && hasTiles ? "ready" : "loading",
  };
}
