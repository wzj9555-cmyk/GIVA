import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { RasterArrays, RasterMeta } from "./types";

interface Props { meta: RasterMeta; arrays: RasterArrays; mode: "cluster" | "pca"; selected: number | null; focused: number | null; focusSerial: number; similar: number[]; visible: boolean[]; mask: Uint8Array; pointSize: number; onSelect: (index: number) => void }
interface Runtime { update: (props: Props) => void; reset: () => void }
const vertex = `attribute vec3 pointColor; attribute float shown; attribute float pointIndex;
varying vec3 vColor; varying float vShown;
uniform float pointSize; uniform float selectedIndex; uniform float picking;
void main(){vShown=shown; vColor=pointColor; vec4 mv=modelViewMatrix*vec4(position,1.0);
 gl_Position=projectionMatrix*mv;
 float selected=abs(pointIndex-selectedIndex)<0.1?1.0:0.0;
 gl_PointSize=clamp(pointSize*130.0/max(8.0,-mv.z),1.0,18.0)+selected*6.0;
 if(picking<0.5 && selected>0.5) vColor=vec3(1.0); }`;
const fragment = `precision highp float; varying vec3 vColor; varying float vShown;
void main(){if(vShown<0.5 || length(gl_PointCoord-vec2(0.5))>0.5) discard; gl_FragColor=vec4(vColor,1.0);}`;
const focusVertex = `void main(){gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); gl_PointSize=36.0;}`;
const focusFragment = `precision highp float;
void main(){float radius=length(gl_PointCoord-vec2(0.5))*2.0;
 if(radius>1.0 || (radius<0.65 && radius>0.24)) discard;
 gl_FragColor=vec4(radius>0.9 || (radius<0.65 && radius>0.17) ? vec3(1.0) : vec3(1.0,0.576,0.329),1.0);}`;

