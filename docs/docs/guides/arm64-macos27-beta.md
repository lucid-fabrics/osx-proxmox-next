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
RAM      runner 8 GB at least (the default), 24 GB or more on the host is better
disk     120 GB on the storage you pick (build tree, the macOS disk, the translation cache, one snapshot)
storage  a storage with the Snippets content type (the local one by default), for the runner's cloud-init file
network  the runner downloads from cloud.debian.org, github.com, gitlab.com and Apple's servers
```

## Install status in this beta build

Creating the runner and building the stack works. **The Mac-free install of macOS itself is not in this beta build yet.** After the build the runner reports `install-pending` (read `/var/lib/osx-next-arm64/status` inside the runner, the log is `/var/log/osx-next-arm64.log`). Until the install lands, the target is useful for trying the build on your host and for checking that the runner is set up correctly.

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
