#!/usr/bin/env bash
# Скачивание русских голосов Piper (medium) в ../voices
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
VOICES_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/voices"
REV="${PIPER_VOICES_REV:-a31bce3ed50c05399b2a830efd1c607df03cf4b5}"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/${REV}/ru/ru_RU"

VOICES=(denis dmitri irina ruslan)

mkdir -p "$VOICES_DIR"
cd "$VOICES_DIR"

if ! command -v wget >/dev/null 2>&1; then
  echo "Нужен wget" >&2
  exit 1
fi

for name in "${VOICES[@]}"; do
  onnx="ru_RU-${name}-medium.onnx"
  json="${onnx}.json"
  if [[ -f "$onnx" && -f "$json" && $(stat -c%s "$onnx") -gt 1000000 ]]; then
    echo "OK  $name (уже есть)"
    continue
  fi
  echo ">>> $name"
  wget -c -O "${onnx}.partial" "${BASE}/${name}/medium/${onnx}"
  mv "${onnx}.partial" "$onnx"
  wget -c -O "${json}.partial" "${BASE}/${name}/medium/${json}"
  mv "${json}.partial" "$json"
  echo "OK  $name ($(stat -c%s "$onnx") bytes)"
done

echo
echo "Готово: $VOICES_DIR"
ls -lh "$VOICES_DIR"
