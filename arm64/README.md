# Snapintosh on x86: arm64 macOS 27 under QEMU (beta)

Snapintosh runs an arm64 macOS 27 guest on an ordinary x86 Linux box (a virtual machine works). There is no KVM and no Mac involved at run time. The CPU is emulated in software (QEMU TCG, a fork with a `vmapple` machine and a persistent translation cache). The guest boots through Ambrosia, an open boot firmware, instead of Apple's AVPBooter and iBoot. Graphics go through `reims-vgpu` (a paravirtual GPU device) and `metal2vulkan` (Metal commands to Vulkan), so the guest's Metal work lands on your host GPU, or on lavapipe if you have none.

This directory holds the beta: one CLI, `snapintosh`, plus the docs and the sources it builds. It is not a finished installer. Read `docs/BLOCKERS.md` before you start: **installing macOS into the guest disk still needs a Mac, once**.

## Legal

We ship no Apple code and no Apple files. You bring your own:

- The macOS restore image (IPSW). `snapintosh fetch` downloads it from Apple's own CDN to your disk. We never host or bundle it.
- The installed disk, which the one Mac step produces from your IPSW (`disk.img`, `aux.img`, `machineid.bin`, `hardwaremodel.bin`).

`AVPBooter.vmapple2.bin` is no longer an input. Ambrosia (https://github.com/steelbrain/ambrosia, v0.1.3, GPL-2.0-or-later OR LGPL-3.0-or-later) replaces AVPBooter and iBoot, so no Apple boot file is run or needed. Our three small Ambrosia patches (optional config keys) are in `patches/ambrosia/` under the same license, and `snapintosh build` builds Ambrosia from the upstream tag plus those patches. The legacy `--fw avp` path still exists and then needs the AVPBooter file from your own Mac, which you must not redistribute. The install leaves Apple's boot code inside your own `aux.img`; Ambrosia does not run it.

The QEMU fork is GPL and its source goes with any release. The series that makes it is in `patches/qemu/` and the renderer sources are in `vendor/`.

Apple's macOS license allows macOS virtual machines only on Apple-branded hardware. Running macOS on a PC is, by that license, not permitted. This project is research tooling: you are responsible for what you run and under which terms. Nothing here is legal advice.

## Speed, honestly

It is still slow: every arm64 instruction is translated in software, and the desktop feels like a 15 fps slideshow. These numbers are from one rig on one day, 5 interleaved cold-boot rounds, seconds from QEMU start, median. "Oldest build" is the first QEMU build that boots the current guest (Nov 10, 6 vCPUs, AVPBooter, no translation cache, old guest profile). "Today" is the default this CLI sets up (Ambrosia, IOFence patch, DSB skips, ARP seed, 12 vCPUs, sshd Interactive golden, warm translation cache).

```
| Measure (seconds, median of 5)         | Oldest build (6 vCPU) | Today, CPU only | Today, with GPU | Oldest to today |
|----------------------------------------|-----------------------|-----------------|-----------------|-----------------|
| ssh answers                            | 393                   | 46 (floor)      | 46 (floor)      | 8.6x or more    |
| Wallpaper on screen                    | 267                   | 82              | 77              | 3.5x            |
| Safari first page drawn                | 597                   | 178             | 170             | 3.5x            |
| Safari launch (from the open command)  | 93                    | 34              | 30              | 3.1x            |
| Window drag (frames per second)        | 4.2                   | 14.7            | 15.5            | 3.7x            |
| Safari window back from a snapshot     | -                     | 2.1             | 1.9             | -               |
| New page after a snapshot restore      | -                     | 16.8            | 14.0            | -               |
```

Rig: AMD Ryzen 9 9950X VM (16 vCPUs, 24 GiB), RADV on a 2-CU iGPU for the GPU column and lavapipe for the CPU column, 2026-11-18, guest 8 GiB at 1440x1080.

### The same defaults through the release CLI

These were measured with `snapintosh run`, `snapshot` and `resume` themselves (a clean-room build of this directory, 2026-11-19), not with the test harness. A probe outside the VM only reads the run directory (QMP screendumps, ssh, the Safari page served from the host). Host: AMD Ryzen 9 9950X VM (16 vCPUs, 24 GiB), RADV on the 2-CU iGPU (`--gpu auto`), 12 guest vCPUs, guest 8 GiB at 1440x1080. Seconds from QEMU start, median of 3 cold boots with a warm pack (min-max); the first column is one boot with an empty pack, which is what a new build costs once.

```
| Measure (seconds)                       | First boot, empty pack | Cold boots, warm pack (n=3) |
|-----------------------------------------|------------------------|-----------------------------|
| ssh answers                             | 51.8                   | 41.8 (41.8-42.0)            |
| Dock on screen                          | 80.8                   | 65.9 (65.9-65.9)            |
| Wallpaper on screen                     | 90.8                   | 75.9 (75.9-75.9)            |
| Safari first page drawn                 | 198.1                  | 175.9 (173.9-176.5)         |
|   same, from the open command           | 39.9                   | 31.7 (30.6-31.7)            |
| Window drag (frames per second)         | 15.47                  | 15.19 (14.94-15.33)         |

Restore from a Safari snapshot (one snapshot, restored 3 times)
| Safari window back, from cont           |                        | 0.23 (0.18-0.23)            |
| ssh answers after cont (clock stepped)  |                        | 0.91 (0.88-0.93)            |
| New page drawn, from the open command   |                        | 7.0 (7.0-7.0)               |
| New page drawn, from cont               |                        | 11.2 (11.1-11.6)            |
```
The warm-pack boots are 15 s faster to the Dock and 22 s faster to the first Safari page. The first boot missed the pack 6.0 million times and translated those blocks (and wrote them, 4.2 GiB); the warm boots missed 13 to 41 thousand times. Definitions as above (Safari is opened after Dock and Finder run, a quiesce and a 60 s settle, so "first page drawn" contains about 100 s of settle). The snapshot was taken with `snapshot safari --settle 200` and Safari opened inside that window. The window time has 0.3 s resolution (screendump polling).

Read the numbers with these limits:

- "Boot" has no single definition. ssh, Dock, wallpaper and Safari are different moments. Use wallpaper or Safari for "usable desktop".
- ssh is floored: the harness first tries at 45 s and it answered at once, so read "46 s or less". The multiplier is a lower bound.
- "Oldest to today" is every change at once (translation cache, golden, Ambrosia, patches, 12 against 6 vCPUs), not one feature. The sshd Interactive tweak alone took tens of seconds off ssh-driven steps.
- "fps" is the harness's count of guest frames during a timed 20 s window drag. It says how fast the machine runs, not how smooth it feels.
- The GPU only helps a little (about 6 percent drag, 4 s on the Safari launch). The guest is the bottleneck. A 2-CU iGPU is small; bigger GPUs scaled 1.6 to 2.2x over lavapipe on drag in older tests.
- `present_probe` reports every frame as dropped on the GPU arm. That is not a frame-drop measure: Apple's paravirtual display driver gives no presentation time. The frames are shown.
- Single host, one session, n=5, min-max not confidence intervals. Nothing is a comparison with real Apple hardware or with a natively virtualized guest.
- Restore numbers are one snapshot per arm restored 5 times. The numbers measure an already installed guest with the quiet profile (144 launchd jobs disabled).

### Hardware table: three hosts

Same defaults, median of 3 to 5 cold boots (min-max), seconds. The first two rows come from the test harness (from QEMU start). The Intel row comes from `snapintosh bench` (built, golden made and measured with the release CLI only, commit c9939ff), so its Safari and Dock definitions differ and are marked.

```
| Host                          | Method | Cores given        | GPU                | Guest        | ssh        | Wallpaper | Safari first page   | Drag fps     | Safari from snapshot |
|-------------------------------|--------|--------------------|--------------------|--------------|------------|-----------|---------------------|--------------|----------------------|
| Ryzen 9 9950X VM (n=5)        | harness| 16 threads         | RADV, 2-CU iGPU    | 12 vCPU, 8G  | 46 (floor) | 77        | 170 (from start)    | 15.5         | 1.9                  |
| Ryzen 9 5950X VM (n=5)        | harness| 12 threads         | none (lavapipe)    | 12 vCPU, 8G  | 58         | 100       | 210 (from start)    | 13.6         | 2.7                  |
| Core i5-1240P VM (n=3)        | bench  | 6 threads, shared  | Iris Xe, Mesa ANV  | 6 vCPU, 4G   | 138 (99-186) | 196 (195-234) | 121 (113-182), open to draw | 6.7 (6.5-6.9) | 122 (106-124), open to draw |
```
Method `bench` means: `snapintosh bench --runs 3 --cpus 6 --ram 4G` on a golden made by `make-disk golden`, translation cache read-only after two warm-up boots, 4 GiB guest. Dock and Finder both up at 196 s (195-234). Safari is opened 60 s after the Dock and timed from the open command to the first drawn page, not from QEMU start, so it cannot be set against the lab rows' "from start" figures. The last column is a new page opened in a resumed snapshot (the snapshot had Safari open, saved after a 120 s settle), timed the same way; the resume itself took 0.9 s and ssh answered 7.3 s (5.9-7.5) after it. Three limits on the restore figures: the resumed guest runs at a load average of 500 for about 5 minutes (it catches up), so with the old fixed 120 s clock-step budget the bench could not complete the resume part (a re-run of the bench on this host with the default budget, 600 s on 6 threads and no `SETTIME_TIMEOUT`, and again after fixing the clock check: all metrics present and the resume part completed in 3 of 3 runs, which is the run this table now shows); the clock after the resume is within the 0.5 s limit in 3 of 3 runs (an earlier run showed 0.8, 2.2 and 1.2 s off, which was the old check reading through spawned ssh commands that take seconds under that load and read half of that too high: the clock itself was within 0.02 s).

The Intel row is a laptop-class chip (12th gen, 4 P-cores plus 8 E-cores, 16 threads) with the iGPU passed through to a virtual machine capped at 6 cores and 16 GiB on a host that runs other VMs, so it measures "6 shared threads and 16 GiB", not the whole chip. The guest got 4 GiB because the default 8 GiB plus the translation cache pack (4.9 GiB here, memory mapped) does not fit in 16 GiB. Safari drew its first page in all 6 cold launches of the two bench runs (140 to 228 s after the open command, limit 300 s), so the Safari kill loop (below) did not stop a launch here; the earlier lab-chain measurement of the same host needed 94 to 825 s. The drag is 5.9 to 7.0 fps in both runs. Results: `docs/bench/bench-20261120-144857.{json,txt}` (the table above) and `docs/bench/bench-20261120-132112.{json,txt}` (default budgets: cold part the same within noise, resume part failed in all 3 runs).

### Smallest host that builds the golden

`make-disk golden` was run end to end on 6 vCPUs of a 12th gen laptop chip and 16 GiB of RAM (Debian 13, Intel iGPU, other VMs running next to it): 644 s, ssh at 115 s, Dock and Finder at 411 s, desktop unlocked, shutdown by itself. That is the minimum: 6 threads, 16 GiB of host RAM, and `--ram 4G --cpus 6` (the 8 GiB guest plus the memory mapped translation cache does not fit in 16 GiB: the vCPUs then wait on the disk for the whole boot). With 24 GiB or more keep the default RAM. Below 6 threads is untested. The step does not use fixed waits for the slow parts: it waits for ssh and for the desktop until `GOLDEN_SSH_TIMEOUT` seconds (default 2400), and for the guest to power off until `GOLDEN_SHUTDOWN_WAIT` (default 900) and only then cuts the power. Early on it turns display sleep and the screen saver lock off, because a first login that takes longer than the 10 minute display sleep leaves a locked session (seen with a deliberate 15 minute hold on this host: `CGSSessionScreenIsLocked` at 777 s).

### Known issue on slow hosts: Safari

On a host with few or slow cores the first Safari launch of a boot can show "A problem repeatedly occurred" or "This webpage was reloaded". It is not a crash in the emulator: Safari's GPU process misses WebKit's 3 s responsiveness watchdog and gets killed in a loop for 2 to 5 minutes, until one start happens to finish in time. It hit every first launch on a 6-core host (9 of 9 boots) and never on the 16-thread Ryzen VMs. A guest-clock dilation option for slow hosts is being built and measured (QEMU branch q2, not the default). Until it lands, expect this on 6 to 8 weak cores and wait it out. What the CLI does about it: `snapshot` never saves a Safari that is not on screen (see `--open` and `--app` above), so the snapshot holds a Safari window whose page process has stayed up (the gate does not read the page itself: a Safari "can't open the page" window with a stable page process passes) or the command says why it did not save, and `--open` goes through one helper (`lib/guestapp.py`) that starts `open` in the guest in a session of its own and in the background, so ssh returns at once and the open still runs (a plain `ssh ... open -a Safari` blocked for 90 s in 7 of 12 settles on this host) and checks that the process is up, asking again up to three times. The cause is in the guest, not in the CLI: Soak runs saw the same loop with the CLI's open timing and without it (a hand-timed open after Dock and Finder, loop in 8 of 12 first launches).

### Clock after a resume

The Ambrosia machine (the default) comes back with the guest clock within 1 s of the host at every gap tried (5 s to 7 min, 10 of 10): `resume` reads the guest clock first and only steps it when it is off (a sub-second `settimeofday` through one long-running guest process, then it reads again and repeats until the clock is within 0.5 s or the budget ends; a reading counts only when its round trip is at most 0.25 s, because a spawned ssh command under the post-resume load takes seconds and reads half of that too high; the samples are in the run dir's `settime.samples`). The `SETTIME` line also says when the guest's sshd first answered a TCP probe and how long the login behind it took: a slow first ssh (57 s once on a 6 thread host) is a login that the busy guest was slow to finish, not the clock step, and `snapshot` steps it just before the save. The legacy `--fw avp` machine comes back behind by the save-to-resume gap until the guest corrects itself about 60 s later; `SYNCCLOCK=1` (or `auto`) moves the guest counter forward at the load, which fixes that for short gaps. It needs the clock patch (`patches/qemu/0004-*.patch`, already in the pinned QEMU commit) to be accurate (the advance was counted from the end of the save, about 7 s late).

### Reproduce the numbers: `snapintosh bench`

```
snapintosh bench                      # 3 runs; about 18 minutes each on 6 shared threads (25 when the clock step gave up early)
snapintosh bench --runs 5 --cpus 6 --gpu 0 --out ./results
snapintosh bench --no-resume          # cold boot part only (about 5 minutes per run)
snapintosh bench --dry-run            # print the plan and the host info
```

Needs the finished install (build and `make-disk golden`; the golden already fills the translation cache, and `make-disk golden --warm 2` or `make-disk warm 2` adds boots that open Safari so the Safari code is in it too, see step 5) and port 10022 and 18080 free. It drives `snapintosh run`, `snapshot` and `resume` itself and measures them over ssh, QMP and a small page server on 127.0.0.1 that the guest opens at 10.0.2.2. Per run:

```
boot to ssh                  seconds from QEMU start; the first ssh try is made at 45 s, so about 46 is the floor
boot to Dock                 Dock and Finder both running (ssh pgrep every 3 s)
Safari cold launch           ssh `open -a Safari` to the first screendump that shows the page (and to the GET reaching the host).
                             Started --settle seconds (default 60) after the Dock, so it is the launch, not the login
window drag                  frames of the apple_gfx_new_frame trace per second during a --drag-secs (20) sweep, after 40 s idle
resume time                  migration load to cont (resume.json)
Safari from a resume         the snapshot is saved with Safari up (opened inside the settle); then a new page is opened, open to draw
```

`--save-settle S` (default 120) is the time between the first ssh answer and the snapshot save, and Safari is opened inside it. After the resume the guest is still catching up (load average about 500 for 5 minutes on 6 threads), so the clock step has a budget that scales with the host: 120 s on 12 or more threads, 300 s on 8 to 11, 600 s below; `SETTIME_TIMEOUT=SECONDS` overrides it (also for `resume`). A clock step that does not confirm in time is recorded in the result as a note, the guest is running, and the Safari-from-resume steps still run. `--settime-limit` (default 900 s) is how long the bench waits for that line.

Result: `bench-<UTC stamp>.json` and `.txt` in `--out` (default `$SNAPINTOSH_HOME/bench`) and the table on stdout, median, min, max and standard deviation per metric. The JSON has the per-run values, every option, and the host: CPU model, host threads, vCPUs given, GPU path (RADV, ANV, NVIDIA, lavapipe or none), QEMU md5, release commit (`--commit`, else git HEAD of the CLI directory), cache mode. No host name, address or key goes in. A run that fails a step keeps its other numbers and lists the problem; the exit status is 1 then.

Differences from the test harness that made the old table: the translation cache is opened read-only (`--pcache rw` to let it grow), the page cache is not dropped (that needs root), and the old "Safari first page drawn from QEMU start" is not reported, because 45.6 s of it is the first ssh try and 60 to 80 s is the harness settle. The real cold launch (open to first draw) was 29 to 31 s on 472. The saves take 3 to 4 minutes each and write a snapshot of about 7.6 GiB under `$SNAPINTOSH_HOME/snaps`, deleted after the resume. Draw times are polled once a second, so they have 1 s resolution (a screendump that is slower than that makes the step longer). Run it on an idle host: other VMs change every number. A 3-run result on a 6-core VM (lavapipe, cache read-only) is in `docs/bench/`.

## What you need

```
host     x86_64 Linux, 16 threads recommended (6 is the smallest tested, see below, and cores matter: see the Safari issue above),
         16 GiB RAM minimum (24 GiB recommended; an iGPU carve-out or crashkernel can show 13 to 14 GiB of it, `check-host` then warns instead of failing), disk: `check-host` adds up what is still missing. Measured
         (GiB): toolchains, sources, build tree and prefix 15 (the build tree can be deleted), Apple IPSW 25,
         imported disk plus golden 31, translation cache pack 4.5 per QEMU binary, 10 headroom for run overlays
         and one snapshot (8.6 each). A fresh host needs 86 with the IPSW (it is for the Mac step: delete it after, so 61), an installed one 10. Measured on the 6 thread host: about 45 GiB in use without the IPSW after build, import, golden, pack (4.9) and one snapshot (4.0). No KVM needed
Vulkan   a device with VK_EXT_fragment_shader_interlock and VK_EXT_external_memory_host
         (tested: AMD RADV Mesa 25.0.7, Intel ANV Mesa 25.0.7, NVIDIA 615.71.10),
         or no GPU and a patched lavapipe that `snapintosh build --only mesa` builds
kernel   /dev/udmabuf, readable by you, with list_limit=65536 and size_limit_mb=4096 (needs sudo once, step 1)
build    meson, ninja, clang, lld, nightly rust (reims-vgpu), and rustup for Ambrosia: it cross
         compiles with rust 1.99.1 and the aarch64-unknown-none target, one `make`, seconds,
         no aarch64 gcc needed (`snapintosh build --deps` lists everything)
Mac      once, to install macOS into a disk image (Apple silicon, Virtualization.framework)
```

`snapintosh check-host` tests the host side, including the Ambrosia cross toolchain, and says what to fix.

## Step by step

```
1. snapintosh check-host                 host needs, GPU, udmabuf limits, build tools. With a host GPU, /dev/udmabuf must work: it prints
                                         the exact commands when it does not. Once, with sudo (the limits reset at every reboot unless
                                         the modprobe.d line is in place):
                                           sudo modprobe udmabuf
                                           echo 'options udmabuf list_limit=65536 size_limit_mb=4096' | sudo tee /etc/modprobe.d/snapintosh-udmabuf.conf
                                           echo 65536 | sudo tee /sys/module/udmabuf/parameters/list_limit
                                           echo 4096 | sudo tee /sys/module/udmabuf/parameters/size_limit_mb
                                           sudo usermod -aG kvm "$USER"      # the device is root:kvm 660; log in again afterwards
                                         Without a GPU (lavapipe only) udmabuf is not used. Over a non-interactive ssh the tools in
                                         ~/.cargo/bin are found without PATH set. A home, CARGO_HOME or RUSTUP_HOME under /mnt makes
                                         `build` stop at once (the reproducible build binds your data directory at /mnt); the release
                                         tree itself may be anywhere
2. snapintosh build --deps               print the packages, install them. rustup comes first on a clean user
                                         (curl https://sh.rustup.rs | sh): build then fetches nightly-2026-11-03 and
                                         rust 1.99.1 by itself
                                         (QEMU comes from upstream plus patches/qemu, the renderer from vendor/; set QEMU_REPO,
                                         REIMS_REPO or M2V_REPO to build from a clone of your own at the commit pinned in pins.env)
   snapintosh build                      reims-vgpu + metal2vulkan, the QEMU fork, then Ambrosia, into the prefix (4 min
                                         on 16 threads, 557 s on 6 shared threads; it prints a lot and is not hung)
   snapintosh build --only mesa          only if you have no usable Vulkan GPU
3. snapintosh fetch                      IPSW from Apple, resumable, size and sha256 checked, exit code non-zero when the download is incomplete or wrong (26A428, 26.6 GB, 10 minutes at 40 MB/s; 6 tries). The
                                         IPSW is only for the Mac step: copy it to the Mac and delete it on the Linux host (25 GiB)
   snapintosh fetch --build 26B131       the second supported build (27.2.1, 25.6 GB), see below
   snapintosh fetch --url U --sha256 H   another file: the size the server announces is always checked, the hash only if you give it (without it the tool says so)
4. snapintosh make-disk mac-steps        the one Mac step (vzinstall, Setup Assistant)
   snapintosh make-disk import ./vmdir   import what the Mac produced
5. snapintosh make-disk adopt-golden F   you already have a golden (a qcow2 overlay chain, for example a
                                         root-quiet3.qcow2 with its ssh key dir): copies the chain next to the imported
                                         disk and rebases it. Tested, no Mac needed for this step
   snapintosh make-disk golden           or build it from the installed disk: a
                                         single-user boot writes the ssh key, the sshd job (ProcessType Interactive), the
                                         timed fix and the autologin fallback; one normal boot applies the quiet profile
                                         over ssh, then optional warm-up boots (--warm N, default 0): the finished golden boots to the desktop,
                                         opens and quits Safari, so the translation cache pack is filled before your first `run`
                                         (about 5 minutes more per boot on 16 threads and no visible gain there, so it is off by default; `make-disk warm [N]` does it alone for an adopted golden).
                                         The translation cache pack, one rule: phase 2 of `make-disk golden` is a normal boot with the cache on, so
                                         `make-disk golden` alone leaves a pack of 4 to 5 GiB and `run`, `snapshot`, `resume` and `bench` work
                                         right after it. That pack has no Safari code in it, so the first Safari launch is slower (22 s on
                                         16 threads). Run `make-disk golden --warm 2` (or `make-disk warm 2` later) when you want Safari warm,
                                         and before a benchmark. After `adopt-golden` there is no pack at all until the first boot writes it.
                                         About 11 minutes on 6 cores (644 s on 6 threads and 16 GiB, guest 4 GiB), 5 on 16 threads without the warm-up. `--gpu auto` (the default) uses a GPU only
                                         when Vulkan really lists one (Debian ships radeon_icd.json on every host and phase 2 died on a
                                         VM without a GPU); --gpu 0 forces lavapipe
   snapintosh make-disk sshd-interactive only for a golden that did not come from `make-disk golden` (manual notes)
   snapintosh make-disk quiet            same: notes for an existing golden
6. snapintosh run --gpu auto             boot (stops after --secs, default 3600, or on Ctrl-C; the golden's phase 2 already wrote the translation cache pack, 4 to 5 GiB, see step 5; with an adopted golden the first boot writes it and the second one uses it, or run `make-disk warm`). On a 6 thread
                                         host add --cpus 6 (the default is 12). Add --vnc to see the screen (127.0.0.1:5900, random password in <run dir>/vnc.pass, `--vnc-insecure` for none)
   ssh -p 10022 -i ~/.local/share/snapintosh/vm/golden/ssh-state/id_ed25519 -o UserKnownHostsFile=~/.local/share/snapintosh/known_hosts -o StrictHostKeyChecking=accept-new root@127.0.0.1
   ssh ... "launchctl asuser 501 open -a Safari http://10.0.2.2:8000/"   a page served on the host (10.0.2.2 is the host in slirp)
7. snapintosh snapshot safari            boot, wait for ssh, settle 60 s (--settle N), save, quit. It prints four steps as it goes
                                         (boot to ssh, settle, clock step, state write) and a timing line at the end. The name is 1 to 64 of letters, digits, dot, underscore and dash
                                         (no leading dot, no `..`), and shorter when `SNAPINTOSH_HOME` is long (see Exit codes, files and secrets). To have Safari open in the snapshot let the CLI do it: `--open URL` (waits for the Dock and Finder, opens the page,
                                         and the save then waits until Safari's window is on screen and its page processes are stable, at most `APPWAIT` seconds, default 180; if it never is, the save
                                         is refused with status 5, nothing is written and an older snapshot of that name stays). `--app NAME` is the same check for an app you opened yourself, and a
                                         running Safari that nobody named only gets a warning. Use --settle 200 or more on 6 threads. About 2 minutes in all on a fast host, nearly all of it waiting
   snapintosh resume safari              restore it (ssh answers about 1 s after the guest continues; on 6 threads the guest then catches up
                                         for about 5 minutes, and the clock step waits for it: `SETTIME_TIMEOUT=SECONDS`, default 120 s on 12 or more
                                         threads, 300 on 8 to 11, 600 below; a clock step that gives up prints "SETTIME did not confirm" and the guest runs on)
```

Housekeeping. The save waits (at most `WALLWAIT` seconds, default 300, 0 turns it off) until the desktop is drawn: more than 500 colours on a sparse sample of the screen, or the two side columns show the wallpaper, so a big Safari window no longer holds the save for the full 300 s. Each `run`, `snapshot` and `resume` works in `~/.local/share/snapintosh/runs/<name>-HHMMSS` (an aux copy, an overlay, logs, 12 to 200 MB). It is removed when the command ends cleanly (QEMU exited 0 or was stopped with Ctrl-C, and a snapshot was written) and kept after a failure, so the logs are there. `KEEP_RUNS=1` keeps every run dir. The translation pack is compacted in the background after each run that wrote to it (also when the run is ended with Ctrl-C or SIGTERM), and `pcache/compact.log` gets a start line and an end line (in words and with the exit code: `compacted (rc=0)`, `nothing to do, not an error (rc=1)`, `pack in use, gave up (rc=2)`, `FAILED (rc=3)`) for every compaction, plus whatever the compactor prints. The compactor retries for up to `PCCOMPACTWAIT` seconds (default 30, 0 = no retry) while the pack is still locked by the exiting QEMU (rc 2, "in use"); the end line shows `waited=Ns`. QEMU dies with its `timeout` wrapper (PR_SET_PDEATHSIG), and every command that starts QEMU (`run`, `snapshot`, `resume`, `make-disk golden` (both phases), `make-disk warm`; not `check-host`, `build`, `fetch` or `status`) first stops the QEMU and helpers that an earlier, dead snapintosh left behind and says so. A process is stopped only when it provably belongs to that dead run: the run dir's `cli.pid` (pid, start tick, pid namespace) names a snapintosh of this pid namespace that is gone, and the process is in this namespace and started after it. Anything else that names a run dir (a container sharing the home, an old `cli.pid` without a namespace, a run dir without `cli.pid`) is skipped and listed, never killed. Golden phase 1 keeps its QEMU, qemu-nbd and helpers in `vm/golden/tmp` and writes the same `cli.pid` there, so a `make-disk golden` after a SIGKILLed one stops them too and sets the half written `root-sshd.qcow2` aside as `root-sshd.failed.qcow2` (run `make-disk golden` again, not `--again`, which needs a finished phase 1). A process you start that names a run dir in its command line (for example `tail -F` on its `qemu.out`, or a python script reading its logs) counts as part of the run: it is stopped when the run ends, and an exit stops everything that names the run dir even when a group signal killed the helper first.

Rebuilding with another pin: set it in the environment or in `~/.config/snapintosh/pins.env` (for example `REIMS_REF=<commit>`) and run
`snapintosh build` again. Only the stages that changed are redone (80 s for a renderer change). A snapshot from the old build resumes with the
new one when `SNAPSHOT_STATE_FORMAT` is unchanged, and `resume` prints both md5s. Each new QEMU binary starts its own translation cache pack
(5 to 6 GiB more): delete the old pack under `~/.local/share/snapintosh/pcache/`.

`run` uses the default settings: QEMU `-machine vmapple,ambrosia=on`, Ambrosia as `-bios` plus a config bundle loaded at 0x120000000, 12 vCPUs, 8 GiB RAM in a sealed memfd, the IOFence patch on (`SNAP_IOFENCE_PATCH=1`, it matches any known macOS 27 build and refuses unknown ones, so the worst case is the old drag speed), the DSB skips on (`P80_BC`, `P85_BC`), the early translation pack open on resume (`P82_PCOPEN`), the ARP seed, `M2V_INPLACE_RT=1`, `LP_NUM_THREADS=8`, translation cache in append mode. `REIMS_VGPU_EARLY_EXEC_DONE` and `REIMS_VGPU_FAST_COMPLETION` stay off on purpose. `snapintosh run --dry-run` prints the exact QEMU command. `--fw avp` gives the legacy AVPBooter boot, `--cpus N` changes the vCPU count.

Ambrosia needs policy keys in its config bundle (`internal-use-only-unit` so the Data volume mounts, `lp-sip0 = 0x7f`, which is what `csr-config=0x7f` did and is needed for speed). That is not a SIP-on configuration. Nobody, including Ambrosia's author, knows yet why APFS accepts the unencrypted Data volume once the first key is set.

Snapshots are bound to the QEMU state format (`SNAPSHOT_STATE_FORMAT` in `pins.env`), GPU mode and settings. A rebuild that keeps the format (for example a renderer bug fix) resumes your old snapshots. When a build changes the format, `snapintosh build` keeps the old QEMU as `qemu-system-aarch64.<md5>` next to the new one and `snapintosh resume` uses it by itself. Resume with the options you saved with.

### Exit codes, files and secrets

- `run`, `snapshot` and `resume` exit non-zero when QEMU exits with an error (anything but 0 or the `--secs` limit), when the helper that drives it fails (a helper killed by a signal counts as failed), or when the snapshot save does not complete. Whatever the command started (qemu-nbd, the memory holder, QEMU, the ssh master) is stopped on every exit path, including a failure before QEMU starts and a helper that fails while QEMU idles. A failed run keeps its run dir for the logs, a clean one removes it. Each run gets its own run dir (`runs/<name>-XXXXXX`).
- Ctrl-C, SIGTERM and SIGHUP end the CLI within a few seconds with status 130, 143 or 129, and stop everything it started: the save or resume helper, the progress `tail`, QEMU, qemu-nbd, the memory holder, the ssh master and whatever they started. An interrupted `snapshot` leaves no staging directory and keeps the older snapshot of that name; the interrupted run's run dir is removed (`KEEP_RUNS=1` keeps it). The save helper also stops waiting for ssh the moment QEMU is gone, so a QEMU that dies at boot ends the command at once. A SIGTERM to the whole process group (`timeout N snapintosh snapshot x`, a service stop) is handled the same way. `build` and `fetch` are plain foreground commands: Ctrl-C stops them as usual. A shell cannot catch a signal that was ignored when it started (`nohup snapintosh ... &` and background jobs of a non-interactive shell ignore SIGINT): there, stop it with SIGTERM.
- After a SIGKILL of the CLI nothing can clean up on the spot (a save leaves its staging directory with the guest RAM, 4 GiB at `--ram 4G`, and a run dir). The next `run`, `snapshot` or `resume` removes them, with the same proof it uses to stop the leftover processes: the run dir's `cli.pid` names a snapintosh of this pid namespace that is gone, and nothing of that run still runs. It says so on stderr. A snapshot that a killed save had just set aside (`.old-NAME-*`) is put back, never deleted. Run dirs kept on purpose (`KEEP_RUNS=1`, `make-disk golden`) stay. Staging directories with no run dir at all (left by an older version) go once nothing has written to them for two minutes. `snapshot` exits 5 when `--app` or `--open` was not on screen at the save.
- A process is only stopped when the CLI is sure it is its own: it records each child's start time, and the one daemon it only knows by a pid file (qemu-nbd) is stopped only if its command line is qemu-nbd on this run's socket. A stale pid file never kills an unrelated process.
- A save is built in a hidden staging directory and moved over the snapshot name only when it is complete: a failed `snapshot NAME` leaves the old NAME as it was.
- Everything the tool writes is owner-only (umask 077, and `$SNAPINTOSH_HOME` is mode 700): the saved RAM in `vm.state`, the overlays (they hold the autologin password file), the logs and the ssh key.
- `--vnc` has a random 8 character password by default, in `<run dir>/vnc.pass` (mode 600, never printed), because the guest logs in as an admin by itself. `--vnc-insecure` turns it off.
- The guest password for `make-disk golden` (default `snaplab`, only matters for autologin) is read from `--password-file FILE` or `SNAPINTOSH_GUEST_PASSWORD`. There is no `--password` option: a password on the command line shows in `ps`. `--user` must be a macOS short name (lower case letters, digits, `_`, `-`, 32 at most).
- ssh to the guest pins the guest's host key in `$SNAPINTOSH_HOME/known_hosts` on first use (`accept-new`). A new golden clears that file. If you replace the guest by other means and ssh refuses the key, delete the file.
- `SNAPINTOSH_HOME`, `SNAPINTOSH_PREFIX` and `SNAPS` must not contain a comma or newline (QEMU option syntax). Other characters, `&` and spaces and glob characters included, are passed through unchanged.
- The run dir holds unix sockets (`nbd.sock`, `qmp.sock`, `ssh.ctl`), and a unix socket path is limited to 107 bytes. The snapshot name is therefore limited to `107 - 33 - length of $SNAPINTOSH_HOME/runs` bytes (31 with the default home `/home/<8 letters>/.local/share/snapintosh`); a longer one is refused with that number before anything starts. A `SNAPINTOSH_HOME` too long for even an 8 byte name is refused too.
- Snapshots made by an older version may sit in a different directory (the default is `$SNAPINTOSH_HOME/snaps`): `SNAPS=DIR snapintosh resume NAME` finds them, and a `resume` that finds nothing says so.
- `SNAPINTOSH_GUEST_PASSWORD` set in your shell is read once and removed from the environment: only the golden helper gets it, never QEMU, qemu-nbd or ssh.
- `build` and `fetch` use the normal umask (022) for the toolchain prefix and the download directory, so a shared `SNAPINTOSH_PREFIX` or `fetch --out DIR` stays usable by other users; everything under `$SNAPINTOSH_HOME` stays private because that directory is mode 700.

### Translation cache pack

The pack lives in `~/.local/share/snapintosh/pcache/<id>.pack`. QEMU names it by a hash of its own executable (the read-only loaded segments), the CPU model and a few switches, so there is no gate on the binary's name, md5 or path: any binary you build gets its own pack, `snapintosh run` creates it on the first boot (about 4 GiB, written while the guest runs) and the second boot loads it. A binary with the same code (the same build in another directory, see below) finds the same pack. Rebuilding QEMU with different code starts an empty pack, and the old one stays on disk: delete it.

### Reproducible build

`snapintosh build` gives the same bytes in any directory and under any HOME (two clean-room builds from different directories: the same md5 for `qemu-system-aarch64`, `qemu-pcache-compact` and every file of the prefix, and for `libreims_vgpu.a`). It maps the build paths away (`-ffile-prefix-map`, `--remap-path-prefix`), configures QEMU for the fixed prefix `/snapintosh` and installs through a staging directory, fixes `PYTHONHASHSEED` for QEMU's decoder generator, and runs the Rust build in a private mount namespace where your data directory is `/mnt` (Cargo hashes the absolute path of metal2vulkan, which lies outside the reims-vgpu workspace, into every Rust symbol; no flag removes that). Without unprivileged user namespaces the Rust half is reproducible only from the same directory. `SNAPINTOSH_NO_REPRO=1` turns all of this off. It also gives the same bytes whatever development packages are installed: QEMU is configured with `--without-default-features` plus an explicit list (TCG, tools, pvg, slirp, VNC, pixman, gnutls, system libfdt, zstd; see `build_qemu`), and SDL3's cmake gets a fixed feature set from `lib/sdl3-features.cmake` (X11 and Vulkan on; D-Bus, IBus, libudev, liburing, PipeWire, Wayland, KMS/DRM, OpenGL off). A minimal root, a root with about 190 development packages and the full lab host all give the same `qemu-system-aarch64` md5 within one distribution. With the current pin (release-v2) that is `c36556f3118e04fca90005ae0f0dfd68` on Debian 13 (gcc 14.2; the pin's value in `pins.env`). The release-v0 pin gave `cf135e84fec1907c762beb52925fa288` on Debian 13 and `7a27d6f4a0181de261f5938440365958` on Ubuntu 26.04 (gcc 15.2). The release-v2 pin gave `7bca48fbfa6f6d4954b5643890243d61` on Ubuntu 26.04 (gcc 15.2; `libreims_vgpu.a` `3105d01f51d9b335d731c5b0ed1be662`, the same as with release-v0), measured on a fresh Ubuntu 26.04.1 server VM with only the `build --deps` set, the unshare profile above and a clean `build --only reims` then `--only qemu` from a git archive. The compiler is the one input that is still not fixed: the same pins give different bytes on Debian and Ubuntu. The result differs from the lab's `qemu-p8p89q` (md5 `4381561b...`), which was linked in the lab's directories with every optional feature on. A new md5 means a new translation cache pack on the first boot.

### The BQL spin (release-v2)

release-v2 adds one small change to the QEMU fork, in `system/cpus.c`. A vCPU thread that needs the big QEMU lock (BQL) used to go to sleep at once when another thread held it, and the wake-up costs more than most waits last. Now it spins for 50 microseconds first and only then sleeps. With 6 or 12 guest vCPUs most waits are shorter than that, so the guest runs a little faster and the host burns slightly fewer CPU seconds for the same boot.

What it gained, measured with the release CLI's own `snapintosh bench` on a 6-core VM (6 vCPUs, 4 GiB guest, interleaved rounds, n=10 each, medians, release-v1 against release-v2; the full record is `docs/bench/r30-release-v2.txt`): boot to Dock 7.6 % faster, Safari open to first draw 13.4 % faster, host CPU per launch 6.1 % lower, window drag unchanged. The 95 % intervals of the Dock and CPU differences exclude zero. The Safari interval still touches zero (release-v2 was ahead in 7 of 10 rounds), so read it as "faster, never worse". These numbers were measured with vCPUs at or below the host's threads (6 on 6); the default guest has 12 vCPUs, and on a host with fewer threads than that a waiting vCPU can spin while the lock holder is descheduled, which was not measured (use `--cpus` to match the host, or turn the spin off).

Turn it off with `P102_BQLSPIN=0` in the environment of `snapintosh run`, `snapshot` or `resume` (`P102_BQLSPIN=N` sets N microseconds). QEMU prints `bqlspin 50 us` on its stderr (`qemu.out` in the run directory) when it is on and prints nothing when it is off; at exit a second stderr line gives the spin's contended and hit counters. A spin-off release-v2 behaves like release-v1 within the measurement noise (about 4 %).

### Faster binary, opt-in: `snapintosh build --pgo`

`snapintosh build --pgo` builds QEMU with profile-guided optimisation and full link-time optimisation. It is off by default and the normal build stays the supported one. The compiler learns from a real boot of your guest: it builds an instrumented QEMU, warms that build's translation pack, boots the golden with 2 vCPUs, opens Safari until the page draws and drags a window, and then builds the final binary with that profile.

Cost: 49 minutes measured on 6 vCPUs (`docs/bench/r30-release-v2.txt`), but the bounds allow up to about 4 hours: instrumented build 2 minutes, pack warm-up two boots of up to 45 minutes each (the instrumented binary is about 4 times slower), a training boot of about 20 minutes that is repeated once when it fails (each up to 75 minutes), final build 3 to 5 minutes. The command prints the plan before it starts. It needs a working `snapintosh build`, a golden image (`make-disk golden`) and unprivileged user namespaces (`unshare -rm`), and it is meant for a machine you can leave alone. The boots it makes pick free ports for ssh and the page server, so a VM you started earlier does not get in the way. `SNAPINTOSH_DRY=1 snapintosh build --pgo` prints the plan and the checks it would make without building anything.

On Ubuntu 23.10 and later (26.04 included) AppArmor refuses user namespaces to ordinary programs (`kernel.apparmor_restrict_unprivileged_userns=1`), so `unshare -rm` fails with "Operation not permitted" and `--pgo` stops at its first check. `snapintosh check-host` says whether `build --pgo` can run, and the refusal prints the fix; snapintosh never changes the host. Either `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0` (this boot only; a file in `/etc/sysctl.d/` makes it permanent), or an AppArmor profile for `unshare` (it runs any program, so this lifts the restriction for everything started through `unshare`, not only for snapintosh): `/etc/apparmor.d/snapintosh-unshare` with `abi <abi/4.0>,`, `include <tunables/global>` and `profile snapintosh-unshare /usr/bin/unshare flags=(unconfined) { userns, }`, loaded with `sudo apparmor_parser -r /etc/apparmor.d/snapintosh-unshare`. Debian 13 allows user namespaces by default.

What it gained (release CLI bench, 6-core VM, 5 PGO rounds against 10 of release-v2): nothing outside the noise. Compared with release-v2, Safari open to draw was 3.8 % slower (interval [-7.9,+15.6]), boot to Dock 2.6 % faster and host CPU per launch unchanged; the intervals all include zero. Against release-v1 it is 10.0 % faster to the Dock and 10.0 % to draw, which is the spin, not the PGO. So it stays opt-in, and the normal build is the one to use unless you rebuild anyway and want to try it.

How it stays safe: the profile only matches the source at one absolute path and with the same compiler, so the instrumented build, the training boot and the final build all run in one private mount namespace that binds your data directory at a fixed path (as the renderer build does). Nothing of the profile is shipped. The compiler's profile warnings stay on in the final build and the build FAILS when any object of `accel/tcg`, `target/arm` or `hw/vmapple` has no profile or a mismatching one. Everything lives in `$SNAPINTOSH_HOME/pgo-work`: a failed or interrupted build (Ctrl-C, SIGTERM, even SIGKILL, which the next snapintosh command cleans up) removes it and stops what it started, and the working `qemu-system-aarch64` is replaced only after the new one compiled cleanly and starts. The new binary gets its own translation pack, so the first boots after it write a new one.

### The remaining Mac step

One Mac session, about 15 minutes plus the download: `vzinstall install` restores the IPSW (296 s on an M3 Max), then you click through Setup Assistant by hand (about 3 minutes: account `snap`, automatic login, Shut Down). Apple's Virtualization.framework is the only tool that personalizes the restore for a machine id and writes `aux.img`. There is no Linux replacement yet. No other Apple-side step is left: AVPBooter is gone from `fetch`, `make-disk` and `run`.

### Guest builds

```
26A428  macOS 27.0    default. Every number above, the quiet profile and the IOFence row come from it.
26B131  macOS 27.2.1  supported, not booted by this stack. The IOFence matcher has its row (unit-tested offline
                      against both kernelcaches), Ambrosia upstream lists it as tested, but you need your own
                      Mac install and golden, and the quiet profile labels, the harness boxes and the apple-gfx
                      guest side are unchecked there (about 4 to 5 working days of
                      engineering work to qualify).
```

## What is tested, what is not

Tested on a laptop with no Apple files: shellcheck, `check-host`, the Vulkan parser on sample dumps (RADV, ANV, lavapipe), `fetch` start and resume against Apple's CDN, `fetch --build` for both builds (dry), `make-disk import` on synthetic files, `run --dry-run` for both the Ambrosia and the legacy command lines, the pinned clones of reims-vgpu and metal2vulkan, and `snapintosh build --only ambrosia`: it applies our three patches to the upstream tag and builds `ambrosia.bin` byte for byte identical to the lab image (sha256 `586f6c7b...`). `tests/smoke.sh` runs the offline part. `python3 -m unittest` (from this directory) runs every `tests/test_*.py` script, one case each; the scripts still run on their own (`python3 tests/test_snapctl.py`).

Rehearsed end to end (16 threads, lavapipe): check-host, build from the pins, fetch, import, golden, run, snapshot, resume, a pin change and a resume of the old snapshot. Tested in a clean-room build of reims-vgpu, the QEMU fork and Ambrosia, `make-disk import` and `adopt-golden`, and cold boots, snapshot and resume through `snapintosh run|snapshot|resume` itself. Not tested here: the Mac install itself, and the build from a public repository (nothing of ours is public, the sources came from file:// bare repos).

Other caveats: in testing the early pack open on cold boots saw 1 of 5 WebContent crash loops, so it runs on resume only; 17 cold boots and 10 resumes ran without it.

## Layout

```
snapintosh         CLI (bash)             pins.env           pinned commits, Ambrosia pin, IPSW table
lib/               snapctl, benchctl (bench), memfdhold, vkparse, guest scripts (quiet, timed, sshd Interactive)
mac/               vzinstall.swift (the Mac step)         patches/   Mesa lavapipe fix, Ambrosia patches
docs/BLOCKERS.md   everything that stops a pure-Linux user      tests/     offline smoke tests
```
