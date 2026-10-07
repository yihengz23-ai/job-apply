// 识别图片里的文字，每行一条：文件路径<TAB>识别出的一行字。用 macOS 自带 Vision，免依赖。
// 用来和 Claude 的识别结果对照，核对图片里的投递邮箱（两种方法独立认，一致才算数）。
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    req.usesLanguageCorrection = false   // 不「纠正」成常见词：邮箱要原样
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    for obs in (req.results ?? []) {
        if let c = obs.topCandidates(1).first {
            print("\(path)\t\(c.string.replacingOccurrences(of: "\n", with: " "))")
        }
    }
}
