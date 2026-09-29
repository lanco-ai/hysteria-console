#!/usr/bin/env bash
# Explicit setup for the optional learning tools; does not restart any service.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
learning_root="${HY2_HY_DIR:-/root/hysteria}"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim poppler-utils
"$learning_root/.venv-web/bin/python" -m pip install --no-cache-dir -r "$repo_dir/requirements-web.txt"
python3 -m venv "$learning_root/.venv-knowledge"
"$learning_root/.venv-knowledge/bin/python" -m pip install --no-cache-dir -r "$repo_dir/requirements-knowledge.txt"
HY2_EMBEDDING_MODEL_DIR="$learning_root/models/multilingual-e5-small" python3 "$repo_dir/scripts/prepare-learning-model.py"
docker build --tag hy2-learning-python:1 "$repo_dir/hysteria/learning_sandbox"
