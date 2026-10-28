# GitHub Pages 公开展示

公开版使用 GitHub Pages 静态托管，显示 `joint_2p5m_512d` 的全部有效像元。地图、三维、类别筛选、候选定位和 Top 500 使用现有界面。坐标换算和精确余弦检索在访客的浏览器中执行，无需 Python 服务或云主机。

首次相似检索需按需下载约 674 MiB 的 512 维有效向量；浏览器会尝试缓存，存储不可用或容量不足时后续可能重新下载。公开仓库和网站中的向量、坐标、配色、航空影像瓦片及导出结果均可被访客获取。网站未包含原始 TIF 或整张航空 JPEG 文件。公开站点以 `/GIVA/` 为根路径。

航空底图位于 `site-data/aerial/`，由本地 JPEG 和配套空间参考导出为 12–20 级静态 JPEG 瓦片；地图范围固定在航空影像范围内。影像坐标按原文件名所标注的 GCJ-02 使用。重建瓦片前须移走旧的 `site-data/aerial/`，然后在 `macos` 环境执行 `python scripts/export_aerial_tiles.py`。

数据由 `scripts/export_web_data.py` 从 `../cache/joint_2p5m_512d` 一次性提取到 `site-data/joint_2p5m_512d`，每个向量块不超过 32 MiB 并带 SHA-256；重新生成前应手动移走旧导出目录。生成需要 Conda `macos` 环境和完整缓存。

推送 `main` 后，`.github/workflows/pages.yml` 检查并构建网站，将 `site-data` 一起打包并部署到 GitHub Pages。仓库的 Settings → Pages → Build and deployment → Source 应选择 **GitHub Actions**。目标地址为 `https://wzj9555-cmyk.github.io/GIVA/`；以实际部署结果为准。

本地双击 `start.command` 继续使用 Python 服务和所有本地数据集。不要将 `tif/`、`cache/`、`node_modules/` 或生成的 `deploy-dist/` 放进仓库。