export function RasterCloud(props: Props) {
  const host = useRef<HTMLDivElement>(null);
  const runtime = useRef<Runtime | null>(null);
  const latest = useRef(props); latest.current = props;
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!host.current) return;
    const container = host.current;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true }); }
    catch { setError("无法创建 WebGL 上下文，请启用浏览器硬件加速后重试。"); return; }
    const scene = new THREE.Scene(); scene.background = new THREE.Color("#101c2a");
    const camera = new THREE.PerspectiveCamera(42, 1, .1, 2000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.dampingFactor = .1;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(props.arrays.positions, 3));
    geometry.computeBoundingSphere();
    const color = new Float32Array(props.meta.valid_count * 3);
    const pickColor = new Float32Array(color.length);
    const shown = new Float32Array(props.meta.valid_count).fill(1);
    const pointIndex = new Float32Array(props.meta.valid_count);
    for (let i = 0; i < pointIndex.length; i++) {
      pointIndex[i] = i; const id = i + 1;
      pickColor[i * 3] = (id & 255) / 255;
      pickColor[i * 3 + 1] = ((id >> 8) & 255) / 255;
      pickColor[i * 3 + 2] = ((id >> 16) & 255) / 255;
    }
    geometry.setAttribute("pointColor", new THREE.BufferAttribute(color, 3));
    geometry.setAttribute("shown", new THREE.BufferAttribute(shown, 1));
    geometry.setAttribute("pointIndex", new THREE.BufferAttribute(pointIndex, 1));
    const material = new THREE.ShaderMaterial({ vertexShader: vertex, fragmentShader: fragment, uniforms: { pointSize: { value: props.pointSize }, selectedIndex: { value: -1 }, picking: { value: 0 } } });
    const cloud = new THREE.Points(geometry, material); scene.add(cloud);
    const highlightGeometry = new THREE.BufferGeometry();
    const highlightMaterial = new THREE.ShaderMaterial({ vertexShader: vertex, fragmentShader: fragment, uniforms: { pointSize: { value: 4 }, selectedIndex: { value: -1 }, picking: { value: 0 } }, depthTest: false, depthWrite: false });
    const highlights = new THREE.Points(highlightGeometry, highlightMaterial); highlights.frustumCulled = false; highlights.renderOrder = 10; scene.add(highlights);
    const focusGeometry = new THREE.BufferGeometry();
    focusGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(3), 3));
    const focusMaterial = new THREE.ShaderMaterial({ vertexShader: focusVertex, fragmentShader: focusFragment, depthTest: false, depthWrite: false });
    const focusMarker = new THREE.Points(focusGeometry, focusMaterial); focusMarker.frustumCulled = false; focusMarker.renderOrder = 20; focusMarker.visible = false; scene.add(focusMarker);
    const pickGeometry = new THREE.BufferGeometry();
    pickGeometry.setAttribute("position", geometry.getAttribute("position"));
    pickGeometry.setAttribute("pointColor", new THREE.BufferAttribute(pickColor, 3));
    pickGeometry.setAttribute("shown", geometry.getAttribute("shown"));
    pickGeometry.setAttribute("pointIndex", geometry.getAttribute("pointIndex"));
    const pickMaterial = material.clone(); pickMaterial.uniforms.picking.value = 1;
    const pickScene = new THREE.Scene(); pickScene.background = new THREE.Color(0);
    pickScene.add(new THREE.Points(pickGeometry, pickMaterial));
    const target = new THREE.WebGLRenderTarget(1, 1, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, depthBuffer: true });
    const grid = new THREE.GridHelper(48, 24, "#375064", "#203444"); grid.position.y = -15; scene.add(grid);
    const axes = new THREE.AxesHelper(4); axes.position.set(-18, -14.9, 12); scene.add(axes);
    container.appendChild(renderer.domElement);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    const resize = () => {
      const width = container.clientWidth, height = container.clientHeight;
      if (!width || !height) return;
      renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix();
      const size = renderer.getDrawingBufferSize(new THREE.Vector2()); target.setSize(size.x, size.y);
    };
    const observer = new ResizeObserver(resize); observer.observe(container); resize();
    const reset = () => {
      const sphere = geometry.boundingSphere!;
      const radius = Math.max(12, sphere.radius);
      controls.target.copy(sphere.center);
      const halfFov = Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * Math.min(camera.aspect, 1));
      camera.position.copy(sphere.center).add(new THREE.Vector3(.6, .35, 1).normalize().multiplyScalar(radius / Math.sin(halfFov) * 1.08));
      camera.far = Math.max(2000, radius * 20); camera.updateProjectionMatrix(); controls.update();
    };
    reset();
    let lastFocused: number | null = null;
    let lastFocusSerial = -1;
    let focusMotion: { started: number; camera: THREE.Vector3; target: THREE.Vector3; delta: THREE.Vector3 } | null = null;
    const stopFocusMotion = () => { focusMotion = null; };
    controls.addEventListener("start", stopFocusMotion);
    runtime.current = { reset: () => { focusMotion = null; reset(); }, update: (next) => {
      const palette = next.meta.palette.map(c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16) / 255));
      const highlight = new Set(next.similar);
      for (let i = 0; i < shown.length; i++) {
        shown[i] = next.mask[i] ? 1 : 0;
        const c = palette[next.arrays.labels[i]];
        for (let channel = 0; channel < 3; channel++) {
          const base = next.mode === "pca" ? next.arrays.colors[i * 3 + channel] / 255 : c[channel];
          color[i * 3 + channel] = highlight.has(i) ? (channel === 0 ? 1 : channel === 1 ? .86 : .3) : base;
        }
      }
      geometry.getAttribute("pointColor").needsUpdate = true; geometry.getAttribute("shown").needsUpdate = true;
      material.uniforms.pointSize.value = next.pointSize; material.uniforms.selectedIndex.value = next.selected ?? -1;
      pickMaterial.uniforms.pointSize.value = next.pointSize; pickMaterial.uniforms.selectedIndex.value = next.selected ?? -1;
      const indices = [...next.similar, ...(next.selected === null ? [] : [next.selected])].filter(i => next.mask[i]);
      const hp = new Float32Array(indices.length * 3), hc = new Float32Array(indices.length * 3);
      indices.forEach((index, i) => { hp.set(next.arrays.positions.subarray(index * 3, index * 3 + 3), i * 3); hc.set(index === next.selected ? [1, 1, 1] : [1, .85, .25], i * 3); });
      highlightGeometry.dispose();
      highlightGeometry.setAttribute("position", new THREE.BufferAttribute(hp, 3));
      highlightGeometry.setAttribute("pointColor", new THREE.BufferAttribute(hc, 3));
      highlightGeometry.setAttribute("shown", new THREE.BufferAttribute(new Float32Array(indices.length).fill(1), 1));
      highlightGeometry.setAttribute("pointIndex", new THREE.BufferAttribute(new Float32Array(indices), 1));
      highlightMaterial.uniforms.selectedIndex.value = next.selected ?? -1;
      const focused = next.focused !== null && next.mask[next.focused] ? next.focused : null;
      focusMarker.visible = focused !== null;
      if (focused !== null) {
        const position = new THREE.Vector3().fromArray(next.arrays.positions, focused * 3);
        const attribute = focusGeometry.getAttribute("position") as THREE.BufferAttribute;
        attribute.setXYZ(0, position.x, position.y, position.z); attribute.needsUpdate = true;
        if (focused !== lastFocused || next.focusSerial !== lastFocusSerial) {
          // Pan toward the candidate while retaining the user's viewing angle
          // and distance; focusing never changes the query or zoom level.
          focusMotion = { started: performance.now(), camera: camera.position.clone(), target: controls.target.clone(), delta: position.sub(controls.target) };
        }
      } else focusMotion = null;
      lastFocused = focused;
      lastFocusSerial = next.focusSerial;
    } };
    runtime.current.update(latest.current);
    let pointerDown = [0, 0];
    const down = (e: PointerEvent) => { pointerDown = [e.clientX, e.clientY]; };
    const up = (e: PointerEvent) => {
      if (e.button !== 0 || Math.hypot(e.clientX - pointerDown[0], e.clientY - pointerDown[1]) > 4) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const x = Math.floor((e.clientX - rect.left) / rect.width * target.width);
      const y = target.height - 1 - Math.floor((e.clientY - rect.top) / rect.height * target.height);
      if (x < 0 || y < 0 || x >= target.width || y >= target.height) return;
      renderer.setRenderTarget(target); renderer.render(pickScene, camera);
      const pixel = new Uint8Array(4); renderer.readRenderTargetPixels(target, x, y, 1, 1, pixel); renderer.setRenderTarget(null);
      const id = pixel[0] + (pixel[1] << 8) + (pixel[2] << 16) - 1;
      if (id >= 0 && id < latest.current.meta.valid_count) latest.current.onSelect(id);
    };
    renderer.domElement.addEventListener("pointerdown", down); renderer.domElement.addEventListener("pointerup", up);
    const contextLost = (event: Event) => { event.preventDefault(); setError("图形上下文已丢失，请刷新页面重新加载。"); };
    renderer.domElement.addEventListener("webglcontextlost", contextLost);
    let frame = 0;
    const render = () => {
      frame = requestAnimationFrame(render);
      if (focusMotion) {
        const progress = Math.min(1, (performance.now() - focusMotion.started) / 450);
        const eased = progress * progress * (3 - 2 * progress);
        camera.position.copy(focusMotion.camera).addScaledVector(focusMotion.delta, eased);
        controls.target.copy(focusMotion.target).addScaledVector(focusMotion.delta, eased);
        if (progress === 1) focusMotion = null;
      }
      controls.update(); renderer.render(scene, camera);
    };
    render();
    return () => {
      cancelAnimationFrame(frame); observer.disconnect(); controls.removeEventListener("start", stopFocusMotion); controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", down); renderer.domElement.removeEventListener("pointerup", up);
      renderer.domElement.removeEventListener("webglcontextlost", contextLost);
      geometry.dispose(); pickGeometry.dispose(); highlightGeometry.dispose(); focusGeometry.dispose(); material.dispose(); pickMaterial.dispose(); highlightMaterial.dispose(); focusMaterial.dispose(); target.dispose();
      grid.geometry.dispose(); (grid.material as THREE.Material).dispose(); axes.geometry.dispose(); (axes.material as THREE.Material).dispose();
      renderer.dispose(); renderer.domElement.remove(); runtime.current = null;
    };
  }, [props.arrays, props.meta]);
  useEffect(() => { runtime.current?.update(props); }, [props.mode, props.selected, props.focused, props.focusSerial, props.similar, props.visible, props.mask, props.pointSize]);
  return <div className="raster-cloud" ref={host}>
    <button className="raster-float-button" onClick={() => runtime.current?.reset()}>重置视角</button>
    <div className="raster-canvas-note">拖动旋转 · 滚轮缩放 · 点击选择像元</div>
    {error && <div className="raster-error">{error}</div>}
  </div>;
}
