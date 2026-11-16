# Snapintosh in QEMU aarch64 TCG on Proxmox VE 9.2 (i9-14900K)

Read-only analysis, 2026-10-24. Sources: `Snapintosh/Snapintosh` at commit
`67013604d7989d8ef0ee6caeafdd69faa1092317` (with submodules) and `Snapintosh/q1n1` at
`7872398694ca8322e9cdebcc4ca2380491295f24`. Paths below are relative to `Snapintosh/` unless prefixed `q1n1/`. Nothing was built
or executed.

## 1. Verdict

A Snapintosh boot inside QEMU TCG is partly done already. The project runs its own
monitor under QEMU as a test control: q1n1 boots under EDK2 on `virt,virtualization=on`
with `-cpu max`, and `probes/run.py xnuboot --qemu` runs "the identical monitor and image"
there (`probes/run.py:42-88`, `docs/BOOT-CHAIN.md:395-399`). It is a single-CPU test
harness, not a supported boot path.

There are two routes, and they get very different results:

- **Route A, the Snapintosh monitor under TCG.** This is realistic up to an XNU serial log
  and probably a BaseSystem RAM-disk userspace (launchd, up to about 70 processes, as on
  the A16). Getting there needs an SMP port off the A16's hard-coded GIC addresses, Linux
  build fixes and a TCG-versus-Oryon debugging loop. A GUI desktop is a blocker. On the
  A16 the desktop exists only because the project wrote a full Adreno Metal driver, and
  WindowServer will not start without a Metal device.
- **Route B, QEMU emulating Apple hardware directly** (`jprx/qemu-sptm` and `jprx/darwin-vm`).
  This is much easier. Snapintosh already uses it as an oracle: it boots this exact 26A428
  J700 kernelcache under Apple's real SPTM/TXM with TCG (`tools/qemu-sptm-oracle/setup.sh:3-5`,
  `tools/qemu-sptm-oracle/run.sh:15-21`). darwin-vm's README says it reaches a macOS 27 root
  shell on serial and "runs anywhere qemu runs". It runs on one CPU with PAC disabled and
  has no GUI. Firmware preparation needs a Mac.

Recommendation: if the goal is "macOS kernel plus a shell in a Proxmox VM", use Route B. Use
Route A only if the goal is to develop or test the Snapintosh monitor itself without the
laptop.

## 2. How the stack maps onto QEMU

```
 A16 (real)                                QEMU virt TCG (Route A)
 ---------------------------------------   ------------------------------------------
 Mac host, cycle.py, USB-C NCM            Linux host, run.py, PL011 socket (--uart-proxy)
 A16 UEFI (EL2)                            EDK2 AAVMF at EL2 (virtualization=on)
 q1n1.efi --boot (NCM proxy)               q1n1.efi --uart-proxy (SPCR PL011)
 Snapintosh monitor EL2                    same monitor binary, EL2 emulated by TCG
 XNU EL1 via FEAT_NV2                      XNU EL1 via QEMU FEAT_NV2 (-cpu max)
 Adreno, NVMe, GENI I2C, QCC2072           none of these exist; ramfb, PL011, opt. nvme
```

## 3. Hardware dependency inventory

Classes: (a) works under QEMU virt as is, (b) config/flag tweak or small patch,
(c) new device model or backend swap, (d) blocker.

