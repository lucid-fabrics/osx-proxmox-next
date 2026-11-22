# Blockers for a pure-Linux user

Ranked by how hard they stop someone who has an x86 Linux box and no Mac. Item 1 still stops a user outright, items 2, 4 and 5 are solved and item 3 is partly solved.

## 1. The installed disk can only be made on a Mac (Mac-only, REMAINS)

The installed disk (`disk.img`, `aux.img`, `machineid.bin`, `hardwaremodel.bin`) comes from `vzinstall` (`mac/vzinstall.swift`), which restores the IPSW into a raw disk with Apple's Virtualization.framework (296 s on an M3 Max). `aux.img` carries an apticket that is personalized to the machine's ECID, and only Virtualization.framework produces it. We know no Linux replacement. Nothing in `snapintosh make-disk` can hide this: the CLI prints the Mac steps (`make-disk mac-steps [--build B]`) and imports the result. Ambrosia boots an installed system only, so it does not remove this step.

Also needed on the Mac, by hand: the Setup Assistant (account `snap`, no Apple Account, automatic login, then Shut Down), about 3 minutes in a window. In total one Mac session of about 15 minutes plus the download. It is now the only Apple-side step left in the chain.

Legal side: Apple's macOS license allows macOS VMs on Apple hardware only. The Mac step is on Apple hardware, the Linux run is not.

Would unblock: an IPSW restore and personalization implemented on Linux for the `vma2macosap` board (idevicerestore-style signing request for a virtual ECID). A study of it found: Stela (tylertms/Stela, GPL-2.0) already installs macOS on a PC with no Mac, for another machine identity, and Ambrosia has a restore boot mode, but nobody has put the pieces together for `vmapple`. A distributable pre-installed image is not possible: it is Apple's software.

## 2. AVPBooter.vmapple2.bin (SOLVED by Ambrosia)

QEMU's vmapple machine used to boot through Apple's `AVPBooter.vmapple2.bin`, which cannot be redistributed and cannot be downloaded alone. Ambrosia (steelbrain, v0.1.3, GPL-2.0-or-later OR LGPL-3.0-or-later) is open boot firmware that reads the installed macOS from the disk, prepares the kernel as iBoot would and starts it. Neither AVPBooter nor iBoot runs. A test booted our golden to a drawn desktop with Dock and Safari through it, it became the default, and `snapintosh build` builds it from the upstream tag plus three small patches of ours (`patches/ambrosia/`: `Graphics.KeepAppleGfx`, `Chosen`, `Asmb`, all optional config keys). The result is byte-identical to the lab image. AVPBooter is now an optional legacy input (`run --fw avp`) and no longer part of `fetch`, `make-disk` or the default `run`.

What is left of this item:

- The policy keys are our policy, not SIP on: `internal-use-only-unit = 1` (without it the Data volume panics at mount: "unencrypted data volume is not allowed") and `lp-sip0 = 0x7f` (without it ssh takes 1044 to 1127 s instead of 606 s on the test rig). Nobody knows what in iBoot's device tree makes APFS accept the unencrypted volume. A release that wants real SIP needs that cause found.
- Our QEMU needs two commits for Ambrosia (the `ambrosia=on` machine layout with RAM at 4 GiB and 64-bit IOSurface registers, and PAuth key copy to secondary CPUs), plus an avp-rtc VMState for snapshots. They are in the d3 branch. They are not public (item 4).
- Apple's LLB and iBoot code stays inside the user's own `aux.img`. Ambrosia does not run it. Zeroing that range (first 0x80000 bytes) was shown to boot to the desktop and ssh in a test, but the CLI does not do it, and the Safari page was not drawn in that one variant.

## 3. The tested IPSW may no longer be installable (PARTLY solved: a second build path)

The whole stack was tested on macOS 27.0 build 26A428. The `ipsw.me` index lists every `VirtualMac2,1` build as not signed, but a check found the same index shows 26A428, 26A434, 26B123 and 26B131 as signed for real Mac identifiers, and the VirtualMac flag is not a signal (it is one UniversalMac IPSW). So 26A428 is most likely still installable. That is not proven: only a TSS request for `vma2macosap`, or the next `vzinstall` on a Mac, proves it. We have not run a fresh install since the lab's first one. Booting never asks Apple again: an install made while a build was signed keeps booting.

