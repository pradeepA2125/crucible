import FluidAudio
import Foundation

private struct ModelFiles: Decodable {
    struct Entry: Decodable {
        let path: String
        let bytes: Int64
    }

    let relativeRoot: String
    let files: [Entry]

    static func areInstalled(at directory: URL) -> Bool {
        let executable = URL(fileURLWithPath: CommandLine.arguments[0])
        let manifest = executable.deletingLastPathComponent().appendingPathComponent("pocket-assets.json")
        guard let data = try? Data(contentsOf: manifest),
              let spec = try? JSONDecoder().decode(Self.self, from: data),
              !spec.files.isEmpty else { return false }

        let root = directory.appendingPathComponent(spec.relativeRoot)
        return spec.files.allSatisfy { file in
            let url = root.appendingPathComponent(file.path)
            guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
                  attributes[.type] as? FileAttributeType == .typeRegular,
                  let size = attributes[.size] as? NSNumber else { return false }
            return size.int64Value == file.bytes
        }
    }
}

@MainActor
private final class PocketTts {
    private let manager: PocketTtsManager
    private var active: (id: String, generation: UUID)?
    private var task: Task<Void, Never>?

    init(manager: PocketTtsManager) {
        self.manager = manager
    }

    func receive(_ request: TtsRequest) {
        switch request.type {
        case "cancel":
            if active?.id == request.id { cancel() }
        case "speak":
            guard let text = request.utterance else {
                emit(TtsEvent(type: "error", id: request.id, error: "Expected a short reply to speak."))
                return
            }
            cancel()
            let previous = task
            let generation = UUID()
            active = (request.id, generation)
            task = Task {
                await previous?.value
                guard !Task.isCancelled, self.active?.generation == generation else { return }
                await self.speak(text, id: request.id, generation: generation)
            }
        default:
            emit(TtsEvent(type: "error", id: request.id, error: "Unknown speech request."))
        }
    }

    func finishInput() async {
        await task?.value
    }

    private func cancel() {
        guard let current = active else { return }
        active = nil
        task?.cancel()
        emit(TtsEvent(type: "done", id: current.id))
    }

    private func speak(_ text: String, id: String, generation: UUID) async {
        var hadAudio = false
        do {
            let stream = try await manager.synthesizeStreaming(text: text)
            for try await frame in stream {
                guard !Task.isCancelled, active?.generation == generation else { return }
                guard !frame.samples.isEmpty else { continue }
                hadAudio = true
                let bytes = pcm16(frame.samples)
                emit(TtsEvent(type: "audio", id: id, pcm: bytes.base64EncodedString(),
                              sampleRate: Double(PocketTtsConstants.audioSampleRate)))
            }
        } catch {
            hadAudio = false
        }
        guard !Task.isCancelled, active?.generation == generation else { return }
        active = nil
        if hadAudio {
            emit(TtsEvent(type: "done", id: id))
        } else {
            emit(TtsEvent(type: "error", id: id, error: "Pocket TTS could not synthesize the reply."))
        }
    }
}

@main
struct PocketTtsMain {
    @MainActor
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        guard args.count == 2, args[0] == "--models", args[1].hasPrefix("/") else {
            FileHandle.standardError.write(Data("Usage: crucible-pocket-tts --models /absolute/model-pack\n".utf8))
            exit(2)
        }
        let directory = URL(fileURLWithPath: args[1], isDirectory: true)
        guard ModelFiles.areInstalled(at: directory) else {
            FileHandle.standardError.write(Data("The Pocket TTS model pack is missing or incomplete.\n".utf8))
            exit(1)
        }

        // Upstream info logs include the input utterance. Keep reply text out of diagnostics.
        AppLogger.minimumLevel = .warning
        AppLogger.mirrorsToConsole = false
        let manager = PocketTtsManager(language: .english, directory: directory)
        do {
            try await manager.initialize()
        } catch {
            FileHandle.standardError.write(Data("Could not load the Pocket TTS model pack.\n".utf8))
            exit(1)
        }

        let worker = PocketTts(manager: manager)
        emit(TtsEvent(type: "ready"))
        let requests = AsyncStream<TtsRequest> { continuation in
            readTtsRequests(
                receive: { continuation.yield($0) },
                finish: { continuation.finish() }
            )
        }
        for await request in requests { worker.receive(request) }
        await worker.finishInput()
    }
}