```
+----+-------------------------------+-------+----------------------------------------------+
| #  | Dependency                    | Class | Evidence / note                              |
+----+-------------------------------+-------+----------------------------------------------+
| 1  | UEFI -> q1n1 at EL2           | a     | run.py:66-79, q1n1 test-uefi.py:556-560      |
| 2  | q1n1 transport (NCM -> UART)  | a     | q1n1 main.c:7, 1186-1192                     |
| 3  | AArch64 EDK2 firmware path    | b     | run.py:39 hardcodes Homebrew edk2 path       |
| 4  | FEAT_NV2 / VNCR_EL2           | a     | BOOT-CHAIN.md:96-104; QEMU -cpu max has NV2  |
| 5  | VHE, TIDCP IMPDEF traps       | a*    | APPLE-ENCODINGS.md:83-91                     |
| 6  | genter/0xd4e00000 calls       | a     | BOOT-CHAIN.md:145-166 (native-call mode)     |
| 7  | 16K granule                   | a     | QEMU has TGran16; shadow still required      |
| 8  | SPRR permission classes       | a     | shadow/sprr-view.c, done in software         |
| 9  | PAC (PACGA, key switching)    | b     | compat/pauth.c; pick fast QEMU PAC algorithm |
| 10 | RNDR seed for PAC domain      | a     | probes/xnuboot/prepare.c:28-33               |
| 11 | AMX / Apple IMPDEF sysregs    | a     | APPLE-ENCODINGS.md:55-75                     |
| 12 | Timer 19.2 MHz -> 24 MHz      | a     | host Hz from bootinfo, xnuboot.py:146-147    |
| 13 | FEAT_ECV counter trapping     | a*    | BOOT-CHAIN.md:356-357                        |
| 14 | Virtual AIC                   | a     | compat/aic.c, BOOT-CHAIN.md:316-330          |
| 15 | Physical GIC (SMP, IRQ fwd)   | b/c   | A16 GIC addresses hard-coded (see 3.3)       |
| 16 | PSCI CPU_ON for secondaries   | b     | cpu_boot.c:298-403; QEMU PSCI over SMC       |
| 17 | 6-vCPU topology (cpus=1 fails)| b     | BOOT-CHAIN.md:306-309                        |
| 18 | Memory map / phys addresses   | a/b   | layout relative, xnuboot.py:288-307          |
| 19 | 36-bit stage-2 limit          | a     | COMPATIBILITY.md:97-99; virt RAM fits        |
| 20 | Storage: RAM disk / ANS2 RAM  | a     | ramdisk route, guestprofile.py:132-134       |
| 21 | Storage: ANS2 on real NVMe    | c     | compat/nvme-host.c is A16 PCIe/SMMU specific |
| 22 | DART / SMMU                   | a/d   | DART is a pure model; SMMU only for Adreno   |
| 23 | Boot framebuffer / console    | a/b   | ramfb GOP, --console serial                  |
| 24 | GPU (Adreno X2-90)            | d     | WindowServer requires Metal                  |
| 25 | Display (DPU, eDP backlight)  | d     | AdrenoDisplay is Adreno/DPU only             |
| 26 | Input (GENI I2C HID)          | c     | not needed for a serial shell                |
| 27 | Wi-Fi QCC2072                 | d     | irrelevant; drop                             |
| 28 | Guest Ethernet                | c     | lives in AdrenoX2 kext (AuxKC)               |
| 29 | Thermal guard / DVFS          | a     | opt-in only; leave off                       |
| 30 | SEP, AES engine, PMGR, WDT... | a     | all monitor-side models (compat/sep-*.c ...) |
| 31 | Firmware acquisition          | b/d   | IPSW on Linux ok; installed image needs Mac  |
| 32 | Build toolchain               | b/d   | monitor: clang+lld ok; kexts need Xcode+KDK  |
+----+-------------------------------+-------+----------------------------------------------+
 a* = expected to work in QEMU >= 9.x TCG, not verified here (see 3.2).
```

### 3.1 EL2 entry path (UEFI -> q1n1 -> monitor)

- QEMU `-machine virt,virtualization=on` starts EDK2 at EL2. EFI applications therefore
  run at EL2, and q1n1 records `entry_el` (`q1n1/platform/uefi/main.c:1141-1146`). The
  project's QEMU harnesses check it: `tools/qualify-guest-stop.py:72` requires
  `current_el==2`.
- q1n1 has a dedicated QEMU mode, `--uart-proxy`, which serves the m1n1 proxy over an SPCR
  PL011 and refuses to run without one (`q1n1/platform/uefi/main.c:7`, `:1186-1192`). It
  also refuses to run without a GOP framebuffer, so the harness adds `-device ramfb`
  (`probes/run.py:72-75`, `q1n1/platform/uefi/main.c:1139-1142`).
- The exact QEMU command the project uses (`probes/run.py:66-79`):
  `-machine virt,virtualization=on,gic-version=3 -accel tcg -cpu max -smp 1 -m 4G
  -bios edk2-aarch64-code.fd -drive format=raw,file=fat:rw:<esp> -device ramfb
  -serial chardev:<unix socket>`. q1n1 is started with
  `startup.nsh: fs0: q1n1.efi --uart-proxy`.
