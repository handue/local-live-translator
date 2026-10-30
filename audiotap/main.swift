// audiotap: captures macOS system audio with ScreenCaptureKit and writes it to stdout
// as 16kHz mono float32 little-endian PCM.
//
// Usage:
//   audiotap                  # audio from all apps (except itself)
//   audiotap --app zoom       # only apps whose name/bundle ID contains "zoom"
//   audiotap --list           # list running apps
//
// Needs the "Screen & System Audio Recording" permission
// (System Settings > Privacy & Security > Screen & System Audio Recording).

import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

let sampleRate = 16000

func log(_ msg: String) {
    FileHandle.standardError.write(("[audiotap] " + msg + "\n").data(using: .utf8)!)
}

final class AudioOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    let out = FileHandle.standardOutput
    var loggedFormat = false

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid else { return }

        if !loggedFormat, let fmt = sb.formatDescription?.audioStreamBasicDescription {
            log("format: \(Int(fmt.mSampleRate))Hz, \(fmt.mChannelsPerFrame)ch, flags=\(fmt.mFormatFlags)")
            loggedFormat = true
        }

        do {
            try sb.withAudioBufferList { abl, _ in
                // channelCount = 1, so the first buffer is mono float32
                guard let buf = abl.first, let ptr = buf.mData else { return }
                out.write(Data(bytes: ptr, count: Int(buf.mDataByteSize)))
            }
        } catch {
            log("buffer error: \(error)")
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        log("stream stopped: \(error.localizedDescription)")
        exit(1)
    }
}

func run() async {
    let args = CommandLine.arguments
    var appFilters: [String] = []
    var i = 1
    while i < args.count {
        if args[i] == "--app", i + 1 < args.count {
            appFilters.append(args[i + 1].lowercased())
            i += 2
        } else {
            i += 1
        }
    }

    let content: SCShareableContent
    do {
        content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    } catch {
        log("Permission needed: allow your terminal app in System Settings > Privacy & Security > Screen & System Audio Recording, then restart the terminal. (\(error.localizedDescription))")
        exit(2)
    }

    if args.contains("--list") {
        for app in content.applications.sorted(by: { $0.applicationName < $1.applicationName })
        where !app.applicationName.isEmpty {
            print("\(app.applicationName)\t\(app.bundleIdentifier)")
        }
        exit(0)
    }

    guard let display = content.displays.first else {
        log("no display found")
        exit(1)
    }

    let filter: SCContentFilter
    if appFilters.isEmpty {
        filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        log("capturing: all apps")
    } else {
        let apps = content.applications.filter { app in
            let name = app.applicationName.lowercased()
            let bid = app.bundleIdentifier.lowercased()
            return appFilters.contains { name.contains($0) || bid.contains($0) }
        }
        if apps.isEmpty {
            log("no running app matches \(appFilters). Start the app first, or check names with --list.")
            exit(1)
        }
        log("capturing: " + apps.map { $0.applicationName }.joined(separator: ", "))
        filter = SCContentFilter(display: display, including: apps, exceptingWindows: [])
    }

    let config = SCStreamConfiguration()
    config.capturesAudio = true
    config.sampleRate = sampleRate
    config.channelCount = 1
    config.excludesCurrentProcessAudio = true
    // Video is not needed: smallest size, lowest frame rate
    config.width = 2
    config.height = 2
    config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
    config.queueDepth = 3

    let output = AudioOutput()
    let stream = SCStream(filter: filter, configuration: config, delegate: output)
    let queue = DispatchQueue(label: "audiotap.audio")
    do {
        try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: queue)
        try stream.addStreamOutput(output, type: .screen, sampleHandlerQueue: DispatchQueue(label: "audiotap.screen"))
        try await stream.startCapture()
    } catch {
        log("failed to start capture: \(error.localizedDescription)")
        exit(1)
    }
    log("started")

    // Exits via SIGPIPE when the reader closes the pipe
    withExtendedLifetime((stream, output)) {
        while true { sleep(3600) }
    }
}

signal(SIGPIPE, SIG_DFL)
Task { await run() }
dispatchMain()
