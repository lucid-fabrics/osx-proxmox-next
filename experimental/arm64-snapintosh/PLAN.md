# Snapintosh on a Proxmox host: arm64 macOS 27 with a desktop, in QEMU TCG

Plan, 2026-10-24. Evidence base: [ANALYSIS.md](ANALYSIS.md) (same folder), the
Snapintosh sources at commit `67013604d798` and q1n1 at `7872398694ca`, and checks run
on the reference Proxmox host and Mac. Durations below are estimates for one
experienced engineer unless the progress log says they were measured.

Testing, lab use and learning only: Apple's license terms govern where macOS may run.

## Progress log

```
+-------+------------+------------------------------------------------------------+
| Phase | Date       | Result                                                     |
+-------+------------+------------------------------------------------------------+
| 0     | 2026-10-24 | Lab VM up, QEMU 11.0.3 reaches the UEFI shell, extracted   |
|       |            | firmware matches every pin. AdrenoX2 kext builds on the    |
|       |            | Mac after two local fixes to upstream defects (generator   |
|       |            | missing three IOMFB members; IONetworkController slots     |
|       |            | 331-334 renamed in 26A428). Independently audited.         |
| 1     | 2026-10-24 | qemu-sptm (2867d847) boots the J700 26A428 kernel to a     |
|       |            | root shell on serial in 21-22 s under TCG on a 14900K      |
|       |            | (5 runs). Far faster than the estimate in section 8.       |
|       |            | Independently audited.                                     |
| 2     | 2026-10-25 | Snapintosh monitor enters XNU under QEMU 11.0.3 TCG and    |
|       |            | stops at the documented single-CPU PMGR assertion. Fixes:  |
|       |            | boot data preloaded from the QEMU disk (guest starts in    |
|       |            | about 26 s instead of 40+ min over the UART), EL2 entered  |
|       |            | in VHE mode by AAVMF converted for the monitor. Audited.   |
| 3     | 2026-10-25 | Six CPUs online (GIC and MPIDR read from ACPI, A16 values  |
|       |            | unchanged), BaseSystem RAM disk mounted as root at about   |
|       |            | 347 s, launchd starts. QEMU-only profile drops             |
|       |            | secure-repair (its GPIO wait races under TCG). Audited.    |
| 4     | 2026-10-25 | launchd runs 65 processes; WindowServer launches and       |
|       |            | aborts with "No suitable Metal devices present after 30    |
|       |            | seconds" at 1244 s, same as the A16 before its GPU driver. |
|       |            | Go for the GUI work (limit was 2 hours). Fixes: T0SZ for   |
|       |            | pointer signing, A16 speed build flags, SEP services in    |
|       |            | the TCG board. Audited.                                    |
| 6a    | 2026-10-25 | SoftGPU protocol spec, revision 4, after three review      |
|       |            | rounds. Ready to implement.                                |
| 6b-S  | 2026-10-25 | Host shader sandbox prototype: no memory escape or leak    |
|       |            | found by two security audits; a guest-triggered host crash |
|       |            | class (compute-only ops in graphics stages) being fixed.   |
+-------+------------+------------------------------------------------------------+
```


## 1. Goal and what "done" means

Goal: the stock macOS 27 (26A428, J700/T8140) kernel booted by the Snapintosh EL2
monitor inside an emulated arm64 machine on a Proxmox host, reaching the login window and a
desktop that takes keyboard and mouse input, visible in a VNC console.

Done means all of these, observed on the real VM, not in an offline test:

1. The installed system boots from a virtual disk to the login window.
2. WindowServer renders through a Metal device we provide (no GPU on the guest side).
3. Login with the keyboard, click the Dock with the mouse, open Finder and Terminal.
4. Reboot keeps state (writes persist on the virtual disk).

Explicit non-goals: smooth frame rate, audio, Wi-Fi, Bluetooth, sleep, iCloud/App Store.


## 2. Facts this plan stands on