- Proxmox: the host lacks `pve-edk2-firmware-aarch64`. Install it, which provides
  `AAVMF_CODE.fd`, or Debian's `qemu-efi-aarch64`. Then change `FIRMWARE` in
  `probes/run.py:39`, which hardcodes `/opt/homebrew/share/qemu/edk2-aarch64-code.fd`.
  Running `qemu-system-aarch64` by hand is simpler than `qm`: Proxmox does not expose
  `virtualization=on` and would need an `args:` override, and the proxy socket has to be
  a host Unix socket anyway. Class (b), hours.
- The same `FIRMWARE` constant appears in `q1n1/tools/test-nvme-hash-qemu.py:39` and as a
  default in `q1n1/tools/test-uefi.py:444`.

### 3.2 CPU features

- **FEAT_NV2.** The monitor depends on it, because XNU at EL1 writes EL2 registers
  (`docs/BOOT-CHAIN.md:96-104`). The probe that checks it is at
  `probes/xnuboot/prepare.c:346-350`. The project's own QEMU control says `-cpu max` "is
  what supplies FEAT_NV2" (`probes/run.py:69-70`). QEMU added FEAT_NV/NV2 to TCG in 9.0.
  PVE 9.x ships QEMU 10.x, so this should be (a). Verify with a dry run of the
  `xnuboot --qemu` probe.
- **VHE / E2H.** XNU expects E2H RES1 at virtual EL2 (`docs/BOOT-CHAIN.md:85-95`). `-cpu
  max` implements VHE, so (a).
- **IMPDEF system registers.** These trap through `HCR_EL2.TIDCP` and are answered by the
  monitor: modelled (fast IPI, deadline timer aliases, SPRR, `GXF_STATUS_EL1`,
  `AMXIDR_EL1`) or read as zero with writes dropped (`docs/APPLE-ENCODINGS.md:81-97`).
  QEMU TCG implements `HCR_EL2.TIDCP` routing for the IMPDEF space (8.0+). That should be
  (a). If a QEMU build instead UNDEFs some encoding at EL1, the native-call fallback
  still applies, because XNU's vector page is stage-2 execute-never
  (`docs/BOOT-CHAIN.md:153-162`).
- **Apple instructions.** `genter` and the alternate encoding are unallocated, so they
  UNDEF to EL1 and are caught through the guarded vector (native-call mode,
  `compat/native-call.h`). The opt-in patch turns them into `HVC #0x5350`
  (`docs/BOOT-CHAIN.md:163-166`). AMX is never enabled
  (`docs/APPLE-ENCODINGS.md:55-75`). Apple's compression instruction is avoided with
  `-vm_compressor_wk_sw` (`docs/APPLE-ENCODINGS.md:24`). All of this is architecture
  neutral, so (a).
- **16 KiB granule.** QEMU `-cpu max` walks 16K tables natively. That is exactly why the
  project used it as the control in `docs/FEASIBILITY.md:84-91`. Shadowing still cannot be
  dropped. XNU's PTEs carry SPRR permission classes, not plain AP bits, and
  `shadow/sprr-view.c` turns them into concrete permissions (`docs/BOOT-CHAIN.md:247-251`).
  Upstream QEMU has no SPRR, so feeding XNU's tables straight to the MMU would give wrong
  permissions. Making shadowing optional would need SPRR in QEMU, which qemu-sptm
  already has. That is a (c) change of weeks, and only a speed win. The existing 4K
  shadow works unchanged on a 16K-capable MMU.
- **PAC.** The monitor manages guest keys across EL1/EL2, emulates PACGA through a host
  backend and needs the generic-auth fields (`probes/xnuboot/pauth.c:27-33`,
  `probes/xnuboot/prepare.c:19`). QEMU `-cpu max` has PAuth, but its default QARMA5 is
  very slow under TCG, and arm64e XNU signs pointers constantly. Use
  `-cpu max,pauth-impdef=on` (fastest) or `pauth-qarma3=on`, which matches the A16's
  `APA3`/`GPA3` (`docs/FEASIBILITY.md:100`). Class (b).
- **SVE/SME.** `-cpu max` advertises both. The monitor then falls back to software AES for
  PAC key derivation (`probes/xnuboot/prepare.c:20-26`): slower, still correct. XNU
  "configures SME unconditionally for this board" (`probes/xnuboot/prepare.c:342-345`).
  Under QEMU it may now find real SME, which is a behaviour difference from the A16. To
  stay close to the qualified path, use `-cpu max,sve=off,sme=off`. Class (b).
- **RNDR.** Needed for the PAC domain seed (`probes/xnuboot/prepare.c:28-33`); `-cpu max`
  has FEAT_RNG, so (a).
