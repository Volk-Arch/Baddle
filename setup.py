#!/usr/bin/env python3
"""
baddle setup — installs llama-cpp-python with GPU support

Strategy:
  1. Detect CUDA version via nvcc / nvidia-smi
  2. Try pre-built GPU wheel from abetlen's index  (fast, no compiler needed)
  3. Fall back to building from source with CMAKE_ARGS="-DGGML_CUDA=on"
  4. If no CUDA found — install CPU version
"""
import os
import re
import subprocess
import sys


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

    if ok:
        print("\n✓ Setup complete.")
        print("  Run:  python main.py step")
        print("        python main.py parallel")
    else:
        print("\n✗ Setup failed — check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