What changed:

- `pins.env` has a table of builds and `snapintosh fetch --build 26A428|26B131` picks one (sha256 checked, file name from the URL).
- 26B131 (27.2.1, 25.6 GB, released 2026-11-09) is a small step for the release path. The one build-tied piece of the default stack, the IOFence patch, now matches any known build by shape and hash with a table row per build (26A428 and 26B131 rows, 104 offline checks pass, unknown builds are refused and the guest simply runs unpatched). Ambrosia upstream lists 27.2.1 as tested under TCG. Everything else is content-keyed or a device-tree property.

What is still open for 26B131:

- This stack has never booted it. The golden is one Mac install per build (item 1), then about 4 to 5 working days of engineering work: golden on Linux, quiet profile label diff (144 labels were found on 26A428, missing ones are harmless, new 27.2 daemons stay on), cold-boot checks of ssh and drag, harness pixel boxes, own translation cache pack.
- The apple-gfx guest protocol (kext 15.0.0 in both, 99.3 percent same code with field offsets masked) is the one real unknown: 0 to 3 days in reims-vgpu if it misbehaves. The guest Metal plug-in was not checked.
- The numbers in the README are 26A428 only.

## 4. Sources (SOLVED: shipped in this repository)

The QEMU fork is the series in `patches/qemu/` on upstream QEMU v11.1.0 (a build fetches the tag from gitlab.com/qemu-project/qemu and applies them). reims-vgpu and metal2vulkan are vendored in `vendor/` at the exact commits the release is tested with (their upstream bases are no longer served, `vendor/README.md` has the details). Ambrosia is fetched from its upstream tag plus our three patches in `patches/ambrosia/`, and the Mesa fix is `patches/mesa-lavapipe-immutable-samplers.patch`. The reference binary md5 is in `pins.env`.

## 5. The golden (ssh-ready, tuned image) is automated since the golden step was automated

`make-disk golden [BASE-DISK]` builds it from the installed disk and needs no Mac and no manual step. Phase 1 is a single-user boot under Ambrosia on a serial socket (65 s): ed25519 root key and the sshd LaunchDaemon (`persist-sshd.py`), `ProcessType Interactive` on that job, the `timed` fix, and an autologin fallback that only acts when the install has none. Phase 2 is one normal boot over ssh: the quiet profile (`apply-quiet.sh`, `backgroundtaskmanagementd` and `kernelmanagerd` kept) and a clean shutdown. The result is `golden/root-quiet.qcow2` on `golden/root-sshd.qcow2` on the base disk, which is opened read only. The Ambrosia policy keys (`internal-use-only-unit`, `lp-sip0`) are not stored in the golden: the config bundle carries them on every boot.

What still needs a user: only the Mac step (item 1) and, in it, the Setup Assistant with automatic login. `make-disk adopt-golden` stays for anyone who already has a golden. The labels of the quiet profile were found on 26A428; on 26B131 diff `launchctl print-disabled system` first.

Not done: editing the APFS volume offline from Linux. The only candidate, the out-of-tree linux-apfs-rw module, is not in the host kernel (the VM runs a cloud kernel without it) and it is experimental for writes, so the single-user boot stays. The guest does not power off by itself after phase 2 (QEMU has to be told to quit, which the CLI does after a 90 s grace period).

## 6. No GPU means building a patched Mesa from git

The renderer needs `VK_EXT_fragment_shader_interlock`. Stock lavapipe in Mesa 25.0.x lacks it. The tested Mesa is 26.3.0 development (`0a6fa60abb73`) plus one local patch that keeps immutable samplers in push descriptor sets (`patches/mesa-lavapipe-immutable-samplers.patch`, not upstream at the last check). `snapintosh build --only mesa` does this and was run on clean Debian 13 and Ubuntu 26.04 roots (191 s and 205 s on 16 threads, rc 0, no missing package beyond the Mesa list in `build --deps`). Hosts with RADV, ANV or NVIDIA need none of it.

## 7. Root setup on the host