```
+------------------------------+------------------------------------------------------+
| Fact                         | Source                                               |
+------------------------------+------------------------------------------------------+
| Reference host CPU           | i9-14900K, 32 threads, P-core threads are CPU 0-15   |
| Reference host software      | Proxmox VE 9.2, QEMU 11.0.3 (pve-qemu-kvm 11.0.3-4), |
|                              | qemu-system-aarch64 present, AArch64 EDK2 firmware   |
|                              | not installed by default                             |
| Reference host GPU           | Intel UHD 770 iGPU (optional later renderer)         |
| Reference Mac                | Apple Silicon (M3 Max), macOS 27.0 build 26A428, the |
|                              | exact build Snapintosh pins, Xcode installed         |
| Monitor already runs in QEMU | probes/run.py:42-88 (xnuboot --qemu, virt,           |
|                              | virtualization=on, -cpu max, -smp 1, ramfb)          |
| One CPU fails                | docs/BOOT-CHAIN.md:306-309 (PMGR assertion)          |
| SMP path hardcodes A16 GIC   | tools/secondary-runtime.py:154, probes/secondary.c:33|
|                              | probes/xnuboot/cpu_boot.c:45,                        |
|                              | compat/boot-relocate.h:84                            |
| WindowServer needs Metal     | docs/GUI-BOOT-ROADMAP.md section 6: "No suitable     |
|                              | Metal devices present after 30 seconds", no software |
|                              | compositor path in SkyLight                          |
| GPU seam                     | docs/ARCHITECTURE.md section 4: plug-in -> IOGPU     |
|                              | user client -> AdrenoX2 kext -> monitor calls (IB    |
|                              | START/POLL, FILL32, FENCE, DART map/unmap, cache)    |
| AIR -> NIR -> llvmpipe exists| gpu/air2ir3/src/nirexec.cpp: runs AIR-derived NIR on |
|                              | Mesa llvmpipe with the binding ABI mapped to gallium |
| Installed image needs a Mac  | docs/FULL-INSTALL.md:44-60 (Virtualization.framework)|
| Kexts need Xcode + KDK       | docs/BUILDING.md:3-12                                |
+------------------------------+------------------------------------------------------+
```


## 3. Architecture of the end state

The Snapintosh stack stays as it is from the kernel down to the monitor. Only the
hardware-facing ends change: the A16 devices become QEMU devices, and the Adreno GPU
becomes a software GPU that renders on the host.

```
 Proxmox host (x86)  
 +-------------------------------------------------------------------------------+
 | Lab VM 'snaplab' (Debian 13, KVM x86, 16 vCPU on P-cores, 24 GB)              |
 | +---------------------------------------------------------------------------+ |
 | | qemu-system-aarch64 (our build: QEMU 11 + snap-softgpu + snap-hid devices)| |
 | |  TCG, -M virt,virtualization=on,gic-version=3 -smp 6 -m 16G              | |
 | |  -cpu max,pauth-impdef=on,sve=off,sme=off                                | |
 | |                                                                           | |
 | |   guest (emulated arm64)                    host side (native x86)        | |
 | |   ------------------------                  ------------------------      | |
 | |   WindowServer, apps                                                       | |
 | |   SoftMetal.bundle (plug-in)                                               | |
 | |     encodes SoftGPU packets, ships AIR                                     | |
 | |   SoftGPU kext (IOGPU accel)  -- ring in --> snap-softgpu device           | |
 | |   SoftDisplay (present)          guest RAM    AIR -> NIR (air2ir3 front)  | |
 | |   SnapHID kext  <------- reports -------     NIR -> llvmpipe (gallium)    | |
 | |   XNU 26A428 (stock, EL1)                     renders into guest memory   | |
 | |   Snapintosh monitor (EL2)                    scanout -> QEMU console     | |
 | |   q1n1 (EFI) -> EDK2 AAVMF                         |                      | |
 | |   ANS2 model -> QEMU NVMe -> disk image            v                      | |
 | +----------------------------------------------- VNC :5901 ----------------+ |
 +-------------------------------------------------------------------------------+
```

Why render on the host and not in the guest: every instruction inside the guest runs
under TCG, 10-30x slower than native. Software rendering inside the guest would pay
that on every pixel. The host side of a QEMU device runs as normal x86 code, with
llvmpipe spreading rasterisation across threads. The guest only encodes packets.

Why a lab VM and not the Proxmox host: the build needs a QEMU fork, Mesa, LLVM and a
lot of packages. Keeping them off the hypervisor protects the Proxmox host. TCG is pure
user-space emulation, so running it inside a KVM guest costs almost nothing.


## 4. The software GPU: options and the choice

