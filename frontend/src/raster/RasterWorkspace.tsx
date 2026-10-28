import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RasterCloud } from "./RasterCloud";
import { RasterMap } from "./RasterMap";
import { getRasterJson, rasterUrl, STATIC_DEMO, type Pixel, type RasterArrays, type RasterDatasetOption, type RasterMeta, type Similarity } from "./types";
import { cancelStaticSearch } from "./staticRaster";
import "./raster.css";

const number = (value: number) => value.toLocaleString("zh-CN");
const binaryNames = ["positions.f32", "colors.u8", "labels.u8", "pixel_ids.u32"];

export function RasterWorkspace() {
  const [meta, setMeta] = useState<RasterMeta | null>(null);
  const [arrays, setArrays] = useState<RasterArrays | null>(null);
  const [datasets, setDatasets] = useState<RasterDatasetOption[]>([]);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("正在读取栅格数据集…");
  const [mode, setMode] = useState<"cluster" | "pca">("cluster");
  const [visible, setVisible] = useState<boolean[]>([]);
  const [pointSize, setPointSize] = useState(1.5);
  const [basemap, setBasemap] = useState(true);
  const [rasterOpacity, setRasterOpacity] = useState(.65);
  const opacityAdjusted = useRef(false);
  const basemapWasEnabled = useRef(true);
  const [selected, setSelected] = useState<Pixel | null>(null);
  const [similar, setSimilar] = useState<Pixel[]>([]);
  const [focused, setFocused] = useState<Pixel | null>(null);
  const [focusSerial, setFocusSerial] = useState(0);
  const candidateStrip = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [k, setK] = useState(30);
  const [searched, setSearched] = useState(false);
  const requestId = useRef(0);
  const datasetRequestId = useRef(0);
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  const visibilityMask = useMemo(() => {
    if (!arrays) return new Uint8Array();
    return Uint8Array.from(arrays.labels, label => visible[label] ? 1 : 0);
  }, [arrays, visible]);
  const maskRef = useRef(visibilityMask); maskRef.current = visibilityMask;

  useEffect(() => {
    const progress = (event: Event) => setNotice((event as CustomEvent<string>).detail);
    window.addEventListener("giva-search-progress", progress);
    return () => { window.removeEventListener("giva-search-progress", progress); cancelStaticSearch(); };
  }, []);

  useEffect(() => { candidateStrip.current?.scrollTo({ left: 0 }); }, [similar]);

  useEffect(() => {
    if (selected && !visible[selected.cluster]) {
      requestId.current++; cancelStaticSearch();
      setSelected(null); setSimilar([]); setFocused(null); setSearched(false); setBusy(false);
      setNotice("选中像元的类别已隐藏，已清除选择与相似高亮");
    }
  }, [visible, selected]);

  useEffect(() => {
    if (focused && !visible[focused.cluster]) setFocused(null);
  }, [visible, focused]);

  const loadDataset = useCallback(async (nextId: string) => {
    const token = ++datasetRequestId.current;
    requestId.current++; cancelStaticSearch();
    setError(null); setMeta(null); setArrays(null); setBusy(false);
    setSelected(null); setSimilar([]); setFocused(null); setSearched(false);
    setNotice("正在读取栅格数据集…");
    try {
      const metadata = await getRasterJson<RasterMeta>(`metadata?dataset_id=${encodeURIComponent(nextId)}`);
      const buffers = await Promise.all(binaryNames.map(async name => {
        const response = await fetch(rasterUrl(`files/${name}`, nextId), { cache: "no-store" });
        if (!response.ok) throw new Error(`${name} 加载失败`);
        return response.arrayBuffer();
      }));
      const n = metadata.valid_count;
      if (buffers[0].byteLength !== n * 12 || buffers[1].byteLength !== n * 3 || buffers[2].byteLength !== n || buffers[3].byteLength !== n * 4) throw new Error("数据长度与有效像元数不一致，请重新生成缓存。");
      if (token !== datasetRequestId.current) return;
      setMeta(metadata); setArrays({ positions: new Float32Array(buffers[0]), colors: new Uint8Array(buffers[1]), labels: new Uint8Array(buffers[2]), pixelIds: new Uint32Array(buffers[3]) });
      setVisible(metadata.palette.map(() => true)); setDatasetId(nextId);
      setNotice(`已加载 ${metadata.name} · 全部 ${number(metadata.valid_count)} 个有效像元`);
    } catch (e) {
      if (token === datasetRequestId.current) setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const registry = await getRasterJson<{ items: RasterDatasetOption[] }>("datasets");
        if (!registry.items.length) throw new Error("没有发现已准备的栅格数据集。");
        if (cancelled) return;
        setDatasets(registry.items); await loadDataset(registry.items[0].dataset_id);
      } catch (e) { if (!cancelled) setError((e as Error).message); }
    };
    void load(); return () => { cancelled = true; requestId.current++; datasetRequestId.current++; };
  }, [loadDataset]);

  const choose = useCallback(async (index: number) => {
    const token = ++requestId.current; cancelStaticSearch(); setBusy(false);
    try { const pixel = await getRasterJson<Pixel>(`pixel?index=${index}&dataset_id=${encodeURIComponent(datasetId ?? "")}`); if (token === requestId.current) { if (!maskRef.current[pixel.index]) { setNotice("该像元所属类别已隐藏"); return; } setSimilar([]); setFocused(null); setSearched(false); setSelected(pixel); setNotice(`已选择像元 [${pixel.row}, ${pixel.col}]`); } }
    catch (e) { if (token === requestId.current) setNotice((e as Error).message); }
  }, [datasetId]);
  const locate = useCallback(async (lng: number, lat: number) => {
    const token = ++requestId.current; cancelStaticSearch(); setBusy(false);
    try { const pixel = await getRasterJson<Pixel>(`locate?lng=${lng}&lat=${lat}&dataset_id=${encodeURIComponent(datasetId ?? "")}`); if (token === requestId.current) { if (!maskRef.current[pixel.index]) { setNotice("该像元所属类别已隐藏"); return; } setSimilar([]); setFocused(null); setSearched(false); setSelected(pixel); setNotice(`已选择像元 [${pixel.row}, ${pixel.col}]`); } }
    catch (e) { if (token === requestId.current) setNotice((e as Error).message); }
  }, [datasetId]);
  const search = async () => {
    if (!selected) return;
    const token = ++requestId.current; setBusy(true); setFocused(null); setNotice("正在全部向量中精确检索…");
    try { const result = await getRasterJson<Similarity>(`similar?index=${selected.index}&k=${k}&dataset_id=${encodeURIComponent(datasetId ?? "")}`); if (token === requestId.current) { setSimilar(result.items); setFocused(null); setSearched(true); setNotice(`已检索全部 ${number(result.searched_count)} 个像元`); } }
    catch (e) { if (token === requestId.current) setNotice((e as Error).message); }
    finally { if (token === requestId.current) setBusy(false); }
  };
  const focusCandidate = (pixel: Pixel) => {
    if (busy || !visibleRef.current[pixel.cluster]) return;
    setFocused({ ...pixel });
    setFocusSerial(serial => serial + 1);
    setNotice(`已定位相似候选 R${pixel.row} C${pixel.col} · 查询像元保持不变`);
  };
  const toggleBasemap = (enabled: boolean) => {
    if (enabled && !basemapWasEnabled.current) {
      basemapWasEnabled.current = true;
      if (!opacityAdjusted.current) setRasterOpacity(.65);
    }
    setBasemap(enabled);
  };

  if (error) return <div className="raster-loading" role="alert"><h1>全量栅格工作台</h1><p>{error}</p><p>请将原始 TIF 放入 tif 文件夹，再双击 start.command。启动与缓存说明见项目 MACOS.md。</p><button onClick={() => window.location.reload()}>重新连接</button></div>;
  if (!meta || !arrays || !datasetId) return <div className="raster-loading"><div className="raster-spinner"/><h1>正在打开全量栅格工作台</h1><p>{notice}</p></div>;
  const shown = visibilityMask.reduce((sum, value) => sum + value, 0);
  const visibleSimilar = similar.filter(pixel => !!visibilityMask[pixel.index]);
  const displayedSimilar = visibleSimilar.slice(0, 50);
  const ranks = new Map(similar.map((pixel, index) => [pixel.index, index + 1]));
  const focusedCandidate = focused && visibilityMask[focused.index] ? focused : null;
  return <div className="raster-workspace">
    <header className="raster-header"><div className="raster-wordmark">GIVA<span>全量栅格工作台</span></div><label className="raster-dataset-picker"><span>数据集</span><select value={datasetId} onChange={e => void loadDataset(e.target.value)}>{datasets.map(item => <option key={item.dataset_id} value={item.dataset_id}>{item.name} · {number(item.valid_count)} 像元</option>)}</select></label><span className="raster-status"><i/>全量加载 · 无抽样</span></header>
    <div className="raster-toolbar" aria-label="可视化设置">
      <div className="raster-toolbar-group raster-color-control"><span className="raster-toolbar-label">配色</span><div className="raster-segment"><button className={mode === "cluster" ? "active" : ""} onClick={() => setMode("cluster")}>聚类标签</button><button className={mode === "pca" ? "active" : ""} onClick={() => setMode("pca")}>PCA 配色</button></div></div>
      <div className="raster-toolbar-group raster-cluster-control"><span className="raster-toolbar-label">类别</span><div className="raster-legend">{meta.palette.map((color, i) => <button key={i} className={visible[i] ? "" : "off"} aria-pressed={visible[i]} title={`C${String(i + 1).padStart(2, "0")} · ${number(meta.cluster_counts[i])} 像元`} onClick={() => setVisible(visible.map((v, j) => i === j ? !v : v))}><i style={{ background: color }}/><span>C{String(i + 1).padStart(2, "0")}</span></button>)}</div><button className="raster-all" onClick={() => setVisible(visible.map(() => true))}>全部</button></div>
      <div className="raster-toolbar-group raster-range-group"><div className="raster-control"><label htmlFor="raster-size">点大小</label><input id="raster-size" type="range" min="0.5" max="3" step="0.1" value={pointSize} onChange={e => setPointSize(Number(e.target.value))}/></div><div className="raster-control raster-opacity-control"><label htmlFor="raster-opacity">不透明度</label><input id="raster-opacity" type="range" min="0" max="100" step="1" value={Math.round(rasterOpacity * 100)} aria-valuetext={`${Math.round(rasterOpacity * 100)}%`} onChange={e => { opacityAdjusted.current = true; setRasterOpacity(Number(e.target.value) / 100); }}/><output htmlFor="raster-opacity">{Math.round(rasterOpacity * 100)}%</output></div><label className="raster-check"><input type="checkbox" checked={basemap} onChange={e => toggleBasemap(e.target.checked)}/>卫星底图</label></div>

      <details className="raster-more"><summary>更多信息</summary><div className="raster-more-panel"><div className="raster-section-label">DATASET / 数据概览</div><div className="raster-big-number">{number(meta.valid_count)}</div><div className="raster-muted">有效像元 · 全部参与分析与显示</div><div className="raster-stats"><div><strong>{meta.bands}</strong><span>表征维度</span></div><div><strong>{meta.resolution[0]} m</strong><span>空间分辨率</span></div><div><strong>{meta.width} × {meta.height}</strong><span>原始栅格</span></div><div><strong>{(meta.valid_count / meta.total_pixels * 100).toFixed(1)}%</strong><span>有效覆盖</span></div></div><p className="raster-help">{mode === "cluster" ? `使用原始 ${meta.bands} 维向量进行 ${meta.palette.length} 类聚类（欧氏距离）；类别反映表征相似性，不代表已标注的土地利用类型。` : `前三主成分映射为 RGB，解释方差 ${(meta.pca_explained_ratio.reduce((a, b) => a + b, 0) * 100).toFixed(1)}%。颜色不是真彩色。`}</p><div className="raster-section-label">EXPORT / 导出结果</div><div className="raster-downloads"><a href={rasterUrl("files/全量栅格可视化总览.png", datasetId)} download>总览 PNG ↗</a><a href={rasterUrl("files/clusters.tif", datasetId)} download>聚类 GeoTIFF ↗</a><a href={rasterUrl("files/pca_rgb.tif", datasetId)} download>PCA GeoTIFF ↗</a></div><p className="raster-footer-note">全量 PCA · {meta.bands} 维聚类 · {meta.crs}<br/>{STATIC_DEMO ? "相似检索在浏览器内运行" : "原始向量保留在本地"}</p></div></details>
    </div>
    <main className="raster-main"><div className="raster-summary"><div><span>双视图可见像元</span><strong>{number(shown)} <small>/ {number(meta.valid_count)}</small></strong></div><div><span>地图可见比例</span><strong>{(shown / meta.valid_count * 100).toFixed(1)}% <small>有效像元</small></strong></div><div><span>选择联动</span><strong>{selected ? `R${selected.row} · C${selected.col}` : "点击任一像元"}</strong></div></div>
      <div className="raster-views"><section className="raster-pane"><div className="raster-pane-title"><div><i className="raster-dot mint"/>表征空间<span>全量 PCA · 3D</span></div><span>{number(shown)} 点</span></div><RasterCloud meta={meta} arrays={arrays} mode={mode} selected={selected?.index ?? null} focused={focusedCandidate?.index ?? null} focusSerial={focusSerial} similar={visibleSimilar.map(p => p.index)} visible={visible} mask={visibilityMask} pointSize={pointSize} onSelect={choose}/></section><section className="raster-pane"><div className="raster-pane-title"><div><i className="raster-dot blue"/>地理空间<span>类别联动</span></div><span>{meta.resolution[0]} m / pixel</span></div><RasterMap datasetId={datasetId} meta={meta} mode={mode} selected={selected} focused={focusedCandidate} similar={visibleSimilar} visible={visible} mask={visibilityMask} basemap={basemap} rasterOpacity={rasterOpacity} onLocate={locate}/></section></div>
      <section className="raster-inspector"><div className="raster-inspector-top"><div><div className="raster-section-label">PIXEL INSPECTOR / 像元详情</div><h2>{selected ? `查询像元 [${selected.row}, ${selected.col}]` : "在地图或三维空间中选择像元"}</h2></div><div className="raster-query-controls"><label>Top <select aria-label="相似像元数量" value={k} onChange={e => setK(Number(e.target.value))}><option>10</option><option>30</option><option>50</option><option>100</option><option>200</option><option>500</option></select></label><button disabled={!selected || busy} onClick={search}>{busy ? "检索中…" : "检索相似像元"}</button><button className="raster-clear" disabled={!selected} onClick={() => { requestId.current++; cancelStaticSearch(); setSelected(null); setSimilar([]); setFocused(null); setBusy(false); setSearched(false); setNotice("已清除选择"); }}>清除</button></div></div>
        {STATIC_DEMO && <p className="raster-muted">首次相似检索需下载约 674 MiB 向量数据，浏览器会尝试缓存以供后续使用。</p>}
        {selected ? <div className="raster-inspector-details"><div className="raster-pixel-meta"><span><i style={{ background: meta.palette[selected.cluster] }}/>C{String(selected.cluster + 1).padStart(2, "0")}</span><span>{selected.lng.toFixed(6)}° E, {selected.lat.toFixed(6)}° N</span><span>向量范数 {selected.norm.toFixed(4)}</span><span>{selected.bands} 维融合表征</span></div><div className="raster-vector"><span>前 12 维</span>{selected.vector_preview.map((v, i) => <i key={i} title={`维度 ${i + 1}: ${v}`} style={{ background: v >= 0 ? "#52c9b0" : "#e784a6", height: `${Math.min(44, 5 + Math.abs(v) * 12)}px` }}/>) }<span className="raster-vector-values">{selected.vector_preview.slice(0, 6).map(v => v.toFixed(3)).join("  ·  ")} …</span><span className="raster-vector-key">绿=正值 · 红=负值 · 柱高=绝对值</span></div></div> : <p className="raster-muted">选择后可查看位置、类别和向量摘要，并在全部有效像元中执行精确余弦检索。</p>}
        {searched && <div className="raster-similar"><div className="raster-similar-header"><span>相似度 Top {similar.length} · 可见 {visibleSimilar.length} · 列表前 {displayedSimilar.length} 项</span><span className="raster-similar-actions"><span>白色：查询点 · 橙色：当前候选 · 黄色：相似点</span><button aria-label="向左查看更多候选" onClick={() => candidateStrip.current?.scrollBy({ left: -400, behavior: "smooth" })}>‹</button><button aria-label="向右查看更多候选" onClick={() => candidateStrip.current?.scrollBy({ left: 400, behavior: "smooth" })}>›</button></span></div>{displayedSimilar.length > 0 ? <><div className="raster-similar-strip" ref={candidateStrip} role="region" aria-label="相似像元候选列表" tabIndex={0}>{displayedSimilar.map(p => <button key={p.index} className={focusedCandidate?.index === p.index ? "focused" : ""} aria-pressed={focusedCandidate?.index === p.index} disabled={busy} title={`定位候选 #${ranks.get(p.index)}，保留查询像元`} onClick={() => focusCandidate(p)}><span className="raster-candidate-rank">#{ranks.get(p.index)}</span><span>R{p.row} C{p.col}</span><b>{p.score?.toFixed(4)}</b></button>)}</div><div className="raster-similar-help"><span>左右滑动查看更多 · 点击候选在双视图定位，查询点保持不变</span><span role="status" aria-live="polite">{focusedCandidate ? `当前候选 #${ranks.get(focusedCandidate.index)} · R${focusedCandidate.row} C${focusedCandidate.col}` : "全部可见候选已在双视图高亮"}</span></div></> : <span>当前筛选下没有可见的相似像元</span>}</div>}
      </section><footer className="raster-bottom"><span><i/>{notice}</span><span>聚类标签与 PCA 颜色均为无监督分析结果</span></footer>
    </main>
  </div>;
}
