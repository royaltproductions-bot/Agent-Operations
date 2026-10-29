#!/usr/bin/env bash
set -euo pipefail
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  sudo systemctl stop ollama.service 2>/dev/null || true
  mkdir -p "$OLLAMA_MODELS"
  nohup ollama serve >/tmp/gambit-ollama.log 2>&1 &
fi
for attempt in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! ollama show "$GAMBIT_TRIAGE_MODEL" >/dev/null 2>&1; then ollama pull "$GAMBIT_TRIAGE_MODEL"; fi
if [ "${1:-triage}" = production ] && ! ollama show "$GAMBIT_MODEL" >/dev/null 2>&1; then
  ollama pull "$GAMBIT_MODEL"
fi