```
+---+-------------------------------+------------+--------------------+----------------+
|   | Option                        | Effort     | Speed under TCG    | Main risk      |
+---+-------------------------------+------------+--------------------+----------------+
| A | Emulate the Adreno X2-90 in   | 3-6 months | rendering native,  | interpreting   |
|   | QEMU (PM4 + ir3 shaders run   |            | but ir3 must be    | undocumented   |
|   | by an interpreter), keep the  |            | interpreted or JIT | a8xx behaviour |
|   | Adreno kext/plug-in unchanged |            | compiled           |                |
| B | SoftGPU: plug-in front end    | 2-4 months | rendering native   | protocol and   |
|   | reused, new packet protocol,  |            | on host (llvmpipe) | kext rewrite   |
|   | host renderer in a QEMU device|            |                    |                |
| C | llvmpipe inside the guest     | 2-3 months | rendering also     | JIT inside     |
|   | plug-in (no QEMU device)      |            | emulated, slowest  | WindowServer   |
|   |                               |            |                    | (code signing) |
| D | Apple paravirt GPU protocol   | 4-9 months | best (stock driver)| undocumented   |
|   | (apple-gfx) reimplemented on  |            |                    | protocol,      |
|   | Linux                         |            |                    | needs vmapple  |
|   |                               |            |                    | kernel, not    |
|   |                               |            |                    | J700           |
+---+-------------------------------+------------+--------------------+----------------+
```

Choice: **B, SoftGPU**. Reasons:

- The shader path already exists. air2ir3's front end turns AIR into NIR, and
  `nirexec.cpp` already feeds that NIR to llvmpipe with Metal's binding rules mapped
  onto gallium state. That is the core of a renderer, written and tested against Metal.
- Everything Snapintosh learned getting WindowServer to accept a Metal device
  (GUI-BOOT-ROADMAP sections 8-12: formats, depth/stencil, multiple attachments, AIR 2.2,
  framebuffer fetch, half floats, submission sync) lives in the plug-in front end and
  the compiler front end, which B keeps.
- A packet protocol we design is far smaller than PM4, and there is no ir3 ISA to
  emulate (rules out A).
- Shaders execute on the host, so no JIT inside WindowServer (rules out the code-signing
  risk of C) and no TCG penalty on pixels.

Later upgrade, not in scope: swap llvmpipe for Mesa's iris driver on the UHD 770 iGPU.
Same NIR, real GPU, if the iGPU can be given to the lab VM.


## 5. Phases

Each phase ends at a gate. A gate is passed only by the stated observation on the real
run, captured in the stop report or the console log. Kill criteria say when to stop.

### Phase 0. Lab and toolchains (1-2 days)

Proxmox host:
- Create VM `snaplab`: Debian 13, q35, 16 vCPU, `affinity: 0-15` (the P-core threads
  on a 14900K, adjust for other CPUs), 24 GB RAM, 200 GB disk, `cpu: host`.
- In snaplab: build deps (clang, lld, LLVM 22, meson, ninja, python3, lzfse, glib/pixman
  for QEMU), `qemu-efi-aarch64` for `AAVMF_CODE.fd`, stock QEMU 11.0.x source tree.
  Debian trixie packages clang/lld/llvm 22 directly (`clang-22`, `ld.lld-22`).
- Clone Snapintosh with submodules and q1n1 at the commits named at the top of this
  file.
- Smoke test the AArch64 build: `-M virt,virtualization=on,gic-version=3 -accel tcg
  -cpu max -bios AAVMF_CODE.fd -nographic -nic none` reaches the UEFI `Shell>` prompt.
  Without `-nic none`, EDK2 spends minutes trying PXE and HTTP boot first.
- `tools/fetch-ipsw.py` and `tools/extract-firmware.py` for the J700 26A428 IPSW (about
  27 GB), run in snaplab: they work on Linux unmodified. The IPSW itself has no pin;
  check the extracted files against the SHA-256 pins in `probes/guestprofile.py`
  (kernelcache, DeviceTree, SPTM, TXM, SEP firmware).

The Mac (already on 26A428):
- Install the KDK for 26A428, `ipsw` (Homebrew), meson/ninja/LLVM 22 per
  gpu/air2ir3/README.md. Without admin rights, the KDK package can be expanded
  with `pkgutil --expand-full` into a user folder and passed to the tools with `--kdk`.
