import Darwin
import Foundation

struct TtsRequest: Decodable, Sendable {
    let type: String
    let id: String
    let text: String?

    var validID: Bool {
        let bytes = id.utf8
        return !bytes.isEmpty && bytes.count <= 80 && bytes.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0)
                || [45, 46, 58, 95].contains($0)
        }
    }

    var utterance: String? {
        guard let text, text.utf8.count <= 4_096 else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct TtsEvent: Encodable {
    let type: String
    var id: String?
    var pcm: String?
    var sampleRate: Double?
    var error: String?
}

func emit(_ event: TtsEvent) {
    guard var line = try? JSONEncoder().encode(event) else { return }
    line.append(0x0a)
    FileHandle.standardOutput.write(line)
}

func readTtsRequests(
    receive: @escaping @Sendable (TtsRequest) -> Void,
    finish: @escaping @Sendable () -> Void
) {
    DispatchQueue.global(qos: .userInitiated).async {
        let decoder = JSONDecoder()
        let maxLineBytes = 32_768
        var line = Data()
        var discard = false

        func deliver() {
            if !discard, let request = try? decoder.decode(TtsRequest.self, from: line), request.validID {
                receive(request)
            }
            line.removeAll(keepingCapacity: true)
            discard = false
        }

        var buffer = [UInt8](repeating: 0, count: 8_192)
        while true {
            let count = buffer.withUnsafeMutableBytes {
                Darwin.read(STDIN_FILENO, $0.baseAddress, $0.count)
            }
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { break }
            var remaining = buffer[..<count]
            while !remaining.isEmpty {
                let newline = remaining.firstIndex(of: 0x0a)
                let end = newline ?? remaining.endIndex
                if !discard {
                    let fragment = remaining[..<end]
                    if line.count + fragment.count <= maxLineBytes {
                        line.append(contentsOf: fragment)
                    } else {
                        discard = true
                    }
                }
                guard let newline else { break }
                deliver()
                remaining = remaining[remaining.index(after: newline)...]
            }
        }

        if !line.isEmpty { deliver() }
        finish()
    }
}

func pcm16(_ samples: [Float]) -> Data {
    let words = samples.map { sample -> Int16 in
        guard sample.isFinite else { return 0 }
        let clamped = min(1, max(-1, sample))
        return Int16(clamping: Int((clamped * 32_768).rounded())).littleEndian
    }
    return words.withUnsafeBytes { Data($0) }
}
