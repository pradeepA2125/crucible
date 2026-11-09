// swift-tools-version: 6.0
import Foundation
import PackageDescription

let fluidAudio: Package.Dependency
if let source = ProcessInfo.processInfo.environment["CRUCIBLE_FLUIDAUDIO_SOURCE_DIR"], !source.isEmpty {
    fluidAudio = .package(name: "FluidAudio", path: source)
} else {
    fluidAudio = .package(url: "https://github.com/FluidInference/FluidAudio.git", revision: "c8b41f98eae668794dd0083bbe028fe7e6585051")
}

let package = Package(
    name: "CruciblePocketTts",
    platforms: [.macOS(.v14)],
    dependencies: [fluidAudio],
    targets: [
        .executableTarget(
            name: "crucible-pocket-tts",
            dependencies: [.product(name: "FluidAudio", package: "FluidAudio")],
            path: "Sources"
        ),
    ]
)
