#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
base_python=${MLXP_BASE_PYTHON:-/opt/conda/bin/python3}
env_dir=${MLXP_ENV_DIR:-/root/work/gpic-linux-env}
requirements=${MLXP_REQUIREMENTS:-$repo/requirements-mlxp.txt}

if [[ ! -x "$base_python" ]]; then
    echo "missing MLXP base Python: $base_python" >&2
    exit 2
fi
if [[ ! -f "$requirements" ]]; then
    echo "missing MLXP requirements: $requirements" >&2
    exit 2
fi

base_torch=$(
    "$base_python" -c "import torch; print(torch.__version__)"
)
if [[ -z "$base_torch" ]]; then
    echo "MLXP base Python did not report a PyTorch version" >&2
    exit 2
fi

if [[ ! -x "$env_dir/bin/python" ]]; then
    "$base_python" -m venv --system-site-packages "$env_dir"
fi

"$env_dir/bin/python" -m pip install --upgrade pip
"$env_dir/bin/python" -m pip install --requirement "$requirements"
"$env_dir/bin/python" -m pip install --upgrade --force-reinstall --no-deps \
    "numpy==1.26.4" \
    "scipy==1.14.1"
"$env_dir/bin/python" -m pip check
"$env_dir/bin/python" - <<PY
from pathlib import Path
import numpy
import scipy

env = Path("$env_dir").resolve()
for module in (numpy, scipy):
    module_path = Path(module.__file__).resolve()
    if env not in module_path.parents:
        raise SystemExit(
            f"{module.__name__} was imported outside the MLXP venv: {module_path}"
        )
print(f"numpy={numpy.__version__} path={Path(numpy.__file__).resolve()}")
print(f"scipy={scipy.__version__} path={Path(scipy.__file__).resolve()}")
PY

cuda_libs=$(
    "$env_dir/bin/python" - <<'PY'
from pathlib import Path
import nvidia

root = Path(nvidia.__file__).resolve().parent
print(":".join(str(path) for path in sorted(root.glob("*/lib")) if path.is_dir()))
PY
)
if [[ -n "$cuda_libs" ]]; then
    export LD_LIBRARY_PATH="$cuda_libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

env_torch=$(
    "$env_dir/bin/python" -c "import torch; print(torch.__version__)"
)
if [[ "$env_torch" != "$base_torch" ]]; then
    echo "MLXP runtime replaced base PyTorch: base=$base_torch env=$env_torch" >&2
    exit 3
fi

"$env_dir/bin/python" "$repo/scripts/check_runtime_env.py" \
    --spacy-model en_core_web_trf \
    --require-spacy-gpu \
    --nvidia-smi
