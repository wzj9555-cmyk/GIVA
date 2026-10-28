import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import type { Pixel, RasterMeta } from "./types";
import { RASTER_API, STATIC_DEMO, rasterUrl } from "./types";
import { applyClusterVisibility, decodeClusterLabels } from "./previewMask";
import { wgsToGcj, gcjToWgs } from "./gcj";

interface Props { datasetId: string; meta: RasterMeta; mode: "cluster" | "pca"; selected: Pixel | null; focused: Pixel | null; similar: Pixel[]; visible: boolean[]; mask: Uint8Array; basemap: boolean; rasterOpacity: number; onLocate: (lng: number, lat: number) => void }
interface Preview { width: number; height: number; rgba: Uint8ClampedArray }
interface MaskPreviews { cluster: Preview; pca: Preview; labels: Int16Array }
const imageCoords = (meta: RasterMeta) => meta.image_coordinates.map(([lng, lat]) => wgsToGcj(lng, lat)) as [[number, number], [number, number], [number, number], [number, number]];
const allVisible = (p: Props) => p.meta.palette.every((_, label) => p.visible[label]);
const markerData = (p: Props) => ({ type: "FeatureCollection" as const, features: [...p.similar.filter(item => p.mask[item.index]).map(item => ({ type: "Feature" as const, geometry: { type: "Point" as const, coordinates: wgsToGcj(item.lng, item.lat) }, properties: { selected: false } })), ...(p.selected && p.mask[p.selected.index] ? [{ type: "Feature" as const, geometry: { type: "Point" as const, coordinates: wgsToGcj(p.selected.lng, p.selected.lat) }, properties: { selected: true } }] : [])] });
const focusData = (p: Props) => ({ type: "FeatureCollection" as const, features: p.focused && p.mask[p.focused.index] ? [{ type: "Feature" as const, geometry: { type: "Point" as const, coordinates: wgsToGcj(p.focused.lng, p.focused.lat) }, properties: {} }] : [] });
const focusOnMap = (map: maplibregl.Map, p: Props) => { if (p.focused && p.mask[p.focused.index]) map.panTo(wgsToGcj(p.focused.lng, p.focused.lat), { duration: 450 }); };

async function loadPreview(url: string, signal: AbortSignal): Promise<Preview> {
  const response = await fetch(url, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`栅格预览加载失败 (${response.status})`);
  const bitmap = await createImageBitmap(await response.blob());
  try {
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width; canvas.height = bitmap.height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("浏览器无法创建栅格筛选掩膜");
    context.drawImage(bitmap, 0, 0);
    return { width: canvas.width, height: canvas.height, rgba: context.getImageData(0, 0, canvas.width, canvas.height).data };
  } finally { bitmap.close(); }
}

