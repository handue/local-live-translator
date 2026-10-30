#!/bin/zsh
# Usage: ./run.sh [--app zoom] [--to ko] ...   (all options: ./run.sh --help)
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Run ./setup.sh first."; exit 1; }
if ! curl -s localhost:11434/api/tags >/dev/null; then
  echo "· Starting Ollama"
  (ollama serve >/dev/null 2>&1 &)
  for i in {1..20}; do curl -s localhost:11434/api/tags >/dev/null && break; sleep 0.5; done
fi
exec .venv/bin/python app.py "$@"
