// Emit mono Float32 PCM as it is synthesised; never play it on the Mac.
// Frame: little-endian UInt32 sample_rate, UInt32 sample_count, Float32 samples.
import Foundation
import AVFoundation
import CoreFoundation
let synth = AVSpeechSynthesizer()
while let line = readLine() {
guard let text = try? JSONDecoder().decode(String.self, from: Data(line.utf8)) else { exit(2) }
let utterance = AVSpeechUtterance(string: text)
utterance.voice = AVSpeechSynthesisVoice.speechVoices().first { $0.name.lowercased() == "tingting" }
    ?? AVSpeechSynthesisVoice(language: "zh-CN")
utterance.rate = 0.52
let mainLoop = CFRunLoopGetMain()
synth.write(utterance) { buffer in
    guard let pcm = buffer as? AVAudioPCMBuffer else { return }
    let count = Int(pcm.frameLength)
    if count == 0 {
        FileHandle.standardOutput.write(Data(repeating: 0, count: 8))
        CFRunLoopStop(mainLoop)
        return
    }
    guard let channels = pcm.floatChannelData else {
        FileHandle.standardError.write(Data("Unsupported speech PCM format\n".utf8))
        exit(2)
    }
    var rate = UInt32(pcm.format.sampleRate).littleEndian
    var length = UInt32(count).littleEndian
    var header = Data(bytes: &rate, count: 4)
    header.append(Data(bytes: &length, count: 4))
    FileHandle.standardOutput.write(header)
    // Speech voices produce non-interleaved Float32; select the mono voice channel.
    FileHandle.standardOutput.write(Data(bytes: channels[0], count: count * 4))
}
CFRunLoopRun()

}
