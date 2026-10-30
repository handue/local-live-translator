#!/bin/zsh
# One-time setup: Python env, audiotap build, model downloads
set -e
cd "$(dirname "$0")"

[[ "$(uname -m)" == "arm64" ]] || { echo "Apple Silicon (M1 or later) is required."; exit 1; }
command -v swiftc >/dev/null || { echo "Swift compiler not found. Run: xcode-select --install"; exit 1; }
command -v ollama >/dev/null || { echo "Ollama not found. Run: brew install ollama  (or https://ollama.com)"; exit 1; }

echo "▶ Python environment"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "▶ Building audiotap (ScreenCaptureKit system audio capture)"
mkdir -p bin
swiftc -O -o bin/audiotap audiotap/main.swift

echo "▶ Downloading speech model (parakeet-tdt-0.6b-v2, ~2.5GB)"
.venv/bin/python -c "from parakeet_mlx import from_pretrained; from_pretrained('mlx-community/parakeet-tdt-0.6b-v2')"

echo "▶ Downloading translation model (${SUBS_LLM:-gemma3:4b})"
if ! curl -s localhost:11434/api/tags >/dev/null; then
  (ollama serve >/dev/null 2>&1 &)
  for i in {1..20}; do curl -s localhost:11434/api/tags >/dev/null && break; sleep 0.5; done
fi
ollama pull "${SUBS_LLM:-gemma3:4b}"

[ -f context.txt ] || cp context.example.txt context.txt
echo "✅ Done. Start with: ./run.sh"
