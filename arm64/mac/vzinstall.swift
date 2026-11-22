// vzinstall: restore a macOS IPSW into a raw sparse disk image with
// Virtualization.framework, and run the result in a window (Setup Assistant).
import Foundation
import Virtualization
import AppKit

func die(_ m: String) -> Never { FileHandle.standardError.write(Data((m + "\n").utf8)); exit(1) }

let args = CommandLine.arguments
guard args.count >= 3 else { die("usage: vzinstall install <ipsw> <dir> [sizeGiB] | run <dir>") }
let mode = args[1]

func machineConfig(dir: URL, ipsw: VZMacOSRestoreImage?, create: Bool, sizeGiB: UInt64) throws -> VZVirtualMachineConfiguration {
    let disk = dir.appendingPathComponent("disk.img")
    let aux = dir.appendingPathComponent("aux.img")
    let hwURL = dir.appendingPathComponent("hardwaremodel.bin")
    let midURL = dir.appendingPathComponent("machineid.bin")
    let hw: VZMacHardwareModel
    let mid: VZMacMachineIdentifier
    let auxStorage: VZMacAuxiliaryStorage
    if create {
        guard let req = ipsw?.mostFeaturefulSupportedConfiguration else { die("no supported configuration") }
        hw = req.hardwareModel
        mid = VZMacMachineIdentifier()
        try hw.dataRepresentation.write(to: hwURL)
        try mid.dataRepresentation.write(to: midURL)
        auxStorage = try VZMacAuxiliaryStorage(creatingStorageAt: aux, hardwareModel: hw, options: [])
        let fd = open(disk.path, O_RDWR | O_CREAT, 0o644)
        if fd < 0 { die("create disk failed") }
        if ftruncate(fd, off_t(sizeGiB << 30)) != 0 { die("ftruncate failed") }
        close(fd)
        print("minCPU \(req.minimumSupportedCPUCount) minMem \(req.minimumSupportedMemorySize)")
    } else {
        hw = VZMacHardwareModel(dataRepresentation: try Data(contentsOf: hwURL))!
        mid = VZMacMachineIdentifier(dataRepresentation: try Data(contentsOf: midURL))!
        auxStorage = VZMacAuxiliaryStorage(contentsOf: aux)
    }
    let c = VZVirtualMachineConfiguration()
    let plat = VZMacPlatformConfiguration()
    plat.hardwareModel = hw; plat.machineIdentifier = mid; plat.auxiliaryStorage = auxStorage
    c.platform = plat
    c.bootLoader = VZMacOSBootLoader()
    c.cpuCount = 4
    c.memorySize = 8 << 30
    let att = try VZDiskImageStorageDeviceAttachment(url: disk, readOnly: false, cachingMode: .automatic, synchronizationMode: .full)
    c.storageDevices = [VZVirtioBlockDeviceConfiguration(attachment: att)]
    let net = VZVirtioNetworkDeviceConfiguration()
    net.attachment = VZNATNetworkDeviceAttachment()
    c.networkDevices = [net]
    let gfx = VZMacGraphicsDeviceConfiguration()
    gfx.displays = [VZMacGraphicsDisplayConfiguration(widthInPixels: 1440, heightInPixels: 900, pixelsPerInch: 144)]
    c.graphicsDevices = [gfx]
    c.keyboards = [VZMacKeyboardConfiguration()]
    c.pointingDevices = [VZMacTrackpadConfiguration()]
    try c.validate()
    return c
}

let dir = URL(fileURLWithPath: args[mode == "install" ? 3 : 2])
try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

