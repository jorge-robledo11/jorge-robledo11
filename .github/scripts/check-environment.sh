#!/usr/bin/env bash
set -euo pipefail

echo "Checking OpenCommit..."

if ! command -v oco >/dev/null 2>&1; then
    echo "ERROR: oco no está disponible en PATH"
    exit 1
fi

echo "OpenCommit disponible:"
command -v oco

echo
echo "Checking Ollama..."

if ! curl \
    --fail \
    --silent \
    --show-error \
    "${OCO_API_URL}/api/tags" \
    >/dev/null; then

    echo "ERROR: Ollama no responde en ${OCO_API_URL}"
    exit 1
fi

echo "Ollama disponible en ${OCO_API_URL}"