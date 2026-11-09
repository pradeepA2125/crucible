import AVFoundation
import CoreML
import FluidAudio
import Foundation

private let sampleRate = 16_000
private let chunk = 2_560

private struct Entry: Decodable {
    let clip: String
    let seconds: Double
}

private struct Result: Encodable {
    let clip: String
    let firstEndpointMs: Int?
    let endpointOffsetMs: Int?
    let transcript: String
}

private final class Endpoint: @unchecked Sendable {
    private let lock = NSLock()
    private var cursor = 0
    private var first: Int?

    func setCursor(_ value: Int) { lock.withLock { cursor = value } }
    func mark() { lock.withLock { if first == nil { first = cursor } } }
    func time() -> Int? { lock.withLock { first } }
}

private enum ReplayError: Error {
    case usage, invalidAudio(URL)
}

@main
struct Replay {
    static func main() async throws {
        let args = CommandLine.arguments
        guard args.count == 5, let debounce = Int(args[3]), debounce >= 0 else { throw ReplayError.usage }
        let manifest = URL(fileURLWithPath: args[1])
        let models = URL(fileURLWithPath: args[2])
        let entries = try JSONDecoder().decode([Entry].self, from: Data(contentsOf: manifest))
        let config = MLModelConfiguration()
        config.computeUnits = .all
        let manager = StreamingEouAsrManager(configuration: config, chunkSize: .ms160, eouDebounceMs: debounce)
        try await manager.loadModels(from: models)
        let results = try await replay(entries, from: manifest.deletingLastPathComponent(), using: manager)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(results).write(to: URL(fileURLWithPath: args[4]), options: .atomic)
    }

    private static func replay(_ entries: [Entry], from directory: URL,
                               using manager: StreamingEouAsrManager) async throws -> [Result] {
        var results: [Result] = []
        for entry in entries {
            let path = directory.appendingPathComponent(entry.clip)
            let file = try AVAudioFile(forReading: path)
            let format = file.processingFormat
            guard format.channelCount == 1, format.sampleRate == Double(sampleRate),
                  let input = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(file.length)),
                  let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(chunk)) else {
                throw ReplayError.invalidAudio(path)
            }
            try file.read(into: input)
            guard let samples = input.floatChannelData?[0], let output = buffer.floatChannelData?[0] else {
                throw ReplayError.invalidAudio(path)
            }
            await manager.reset()
            let endpoint = Endpoint()
            await manager.setEouCallback { _ in endpoint.mark() }
            let length = Int(input.frameLength)
            var cursor = 0
            // Feed at the model's frame cadence in audio time. Keep feeding the original clip
            // after an endpoint so we can score the full transcript separately.
            while cursor < length || (endpoint.time() == nil && cursor < length + 4 * sampleRate) {
                let count = cursor < length ? min(chunk, length - cursor) : chunk
                if cursor < length { output.update(from: samples + cursor, count: count) }
                else { for i in 0..<count { output[i] = 0 } }
                buffer.frameLength = AVAudioFrameCount(count)
                cursor += count
                endpoint.setCursor(cursor * 1_000 / sampleRate)
                _ = try await manager.process(audioBuffer: buffer)
            }
            let first = endpoint.time()
            let text = try await manager.finish()
            let offset = first.map { $0 - Int((entry.seconds * 1_000).rounded()) }
            print("\(entry.clip) endpoint offset \(offset.map(String.init) ?? "none") ms")
            results.append(Result(clip: entry.clip, firstEndpointMs: first, endpointOffsetMs: offset, transcript: text))
        }
        return results
    }
}
