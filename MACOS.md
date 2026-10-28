# macOS 部署

本包只提供全量 TIF 工作台。程序使用 Conda **macos** 环境（Python 3.12），不创建项目内 Python 虚拟环境。

1. 保持 `GIVA-Chinese`、`tif`、`cache` 位于同一父目录。
2. 双击 `GIVA-Chinese/start.command`。也可在任意终端通过完整路径执行该文件，不要求提前激活 Conda。
3. 浏览器打开 `http://127.0.0.1:8000/`；关闭服务使用 Ctrl+C。

两个 `.command` 入口行为一致。Finder 无法执行时，在终端运行 `bash /完整路径/GIVA-Chinese/start.command`。若迁移导致执行位丢失，可运行 `chmod +x start.command start-raster.command`。

启动器优先使用 `CONDA_EXE`、PATH 中的 Conda，随后检查用户目录的 miniconda3、miniforge3、anaconda3 及常见 `/opt` 安装位置。安装在其他位置时设置 `CONDA_EXE` 为实际 Conda 可执行文件路径。

原始 TIF 只放在同级 `tif`。首次生成会占用时间、内存和磁盘；当前两份 512 维数据每份生成结果约 1.2 GB，建议保留至少 8 GB 可用磁盘空间。没有 CUDA 或独立数据库要求。支持移动到包含中文、空格的路径。

常用命令：

```bash
./start.command --no-browser
./start.command --port 8010
./start.command --force-rebuild
```

`--source` 指定输入目录中的单个 TIF；`--input-dir` 与 `--output` 分别覆盖原始数据目录和缓存根目录。服务仅监听 `127.0.0.1`。

完整操作与环境安装见 [README](README.md)。
