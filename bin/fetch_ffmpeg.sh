#!/bin/sh
# Fetch static ffmpeg + ffprobe (macOS, no Homebrew) into this dir.
# config.py prepends ./bin to PATH so the pipeline finds them.
set -e
cd "$(dirname "$0")"
for tool in ffmpeg ffprobe; do
  curl -sL "https://evermeet.cx/ffmpeg/getrelease/$tool/zip" -o "$tool.zip"
  unzip -o -q "$tool.zip" && chmod +x "$tool" && rm -f "$tool.zip"
  echo "installed $tool: $(./$tool -version | head -1)"
done
