# Crucible native voice workers

Build on an Apple Silicon Mac with CMake and Apple's developer tools. The current workers target macOS 14 or newer.

```bash
apps/desktop/voice-native/build.sh /absolute/path/to/voice-resources
```

This produces `crucible-whisper`, `crucible-tts`, and the Whisper source license. It fetches the checksum-pinned Whisper.cpp v1.9.5 source archive. For an offline build, set `CRUCIBLE_WHISPER_SOURCE_DIR` to an already verified copy of that source. The binaries contain no downloaded speech models.

To compare the macOS system voice with Pocket TTS, also build the optional helper with a recent Apple Swift toolchain that can compile FluidAudio.

```bash
apps/desktop/voice-native/build-pocket.sh /absolute/path/to/voice-resources
```

This produces `crucible-pocket-tts`, its SwiftPM resource bundle, the FluidAudio license, and `pocket-assets.json`. Keep these files and the bundle beside the helper in the desktop voice resources directory. The pack manifest pins the English FP16 files used by the pilot, including their sizes and SHA-256 hashes. Pocket loads only a complete local pack from the supplied directory. Its English model is distributed through the FluidInference conversion of Kyutai Pocket TTS. [FluidAudio v0.18.3](https://github.com/FluidInference/FluidAudio/releases/tag/v0.18.3) · [Pocket TTS Core ML model](https://huggingface.co/FluidInference/pocket-tts-coreml)

## Worker protocols

`crucible-whisper --model /absolute/path/to/model.bin` loads and warms one model, then prints `ready`. Pass each English audio window as one line with a request ID, a tab, and base64-encoded 16 kHz mono PCM16 little-endian audio. Windows may contain up to 30 seconds of audio. The worker returns one tab-separated result or error for each valid request ID. IDs allow 1–80 ASCII letters, digits, hyphens, underscores, periods, or colons. The desktop host chooses when to send rolling windows and final utterances. Pass `--cpu` after the model path to compare CPU inference.

`crucible-whisper --vad /absolute/path/to/ggml-silero-v6.2.0.bin` runs Silero voice activity detection instead. After `ready`, send `id<TAB>base64 PCM16` lines of up to one second; each reply is `id<TAB>result<TAB>p1,p2,…` with one speech probability per 512-sample (32 ms) window, buffering any remainder for the next line. `id<TAB>reset` clears the streaming state between utterances.

`crucible-tts --engine system` and `crucible-pocket-tts --models /absolute/path/to/pack` use one JSON object per line. The worker emits `{ "type": "ready" }` before reading requests.

- `{ "type": "speak", "id": "...", "text": "..." }` starts generating audio. Text is limited to 4,096 UTF-8 bytes.
- `{ "type": "audio", "id": "...", "pcm": "...", "sampleRate": 22050 }` carries base64-encoded mono PCM16 little-endian audio. The sample rate comes from the engine.
- `{ "type": "cancel", "id": "..." }` drops remaining audio for that generation. Starting another request also cancels the previous one.
- `{ "type": "done", "id": "..." }` marks completion or cancellation. A failed request emits `{ "type": "error", "id": "...", "error": "..." }` instead.

Workers keep audio and text in process memory. The desktop host owns microphone access, audio playback, model installation and the voice session lifetime.