- Generate the private class-layout headers per docs/BUILDING.md:
  `IOMobileGraphicsFamily26A428.h` with `tools/gen-iomfb-header.py`, and
  `IOGPUFamily26A428.h` by hand from the KDK using the recipe in AGENTS.md (no
  generator ships for it). Both stay local, never committed.

Gate 0: snaplab boots, the extracted firmware matches the pins, Xcode + KDK build the
AdrenoX2 kext unmodified on the Mac (proves the kext toolchain).

### Phase 1. Baseline with qemu-sptm / darwin-vm (0.5-2 days)

Purpose: prove the firmware prep and measure raw TCG speed on this host before touching
Snapintosh.
- Build `jprx/qemu-sptm` in snaplab with Linux fixes (drop Homebrew include paths in
  `tools/qemu-sptm-oracle/setup.sh:26-27`, replace BSD `sed -i ''` at `:20`).
- Prepare files with darwin-vm's `get_files.sh` / `fix_perms.sh` on the Mac, copy to
  snaplab.
- Boot with `tools/qemu-sptm-oracle/run.sh` settings (16 GB, cpus=1, wdt=-1).

Gate 1: root shell on serial. Record time-to-shell, this is the speed baseline.
Kill: no shell after 2 days of debugging means the firmware or QEMU base is wrong.
Fix that before anything else.

### Phase 2. Snapintosh monitor, one CPU (about 1 week)

- Port the host tooling to Linux: `probes/run.py:33-39` (clang/lld/EDK2 paths), the
  same constant in `q1n1/tools/test-uefi.py:444` and `test-nvme-hash-qemu.py:39`.
- Build q1n1 (`make uefi`) and the xnuboot blob.
- QEMU line from `probes/run.py:66-79` with `-cpu max,pauth-impdef=on,sve=off,sme=off`
  and `-m 16G`. pauth-impdef because QEMU's default QARMA5 is very slow under TCG and
  arm64e XNU signs pointers constantly. SVE/SME off to stay close to the A16 path.
- Run `SNAP_NO_FB=1 python3 probes/run.py xnuboot --qemu --console serial`, proxy over
  the PL011 Unix socket.

Gate 2: the monitor enters XNU and stops at the documented PMGR CPU-complex assertion
(BOOT-CHAIN.md:306-309). Reproducing that known failure is the pass: it proves the
whole chain up to the kernel behaves as on the A16.
Kill: if the monitor faults before XNU entry for more than a week, QEMU's NV2
emulation differs from Oryon too much. Fall back to section 7.

### Phase 3. SMP port and kernel boot (1-3 weeks)

- Replace the hardcoded A16 GIC values with values read from the ACPI MADT (or passed
  in q1n1 boot info): GICD (A16 `0x17000000`, virt `0x08000000`), GICR base (A16
  `0x17080000`, virt `0x080A0000`), redistributor stride (`0x40000` on A16, `0x20000`
  for virt GICv3), MPIDR layout (`PRIME_CLUSTER=0x10000`,
  `tools/secondary-runtime.py:28,84-86`).
- Secondaries via PSCI `CPU_ON` over SMC (already used at EL2 in
  `tools/measure-a16-cpu-clock.py:537-560`). `-smp 6`, `SNAP_SMP=1`.
- Disable the guest watchdog (`wdt=-1`) and stretch any boot timeouts: TCG is slow.
- Live console: add a second PL011 for the monitor console ring, so the log is visible
  during the run instead of only after the stop.
- The time sink here is the TCG vs Oryon divergence loop: NV2 register redirection
  coverage, HPFAR validity, single-step. Each stop report is decoded with the
  project's own verdict decoder before the next run.

Gate 3: six CPUs online, verbose kernel log on serial, BaseSystem RAM disk mounted as
root.
Kill: three weeks without reaching root mount means stop or switch to section 7.

### Phase 4. BaseSystem userspace (1-2 weeks)

- BaseSystem and the iBootSystemContainer image as RAM media, preloaded by q1n1 from an
  EFI file with its SHA-256 manifest (`probes/run.py:1600-1621`), not pushed through the
  UART proxy (multi-GB over an emulated serial line is too slow).
- Guest RAM 16 GB covers the about 3.3 GB of RAM media plus monitor arenas.

