// swift-tools-version:5.9
// The menu-bar app's Swift shell. build.sh compiles it and assembles mkay.app around it.
import PackageDescription

let package = Package(
    name: "mkay",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "mkay", path: "Sources/mkay"),
    ]
)