if mode == "install" {
    let ipsw = URL(fileURLWithPath: args[2])
    let size = args.count > 4 ? UInt64(args[4])! : 64
    VZMacOSRestoreImage.load(from: ipsw) { r in
        switch r {
        case .failure(let e): die("load: \(e)")
        case .success(let img): DispatchQueue.main.async {
            print("image build \(img.buildVersion) os \(img.operatingSystemVersion)")
            do {
                let cfg = try machineConfig(dir: dir, ipsw: img, create: true, sizeGiB: size)
                let vm = VZVirtualMachine(configuration: cfg)
                let inst = VZMacOSInstaller(virtualMachine: vm, restoringFromImageAt: ipsw)
                let obs = inst.progress.observe(\.fractionCompleted, options: [.new]) { p, _ in
                    print(String(format: "progress %.1f%%", p.fractionCompleted * 100)); fflush(stdout)
                }
                _ = obs
                inst.install { res in
                    switch res {
                    case .success: print("INSTALL OK"); exit(0)
                    case .failure(let e): die("install failed: \(e)")
                    }
                }
            } catch { die("config: \(error)") }
        }}
    }
    dispatchMain()
} else if mode == "run" {
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    let cfg = try machineConfig(dir: dir, ipsw: nil, create: false, sizeGiB: 0)
    let vm = VZVirtualMachine(configuration: cfg)
    let view = VZVirtualMachineView(frame: NSRect(x: 0, y: 0, width: 1440, height: 900))
    view.virtualMachine = vm
    view.capturesSystemKeys = true
    let win = NSWindow(contentRect: view.frame, styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
    win.contentView = view; win.title = "p5 macOS"; win.makeKeyAndOrderFront(nil)
    app.activate(ignoringOtherApps: true)
    vm.start { r in if case .failure(let e) = r { die("start: \(e)") } else { print("VM started") } }
    // control channel: lines in <dir>/ctl: "type TEXT", "key CODE [shift]", "click X Y", "shot PATH"
    let ctl = dir.appendingPathComponent("ctl")
    FileManager.default.createFile(atPath: ctl.path, contents: nil)
    var done = 0
    func ev(_ down: Bool, _ code: UInt16, _ chars: String, _ mods: NSEvent.ModifierFlags = []) -> NSEvent {
        NSEvent.keyEvent(with: down ? .keyDown : .keyUp, location: .zero, modifierFlags: mods, timestamp: ProcessInfo.processInfo.systemUptime,
                         windowNumber: win.windowNumber, context: nil, characters: chars, charactersIgnoringModifiers: chars.lowercased(), isARepeat: false, keyCode: code)!
    }
    let codes: [Character: UInt16] = ["a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,"b":11,"q":12,"w":13,"e":14,"r":15,"y":16,"t":17,"1":18,"2":19,"3":20,"4":21,"6":22,"5":23,"=":24,"9":25,"7":26,"-":27,"8":28,"0":29,"]":30,"o":31,"u":32,"[":33,"i":34,"p":35,"l":37,"j":38,"'":39,"k":40,";":41,"\\":42,",":43,"/":44,"n":45,"m":46,".":47," ":49]
    func click(_ x: Double, _ y: Double) {
        let h = view.bounds.height
        let p = NSPoint(x: x, y: h - y)
        for t in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            let e = NSEvent.mouseEvent(with: t, location: view.convert(p, to: nil), modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                                       windowNumber: win.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)!
            if t == .leftMouseDown { view.mouseDown(with: e) } else { view.mouseUp(with: e) }
        }
    }
    Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
        guard let txt = try? String(contentsOf: ctl, encoding: .utf8) else { return }
        let lines = txt.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        while done < lines.count - 1 {
            let l = lines[done]; done += 1
            let parts = l.split(separator: " ", maxSplits: 1).map(String.init)
            guard let cmd = parts.first else { continue }
            let arg = parts.count > 1 ? parts[1] : ""
            switch cmd {
            case "type":
                for ch in arg {
                    let lower = Character(ch.lowercased())
                    var shift = ch.isUppercase
                    var base = lower
                    let shifted: [Character: Character] = ["!":"1","@":"2","#":"3","$":"4","%":"5","^":"6","&":"7","*":"8","(":"9",")":"0","_":"-","+":"=",":":";","\"":"'","<":",",">":".","?":"/"]
                    if let b = shifted[ch] { base = b; shift = true }
                    guard let c = codes[base] else { continue }
                    let m: NSEvent.ModifierFlags = shift ? [.shift] : []
                    view.keyDown(with: ev(true, c, String(ch), m)); view.keyUp(with: ev(false, c, String(ch), m))
                }
            case "key":
                let a = arg.split(separator: " ").map(String.init)
                let code = UInt16(a[0])!
                let m: NSEvent.ModifierFlags = a.count > 1 ? [.shift] : []
                view.keyDown(with: ev(true, code, "", m)); view.keyUp(with: ev(false, code, "", m))
            case "click":
                let a = arg.split(separator: " ").map { Double($0)! }
                click(a[0], a[1])
            case "shot":
                if let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                    view.cacheDisplay(in: view.bounds, to: rep)
                    try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: arg))
                }

            default: break
            }
        }
    }
    app.run()
}
