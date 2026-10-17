#!/bin/sh
# Assemble a folder that is a complete Hugging Face Space (Docker SDK) from this repo.
# Usage: sh deploy/make_space.sh [output-dir]      (default: space_build/)
# The Space needs: a root Dockerfile, a README.md with the YAML header, and the files the Dockerfile copies. Nothing else.
set -eu
OUT="${1:-space_build}"
rm -rf "$OUT" && mkdir -p "$OUT/deploy"
cp Dockerfile.single "$OUT/Dockerfile"
cp pyproject.toml uv.lock .python-version "$OUT/"
cp -r lineage snapshot "$OUT/"
mkdir "$OUT/web" && cp -r web/package.json web/package-lock.json web/index.html web/vite.config.js web/src "$OUT/web/"
[ -d web/public ] && cp -r web/public "$OUT/web/" || true
cp deploy/start.sh "$OUT/deploy/start.sh"
cat deploy/spaces/README_header.md README.md > "$OUT/README.md"
find "$OUT" -name __pycache__ -prune -exec rm -rf {} +
echo "Space folder ready: $OUT ($(du -sh "$OUT" | cut -f1))"
