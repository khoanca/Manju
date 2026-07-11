// Phase 0 (BRD mục 4): benchmark SpeechTranscriber (macOS 26, on-device) trên
// file audio, đối chiếu với mlx-whisper. Stdout là JSON lines để script Python
// đọc; log người đọc ra stderr.
//
// Build:  swiftc -O native/asr/main.swift -o native/bin/native-asr
// Usage:  native-asr <audio-file> [locale]   (mặc định vi_VN)
//
// JSON lines:
//   {"type":"final","start":1.2,"end":4.5,"text":"..."}
//   {"type":"done","audio_s":..,"elapsed_s":..,"speed":..,"segments":N}

import AVFoundation
import Foundation
import Speech

func log(_ s: String) {
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
}

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
          let line = String(data: data, encoding: .utf8)
    else { return }
    print(line)
}

// SpeechTranscriber.Result và DictationTranscriber.Result cùng shape nhưng là
// 2 type riêng → gom về một struct chung để vòng collector dùng chung.
struct AnyTranscriberResult {
    let text: String
    let isFinal: Bool
    let range: CMTimeRange
}

func wrapResults<S: AsyncSequence>(
    _ seq: S, _ transform: @escaping (S.Element) -> AnyTranscriberResult
) -> AsyncThrowingStream<AnyTranscriberResult, Error> {
    AsyncThrowingStream { continuation in
        let task = Task {
            do {
                for try await item in seq { continuation.yield(transform(item)) }
                continuation.finish()
            } catch {
                continuation.finish(throwing: error)
            }
        }
        continuation.onTermination = { _ in task.cancel() }
    }
}

guard CommandLine.arguments.count >= 2 else {
    log("usage: native-asr <audio-file> [locale]")
    exit(1)
}
let audioURL = URL(fileURLWithPath: CommandLine.arguments[1])
let localeID = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "vi_VN"
let locale = Locale(identifier: localeID)

func matches(_ locales: [Locale]) -> Bool {
    locales.contains { $0.identifier(.bcp47) == locale.identifier(.bcp47) }
}

do {
    // Model mới (SpeechTranscriber) chất lượng cao nhưng ít ngôn ngữ; chưa có
    // vi_VN trên macOS 26.5 → fallback DictationTranscriber (model dictation
    // on-device của bàn phím, phủ rộng hơn). Cả hai đều là module SpeechAnalyzer.
    let stLocales = await SpeechTranscriber.supportedLocales
    let module: any SpeechModule
    let results: AsyncThrowingStream<AnyTranscriberResult, Error>

    if matches(stLocales) {
        log("Engine: SpeechTranscriber")
        let t = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [],  // benchmark chỉ cần final, không cần volatile
            attributeOptions: [.audioTimeRange]
        )
        module = t
        results = wrapResults(t.results) {
            AnyTranscriberResult(text: String($0.text.characters), isFinal: $0.isFinal, range: $0.range)
        }
    } else if matches(await DictationTranscriber.supportedLocales) {
        log("Engine: DictationTranscriber (SpeechTranscriber chưa hỗ trợ \(localeID))")
        let t = DictationTranscriber(
            locale: locale,
            contentHints: [],
            transcriptionOptions: [],
            reportingOptions: [],
            attributeOptions: [.audioTimeRange]
        )
        module = t
        results = wrapResults(t.results) {
            AnyTranscriberResult(text: String($0.text.characters), isFinal: $0.isFinal, range: $0.range)
        }
    } else {
        log("Locale \(localeID) không được hỗ trợ.")
        log("SpeechTranscriber: " + stLocales.map { $0.identifier }.sorted().joined(separator: " "))
        let dtLocales = await DictationTranscriber.supportedLocales
        log("DictationTranscriber: " + dtLocales.map { $0.identifier }.sorted().joined(separator: " "))
        exit(2)
    }

    // Lần đầu phải tải model on-device về (một lần cho cả máy).
    if let request = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
        log("Đang tải model \(localeID) (chỉ lần đầu)...")
        try await request.downloadAndInstall()
        log("Tải model xong.")
    }

    let file = try AVAudioFile(forReading: audioURL)
    let audioSeconds = Double(file.length) / file.processingFormat.sampleRate

    let analyzer = SpeechAnalyzer(modules: [module])

    let collector = Task {
        var count = 0
        for try await result in results where result.isFinal {
            emit([
                "type": "final",
                "start": (result.range.start.seconds * 100).rounded() / 100,
                "end": (result.range.end.seconds * 100).rounded() / 100,
                "text": result.text,
            ])
            count += 1
        }
        return count
    }

    let started = Date()
    if let lastSample = try await analyzer.analyzeSequence(from: file) {
        try await analyzer.finalizeAndFinish(through: lastSample)
    } else {
        await analyzer.cancelAndFinishNow()
    }
    let segments = try await collector.value
    let elapsed = Date().timeIntervalSince(started)

    emit([
        "type": "done",
        "audio_s": (audioSeconds * 100).rounded() / 100,
        "elapsed_s": (elapsed * 1000).rounded() / 1000,
        "speed": ((audioSeconds / max(elapsed, 0.001)) * 10).rounded() / 10,
        "segments": segments,
    ])
} catch {
    log("Lỗi: \(error)")
    exit(3)
}
