#!/usr/bin/env bash
# Render every Mermaid source in diagrams/src to diagrams/svg. Requires mermaid-cli (mmdc).
# Usage: bash diagrams/render.sh          render all
#        bash diagrams/render.sh --check  fail if any source does not parse (writes to a temp dir)
set -euo pipefail
cd "$(dirname "$0")"
out=svg
[ "${1:-}" = "--check" ] && out="$(mktemp -d)"
cfg="$(mktemp)"; echo '{"args":["--no-sandbox"]}' > "$cfg"
fail=0
for f in src/*.mmd; do
  n="$(basename "$f" .mmd)"
  if mmdc -q -i "$f" -o "$out/$n.svg" -p "$cfg" -b transparent >/dev/null 2>&1; then echo "ok   $n"; else echo "FAIL $n"; fail=1; fi
done
exit $fail