Gate 4: launchd runs, around 70 processes as on the A16, and WindowServer launches and
aborts with "No suitable Metal devices present after 30 seconds". Same failure as the
A16 at the same point = parity reached.
Also record time-to-launchd. Go/no-go for the GUI: if it is over 2 hours, a desktop
will be a proof of concept you can watch, not use. Decide whether that is still worth
it before Phase 6.

### Phase 5. Installed system on a virtual disk (2-3 weeks)

BaseSystem never loads third-party kexts, so the GUI needs a real install.
- Produce the installed image on the Mac with Virtualization.framework, as in
  docs/FULL-INSTALL.md:44-60. Size it 64 GB.
- Port the ANS2 storage backend from the A16 NVMe (`compat/nvme-host.c`, tied to the
  A16's PCIe root port and SMMU) to QEMU's `-device nvme` backed by the image file.
  q1n1 already drives QEMU NVMe in `q1n1/tools/test-nvme-hash-qemu.py`. The kernel has
  no VirtIO storage driver (FULL-INSTALL.md:41-42), so the ANS2 model stays as the guest
  side.
- Build an AuxKC on the Mac with a trivial test kext, per docs/BUILDING.md.

Gate 5: the installed system boots from the virtual disk, a file written in one boot is
there in the next, and the test kext from the AuxKC loads (shows in `kextstat`).

### Phase 6. SoftGPU (2-4 months, the big one)

Work package 6b does not need a booting guest, so it runs in parallel with Phases 2-5.

**6a. Protocol spec (1 week).** One document, `SOFTGPU-PROTOCOL.md`:
- Transport: a command ring in guest RAM, a doorbell register and a completion
  interrupt on the snap-softgpu device. The monitor maps the device's MMIO page into
  the guest at stage 2 and forwards its interrupt through the existing
  `compat/irq-forward.c` path (built for Adreno passthrough).
- Memory: resources are lists of guest physical pages. The host reads and writes guest
  RAM directly (it is QEMU's memory), so there is no SMMU and no cache maintenance.
  The monitor's DART model translates the guest's GPU addresses to physical ones.
- Packets: CREATE/DESTROY resource, UPLOAD_SHADER (AIR blob + function constants),
  CREATE_PIPELINE (render: shaders, vertex layout, blend, depth/stencil, attachment
  formats; compute: kernel, threadgroup size), BEGIN/END_RENDER_PASS (attachments, load
  and store actions, clear values), SET_BINDINGS (buffers, textures, samplers per the
  binding ABI in gpu/air2ir3/BINDING-ABI.md), DRAW, DRAW_INDEXED, DISPATCH, BLIT, COPY,
  FILL, FENCE, PRESENT(surface).

**6b. Host renderer, the snap-softgpu QEMU device (3-5 weeks, parallel).**
- An out-of-tree device in our QEMU 11 build. Worker thread per context, llvmpipe
  through gallium, the air2ir3 front end compiled for x86 Linux (it is portable C++).
- Start from `nirexec.cpp`, which already maps the binding ABI to gallium state, handles
  XR formats and blending, and renders Metal-layout output.
- Host-side shader cache keyed by the AIR hash and function constants, so each shader
  compiles once.
- PRESENT copies the surface into a QEMU graphic console, which shows in VNC.
- Test offline against Metal: render the existing scene sets (`gpu/air2ir3/scenes`,
  `vscenes`, `xrscenes`, `xr5scenes`) through the device's renderer and compare with
  goldens made on the Mac by `tools/mtlgold.swift`. Their pixel checks become ours.

Gate 6b: every scene in those sets matches its Metal golden through the device code
path, outside any guest.
Kill: if a class of scenes cannot match through llvmpipe (not a bug, a missing
feature), stop and reassess before writing guest code.

**6c. Monitor plumbing (1-2 weeks).**
- Retire the Adreno IB validation (`compat/adreno-ib*.c`) for the QEMU profile. The
  host device validates packets itself, so the monitor only maps the MMIO page and
  forwards the interrupt.
- Remove the Adreno power, GMU and SMMU paths from the QEMU profile. Keep the DART model.
- Keep the `sgx` node withdrawal and interrupt reuse that the Adreno route already does
  (`compat/adreno.h`), pointed at the new device.

**6d. Kext: AdrenoX2 -> SoftGPU (2-3 weeks).**
- Keep: the IOGPU accelerator class, user client, resource lists, segment list, fences
  and completion stamps (the parts that make IOGPU and Metal.framework happy).
- Replace: "stage, relocate, validate, ask the monitor to run the IB" becomes "write
  the packet buffer to the ring, ring the doorbell". Drop cache maintenance (the device
  is coherent with guest RAM).
- AdrenoDisplay -> SoftDisplay: keep surfaces, modeset answers, cursor plane and
  present sync. Drop the DPU, backlight, beam sync and eDP. Present = PRESENT packet. A
  fixed mode (1920x1080 at 60 Hz) to start.
- `MetalPluginName` in the personality points to SoftMetal.bundle.

**6e. Plug-in: AdrenoMetal -> SoftMetal (3-5 weeks).**
- Keep: MTLDevice, resources, command buffer, render/blit/compute encoders, staging
  arenas, the submission sync fix (`commitAndWaitUntilSubmitted` waits for the kernel).
- Rewrite: `AdrenoDrawCommands/` (PM4 encoding: CommandStream, DrawEncoding, RenderPass,
  ProgramState, Textures, UniformBuffers, Compute, Blit, about 4,500 lines) as SoftGPU
  packet encoding. Packets are higher level than PM4, so this shrinks.
- Shaders: send AIR plus function constants with UPLOAD_SHADER instead of calling
  air2ir3 in the guest. Guest-side compile disappears, which also saves TCG time.

Gate 6e: a guest Metal client renders the same triangle test Snapintosh used on the
A16 (851 pixels matching an independent reference), and the image reaches the VNC
console through SoftDisplay.

**6f. WindowServer bring-up (2-6 weeks).**
Expect a replay of GUI-BOOT-ROADMAP section 9 (mode request argument order, the time
sync wait, 10-bit framebuffer default, render target shapes, depth/stencil passes,
several colour attachments) and section 12 (AIR 2.2 texture reads, Accelerate's
coprocessor paths). Many are already solved in the code we keep. New ones will come
from timing: Metal and WindowServer timeouts tuned for real hardware may fire under
TCG, so find and stretch them early.

Gate 6f: the login window on the VNC console.

### Phase 7. Input (1-2 weeks, can start after Gate 5)

- A `snap-hid` QEMU device takes QEMU's keyboard and absolute pointer events (VNC
  input) and queues HID reports in a ring, same doorbell and interrupt pattern as
  SoftGPU.