- **FEAT_ECV and CNTHCTL traps.** Used to hide the physical counter rate
  (`docs/BOOT-CHAIN.md:356-357`). QEMU implements FEAT_ECV in TCG (9.x). Expected (a),
  not verified.
- **Silicon quirks the monitor works around.** `HPFAR_EL2` reads zero on fetch permission
  faults, firmware leaves the OS Lock set and stale EL1 fault registers survive
  (`docs/BOOT-CHAIN.md:381-386`). Under QEMU these become no-ops. The risk runs the other
  way: QEMU's corner cases differ from Oryon's (NV2 register redirection coverage,
  `HPFAR` validity, single-step). Expect a debugging loop here. It is the single biggest
  unknown in effort.

### 3.3 Interrupts, IPIs, SMP

- **Guest-facing AIC.** A pure model at EL2 (`compat/aic.c`, `docs/BOOT-CHAIN.md:316-330`).
  The guest never sees the GIC, so (a).
- **Host GIC addresses are hard-coded to the A16** in the SMP path:
  - `tools/secondary-runtime.py:154,163`: GICD at `0x17000000`;
  - `probes/secondary.c:33-34`: redistributors in `[0x17080000, 0x17500000)`, stride
    `0x40000`;
  - `probes/xnuboot/cpu_boot.c:45`: the same window check;
  - `compat/boot-relocate.h:84`: `BOOT_RELOCATE_GICR_FIRST 0x17080000`;
  - `tools/secondary-runtime.py:28,84-86`: `PRIME_CLUSTER=0x10000`, the A16 MPIDR layout.

  QEMU virt puts GICD at `0x08000000` and GICR at `0x080A0000`, with stride `0x20000` for
  GICv3 and `0x40000` for `gic-version=4`. These constants need to come from ACPI MADT or
  boot info. Class (b) to (c), 1 to 3 days.
- **SMP is mandatory.** With `cpus=1` "it trips a PMGR CPU-complex assertion earlier in
  boot" (`docs/BOOT-CHAIN.md:306-309`), and the existing QEMU harness is `-smp 1`
  (`probes/run.py:70`). Secondaries start with PSCI `CPU_ON` (`0xc4000003`,
  `probes/xnuboot/cpu_boot.c:359,399`). QEMU virt with EL2 and no EL3 offers PSCI over
  SMC. The project already ran two CPUs that way in `tools/measure-a16-cpu-clock.py:537-560`
  ("PSCI over SMC at EL2"). So the remaining work is the GIC and affinity port above, plus
  `-smp 6` and `SNAP_SMP=1` (`tools/cycle_lib/installed_fresh.py:431`). The 6-core J700
  topology itself is virtual (`docs/BOOT-CHAIN.md:290-305`).
- **IPIs** are emulated fast-IPI registers, with a 1 ms EL2 timer poll per vCPU
  (`docs/BOOT-CHAIN.md:336-342`). This works under MTTCG, at a CPU cost of 6 x 1 kHz exits.
- **IRQ forwarding** (`compat/irq-forward.c`) exists only for passthrough devices (Adreno,
  QCC2072). In QEMU there are none, so it is unused.

### 3.4 Timers

The host counter rate comes from q1n1 boot info (`info.timer_hz`,
`q1n1/platform/uefi/main.c:1145`). The loader default of 19.2 MHz
(`probes/xnuboot.py:146-147`) is replaced at run time, and the monitor scales host Hz to the
24 MHz guest rate with quotient/remainder arithmetic (`compat/clock.c`,
`docs/BOOT-CHAIN.md:346-352`). QEMU's 1 GHz (newer virt machine types) or 62.5 MHz counter
is just a different `host_hz`, so (a). Apple deadline timers map to `CNTHP_*_EL2`, and the
WD1 watchdog is a model (`docs/BOOT-CHAIN.md:353-365`). Under slow TCG, watch for watchdog
expiry. Boot args already use `wdt=-1` in the oracle (`tools/qemu-sptm-oracle/run.sh:19`).

### 3.5 Memory map

