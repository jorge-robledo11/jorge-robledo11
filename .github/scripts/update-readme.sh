#!/usr/bin/env bash
set -euo pipefail

README="README.md"
MARKER="<!-- opencommit-run: ${GITHUB_RUN_ID} -->"

if [[ ! -f "$README" ]]; then
    echo "ERROR: $README no existe"
    exit 1
fi

if grep -q '^<!-- opencommit-run:' "$README"; then
    sed -i \
        "s/^<!-- opencommit-run: .* -->$/${MARKER}/" \
        "$README"
else
    printf '\n%s\n' "$MARKER" >> "$README"
fi

git add "$README"

if git diff --cached --quiet; then
    echo "ERROR: no hay cambios para enviar a OpenCommit"
    exit 1
fi

echo "Cambio que recibirá OpenCommit:"
echo

git diff --cached -- "$README"
