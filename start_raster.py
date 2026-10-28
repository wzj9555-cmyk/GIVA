"""Prepare all full-resolution TIF caches, build the UI, and serve it on macOS."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent


def check_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise RuntimeError("端口必须在 1 到 65535 之间。")
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(f"端口 {port} 已被占用，请关闭已有服务或使用 --port。") from None


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def wait_ready(url: str, process: subprocess.Popen, timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("栅格服务启动失败，请查看上方日志。")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.25)
    raise RuntimeError("栅格服务启动超时。")


def frontend_signature() -> str:
    frontend = ROOT / "frontend"
    files = [p for p in (frontend / "src").rglob("*") if p.is_file() and p.name != ".DS_Store"]
    files += [frontend / name for name in ("package.json", "pnpm-lock.yaml", "index.html", "vite.config.ts", "tsconfig.json")]
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(frontend)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_frontend() -> None:
    frontend = ROOT / "frontend"
    marker = frontend / "dist" / ".build-signature"
    signature = frontend_signature()
    if (frontend / "dist" / "index.html").is_file() and marker.is_file() and marker.read_text().strip() == signature:
        return
    print("正在准备并构建全量 TIF 页面……", flush=True)
    for arguments in (["install", "--frozen-lockfile"], ["run", "typecheck"], ["run", "build"]):
        subprocess.run(["npx", "--yes", "pnpm@9.15.9", *arguments], cwd=frontend, check=True)
    marker.write_text(signature + "\n")


def main() -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from raster_common import DEFAULT_CACHE_DIR, DEFAULT_INPUT_DIR, cache_directory, discover_sources, read_cache_metadata

    parser = argparse.ArgumentParser(description="GIVA macOS 全量 TIF 工作台")
    parser.add_argument("--source", type=Path, help="只准备输入目录中的指定 TIF")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_CACHE_DIR, help="生成缓存的根目录")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--force-rebuild", action="store_true", help="忽略缓存，重新计算全部指定 TIF")
    args = parser.parse_args()
    check_port(args.port)
    cache_root = args.output.resolve()
    input_dir = args.input_dir.resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    with (cache_root / ".workspace.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("此缓存目录已被另一个 GIVA 进程使用，请先关闭已有服务。") from None
        sources = discover_sources(input_dir)
        if args.source:
            wanted = args.source.resolve()
            sources = [p for p in sources if p.resolve() == wanted]
            if not sources:
                raise RuntimeError("--source 必须指向 --input-dir 中存在的 TIF 文件。")
        for source in sources:
            command = [sys.executable, str(ROOT / "backend" / "prepare_raster.py"),
                       "--source", str(source), "--output", str(cache_directory(source, cache_root)),
                       "--input-dir", str(input_dir)]
            if args.force_rebuild:
                command.append("--force-rebuild")
            subprocess.run(command, check=True)
        valid = [p for p in cache_root.iterdir() if p.is_dir() and not p.name.startswith(".") and read_cache_metadata(p)]
        if not valid:
            raise RuntimeError(f"未发现完整缓存，请将原始 TIF 放入 {input_dir} 后重新启动。")
        if args.force_rebuild and not sources:
            raise RuntimeError("未找到原始 TIF，无法强制重建缓存。")
        build_frontend()
        env = os.environ.copy()
        env["GIVA_RASTER_DIR"] = str(cache_root)
        env["GIVA_TIF_DIR"] = str(input_dir)
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.raster_main:app",
                                    "--host", "127.0.0.1", "--port", str(args.port)],
                                   cwd=ROOT / "backend", env=env, start_new_session=True)
        try:
            wait_ready(f"http://127.0.0.1:{args.port}/health", process)
            url = f"http://127.0.0.1:{args.port}/"
            print(f"\nGIVA 已启动：{url}\n原始数据：{input_dir}\n缓存目录：{cache_root}\n按 Ctrl+C 停止。", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            code = process.wait()
            if code:
                raise RuntimeError(f"栅格服务异常退出（{code}）。")
        finally:
            stop(process)


def terminate(_signal, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    if os.environ.get("GIVA_ENV_READY") != "1":
        os.execv(sys.executable, [sys.executable, str(ROOT / "start.py"), *sys.argv[1:]])
    signal.signal(signal.SIGTERM, terminate)
    try:
        main()
    except KeyboardInterrupt:
        print("\nGIVA 已停止。")
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        sys.exit(1)