The layout is relative: `place()` moves it into whatever free memory the target reports,
precisely so it runs under QEMU (`probes/xnuboot.py:288-307`). Guest RAM is explicit through
`SNAP_GUEST_MIB` (384 MiB default, `probes/xnuboot.py:344-357`; 8192 in
`tools/cycle_lib/speed_test.py:36`). The stage-2 backend is limited to a 36-bit PA
(`docs/COMPATIBILITY.md:97-99`). virt RAM starts at `0x40000000`, so `-m` up to about
60 GiB fits. Size it at 8 to 16 GiB: guest RAM, plus the about 3.3 GiB BaseSystem and ISC
RAM media (`tools/qemu-sptm-oracle/j700_dt_fixup.py:24`), plus the monitor arenas. The guest
IPA map is the J700 device tree's, for example PMGR at `0x300734000`
(`probes/xnuboot/trap_dispatch.c:440`), and all of it is emulated at stage 2. Class (a);
`-m` and `SNAP_GUEST_MIB` are (b).

### 3.6 Storage

- **RAM-disk route (no host storage).** BaseSystem is published through DiskImages as RAM
  media, with a separate iBootSystemContainer image for xART, Preboot and Hardware
  (`docs/GUI-BOOT-ROADMAP.md:85-96`, `probes/bootmedia.py`). The BaseSystem copy can be
  preloaded by q1n1 from an EFI file with a SHA-256 manifest, so it does not cross the
  proxy (`probes/run.py:1600-1621`, `q1n1/platform/uefi/preload.c`). That matters because a
  multi-GiB upload over an emulated PL011 would be slow. The EFI filesystem exists in QEMU
  too. Class (a).
- **Virtual ANS2 with a RAM-backed disk** (`guestprofile.py:132-134`, "M2") also needs no
  host storage. It is limited by RAM, so it is fine for a test disk and not for a 64 GiB
  install. Class (a).
- **ANS2 on the real NVMe** (`compat/nvme-host.c`, `docs/FULL-INSTALL.md`) is tied to A16
  locators: ACPI segment-5 root port, SMMU and a Snapintosh partition header
  (`q1n1/tools/test-nvme-hash-qemu.py:9-12`). q1n1 already drives QEMU's `-device nvme`
  in that test, so swapping the backend to QEMU NVMe is plausible. Class (c), 1 to 2
  weeks. The kernelcache has no VirtIO storage driver (`docs/FULL-INSTALL.md:41-42`), so
  the ANS2 model must stay.

### 3.7 DART / SMMU

DART is a monitor-side model with its own state (`compat/dart.c`, `compat/sptm-dart.c`,
`docs/DEVICE-REFERENCES.md:52-58`). The Qualcomm SMMU is touched only for the Adreno and
QCC2072 passthrough. Under QEMU, DART is (a). SMMU work does not apply (d), because there
is no device to pass through.

### 3.8 Framebuffer, display, console

- XNU's own video console works from `boot_args.Video`, pointed at the GOP framebuffer q1n1
  captures (`probes/xnuboot.py:160-175`, `docs/GUI-BOOT-ROADMAP.md:72-79`). With ramfb as
  the GOP, `--console panel` should draw XNU's verbose console in the QEMU display (b). The
  safest first target is `SNAP_NO_FB=1 ... --console serial`
  (`docs/COMPATIBILITY.md:130-131`).
- Serial output goes through the SPTM `SERIAL_PUTC` endpoint into the monitor console ring
  (`docs/BOOT-CHAIN.md:168-170`). On the A16 the Mac reads the ring through q1n1. Under
  QEMU the only PL011 carries the proxy, so the ring is read after the stop. A live view
  needs proxy polling or a second UART. Class (b).
- `SnapintoshBootFramebuffer` (`drivers/boot-framebuffer/`) is a codeless kext for early
  boot profiles. It needs an AuxKC, which `kmutil` builds on a Mac. It cannot replace
  Metal (`docs/GUI-BOOT-ROADMAP.md:108-115`).

### 3.9 GPU and GUI: blocker

WindowServer "waits 30 s for a Metal device and aborts ('No suitable Metal devices present
after 30 seconds'); SkyLight has no software-compositor startup path"
(`docs/GUI-BOOT-ROADMAP.md:108-111`). The project rejected CPU, software, paravirtual and
host rendering (`docs/GUI-BOOT-ROADMAP.md:13-15`). Every GPU piece is Adreno a8xx
specific: AdrenoX2 kext, AdrenoMetal, air2ir3 targeting ir3, and monitor IB validation in
`compat/adreno-ib.c`. QEMU has no Adreno. Apple's paravirtual GPU (QEMU `apple-gfx`) needs
a macOS host's ParavirtualizedGraphics.framework and the vmapple kernel, not J700.

