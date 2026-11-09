#!/bin/bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    printf '%s\n' 'Usage: build.sh <output-directory>' >&2
    exit 2
fi
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    printf '%s\n' 'Crucible native speech currently requires an Apple Silicon Mac.' >&2
    exit 1
fi

source_dir="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$1"
output_dir="$(cd "$1" && pwd)"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/crucible-voice-build.XXXXXXXX")"
trap 'rm -rf "$build_dir"' EXIT
mkdir "$build_dir/bin"

set --
if [[ -n "${CRUCIBLE_WHISPER_SOURCE_DIR:-}" ]]; then
    set -- "-DFETCHCONTENT_SOURCE_DIR_WHISPER=$CRUCIBLE_WHISPER_SOURCE_DIR"
fi
cmake -S "$source_dir" -B "$build_dir/whisper" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0 "$@"
cmake --build "$build_dir/whisper" --target crucible-whisper --parallel 4
cmake --install "$build_dir/whisper" --prefix "$build_dir/bin" --component VoiceWorker

xcrun swiftc -O -target arm64-apple-macosx14.0 -module-cache-path "$build_dir/swift-cache" \
    "$source_dir/TtsProtocol.swift" "$source_dir/SystemTts.swift" \
    -o "$build_dir/bin/crucible-tts"

install -m 644 "$build_dir/bin/LICENSE.whisper.cpp" "$output_dir/LICENSE.whisper.cpp"
install -m 755 "$build_dir/bin/crucible-whisper" "$build_dir/bin/crucible-tts" "$output_dir"