export function RasterMap(props: Props) {
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const syncPreviewRef = useRef<(() => void) | null>(null);
  const syncBasemapRef = useRef<(() => void) | null>(null);
  const aerialBoundsRef = useRef<[number, number, number, number] | null>(null);
  const propsRef = useRef(props); propsRef.current = props;
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [aerialError, setAerialError] = useState<string | null>(null);
  const reset = () => { const b = propsRef.current.meta.bounds; mapRef.current?.fitBounds([wgsToGcj(b[0], b[1]), wgsToGcj(b[2], b[3])], { padding: 40, duration: 500 }); };
  useEffect(() => {
    if (!host.current) return;
    const map = new maplibregl.Map({ container: host.current, style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#122332" } }] }, center: wgsToGcj((props.meta.bounds[0] + props.meta.bounds[2]) / 2, (props.meta.bounds[1] + props.meta.bounds[3]) / 2), zoom: 14, maxZoom: 23, attributionControl: false, preserveDrawingBuffer: true });
    mapRef.current = map;
    let disposed = false;
    let revision = 0;
    let previews: Promise<MaskPreviews> | null = null;
    let preparedPreviews: MaskPreviews | null = null;
    let canvasDirty = false;
    const syncBasemap = () => {
      if (!disposed && map.getLayer("aerial")) map.setLayoutProperty("aerial", "visibility", propsRef.current.basemap ? "visible" : "none");
    };
    syncBasemapRef.current = syncBasemap;
    const canvas = document.createElement("canvas");
    [canvas.width, canvas.height] = props.meta.preview_size;
    const context = canvas.getContext("2d");
    const controller = new AbortController();
    const source = () => map.getSource("full-raster") as maplibregl.CanvasSource | undefined;
    const refreshCanvas = () => {
      const currentSource = source();
      if (!currentSource || disposed || !canvasDirty) return;
      // Keep playing through one render so prepare() uploads after visible tiles
      // are known. pause() in the render handler freezes that latest canvas.
      currentSource.play();
      map.triggerRepaint();
    };
    const imageInView = () => {
      const view = map.getBounds();
      const bounds = props.meta.bounds;
      const [west, south] = wgsToGcj(bounds[0], bounds[1]);
      const [east, north] = wgsToGcj(bounds[2], bounds[3]);
      const wrap = Math.round(((view.getWest() + view.getEast()) / 2 - (west + east) / 2) / 360) * 360;
      return east + wrap >= view.getWest() && west + wrap <= view.getEast() && north >= view.getSouth() && south <= view.getNorth();
    };
    const drawPreview = (prepared: MaskPreviews, current: Props) => {
      if (!context) throw new Error("浏览器无法创建栅格筛选掩膜");
      const preview = prepared[current.mode];
      const pixels = context.createImageData(canvas.width, canvas.height);
      pixels.data.set(allVisible(current) ? preview.rgba : applyClusterVisibility(preview.rgba, prepared.labels, current.visible));
      context.putImageData(pixels, 0, 0);
      canvasDirty = true;
      refreshCanvas();
    };
    const preparePreviews = () => {
      if (!previews) {
        previews = Promise.all([
          loadPreview(rasterUrl("files/clusters.png", props.datasetId), controller.signal),
          loadPreview(rasterUrl("files/pca.png", props.datasetId), controller.signal),
        ]).then(([cluster, pca]) => {
          if (cluster.width !== pca.width || cluster.height !== pca.height || cluster.width !== canvas.width || cluster.height !== canvas.height) throw new Error("栅格预览与元数据尺寸不一致，请重新生成缓存");
          preparedPreviews = { cluster, pca, labels: decodeClusterLabels(cluster.rgba, props.meta.palette) };
          return preparedPreviews;
        }).catch(error => { previews = null; throw error; });
      }
      return previews;
    };
    const syncPreview = async () => {
      const token = ++revision;
      if (!source() || disposed) return;
      const current = propsRef.current;
      if (current.datasetId !== props.datasetId || current.meta !== props.meta) return;
      setPreviewError(null);
      try {
        if (!context) throw new Error("浏览器无法创建栅格筛选掩膜");
        if (!current.meta.palette.some((_, label) => current.visible[label])) {
          context.clearRect(0, 0, canvas.width, canvas.height);
          canvasDirty = true;
          refreshCanvas();
          return;
        }
        if (preparedPreviews) { drawPreview(preparedPreviews, current); return; }
        const prepared = await preparePreviews();
        if (disposed || token !== revision || mapRef.current !== map) return;
        const latest = propsRef.current;
        if (latest.datasetId !== props.datasetId || latest.meta !== props.meta) return;
        drawPreview(prepared, latest);
      } catch (error) {
        if (!disposed && token === revision) setPreviewError((error as Error).message);
      }
    };
    syncPreviewRef.current = () => { void syncPreview(); };
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    map.on("load", () => {
      if (disposed) return;
      const latest = propsRef.current;
      map.addSource("full-raster", { type: "canvas", canvas, animate: false, coordinates: imageCoords(latest.meta) });
      map.addLayer({ id: "full-raster", type: "raster", source: "full-raster", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0, "raster-opacity": latest.rasterOpacity } });
      {
        const aerialBase = `${import.meta.env.BASE_URL}site-data/aerial`;
        const metadataUrl = STATIC_DEMO ? `${aerialBase}/metadata.json` : `${RASTER_API}/raster/aerial`;
        fetch(metadataUrl, { signal: controller.signal }).then(response => {
          if (!response.ok) throw new Error("无法读取航空底图配置");
          return response.json() as Promise<{ available: boolean; version?: string; maxzoom?: number; bounds?: [number, number, number, number]; minzoom?: number }>;
        }).then(info => {
          if (disposed || !map.getLayer("full-raster")) return;
          if (!info.available || !info.bounds) { setAerialError("航空底图未准备或配置缺失"); return; }
          aerialBoundsRef.current = info.bounds;
          const [west, south, east, north] = info.bounds;
          map.setMaxBounds([[west, south], [east, north]]);
          constrainAerialZoom();
          map.addSource("aerial", { type: "raster", tileSize: 256, maxzoom: info.maxzoom ?? 20,
            bounds: info.bounds, minzoom: STATIC_DEMO ? (info.minzoom ?? 12) : 0,
            tiles: [STATIC_DEMO ? `${aerialBase}/{z}/{x}/{y}.jpg` : `${RASTER_API}/raster/aerial/tiles/{z}/{x}/{y}.png?v=${encodeURIComponent(info.version ?? "")}`],
            attribution: "2025 航空影像" });
          map.addLayer({ id: "aerial", type: "raster", source: "aerial",
            layout: { visibility: propsRef.current.basemap ? "visible" : "none" },
            paint: { "raster-fade-duration": 0 } }, "full-raster");
          setAerialError(null);
        }).catch(error => { if (!disposed && error.name !== "AbortError") setAerialError("航空底图加载失败，请检查网络或本地服务"); });
      }
      map.addSource("selected", { type: "geojson", data: markerData(latest) });
      map.addLayer({ id: "selection", type: "circle", source: "selected", paint: { "circle-radius": ["case", ["get", "selected"], 7, 4], "circle-color": ["case", ["get", "selected"], "#ffffff", "#ffdb68"], "circle-stroke-width": 2, "circle-stroke-color": "#142739" } });
      map.addSource("focused", { type: "geojson", data: focusData(latest) });
      map.addLayer({ id: "focus-ring", type: "circle", source: "focused", paint: { "circle-radius": 12, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 3, "circle-stroke-color": "#ff9354" } });
      map.addLayer({ id: "focus-center", type: "circle", source: "focused", paint: { "circle-radius": 4, "circle-color": "#ff9354", "circle-stroke-width": 1, "circle-stroke-color": "#ffffff" } });
      const b = latest.meta.bounds;
      map.fitBounds([wgsToGcj(b[0], b[1]), wgsToGcj(b[2], b[3])], { padding: 40, duration: 0 });
      focusOnMap(map, latest);
      syncBasemap();
      void syncPreview();
    });
    map.on("error", event => {
      if ((event as typeof event & { sourceId?: string }).sourceId === "aerial") setAerialError("航空底图加载失败，请检查网络或本地服务");
      else console.error(event.error);
    });
    map.on("render", () => {
      const currentSource = source();
      if (!currentSource || !canvasDirty || disposed) return;
      // Outside the image there may be no tiles to upload. Retain the dirty
      // state; the next move starts one upload before drawing the image again.
      if (!imageInView()) { currentSource.pause(); return; }
      if (map.isSourceLoaded("full-raster")) { currentSource.pause(); canvasDirty = false; }
    });
    map.on("move", refreshCanvas);
    map.on("resize", refreshCanvas);
    map.on("click", event => propsRef.current.onLocate(...gcjToWgs(event.lngLat.lng, event.lngLat.lat)));
    const constrainAerialZoom = () => {
      const bounds = aerialBoundsRef.current;
      if (!bounds || !host.current) return;
      const [west, south, east, north] = bounds;
      const nw = maplibregl.MercatorCoordinate.fromLngLat([west, north]);
      const se = maplibregl.MercatorCoordinate.fromLngLat([east, south]);
      const width = host.current.clientWidth, height = host.current.clientHeight;
      if (!width || !height || se.x <= nw.x || se.y <= nw.y) return;
      const minZoom = Math.log2(Math.max(width / (512 * (se.x - nw.x)), height / (512 * (se.y - nw.y))));
      map.setMinZoom(Math.max(0, Math.min(23, minZoom + 0.001)));
    };
    const observer = new ResizeObserver(() => { map.resize(); constrainAerialZoom(); }); observer.observe(host.current);
    return () => { disposed = true; revision++; controller.abort(); syncPreviewRef.current = null; syncBasemapRef.current = null; aerialBoundsRef.current = null; observer.disconnect(); map.remove(); if (mapRef.current === map) mapRef.current = null; };
  }, [props.meta, props.datasetId]);
  useEffect(() => { syncPreviewRef.current?.(); }, [props.mode, props.visible, props.mask, props.datasetId]);
  useEffect(() => {
    const map = mapRef.current; if (!map) return;
    (map.getSource("selected") as maplibregl.GeoJSONSource | undefined)?.setData(markerData(props));
    (map.getSource("focused") as maplibregl.GeoJSONSource | undefined)?.setData(focusData(props));
  }, [props.selected, props.focused, props.similar, props.visible, props.mask]);
  useEffect(() => {
    const map = mapRef.current; if (!map) return;
    if (props.selected && props.mask[props.selected.index]) { const { lng, lat } = props.selected; const point = wgsToGcj(lng, lat); if (!map.getBounds().contains(point)) map.panTo(point); }
  }, [props.selected]);
  useEffect(() => {
    const map = mapRef.current; if (map?.getSource("focused")) focusOnMap(map, props);
    return () => { if (map && mapRef.current === map) map.stop(); };
  }, [props.focused]);
  useEffect(() => { syncBasemapRef.current?.(); }, [props.basemap]);
  useEffect(() => { const map = mapRef.current; if (map?.getLayer("full-raster")) map.setPaintProperty("full-raster", "raster-opacity", props.rasterOpacity); }, [props.rasterOpacity]);
  return <div className="raster-map" ref={host}><button className="raster-float-button" onClick={reset}>完整范围</button><div className="raster-map-note" role={previewError || aerialError ? "status" : undefined} aria-live="polite">{previewError && <div role="alert">{previewError}</div>}{props.basemap && aerialError && <div role="alert">{aerialError}</div>}<div>{props.meta.resolution[0]} m 原始像元 · 类别筛选与三维同步</div></div></div>;
}
