#!/usr/bin/env python3
"""
baddle setup — installs llama-cpp-python with GPU support + llama-server binary

Strategy:
  1. Detect CUDA version via nvcc / nvidia-smi
  2. Try pre-built GPU wheel from abetlen's index  (fast, no compiler needed)
  3. Fall back to building from source with CMAKE_ARGS="-DGGML_CUDA=on"
  4. If no CUDA found — install CPU version
  5. Download native llama-server binary for parallel mode
"""
import io
import os
import platform
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path


def run(*cmd, env=None):
    print("$", " ".join(str(c) for c in cmd))
    return subprocess.run(list(cmd), env=env).returncode


def detect_cuda() -> str | None:
    """Return CUDA version string like '12.4', or None."""
    # Try nvcc first (most precise)
    try:
        out = subprocess.run(["nvcc", "--version"], capture_output=True, text=True).stdout
        m = re.search(r"release\s+(\d+\.\d+)", out)
        if m:
            return m.group(1)
    except FileNotFoundError:
        pass

    # Try nvidia-smi (always present if driver is installed)
    try:
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True).stdout
        m = re.search(r"CUDA Version:\s*(\d+\.\d+)", out)
        if m:
            return m.group(1)
    except FileNotFoundError:
        pass

    return None


# Map CUDA version → llama-cpp-python wheel tag
_WHEEL_TAGS = {
    (11, None): "cu118",
    (12,  0):   "cu121",
    (12,  1):   "cu121",
    (12,  2):   "cu122",
    (12,  3):   "cu123",
    (12,  4):   "cu124",
    (12,  5):   "cu125",
    (12,  6):   "cu126",
}

def _wheel_tag(cuda_ver: str) -> str | None:
    major, minor = int(cuda_ver.split(".")[0]), int(cuda_ver.split(".")[1])
    key = (major, minor)
    if key in _WHEEL_TAGS:
        return _WHEEL_TAGS[key]
    # Unknown minor → take the highest known for this major
    candidates = [v for (ma, mi), v in _WHEEL_TAGS.items() if ma == major and mi is not None]
    return candidates[-1] if candidates else None


def install_gpu(cuda_ver: str) -> bool:
    tag = _wheel_tag(cuda_ver)

    # ── attempt 1: pre-built wheel ───────────────────────────────────────────
    if tag:
        index_url = f"https://abetlen.github.io/llama-cpp-python/whl/{tag}"
        print(f"\n[1/2] Trying pre-built wheel for {tag}  ({index_url})")
        rc = run(
            sys.executable, "-m", "pip", "install", "llama-cpp-python",
            "--extra-index-url", index_url,
            "--upgrade",
        )
        if rc == 0:
            return True
        print("Pre-built wheel failed or not yet available for this version.")

    # ── attempt 2: build from source ────────────────────────────────────────
    print("\n[2/2] Building from source with CUDA support (requires CUDA toolkit + MSVC/gcc).")
    print("      This may take several minutes...\n")
    env = {**os.environ, "CMAKE_ARGS": "-DGGML_CUDA=on"}
    rc = subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "llama-cpp-python",
            "--upgrade", "--force-reinstall", "--no-cache-dir",
        ],
        env=env,
    ).returncode
    return rc == 0


def install_cpu() -> bool:
    print("\nInstalling llama-cpp-python (CPU)...")
    rc = run(sys.executable, "-m", "pip", "install", "llama-cpp-python", "--upgrade")
    return rc == 0


_GITHUB_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

# CUDA version → release asset suffix
_SERVER_CUDA_MAP = {
    "12.4": "cuda-12.4",
    "13.1": "cuda-13.1",
}


