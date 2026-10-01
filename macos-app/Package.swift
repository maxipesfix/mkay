// swift-tools-version:5.9
// The menu-bar app's Swift shell. build.sh compiles it and assembles mkay.app around it.
import PackageDescription

let package = Package(
    name: "mkay",
    platforms: [.macOS(.v13)],
    // Updates. release.sh downloads the same version's signing tools: keep them in step.
    dependencies: [.package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.0")],
    targets: [
        .executableTarget(name: "mkay", dependencies: [.product(name: "Sparkle", package: "Sparkle")],
                          path: "Sources/mkay"),
    ]
)
