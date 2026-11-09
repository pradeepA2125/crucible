import AVFoundation
import Foundation

private enum AudioChunk: Sendable {
    case pcm(Data, Double)
    case done
    case invalid

    init(buffer: AVAudioBuffer) {
        guard let pcm = buffer as? AVAudioPCMBuffer else {
            self = .invalid
            return
        }
        guard pcm.frameLength > 0 else {
            self = .done
            return
        }

        let channels = Int(pcm.format.channelCount)
        let sampleRate = pcm.format.sampleRate
        let frames = Int(pcm.frameLength)
        guard channels > 0, channels <= 8, sampleRate.isFinite, sampleRate > 0,
              frames <= 192_000 else {
            self = .invalid
            return
        }

        let stride = pcm.stride
        let samples: [Float]
        if let data = pcm.floatChannelData {
            samples = Self.mono(data, channels: channels, stride: stride, frames: frames) { $0 }
        } else if let data = pcm.int16ChannelData {
            samples = Self.mono(data, channels: channels, stride: stride, frames: frames) { Float($0) / 32_768 }
        } else if let data = pcm.int32ChannelData {
            samples = Self.mono(data, channels: channels, stride: stride, frames: frames) { Float($0) / 2_147_483_648 }
        } else {
            self = .invalid
            return
        }
        self = .pcm(pcm16(samples), sampleRate)
    }

    private static func mono<T>(
        _ data: UnsafePointer<UnsafeMutablePointer<T>>,
        channels: Int,
        stride: Int,
        frames: Int,
        convert: (T) -> Float
    ) -> [Float] {
        (0..<frames).map { frame in
            var sum: Float = 0
            for channel in 0..<channels {
                sum += convert(data[channel][frame * stride])
            }
            return sum / Float(channels)
        }
    }
}

@MainActor
private final class SystemTts {
    private let synthesizer = AVSpeechSynthesizer()
    private var active: (id: String, generation: UUID, hasAudio: Bool)?
    private var inputEnded = false
    private var pendingAudio = Data()
    private var pendingRate: Double?

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
            let generation = UUID()
            active = (request.id, generation, false)
            let utterance = AVSpeechUtterance(string: text)
            synthesizer.write(utterance) { [weak self] buffer in
                // Apple owns the buffer. Copy the PCM before returning from the callback.
                let chunk = AudioChunk(buffer: buffer)
                DispatchQueue.main.async { self?.handle(chunk, generation: generation) }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                guard let self, self.active?.generation == generation, self.active?.hasAudio == false else { return }
                self.handle(.invalid, generation: generation)
            }
        default:
            emit(TtsEvent(type: "error", id: request.id, error: "Unknown speech request."))
        }
    }

    func finishInput() {
        inputEnded = true
        if active == nil { exit(0) }
    }

    private func cancel() {
        guard let current = active else { return }
        active = nil
        pendingAudio.removeAll(keepingCapacity: true)
        pendingRate = nil
        synthesizer.stopSpeaking(at: .immediate)
        emit(TtsEvent(type: "done", id: current.id))
    }

    private func handle(_ chunk: AudioChunk, generation: UUID) {
        guard let current = active, current.generation == generation else { return }
        switch chunk {
        case let .pcm(bytes, sampleRate):
            if !current.hasAudio {
                active?.hasAudio = true
                emitAudio(bytes, sampleRate: sampleRate, id: current.id)
            } else {
                if pendingRate != sampleRate { flushAudio(id: current.id) }
                pendingRate = sampleRate
                pendingAudio.append(bytes)
                if pendingAudio.count >= Int(sampleRate * 0.04) * 2 { flushAudio(id: current.id) }
            }
        case .done, .invalid:
            active = nil
            if case .done = chunk, current.hasAudio {
                flushAudio(id: current.id)
                emit(TtsEvent(type: "done", id: current.id))
            } else {
                pendingAudio.removeAll(keepingCapacity: true)
                pendingRate = nil
                synthesizer.stopSpeaking(at: .immediate)
                emit(TtsEvent(type: "error", id: current.id, error: "The system voice did not return speech audio."))
            }
            if inputEnded { exit(0) }
        }
    }

    private func flushAudio(id: String) {
        if let pendingRate, !pendingAudio.isEmpty {
            emitAudio(pendingAudio, sampleRate: pendingRate, id: id)
        }
        pendingAudio.removeAll(keepingCapacity: true)
        pendingRate = nil
    }

    private func emitAudio(_ bytes: Data, sampleRate: Double, id: String) {
        emit(TtsEvent(type: "audio", id: id, pcm: bytes.base64EncodedString(), sampleRate: sampleRate))
    }
}

@main
struct SystemTtsMain {
    @MainActor
    static func main() {
        guard Array(CommandLine.arguments.dropFirst()) == ["--engine", "system"] else {
            FileHandle.standardError.write(Data("Usage: crucible-tts --engine system\n".utf8))
            exit(2)
        }
        let worker = SystemTts()
        emit(TtsEvent(type: "ready"))
        readTtsRequests(
            receive: { request in DispatchQueue.main.async { worker.receive(request) } },
            finish: { DispatchQueue.main.async { worker.finishInput() } }
        )
        RunLoop.main.run()
    }
}
