/// <reference lib="webworker" />
interface Chunk { name: string; start: number; rows: number; bytes: number; sha256: string }
interface Job { base: string; chunks: Chunk[]; bands: number; norms: Float32Array; index: number; k: number }
interface Entry { index: number; score: number }
const scope = self as unknown as DedicatedWorkerGlobalScope;

async function database(): Promise<IDBDatabase | null> {
  try { return await new Promise((resolve, reject) => {
    const request = indexedDB.open("giva-public-vectors", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("chunks");
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  }); } catch { return null; }
}
async function cached(db: IDBDatabase | null, key: string): Promise<ArrayBuffer | null> {
  if (!db) return null;
  try { return await new Promise((resolve, reject) => {
    const request = db.transaction("chunks", "readonly").objectStore("chunks").get(key);
    request.onsuccess = () => resolve(request.result instanceof ArrayBuffer ? request.result : null); request.onerror = () => reject(request.error);
  }); } catch { return null; }
}
async function save(db: IDBDatabase | null, key: string, value: ArrayBuffer): Promise<void> {
  if (!db) return;
  try { await new Promise<void>((resolve, reject) => {
    const tx = db.transaction("chunks", "readwrite"); tx.objectStore("chunks").put(value, key);
    tx.oncomplete = () => resolve(); tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error);
  }); } catch { /* Storage can be unavailable or full; computation still works. */ }
}
async function getChunk(job: Job, chunk: Chunk, db: IDBDatabase | null): Promise<ArrayBuffer> {
  let buffer = await cached(db, chunk.sha256);
  if (!buffer || buffer.byteLength !== chunk.bytes) {
    const response = await fetch(new URL(chunk.name, job.base));
    if (!response.ok) throw new Error(`向量数据下载失败 (${response.status})`);
    buffer = await response.arrayBuffer();
    if (buffer.byteLength !== chunk.bytes) throw new Error(`向量数据长度错误：${chunk.name}`);
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", buffer))).map(n => n.toString(16).padStart(2,"0")).join("");
    if (hash !== chunk.sha256) throw new Error(`向量数据校验失败：${chunk.name}`);
    await save(db, chunk.sha256, buffer);
  }
  return buffer;
}
function push(heap: Entry[], item: Entry, k: number): void {
  if (heap.length < k) {
    heap.push(item); let i = heap.length-1;
    while (i > 0) { const p = Math.floor((i-1)/2); if (heap[p].score <= heap[i].score) break; [heap[p],heap[i]]=[heap[i],heap[p]]; i=p; }
  } else if (item.score > heap[0].score) {
    heap[0]=item; let i=0;
    for (;;) { const l=i*2+1, r=l+1; if (l>=heap.length) break; const child=r<heap.length && heap[r].score<heap[l].score ? r:l; if (heap[i].score<=heap[child].score) break; [heap[i],heap[child]]=[heap[child],heap[i]]; i=child; }
  }
}
async function run(job: Job): Promise<Entry[]> {
  if (!Number.isInteger(job.index) || job.index < 0 || job.index >= job.norms.length || !Number.isInteger(job.k) || job.k < 1 || job.k > 500) throw new Error("检索参数无效");
  const db = await database();
  try {
    const queryChunk = job.chunks.find(c => job.index >= c.start && job.index < c.start+c.rows);
    if (!queryChunk) throw new Error("找不到查询向量");
    scope.postMessage({type:"progress",message:"正在读取查询向量…"});
    const initial = await getChunk(job,queryChunk,db);
    const query = new Float32Array(initial, (job.index-queryChunk.start)*job.bands*4, job.bands).slice();
    const qnorm=job.norms[job.index], heap: Entry[]=[];
    for (let part=0; part<job.chunks.length; part++) {
      const chunk=job.chunks[part];
      scope.postMessage({type:"progress",message:`正在加载并检索向量 ${part+1}/${job.chunks.length}（首次约 674 MiB）…`});
      const vector = new Float32Array(chunk === queryChunk ? initial : await getChunk(job,chunk,db));
      for (let row=0; row<chunk.rows; row++) {
        const index=chunk.start+row, norm=job.norms[index]; if (index===job.index || norm<=0) continue;
        let dot=0; const offset=row*job.bands;
        for (let band=0; band<job.bands; band++) dot+=vector[offset+band]*query[band];
        push(heap,{index,score:Math.max(-1,Math.min(1,dot/Math.max(norm*qnorm,1e-12)))},job.k);
      }
    }
    return heap.sort((a,b)=>b.score-a.score);
  } finally { db?.close(); }
}
scope.onmessage = event => { void run(event.data as Job).then(items => scope.postMessage({type:"done",items})).catch(error => scope.postMessage({type:"error",message:(error as Error).message})); };
export {};
