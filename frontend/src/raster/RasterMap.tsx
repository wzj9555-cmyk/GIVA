import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import type { Pixel, RasterMeta } from "./types";
import { rasterUrl } from "./types";
import { applyClusterVisibility, decodeClusterLabels } from "./previewMask";
import { initialSatelliteStatus, updateSatelliteStatus } from "./satelliteStatus";

interface Props { datasetId: string; meta: RasterMeta; mode: "cluster" | "pca"; selected: Pixel | null; focused: Pixel | null; similar: Pixel[]; visible: boolean[]; basemap: boolean; rasterOpacity: number; onLocate: (lng: number, lat: number) => void }
interface Preview { width: number; height: number; rgba: Uint8ClampedArray }
interface MaskPreviews { cluster: Preview; pca: Preview; labels: Int16Array }
const imageCoords = (meta: RasterMeta) => meta.image_coordinates as [[number, number], [number, number], [number, number], [number, number]];
const allVisible = (p: Props) => p.meta.palette.every((_, label) => p.visible[label]);
const markerData = (p: Props) => ({ type: "FeatureCollection" as const, features: [...p.similar.filter(item => p.visible[item.cluster]).map(item => ({ type: "Feature" as const, geometry: { type: "Point" as const, coordinates: [item.lng, item.lat] }, properties: { selected: false } })), ...(p.selected && p.visible[p.selected.cluster] ? [{ type: "Feature" as const, geometry: { type: "Point" as const, coordinates: [p.selected.lng, p.selected.lat] }, properties: { selected: true } }] : [])] });
const focusData = (p: Props) => ({ type: "FeatureCollection" as const, features: p.focused && p.visible[p.focused.cluster] ? [{ type: "Feature" as const, geometry: { type: "Point" as const, coordinates: [p.focused.lng, p.focused.lat] }, properties: {} }] : [] });
const focusOnMap = (map: maplibregl.Map, p: Props) => { if (p.focused && p.visible[p.focused.cluster]) map.panTo([p.focused.lng, p.focused.lat], { duration: 450 }); };
const satelliteTiles = ["https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"];

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
  const propsRef = useRef(props); propsRef.current = props;
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [satelliteStatus, setSatelliteStatus] = useState(initialSatelliteStatus(false));
  const reset = () => { const b = propsRef.current.meta.bounds; mapRef.current?.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 500 }); };
  useEffect(() => {
    if (!host.current) return;
    const map = new maplibregl.Map({ container: host.current, style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#122332" } }] }, center: [(props.meta.bounds[0] + props.meta.bounds[2]) / 2, (props.meta.bounds[1] + props.meta.bounds[3]) / 2], zoom: 14, maxZoom: 23, attributionControl: false, preserveDrawingBuffer: true });
    mapRef.current = map;
    let disposed = false;
    let revision = 0;
    let previews: Promise<MaskPreviews> | null = null;
    let preparedPreviews: MaskPreviews | null = null;
    let canvasDirty = false;
    let satelliteState = initialSatelliteStatus(propsRef.current.basemap);
    setSatelliteStatus(satelliteState);
    const updateSatellite = (event: Parameters<typeof updateSatelliteStatus>[1]) => {
      if (disposed) return;
      satelliteState = updateSatelliteStatus(satelliteState, event);
      setSatelliteStatus(satelliteState);
    };
    const addSatellite = () => {
      map.addSource("satellite", { type: "raster", tiles: satelliteTiles, tileSize: 256, maxzoom: 19, attribution: '© <a href="https://www.google.com/maps" target="_blank" rel="noopener noreferrer">Google</a> · 卫星影像' });
      map.addLayer({ id: "satellite", type: "raster", source: "satellite", layout: { visibility: propsRef.current.basemap ? "visible" : "none" }, paint: { "raster-opacity": 1 } }, map.getLayer("full-raster") ? "full-raster" : undefined);
    };
    const syncBasemap = () => {
      if (disposed || !map.getLayer("satellite")) return;
      const enabled = propsRef.current.basemap;
      const retry = enabled && satelliteState.failed;
      updateSatellite({ type: "visibility", enabled });
      // setTiles() reloads healthy tiles but retains errored ones in MapLibre.
      // Recreate this source to give a failed request a fresh attempt.
      if (retry) { map.removeLayer("satellite"); map.removeSource("satellite"); addSatellite(); }
      map.setLayoutProperty("satellite", "visibility", enabled ? "visible" : "none");
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
      const [west, south, east, north] = props.meta.bounds;
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
      addSatellite();
      map.addSource("full-raster", { type: "canvas", canvas, animate: false, coordinates: imageCoords(latest.meta) });
      map.addLayer({ id: "full-raster", type: "raster", source: "full-raster", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0, "raster-opacity": latest.rasterOpacity } });
      map.addSource("selected", { type: "geojson", data: markerData(latest) });
      map.addLayer({ id: "selection", type: "circle", source: "selected", paint: { "circle-radius": ["case", ["get", "selected"], 7, 4], "circle-color": ["case", ["get", "selected"], "#ffffff", "#ffdb68"], "circle-stroke-width": 2, "circle-stroke-color": "#142739" } });
      map.addSource("focused", { type: "geojson", data: focusData(latest) });
      map.addLayer({ id: "focus-ring", type: "circle", source: "focused", paint: { "circle-radius": 12, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 3, "circle-stroke-color": "#ff9354" } });
      map.addLayer({ id: "focus-center", type: "circle", source: "focused", paint: { "circle-radius": 4, "circle-color": "#ff9354", "circle-stroke-width": 1, "circle-stroke-color": "#ffffff" } });
      const b = latest.meta.bounds;
      map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 0 });
      focusOnMap(map, latest);
      syncBasemap();
      void syncPreview();
    });
    map.on("sourcedataloading", event => { if (event.sourceId === "satellite") updateSatellite({ type: "loading" }); });
    map.on("sourcedata", event => {
      if (event.sourceId === "satellite") updateSatellite({ type: "data", tileLoaded: event.tile?.state === "loaded", complete: event.isSourceLoaded });
    });
    map.on("error", event => {
      if ((event as typeof event & { sourceId?: string }).sourceId === "satellite") updateSatellite({ type: "error" });
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
    map.on("click", event => propsRef.current.onLocate(event.lngLat.lng, event.lngLat.lat));
    const observer = new ResizeObserver(() => map.resize()); observer.observe(host.current);
    return () => { disposed = true; revision++; controller.abort(); syncPreviewRef.current = null; syncBasemapRef.current = null; observer.disconnect(); map.remove(); if (mapRef.current === map) mapRef.current = null; };
  }, [props.meta, props.datasetId]);
  useEffect(() => { syncPreviewRef.current?.(); }, [props.mode, props.visible, props.datasetId]);
  useEffect(() => {
    const map = mapRef.current; if (!map) return;
    (map.getSource("selected") as maplibregl.GeoJSONSource | undefined)?.setData(markerData(props));
    (map.getSource("focused") as maplibregl.GeoJSONSource | undefined)?.setData(focusData(props));
  }, [props.selected, props.focused, props.similar, props.visible]);
  useEffect(() => {
    const map = mapRef.current; if (!map) return;
    if (props.selected && props.visible[props.selected.cluster]) { const { lng, lat } = props.selected; if (!map.getBounds().contains([lng, lat])) map.panTo([lng, lat]); }
  }, [props.selected]);
  useEffect(() => {
    const map = mapRef.current; if (map?.getSource("focused")) focusOnMap(map, props);
    return () => { if (map && mapRef.current === map) map.stop(); };
  }, [props.focused]);
  useEffect(() => { syncBasemapRef.current?.(); }, [props.basemap]);
  useEffect(() => { const map = mapRef.current; if (map?.getLayer("full-raster")) map.setPaintProperty("full-raster", "raster-opacity", props.rasterOpacity); }, [props.rasterOpacity]);
  const satelliteNote = !props.basemap ? null : satelliteStatus.phase === "error" ? "卫星底图加载失败，请检查网络后重新开启" : satelliteStatus.phase === "loading" ? "卫星底图加载中…" : null;
  return <div className="raster-map" ref={host}><button className="raster-float-button" onClick={reset}>完整范围</button><div className="raster-map-note" role={previewError || satelliteNote ? "status" : undefined} aria-live="polite">{previewError && <div role="alert">{previewError}</div>}{satelliteNote && <div role={satelliteStatus.phase === "error" ? "alert" : undefined}>{satelliteNote}</div>}<div>{props.meta.resolution[0]} m 原始像元 · 类别筛选与三维同步</div></div></div>;
}
