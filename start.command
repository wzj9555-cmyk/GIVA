#!/bin/bash
set -eu
cd "$(dirname "$0")"
conda_bin="${CONDA_EXE:-}"
if [ -z "$conda_bin" ] || [ ! -x "$conda_bin" ]; then
  conda_bin="$(command -v conda || true)"
fi
if [ -z "$conda_bin" ] || [ ! -x "$conda_bin" ]; then
  for candidate in "$HOME/miniconda3/bin/conda" "$HOME/miniforge3/bin/conda" "$HOME/anaconda3/bin/conda" /opt/anaconda3/bin/conda /opt/miniconda3/bin/conda /opt/homebrew/Caskroom/miniconda/base/bin/conda; do
    if [ -x "$candidate" ]; then conda_bin="$candidate"; break; fi
  done
fi
if [ -z "$conda_bin" ] || [ ! -x "$conda_bin" ]; then
  echo "未找到 Conda，请先安装 macOS 版 Miniconda 或 Miniforge。" >&2
  exit 1
fi
export CONDA_EXE="$conda_bin"
conda_root="$("$conda_bin" info --base)"
exec "$conda_root/bin/python" start.py "$@"
