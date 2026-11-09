#!/bin/bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    printf '%s\n' 'Usage: build-pocket.sh <output-directory>' >&2
    exit 2
fi
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    printf '%s\n' 'Pocket TTS currently requires an Apple Silicon Mac.' >&2
    exit 1
fi

source_dir="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$1"
output_dir="$(cd "$1" && pwd)"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/crucible-pocket-build.XXXXXXXX")"
trap 'rm -rf "$build_dir"' EXIT
mkdir -p "$build_dir/package/Sources" "$build_dir/cache" "$build_dir/config" "$build_dir/security" "$build_dir/module-cache"
export SWIFTPM_MODULECACHE_OVERRIDE="$build_dir/module-cache"
cp "$source_dir/Package.swift" "$build_dir/package/"
cp "$source_dir/PocketTts.swift" "$source_dir/TtsProtocol.swift" "$build_dir/package/Sources/"
if [[ -n "${CRUCIBLE_SWIFT_PACKAGE_CACHE:-}" ]]; then
    cp -R "$CRUCIBLE_SWIFT_PACKAGE_CACHE/." "$build_dir/cache/"
fi

swift_build() {
    xcrun swift build --package-path "$build_dir/package" --scratch-path "$build_dir/spm" \
        --cache-path "$build_dir/cache" --config-path "$build_dir/config" --security-path "$build_dir/security" \
        -c release -j 4 "$@"
}
swift_build --product crucible-pocket-tts
bin_dir="$(swift_build --show-bin-path)"
for bundle in "$bin_dir"/*.bundle; do
    [[ ! -d "$bundle" ]] || ditto "$bundle" "$output_dir/$(basename "$bundle")"
done
install -m 644 "$source_dir/pocket-assets.json" "$output_dir/pocket-assets.json"
if [[ -n "${CRUCIBLE_FLUIDAUDIO_SOURCE_DIR:-}" ]]; then
    install -m 644 "$CRUCIBLE_FLUIDAUDIO_SOURCE_DIR/LICENSE" "$output_dir/LICENSE.FluidAudio"
else
    install -m 644 "$build_dir/spm/checkouts/FluidAudio/LICENSE" "$output_dir/LICENSE.FluidAudio"
fi
install -m 755 "$bin_dir/crucible-pocket-tts" "$output_dir/crucible-pocket-tts"
