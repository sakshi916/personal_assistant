// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PersonalAssistant",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "PersonalAssistant", targets: ["PersonalAssistant"])
    ],
    dependencies: [
        .package(url: "https://github.com/argmaxinc/argmax-oss-swift.git", exact: "0.9.4"),
        .package(url: "https://github.com/gonzalezreal/swift-markdown-ui.git", exact: "2.4.1")
    ],
    targets: [
        .executableTarget(
            name: "PersonalAssistant",
            dependencies: [
                .product(name: "WhisperKit", package: "argmax-oss-swift"),
                .product(name: "MarkdownUI", package: "swift-markdown-ui")
            ]
        )
    ]
)
