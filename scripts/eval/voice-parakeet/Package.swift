// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "VoiceParakeetReplay",
    platforms: [.macOS(.v14)],
    dependencies: [.package(url: "https://github.com/FluidInference/FluidAudio.git", exact: "0.18.3")],
    targets: [
        .executableTarget(name: "VoiceParakeetReplay", dependencies: [
            .product(name: "FluidAudio", package: "FluidAudio"),
        ], path: ".", exclude: ["Package.swift"], sources: ["Replay.swift"]),
    ]
)
