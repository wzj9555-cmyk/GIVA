# GIVA · macOS 全量 TIF 工作台

以全部有效像元探索多波段地理表征：地图与三维 PCA 联动、原始全部维度的无监督聚类、原始向量精确余弦检索。类别筛选同时隐藏左右视图对应像元。仅包含全量 TIF 功能。

## 启动

安装 macOS 版 Conda 后，双击 `start.command` 或 `start-raster.command`。首次启动会在名为 **macos** 的 Conda 环境内准备 Python 3.12、栅格依赖和 Node.js 22；不会修改 base 环境。

也可在终端运行：

```bash
cd /你的路径/GIVA-Chinese-mac/GIVA-Chinese
./start.command
```

页面：`http://127.0.0.1:8000/`；旧 `/?raster=1` 地址同样有效。终端保持开启，按 Ctrl+C 停止并释放端口。更换端口：`./start.command --port 8010`。

## 原始数据与缓存

```text
GIVA-Chinese-mac/
├── tif/                       # 原始 .tif / .tiff，程序只读
├── cache/
│   ├── joint_2p5m_512d/         # 从原始数据生成
│   └── concat_2p5m_512d/
└── GIVA-Chinese/               # 本程序
```

将新的多波段 GeoTIFF 放入 `tif` 后重新启动。扫描不递归，接受大小写不同的 TIF 扩展名；文件名规范化后重复会报错。原始影像应包含有效坐标系、仿射变换及至少 8 个有效向量（默认聚类为 8 类）。

首次启动自动生成缓存；后续按源文件 SHA-256、处理版本、参数及产品完整性复用。更换同名、同大小但内容不同的 TIF 也会重建。

强制重建全部原始 TIF：

```bash
./start.command --force-rebuild
```

必须先停止已有 GIVA 服务，再进行重建。不要在运行时手动删除缓存；重新启动后内存映射和数据集注册表随进程更新。缓存可删除并由原始 TIF 重建；`tif` 中的原始文件不可删除。只有完整缓存时，也支持直接查看，无需重新读取原始影像；强制重建仍需要原始文件。

每个缓存目录包含二进制点云、后端向量、地图 PNG、原投影的聚类/PCA GeoTIFF、总览、解释方差 PNG/CSV/JSON 和元数据清单。失败的生成过程不会发布半成品。

## 环境与构建

运行环境定义在 `environment.yml`，Apple Silicon 的已验证依赖版本记录在 `environment-osx-arm64.lock`。手动安装或更新：

```bash
conda env update -n macos -f environment.yml
conda activate macos
cd frontend
npx --yes pnpm@9.15.9 install --frozen-lockfile
npx --yes pnpm@9.15.9 run typecheck
npx --yes pnpm@9.15.9 run build
```

启动器自动检查前端源码指纹，必要时重新构建。依赖安装和首次前端构建需要网络；已有依赖、构建和缓存时，工作台无需外网，卫星底图除外。使用 macOS 系统中文字体；可通过 `GIVA_FONT_PATH` 指定其他支持中文的字体。

## 校验

停止服务时，可运行离线数据完整性检查；在线参数见 `backend/verify_raster.py --help`：

```bash
conda run -n macos python backend/verify_raster.py
```

详见 [全量分析说明](FULL_RASTER.md) 与 [macOS 部署说明](MACOS.md)。

GitHub Pages 无服务器公开版及数据准备说明见 [GITHUB_PAGES.md](GITHUB_PAGES.md)。
