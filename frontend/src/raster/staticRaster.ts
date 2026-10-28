import proj4 from "proj4";
import type { Pixel, RasterMeta, Similarity } from "./types";
import { rasterUrl } from "./types";

interface Chunk { name: string; start: number; rows: number; bytes: number; sha256: string }
interface StaticMeta extends RasterMeta { dataset_id: string; transform: number[]; web_vector_chunks: Chunk[] }
interface StaticData { meta: StaticMeta; ids: Uint32Array; labels: Uint8Array; norms: Float32Array; lookup: Int32Array; previews: Float32Array }
const projection = proj4("+proj=utm +zone=51 +datum=WGS84 +units=m +no_defs");
let dataPromise: Promise<StaticData> | null = null;
let currentWorker: Worker | null = null;
let rejectSearch: ((error: Error) => void) | null = null;

async function bytes(name: string): Promise<ArrayBuffer> {
  const response = await fetch(rasterUrl(`files/${name}`));
  if (!response.ok) throw new Error(`无法读取公开数据 ${name} (${response.status})`);
  return response.arrayBuffer();
}

async function data(): Promise<StaticData> {
  if (!dataPromise) dataPromise = (async () => {
    const response = await fetch(rasterUrl("files/metadata.json"));
    if (!response.ok) throw new Error("无法读取公开数据集元信息");
    const meta = await response.json() as StaticMeta;
    if (meta.crs !== "EPSG:32651" || meta.dataset_id !== "joint_2p5m_512d") throw new Error("公开数据集坐标系或名称不匹配");
    const [ids, labels, norms, lookup, previews] = await Promise.all(["pixel_ids.u32", "labels.u8", "norms.f32", "lookup.i32", "vector_preview.f32"].map(bytes));
    if (ids.byteLength !== meta.valid_count*4 || labels.byteLength !== meta.valid_count || norms.byteLength !== meta.valid_count*4 || lookup.byteLength !== meta.width*meta.height*4 || previews.byteLength !== meta.valid_count*12*4) throw new Error("公开数据长度不匹配，请检查部署文件");
    return { meta, ids: new Uint32Array(ids), labels: new Uint8Array(labels), norms: new Float32Array(norms), lookup: new Int32Array(lookup), previews: new Float32Array(previews) };
  })().catch(error => { dataPromise = null; throw error; });
  return dataPromise;
}

function pixel(d: StaticData, index: number): Pixel {
  if (!Number.isInteger(index) || index < 0 || index >= d.meta.valid_count) throw new Error("像元不在有效区内");
  const id = d.ids[index], row = Math.floor(id / d.meta.width), col = id % d.meta.width;
  const [a,b,c,e,f,g] = d.meta.transform;
  const [lng, lat] = projection.inverse([a*(col+.5)+b*(row+.5)+c, e*(col+.5)+f*(row+.5)+g]);
  return { index, row, col, pixel_id: id, lng, lat, cluster: d.labels[index], norm: d.norms[index], vector_preview: Array.from(d.previews.subarray(index*12,index*12+12)), bands: d.meta.bands };
}

export function cancelStaticSearch(): void {
  if (!currentWorker) return;
  currentWorker.terminate(); currentWorker = null;
  rejectSearch?.(new Error("检索已取消")); rejectSearch = null;
}

async function similar(d: StaticData, index: number, k: number): Promise<Similarity> {
  const query = pixel(d,index);
  if (query.norm <= 0) throw new Error("零向量没有余弦相似度");
  cancelStaticSearch();
  const worker = new Worker(new URL("./similarity.worker.ts", import.meta.url), { type: "module" });
  currentWorker = worker;
  const top = await new Promise<Array<{ index: number; score: number }>>((resolve,reject) => {
    rejectSearch = reject;
    worker.onmessage = event => {
      if (event.data.type === "progress") window.dispatchEvent(new CustomEvent("giva-search-progress", { detail: event.data.message }));
      else if (event.data.type === "error") reject(new Error(event.data.message));
      else if (event.data.type === "done") resolve(event.data.items);
    };
    worker.onerror = event => reject(new Error(event.message || "浏览器检索失败"));
    worker.postMessage({ base: new URL(rasterUrl("files/"), window.location.href).href, chunks: d.meta.web_vector_chunks, bands: d.meta.bands, norms: d.norms, index, k });
  }).finally(() => { worker.terminate(); if (currentWorker === worker) { currentWorker = null; rejectSearch = null; } });
  return { query, searched_count: d.meta.valid_count, method: `exact_cosine_${d.meta.bands}d`, items: top.map(item => ({...pixel(d,item.index), score:item.score})) };
}

export async function staticRasterJson<T>(path: string): Promise<T> {
  const d = await data();
  const url = new URL(path, "https://giva.invalid/");
  const requested = url.searchParams.get("dataset_id");
  if (requested && requested !== d.meta.dataset_id) throw new Error("数据集不存在");
  let result: unknown;
  if (url.pathname === "/datasets") result = { items: [{ dataset_id:d.meta.dataset_id, name:d.meta.name, valid_count:d.meta.valid_count, bands:d.meta.bands, width:d.meta.width, height:d.meta.height, crs:d.meta.crs, sampled:false, directory:d.meta.dataset_id }] };
  else if (url.pathname === "/metadata") result = d.meta;
  else if (url.pathname === "/pixel") result = pixel(d, Number(url.searchParams.get("index")));
  else if (url.pathname === "/locate") {
    const lng=Number(url.searchParams.get("lng")), lat=Number(url.searchParams.get("lat"));
    const [x,y]=projection.forward([lng,lat]); const [a,b,c,e,f,g]=d.meta.transform;
    const determinant=a*f-b*e; const col=Math.floor((f*(x-c)-b*(y-g))/determinant), row=Math.floor((-e*(x-c)+a*(y-g))/determinant);
    if (row < 0 || col < 0 || row >= d.meta.height || col >= d.meta.width) throw new Error("点击位置超出栅格范围");
    result = pixel(d, d.lookup[row*d.meta.width+col]);
  } else if (url.pathname === "/similar") result = await similar(d, Number(url.searchParams.get("index")), Number(url.searchParams.get("k")));
  else throw new Error("不支持的公开数据请求");
  return result as T;
}
