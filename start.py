"""Bootstrap the macOS TIF workspace in the Conda macos environment."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ENVIRONMENT = "macos"


def find_conda() -> str:
    candidates = [os.environ.get("CONDA_EXE"), shutil.which("conda")]
    candidates.extend(str(Path.home() / name / "bin" / "conda")
                      for name in ("miniconda3", "miniforge3", "anaconda3"))
    candidates.extend(("/opt/anaconda3/bin/conda", "/opt/miniconda3/bin/conda",
                       "/opt/homebrew/Caskroom/miniconda/base/bin/conda"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise RuntimeError("未找到 Conda。请先安装 macOS 版 Miniconda 或 Miniforge。")


def probe_environment(conda: str) -> dict | None:
    code = (
        "import json,os,sys,subprocess; "
        "import numpy,rasterio,sklearn,PIL,fastapi,uvicorn,threadpoolctl,matplotlib; "
        "assert sys.version_info[:2] == (3,12); "
        "assert subprocess.check_output(['node','--version'],text=True).strip().split('.')[0]=='v22'; "
        "print(json.dumps({'python':sys.executable,'env':dict(os.environ)}))"
    )
    result = subprocess.run([conda, "run", "-n", ENVIRONMENT, "python", "-c", code],
                            capture_output=True, text=True)
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def main() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("此启动器用于 macOS。")
    conda = find_conda()
    ready = probe_environment(conda)
    if ready is None:
        envs = subprocess.run([conda, "env", "list", "--json"], check=True,
                              capture_output=True, text=True)
        exists = any(Path(p).name == ENVIRONMENT for p in json.loads(envs.stdout)["envs"])
        operation = "update" if exists else "create"
        print(f"正在为 Conda {ENVIRONMENT} 环境安装所需依赖……", flush=True)
        command = [conda, "env", operation, "--name", ENVIRONMENT,
                   "--file", str(ROOT / "environment.yml")]
        if operation == "create":
            command.append("--yes")
        subprocess.run(command, check=True,
                       env={**os.environ, "CONDA_ALWAYS_YES": "true"})
        ready = probe_environment(conda)
        if ready is None:
            raise RuntimeError("macos 环境依赖检查失败，请检查上方 Conda 安装信息。")
    env = ready["env"]
    env["GIVA_ENV_READY"] = "1"
    os.execve(ready["python"], [ready["python"], str(ROOT / "start_raster.py"), *sys.argv[1:]], env)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n启动已取消。")
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        sys.exit(1)
