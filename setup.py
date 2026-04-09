#!/usr/bin/env python3
"""
baddle setup — install Python dependencies.

Baddle uses an OpenAI-compatible API for all LLM calls. You need a local or
remote endpoint (LM Studio, llama-server, Ollama, OpenAI, etc.). After setup,
configure it in Settings on first launch.
"""
import subprocess
import sys


def main():
    print("=" * 40)
    print("  baddle setup")
    print("=" * 40, "\n")

    print("Installing Python dependencies...")
    rc = subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "flask", "numpy", "--upgrade"],
    ).returncode

    if rc != 0:
        print("\n✗ Setup failed — check the errors above.")
        sys.exit(1)

    print("\n✓ Setup complete.\n")
    print("Next steps:")
    print("  1. Start an OpenAI-compatible LLM server. Options:")
    print("     - LM Studio (easiest): https://lmstudio.ai")
    print("     - llama-server: https://github.com/ggml-org/llama.cpp")
    print("     - Ollama with OpenAI compat: https://ollama.com")
    print("     - Any OpenAI-compatible API (OpenAI, Groq, Together, ...)")
    print()
    print("  2. Run the UI:  python ui.py")
    print("  3. Open Settings in the UI and set api_url + api_model.")
    print()
    print("  For best results load both a chat model (e.g. qwen/qwen3-8b)")
    print("  and an embedding model (e.g. text-embedding-nomic-embed-text-v1.5).")


if __name__ == "__main__":
    main()