def _server_cuda_suffix(cuda_ver: str | None) -> str | None:
    """Map CUDA version to llama-server release suffix, or None for CPU."""
    if not cuda_ver:
        return None
    major, minor = int(cuda_ver.split(".")[0]), int(cuda_ver.split(".")[1])
    key = f"{major}.{minor}"
    if key in _SERVER_CUDA_MAP:
        return _SERVER_CUDA_MAP[key]
    # Find closest available for this major
    candidates = [v for k, v in _SERVER_CUDA_MAP.items() if k.startswith(f"{major}.")]
    return candidates[-1] if candidates else None


def _download_and_extract(url: str, dest: Path):
    """Download a zip from url and extract to dest."""
    print(f"  Downloading {url.split('/')[-1]} ...")
    resp = urllib.request.urlopen(url, timeout=120)
    data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)


def download_server(cuda_ver: str | None) -> bool:
    """Download native llama-server binary into llama-server/ folder.

    Returns True if successful, False on any error.
    """
    dest = Path(__file__).resolve().parent / "llama-server"

    # Already downloaded?
    for name in ("llama-server", "llama-server.exe"):
        if (dest / name).is_file():
            print(f"\n✓ llama-server already present in {dest}")
            return True

    if platform.system() != "Windows" or platform.machine() not in ("AMD64", "x86_64"):
        print(f"\n⚠ llama-server auto-download only supports Windows x64.")
        print(f"  Your platform: {platform.system()} {platform.machine()}")
        print(f"  Download manually: https://github.com/ggml-org/llama.cpp/releases")
        print(f"  Or use API mode (Settings → API) — no local server needed.")
        return False

    print("\n── Downloading llama-server ──")

    # Get latest release tag
    try:
        import json as _json
        resp = urllib.request.urlopen(_GITHUB_API, timeout=30)
        release = _json.loads(resp.read())
        tag = release["tag_name"]
        assets = {a["name"]: a["browser_download_url"] for a in release["assets"]}
    except Exception as e:
        print(f"  Failed to fetch release info: {e}")
        return False

    # Determine which archive to download
    cuda_suffix = _server_cuda_suffix(cuda_ver)
    if cuda_suffix:
        bin_name = f"llama-{tag}-bin-win-{cuda_suffix}-x64.zip"
        cudart_name = f"cudart-llama-bin-win-{cuda_suffix}-x64.zip"
    else:
        bin_name = f"llama-{tag}-bin-win-cpu-x64.zip"
        cudart_name = None

    if bin_name not in assets:
        print(f"  Asset {bin_name} not found in release {tag}.")
        print("  Download manually from https://github.com/ggml-org/llama.cpp/releases")
        return False

    dest.mkdir(exist_ok=True)

    try:
        _download_and_extract(assets[bin_name], dest)
        if cudart_name and cudart_name in assets:
            _download_and_extract(assets[cudart_name], dest)
        print(f"  ✓ llama-server installed to {dest}")
        return True
    except Exception as e:
        print(f"  Download failed: {e}")
        return False


def main():
    print("=" * 40)
    print("  baddle setup")
    print("=" * 40, "\n")

    cuda = detect_cuda()

    if cuda:
        print(f"CUDA detected: {cuda}")
        ok = install_gpu(cuda)
    else:
        print("No CUDA detected — installing CPU-only build.")
        ok = install_cpu()

    # UI dependencies
    run(sys.executable, "-m", "pip", "install",
        "numpy", "rich", "prompt_toolkit", "questionary",
        "--upgrade", "--quiet")

    # Create models/ folder if missing
    models_dir = Path(__file__).resolve().parent / "models"
    if not models_dir.exists():
        models_dir.mkdir()
        print(f"\n✓ Created {models_dir}")
        print("  Put a GGUF model there, e.g.: models/Qwen3-8B-Q4_K_M.gguf")

    # llama-server binary (for parallel/compare with true parallelism)
    srv_ok = download_server(cuda)

    if ok:
        print("\n✓ Setup complete.")
        print("  Run:  python main.py         # CLI")
        print("        python ui.py           # Web UI")
        if srv_ok:
            print("        python ui.py --server  # Web UI + parallel server")
    else:
        print("\n✗ Setup failed — check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
