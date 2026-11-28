# Mac-free install (restore host)

The code in this directory restores macOS into a raw disk with no Mac. It is a port of the lab's
`tools/m2b` and `tools/m3` helpers, with the lab paths (`/scratch/m2b`, `~/p18`) replaced by
arguments or by defaults under the runner's snapintosh data directory. `snapintosh install` drives it.

## What it runs

1. `snapintosh install` validates the inputs and, with `--dry-run`, prints the plan and the QEMU command
   and writes nothing.
2. `go.sh` starts `run-restore.sh`: Ambrosia's restore mode boots a restore ramdisk in the vmapple guest
   with the `vmapple-virtio-usb-pci` device port.
3. `m2bhost.py` speaks restored's protocol 15 over that USB port, answers its data requests from the
   IPSW and Apple's TSS, and streams the OS image over ASR.
4. The guest reports a final `StatusMsg`. `Status 0` with no `AMRError` is a finished restore.

Exit status: `0` installed, `3` install-pending (the restore finished, but the first-boot LocalPolicy is
not there yet; build it with `mkpolicy-hack.py` and pass `--policy FILE`, or run `snapintosh firstboot`),
`2` a bad input, anything else the restore host's own status. A live run on 2026-11-26 restored macOS 27.2.1
to a fresh disk with `Status 0` and `AMRError 0`; the disk then booted to a `snap` console session.

## Inputs

| variable | default | meaning |
| --- | --- | --- |
| `--ipsw` / `IPSW` | newest `$HOME_DIR/dl/*.ipsw` | the UniversalMac IPSW to restore |
| `--aux` / `AUX` | `$HOME_DIR/vm/aux.img` | the aux image; its first 16 KiB are dropped for the NVRAM bank |
| `--ecid` / `ECID` | `ECID` in `$HOME_DIR/vm/machineid.bin` | the machine uuid the restore is personalized for |
| `--disk` / `DISK` | `$HOME_DIR/vm/install.raw` | the raw disk the restore writes (made when missing) |
| `BUNDLE` | `$HOME_DIR/install/bundle/restore.cpio` | the Ambrosia restore bundle |
| `RAMDISK` | `$HOME_DIR/install/bundle/restore-ramdisk.im4p` | the restore ramdisk |
| `AMBROSIA` | `$HOME_DIR/install/bundle/ambrosia-r4.bin` | the Ambrosia build with restore mode |
| `SNAPINTOSH_INSTALL_RUNS` | `$HOME_DIR/install/runs` | run directories (`qemu.out`, `host.out`, serial) |
| `--cache` | `$HOME_DIR/install/tsscache` | TSS answers keyed by the request without its UUID |

`--tss` allows the TSS requests. Every request is appended to `apple-requests.jsonl` in the run directory.

`mkbundle.py` replaces members of a restore bundle (`apticket.der`, `restore-policy.img4`, `nonce`,
`boot-args`, ...). `snapintosh install --policy FILE` uses it to add `local-policy.img4`.

## QEMU with the install patches

`patches/qemu-m2b/` has three patches that apply on top of `patches/qemu/` (see `pins.env`). Build with them
with `snapintosh build --m2b`; the one-command cloud-init runs `build --m2b` so the runner has the USB device
and the rnd property. A plain build keeps the release-v2 md5s. To apply them by hand to a QEMU tree:

```sh
git am ../arm64/patches/qemu/*.patch ../arm64/patches/qemu-m2b/*.patch
```

## firstboot

`snapintosh firstboot` finishes the restored disk without a Mac: it reads `vuid`, `nsih` and `spih` from the
restored disk and `lpnh`/`rpnh` from bank 0 of the restored aux, builds the LocalPolicy with
`mkpolicy-hack.py`, runs the single-user offline pass (sshd job, account, autologin), boots the desktop with
the local policy and the Kernel patch, waits for Dock/Finder and prints the VNC tunnel. The KDK and the
golden template are runtime inputs; keep them on the runner and out of this repository. The one-command
planner uploads them from the Proxmox host, then runs firstboot after the restore.

## Licenses

The ported files carry their original notices: GPL-2.0-or-later for `m2bhost.py`, `tss.py`, `vusbhost.py`,
`qmp.py`, `mkbundle.py`, `mkpolicy-hack.py` and `mkpolicy-sep.py`. `hackkey.pem` is a P-384 key generated
for these stand-in policies, not a secret. No Apple object is shipped here; the IPSW, aux, ramdisk, bundle
and KDK are runtime inputs.