A Metal driver for virtio-gpu (Venus or a custom protocol) would mean redoing the whole
Adreno effort against a new target: many months. **Class (d).** Reachable without a GPU:
verbose console, single-user or BaseSystem shell over serial, and headless userspace.

### 3.10 Input, network, host link

- **Input.** SnapI2CHID drives Qualcomm GENI I2C (`drivers/snap-i2c-hid/`). That is
  irrelevant headless. For a GUI it would need a new HID path, for example a virtual AIC
  device plus a kext (c). It is moot because of 3.9.
- **USB NCM host link.** It is only the proxy transport. The PL011 `--uart-proxy` already
  replaces it in QEMU (a). Throughput is the issue for bulk uploads. Use EFI preload, or
  QEMU's `-device loader,file=...,addr=` into memory EDK2 leaves alone; that variant is
  untested.
- **Guest networking.** The guest virtual Ethernet lives inside the AdrenoX2 kext
  (`docs/ARCHITECTURE.md:113-115`), which needs a Mac-built AuxKC. BaseSystem does not load
  third-party kexts anyway (`docs/GUI-BOOT-ROADMAP.md:114-115`). A virtio-net or
  semihosting path would need a new kext plus a monitor device model (c). Serial is the
  practical channel.
- **Wi-Fi, audio, thermal, DVFS.** Opt-in or A16-only (`docs/ARCHITECTURE.md:213-240`).
  Leave them off.

### 3.11 Firmware and tooling

- The IPSW download (`tools/fetch-ipsw.py`) and `tools/extract-firmware.py` are plain
  Python plus `lzfse`, so they work on Linux (b).
- The BaseSystem APFS image and the ISC/xART image the RAM route needs are not produced by
  any script in the repo. `tools/qemu-sptm-oracle/setup.sh:32-33` consumes
  `basesystem-26A428.apfs` and `isc-three-xart-20260923.apfs`. On Linux you would need
  `ipsw`/dmg extraction and possibly AEA decryption. darwin-vm's `get_files.sh` and
  `fix_perms.sh` "require a Mac". Treat it as (b) with a borrowed Mac, (d) without one.
- The installed macOS image is produced with Virtualization.framework on an Apple-silicon
  Mac (`docs/FULL-INSTALL.md:44-60`). That is a Mac dependency, class (d) without a Mac.
- The monitor and probes build with clang and lld, using Homebrew paths
  (`probes/run.py:33-37`). Linux clang/lld works with path edits (b).
- Kexts, the AuxKC and the Metal plug-in need macOS 27, Xcode and the 26A428 KDK
  (`docs/BUILDING.md:3-12`). That is class (d) on Linux, and needed only for GUI or network
  work.
- Images are pinned by SHA-256, and unknown kernelcaches are refused
  (`docs/COMPATIBILITY.md:13-20`). Use exactly 26A428 J700.

## 4. Route B: QEMU emulates the Apple machine (no monitor)

`jprx/qemu-sptm` (QEMU fork, `-M darwin`) and `jprx/darwin-vm` (scripts), checked through
their GitHub READMEs on 2026-10-24:

- They emulate SPTM/TXM, SPRR/GXF, AIC v1-v3, AMCC, CPU implementation registers, a fake
  SEP, UART/FIQ and an APFS RAM disk plus trustcache loader. They boot "with only 1 patch
  to the kernel and zero patches to userspace".
- Limits: "PAC is completely disabled", "Multicore doesn't work", no display or GUI.
- darwin-vm boots macOS 26.6 and 27.0 for M1-M5 to "a barebones root shell in just a few
  seconds" and "runs anywhere qemu runs (ARM host not required)". Firmware extraction
  needs a Mac with `ipsw`.
- Snapintosh's own oracle runs exactly this with the 26A428 J700 kernelcache, the real
  SPTM and TXM, 16 GiB, serial to a file and `cpus=1`
  (`tools/qemu-sptm-oracle/run.sh:15-21`). It patches the device tree to drop
  `/dock-connect` (`tools/qemu-sptm-oracle/j700_dt_fixup.py:7-9,26-27`). Their build note
  is about 100 s on an M3 Max (`setup.sh:5`). On Linux, drop the Homebrew include paths in
  `setup.sh:26-27` and the BSD `sed -i ''` (`setup.sh:20`).