- `/dev/udmabuf` must exist, be accessible to the user (for example by putting the user in group `kvm`) and have `list_limit=65536 size_limit_mb=4096`. The kernel defaults (1024 and 64) kill the first frame. This laptop has the defaults, so `check-host` fails it. Needs `sudo` and a modprobe config.
- Guest RAM must be a sealed memfd, 2 MiB aligned: `lib/memfdhold.py` does it (`RAMMEMFD=1`). Normal memory backends fail (`task 1 not created`, black screen).
- Vulkan driver, `vulkaninfo`, udev rules and group membership differ per distribution and GPU vendor.

## 8. Build toolchain is heavy (package list now verified)

The list was verified on minimal debootstrap roots (Debian 13 and Ubuntu 26.04): `build` and `build --only mesa` pass with `build --deps` as it is now. The old list lacked `cmake` and the X11 development packages (reims-vgpu compiles SDL3 from source), now added. The caveat was closed that followed: QEMU's configure and SDL3's cmake no longer pick optional features from the installed packages (fixed feature set), so the minimal list gives the same md5 as a full install (`cf135e84fec1907c762beb52925fa288` on Debian 13, `7a27d6f4a0181de261f5938440365958` on Ubuntu 26.04, which has another compiler). The list lost nothing and gained `libfdt-dev` (system libfdt instead of a network clone) and keeps `libgnutls28-dev` (VNC password authentication needs gnutls' DES); the 22 optional packages are no longer needed. The paragraph below is the older state.

Ambrosia is the easy part: `snapintosh build --only ambrosia` ran on a laptop (rust 1.99.1, `aarch64-unknown-none`, 7 seconds) and reproduces the reference image byte for byte. reims-vgpu pins Rust `nightly-2026-11-03` and builds a firmware ROM with `clang --target=x86_64-unknown-windows` and `lld-link`/`ld.lld`. QEMU needs meson, ninja, glib, pixman, slirp, gnutls (the `configure` call uses `--enable-pvg --enable-slirp`). `snapintosh build --deps` lists packages from memory of the test VMs, not from a clean install. A test in a throwaway Debian container was not possible: the laptop has the docker client but no local daemon (the configured context points at another machine, which this task may not use). The laptop itself lacks clang, lld and meson, so only the pin and dependency-fetch steps ran. Running `cargo build` straight on the crate pulls SDL3 and needs cmake, so use `cargo xtask dist --platform vulkan`, which `build` does.

## 9. Binary, snapshot and cache coupling

- Translation cache packs are keyed by the QEMU binary: a new build starts empty, and needs two warm-up boots (about 4.3 GB). Cold boots without a cache are slower.
- Snapshots are bound to the state format (`SNAPSHOT_STATE_FORMAT`; a build with the same format resumes them, `build` keeps the old binary when it changes), GPU mode, vCPU count, RAM and extra flags. Each is 8.6 GB.
- The golden is a qcow2 overlay with an absolute backing path to `disk.img`. Moving `~/.local/share/snapintosh/vm` breaks it until `qemu-img rebase -u`.

## 10. Smaller things

- Guest speed is not interactive-grade: about 15 fps drag at best on a 16-thread host, 3 fps or less on 6 cores. Expect complaints.
- Slow hosts: on 6 to 8 weak cores the first Safari launch of a boot loops through GPU-process kills (WebKit's 3 s watchdog) for 2 to 5 minutes. Guest clock dilation (branch q2) is the fix in progress, not in the default.
- VNC (`--vnc`) is loopback only and has a random password by default (`<run dir>/vnc.pass`, mode 600); `--vnc-insecure` removes it.
- The guest account is `snap` with password `snaplab` (a convention for the golden image), and ssh is root with a generated key. Fine for a test box, change it for anything else.
- `REIMS_VGPU_EARLY_EXEC_DONE` is unsafe and `REIMS_VGPU_FAST_COMPLETION` gives no gain: both stay off, the CLI never sets them.
- Late ssh after a restore (3 of 10 in early tests) is fixed by the sshd Interactive tweak, the ARP seed and a 6 s connect timeout in the resume probe: 10 of 10 resumes answered in 3.9 to 5.1 s.
- Display is `-display none`: there is no local window, only VNC and QMP screendump.