- SnapI2CHID keeps its HID descriptor and report handling. Its GENI I2C transport is
  replaced by the ring.
- Absolute pointer (tablet) instead of a relative mouse, so the cursor follows VNC
  without acceleration drift.

Gate 7: log in with the keyboard, click the Dock, open Finder and Terminal. With Gate
6f this meets the definition of done.

### Phase 8. Comfort (optional, open-ended)

- Persistent shader cache and a precompiled WindowServer pipeline pack on the host.
- Networking: SnapintoshVirtualEthernet (inside the AdrenoX2 kext,
  docs/ARCHITECTURE.md:113-115) moved onto a ring device bridged to QEMU user-net.
- Measure frames per second and boot times. Try iris on the UHD 770.
- A Proxmox-style wrapper: systemd unit in snaplab, VNC exposed on the LAN.


## 6. Timeline and critical path

```
 week   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15  16  17  18  19  20
 P0     ##
 P1      #
 P2         ####
 P3             ############
 P4                         ########
 P5                                 ############
 P6a        ####
 P6b            ####################                    (parallel, off critical path)
 P6c                                             ######
 P6d                                             ############
 P6e                                                   ####################
 P6f                                                                   ##############>
 P7                                             ########
```

Critical path: P0 -> P1 -> P2 -> P3 -> P4 -> P5 -> P6c/d -> P6e -> P6f.
Total to the definition of done: about 4-7 months. The low end assumes the TCG
divergence loop in Phase 3 is short and WindowServer bring-up mostly reuses solved
problems. The high end assumes neither.

The real schedule driver is iteration speed, not code volume. On the A16 a boot run
takes minutes. Under TCG an installed boot to the login window may take 30-90 minutes,
which caps debugging at roughly 5-15 runs a day. Everything that can be tested
offline (6b scenes, protocol unit tests, kext logic) must be, to save boot runs.


## 7. Fallback if the monitor will not run under TCG

