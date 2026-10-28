"""Lightweight full-raster server; also serves the built GIVA frontend."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .raster import router

app = FastAPI(title="GIVA 全量栅格可视化")
app.add_middleware(CORSMiddleware, allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$", allow_methods=["GET"])
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "workspace": "full-raster"}


frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend.exists():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
