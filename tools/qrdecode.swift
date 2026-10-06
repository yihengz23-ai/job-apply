// 识别图片里的二维码 / 条码，每个结果一行：文件路径<TAB>内容。用 macOS 自带 Vision，免依赖。
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNDetectBarcodesRequest()
    req.symbologies = [.qr, .aztec, .dataMatrix, .pdf417]
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    for obs in (req.results ?? []) {
        if let s = obs.payloadStringValue, !s.isEmpty {
            print("\(path)\t\(s.replacingOccurrences(of: "\n", with: " "))")
        }
    }
}
