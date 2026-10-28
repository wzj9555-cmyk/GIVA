export interface RasterMeta {
  dataset_id?: string;
  name: string; width: number; height: number; bands: number; valid_count: number;
  total_pixels: number; resolution: number[]; crs: string; bounds: [number, number, number, number];
  image_coordinates: [number, number][]; palette: string[]; cluster_counts: number[];
  preview_size: [number, number];
  pca_explained_ratio: number[]; elapsed_seconds: number; sampled: boolean;
}
export interface RasterArrays { positions: Float32Array; colors: Uint8Array; labels: Uint8Array; pixelIds: Uint32Array }
export interface Pixel { index: number; row: number; col: number; pixel_id?: number; lng: number; lat: number; cluster: number; norm: number; vector_preview: number[]; bands: number; score?: number }
export interface Similarity { query?: Pixel; searched_count: number; method?: string; items: Pixel[] }
export interface RasterDatasetOption { dataset_id: string; name: string; valid_count: number; bands: number; width: number; height: number; crs: string; sampled: boolean; directory: string }
export const RASTER_API = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");
export const STATIC_DEMO = import.meta.env.VITE_STATIC_DEMO === "1";
export const rasterUrl = (path: string, datasetId?: string) => STATIC_DEMO
  ? `${import.meta.env.BASE_URL}site-data/joint_2p5m_512d/${path.replace(/^files\//, "")}`
  : `${RASTER_API}/raster/${path}${datasetId ? `${path.includes("?") ? "&" : "?"}dataset_id=${encodeURIComponent(datasetId)}` : ""}`;
export async function getRasterJson<T>(path: string): Promise<T> {
  if (STATIC_DEMO) return (await import("./staticRaster")).staticRasterJson<T>(path);
  const response = await fetch(rasterUrl(path), { cache: "no-store" });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `请求失败 ${response.status}`); }
  return response.json() as Promise<T>;
}
