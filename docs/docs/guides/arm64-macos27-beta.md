---
title: macOS 27 arm64 (beta)
description: Run an arm64 macOS 27 guest on an ordinary x86 Proxmox host, with no Apple hardware at run time.
---

# macOS 27 arm64 (beta)

The wizard offers a second kind of Mac next to the OpenCore ones: **macOS 27 arm64 (beta)**. It is a real arm64 guest, emulated in software (QEMU TCG) on your x86 host. There is no KVM acceleration and it needs no OpenCore. It is slow and it is a beta.

## How it works

The wizard creates one normal Proxmox VM, the **runner**: a Debian 13 x86-64 cloud image that you can see and manage in the Proxmox UI. On its first boot the runner downloads the `arm64/` folder of this repository at a pinned release tag or commit and refuses the download unless its sha256 matches, fetches the renderer sources as one more checksummed tarball (a release asset of this project), builds our QEMU, the renderer and the boot firmware inside itself, and runs the macOS guest there. Nothing is installed on the Proxmox host itself. Destroying the VM removes everything.

```
Proxmox host
  runner VM (Debian 13, cores and RAM from the wizard)
    snapintosh build         QEMU patches, renderer, boot firmware (about 10 minutes)
    macOS 27 guest (arm64)   VNC with a random password, never open to the network
```

Graphics run on lavapipe (software Vulkan) by default. If the host has a spare GPU you can pass it to the runner (CLI option `--gpu-pci`, off by default).

Every runner gets a login: the wizard makes an ed25519 key on the Proxmox host (`/root/.ssh/osx-next-arm64-<vmid>`, mode 600) or uses the public key you pass with `--ssh-key-file`. Log in with `ssh -i /root/.ssh/osx-next-arm64-<vmid> snapintosh@<runner address>`. `arm64 uninstall` removes the key with the VM, and only acts on a VM that carries the `osx-next-arm64` tag the wizard puts on its runners.

## What it needs

```
host     x86-64 Proxmox VE 8.1 or newer, 6 free cores or more (16 threads is the comfortable size)
RAM      runner 12 GB with a 6 GB guest (10 GB is the floor; the restore can OOM there); 24 GB or more on the host is better
disk     120 GB on the storage you pick (build tree, the macOS disk, the translation cache, one snapshot)
Apple    an aux image, a KDK directory and a golden LocalPolicy img4, passed as local files (see below)
storage  a storage with the Snippets content type (the local one by default), for the runner's cloud-init file
network  the runner downloads from cloud.debian.org, github.com, gitlab.com and Apple's servers
```

## One command, end to end

The runner no longer stops at `install-pending`. One `arm64 apply --execute` creates the VM, builds the stack (including the QEMU USB patches), restores macOS, runs the offline firstboot pass, boots the desktop and writes `ready` only after Dock and Finder answer.

Supply the Apple inputs on the Proxmox host. They are uploaded to the runner over the generated runner key; nothing Apple is stored in this repository:

```
osx-next-cli arm64 apply --vmid 310 --name macos27-runner --storage local-lvm \
  --cores 10 --memory 12288 --disk 120 --bridge vmbr1 \
  --bundle-file /local-4tb/macos27/artifacts/restore.cpio \
  --ramdisk-file /local-4tb/macos27/artifacts/restore-ramdisk.im4p \
  --restore-ambrosia-file /local-4tb/macos27/artifacts/ambrosia-r4.bin \
  --cache-dir /local-4tb/macos27/artifacts/tsscache \
  --aux-file /local-4tb/macos27/cfg/aux.img \
  --kdk-dir  /local-4tb/macos27/artifacts/kdk \
  --template-file /local-4tb/macos27/artifacts/golden-policy.img4 \
  --ecid 4533301000242790675 --password-file /local-4tb/macos27/password \
  --execute
```

- `--bundle-file`, `--ramdisk-file` and `--restore-ambrosia-file` are the Ambrosia restore bundle, the restore ramdisk from the IPSW image set and Ambrosia's restore-mode build. The build produces the desktop Ambrosia; these three are runtime inputs.
- `--cache-dir` is the TSS cache the restore reuses; without it the live Apple TSS answers can be rejected with `failed to request root ticket`.
- `--aux-file` is the aux image the restore boots with. The copy the restore writes is preferred when present, so the policy nonces match the disk.
- `--kdk-dir` is the KDK directory; the `AppleVPBootPolicy` kext is found inside it.
- `--template-file` is a golden LocalPolicy img4 for the same macOS build.
- `--password-file` is the first macOS account's password. `--user` names the account (`snap` by default).
- `--ipsw-file` copies an IPSW from the Proxmox host to the runner; that is the fastest path when the file is already there. Without it (and without a runner path in `--ipsw`) the runner runs `snapintosh fetch --build 26B131` before the restore.
- The planner polls `qm guest cmd <vmid> network-get-interfaces` through the guest agent, then copies the inputs with `scp` and the host key at `/root/.ssh/osx-next-arm64-<vmid>`. Enable the guest agent (the plan does) and give the runner a reachable bridge.