If Gate 2 or Gate 3 fails on its kill criterion, the fallback base is qemu-sptm
(Phase 1): QEMU itself emulates the Apple machine, no monitor and no nested
virtualisation. The SoftGPU device, kext and plug-in carry over almost unchanged,
because they talk to a QEMU device, not to the monitor. Limits of that base today: one
CPU, PAC disabled, RAM-disk boot. It would need an installed-system storage path and
AuxKC loading added. Slower desktop (one CPU), but it removes the biggest unknown.


## 8. Performance expectations (estimates, to be replaced by Gate 1 and 4 numbers)

```
+-------------------------------+-------------------------------+
| Step                          | Expected under TCG            |
+-------------------------------+-------------------------------+
| darwin-vm root shell          | seconds to a few minutes      |
| XNU verbose log (Snapintosh)  | minutes                       |
| BaseSystem launchd            | 10-60 minutes                 |
| Installed boot to login       | 30-90 minutes                 |
| Desktop interaction           | about 1-3 frames per second   |
+-------------------------------+-------------------------------+
```

Why the desktop stays slow even with host rendering: WindowServer, Core Animation and
the apps run as guest code, 10-30x slower than on the A16's Oryon cores, and on the A16
itself presentation is "usable, not yet smooth". The host renderer removes the pixel
cost, not the CPU cost. The only fix for that is an arm64 host with hardware
virtualisation (KVM with nested virtualisation, or the A16 itself); out of scope here.


## 9. Risks

```
+----+----------------------------------------+--------+--------------------------------+
| #  | Risk                                   | Level  | Mitigation                     |
+----+----------------------------------------+--------+--------------------------------+
| 1  | QEMU NV2/EL2 behaviour differs from    | high   | Gate 2/3 kill criteria, fall   |
|    | Oryon in ways the monitor depends on   |        | back to section 7              |
| 2  | Boot runs too slow to iterate          | high   | offline tests first, Gate 4    |
|    |                                        |        | go/no-go on measured speed     |
| 3  | Hardware-tuned timeouts fire under TCG | medium | wdt=-1, find Metal/WindowServer|
|    | (watchdog, Metal, WindowServer)        |        | timeouts in 6f early           |
| 4  | llvmpipe lacks a Metal feature         | medium | Gate 6b before guest work      |
|    | WindowServer needs                     |        |                                |
| 5  | Snapintosh changes fast (Sept-Oct 2026)| medium | pin commits in Phase 0, rebase |
|    |                                        |        | only at gates                  |
| 6  | AuxKC / plug-in signing on the         | low    | follow FULL-INSTALL/BUILDING,  |
|    | installed system                       |        | same as on the A16             |
| 7  | Host RAM (lab VM needs 24 GB)          | low    | pause other VMs during runs    |
| 8  | Kernelcache pinned to 26A428           | low    | stay offline on 26A428, the    |
|    |                                        |        | reference Mac matches it       |
+----+----------------------------------------+--------+--------------------------------+
```


## 10. Where the code lives

- This repository (Apache-2.0) holds only the plan and, later, the orchestration:
  lab VM creation, build scripts and a CLI entry point once Phase 1 works.
- Changes to Snapintosh (monitor, kext, plug-in) go into a fork of the Snapintosh
  repositories, and the QEMU devices into a QEMU fork. Snapintosh is MIT with GPL-2.0
  components, so none of its code is copied here; the scripts fetch and build it.
- Phase 6b runs in parallel with Phases 2-5.
- A gate counts as passed only after a second person (or reviewer) checks the stop
  report and console log against the gate's stated observation.
- The Mac does firmware prep, the installed image, kext/plug-in/AuxKC builds and the
  Metal goldens. snaplab does everything QEMU.


## 11. First concrete actions (Phase 0, in order)

1. Create `snaplab` on the Proxmox host (16 vCPU on the P-cores, 24 GB, 200 GB).
2. Install build deps and `qemu-efi-aarch64` in snaplab, clone QEMU 11.0.x source.
3. On the Mac: `brew install blacktop/tap/ipsw meson ninja llvm@22`, install the
   26A428 KDK.
4. Clone Snapintosh (with submodules) and q1n1 at the commits above.
5. Fetch and extract the J700 26A428 IPSW, verify hashes.
6. Build the unmodified AdrenoX2 kext on the Mac (toolchain check).
7. Move to Phase 1.