Route B is clearly easier on Proxmox: no NV2, no shadow paging, no monitor port. It is
also faster under TCG, because it has no EL2 trap-and-emulate layer. It cannot give a GUI
either. Its value for Snapintosh is as the reference oracle.

Other prior art, **unverified here**:

- alephsecurity `xnu-qemu-arm64` (iOS 12 on QEMU);
- TrungNguyen1909 `qemu-t8030` and the ChefKiss "Inferno" fork (iPhone 11 / iOS 14+ with
  some display);
- upstream QEMU `-M vmapple` (10.0+, the Virtualization.framework guest, practical only on
  macOS hosts because of `apple-gfx`);
- Corellium (commercial);
- the Huawei Qingyun project that ran vmapple under KVM (`docs/FEASIBILITY.md:117-123`).

## 5. Effort (Route A, one experienced engineer)

```
+-----------------------------------------------+----------------+------------------------+
| Item                                          | Effort         | Notes                  |
+-----------------------------------------------+----------------+------------------------+
| Linux tooling: llvm/lld/edk2 paths, py deps   | 0.5-1 day      | run.py:33-39           |
| AAVMF install + hand-run QEMU cmdline         | 2-4 h          | virtualization=on      |
| Firmware: IPSW, BaseSystem/ISC images         | 1-3 days       | Mac makes it ~hours    |
| Single-CPU xnuboot --qemu up to XNU entry     | 1-3 days       | harness exists         |
| SMP port: GIC/affinity from MADT, -smp 6      | 1-3 days       | secondary.c, cpu_boot  |
| TCG vs Oryon divergence debugging             | 1-3 weeks      | biggest unknown        |
| Serial console ring live view                 | 0.5-1 day      | optional               |
| BaseSystem RAM-disk userspace (launchd)       | +1-2 weeks     | after XNU serial log   |
| ANS2 backend on QEMU NVMe (installed system)  | 1-2 weeks      | + Mac for the image    |
| Guest network (virtio/semihost + kext)        | 2-4 weeks      | needs Mac for kext     |
| GUI (Metal on virtio-gpu or similar)          | many months    | effectively blocker    |
+-----------------------------------------------+----------------+------------------------+
 Milestone 1, XNU serial log in TCG (SMP):       about 2-4 weeks
 Milestone 2, BaseSystem launchd/shell:          about 3-6 weeks total
 Route B, macOS 27 root shell on serial:         0.5-2 days (Mac needed once for firmware)
```

## 6. TCG performance expectation

- Route B runs on one vCPU, PAC off, no EL2 layer. darwin-vm claims a root shell "in a few
  seconds" (host unstated). On an i9-14900K, expect seconds to a few minutes; not
  measured. The oracle's default 600 s run budget (`run.sh:12`) gives an upper bound for a
  useful trace.
- Route A carries several TCG costs:
  - every SPTM call, IMPDEF sysreg and native-call fault is an emulated EL2 exception;
  - the shadow walks run as emulated code;
  - PAC is emulated, even with `pauth-impdef`;
  - 6 vCPUs each take a 1 kHz poll.

  MTTCG gives real parallelism across the 14900K's P-cores, but expect TCG at about
  1/10 to 1/30 of native single-thread speed. On the A16 the BaseSystem route ran to a
  1,200 s watchdog with 70 processes (`docs/GUI-BOOT-ROADMAP.md:104-105`). In TCG, expect
  the XNU serial log within minutes and launchd in tens of minutes to an hour or more.
  Disable the watchdog and lengthen timeouts.

## 7. Minimal reachable milestone and first steps

The minimal milestone is the stock 26A428 kernel printing its verbose boot log over the
monitor's serial ring in QEMU TCG, then mounting the BaseSystem RAM disk.

1. `apt install pve-edk2-firmware-aarch64` (or `qemu-efi-aarch64`), plus clang, lld and
   python3.
2. Build q1n1 `make uefi`, and the `xnuboot` blob from `probes/run.py`, with Linux paths.
3. Run `SNAP_NO_FB=1 python3 probes/run.py xnuboot --qemu --console serial` with
   `-cpu max,pauth-impdef=on,sve=off,sme=off` and `-m 8G` edited into `probes/run.py:66-79`.
   Expect a stop at the PMGR CPU-complex assertion with one CPU.
4. Port the GIC/affinity constants, run `SNAP_SMP=1` with `-smp 6`, and iterate on stops
   using the project's own stop reports (`probes/run.py` verdict decoder).

Before step 1, consider whether Route B already answers the question.