Progress is in `/var/lib/osx-next-arm64/status` inside the runner and in `/var/log/osx-next-arm64.log`. The values are `fetching-source`, `building`, `waiting-for-inputs`, `fetching-ipsw`, `installing-macos`, `firstboot` and finally `ready`; a failed step says `failed: <command>`.

## Standalone firstboot

The same flow is a verb on the runner, for a disk restored by another run:

```
snapintosh firstboot --disk /home/snapintosh/macos.raw --aux /home/snapintosh/aux.img \
  --ecid 4533301000242790675 --kdk /home/snapintosh/kdk \
  --template /home/snapintosh/golden-policy.img4 --user snap --password-file /home/snapintosh/password
```

`--dry-run` prints the plan and writes nothing. The verb reads `vuid`, `nsih`, `spih` from the restored disk and `lpnh`, `rpnh` from the aux bank 0, builds the LocalPolicy with `lib/install/mkpolicy-hack.py`, and runs one single-user boot and one desktop boot. Both boots are bounded by `--secs` (1500 by default). On success QEMU is left running.

## Memory

Use 12 GB for the runner and 6 GB for the guest (10 GB is the floor). The restore was OOM-killed on an 8 GB runner and on a 6 GB guest with 8 vCPUs; the working shape is a 10 GB runner, 12 vCPUs, a 6 GB guest and a second swap file. The cloud-init setup adds one swap file; add a second (`/swap2`) only on a memory-tight host. `--memory 8192` is the minimum, not the comfortable size.

## VNC

The desktop QEMU listens on `127.0.0.1:5900` inside the runner with a random password. Tunnel it from your workstation and open the VNC client:

```
ssh -L 5900:127.0.0.1:5900 -i /root/.ssh/osx-next-arm64-310 snapintosh@<runner address>
vncviewer 127.0.0.1:5900
```

The password file is the `vnc.pass` path printed with the tunnel line in `/var/log/osx-next-arm64.log` (mode 600, usually under `/home/snapintosh/.local/share/snapintosh/firstboot/desktop/`). The guest account is `snap` and its password is the one supplied to `--password-file`.

## Expected speed

These are measurements from a 16-thread Ryzen VM, a clean install, seconds from QEMU start, warm translation cache:

```
ssh answers               about 42 s
Dock on screen            about 66 s (81 s on the very first boot)
Safari first page drawn   about 176 s
window drag               about 15 frames per second
```

On 6 shared threads expect roughly three times slower (Dock about 196 s, drag about 7 fps). A GPU helps only a little: the emulated CPU is the bottleneck. See `arm64/README.md` for the full tables and how they were measured.

## Known issues

- On a host with few or weak cores (6 to 8) the first Safari launch of a boot can loop through "A problem repeatedly occurred" for 2 to 5 minutes before it settles. It is not a crash of the emulator. Wait it out.
- No audio.
- No local display window: use VNC or the Proxmox serial console of the runner.
- The first boot after a build is slower (it fills the translation cache); the second one is faster.

## From the command line

```
osx-next-cli arm64 plan --vmid 478 --name macos27-runner --storage local-lvm
osx-next-cli arm64 apply --vmid 478 --name macos27-runner --storage local-lvm --execute
osx-next-cli status --vmid 478
osx-next-cli arm64 uninstall --vmid 478 --execute
```

Useful options: `--cores`, `--memory`, `--disk`, `--bridge`, `--vlan`, `--cpuunits`, `--ssh-key-file`, `--gpu-pci`.

The source the runner builds from is pinned: `--source-ref` takes a release tag (`v1.2`) or a full commit id (never a branch) and `--source-sha256` the checksum of its archive. A release build carries both as defaults. `--source-url` (with `{ref}`) and `--renderer-url` with `--renderer-sha256` point the runner at your own copies, for example on a local web server.

## Licenses

Everything under `arm64/` is GPL-2.0-or-later (`arm64/LICENSE`); the rest of the project stays Apache-2.0. We ship no Apple code and no Apple files: you download the macOS restore image yourself, from Apple. Apple's macOS license allows macOS virtual machines on Apple-branded hardware only, and you are responsible for what you run.

## Reporting bugs

Open an issue on GitHub with the output of `osx-next-cli arm64 plan` for your settings, `/var/log/osx-next-arm64.log` from the runner and the host's CPU model, RAM and storage type. Say whether you used lavapipe or a GPU.
