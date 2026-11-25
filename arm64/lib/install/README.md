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
not there yet; build it with `mkpolicy-hack.py` and pass `--policy FILE`), `2` a bad input, anything else
the restore host's own status. The install is code- and unit-tested only; a live end-to-end restore is a
follow-up.

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
| TSS cache | `$HOME_DIR/install/tsscache` | answers from Apple's TSS, keyed by the request without its UUID |

`--tss` allows the TSS requests. Every request is appended to `apple-requests.jsonl` in the run directory.

`mkbundle.py` replaces members of a restore bundle (`apticket.der`, `restore-policy.img4`, `nonce`,
`boot-args`, ...). `snapintosh install --policy FILE` uses it to add `local-policy.img4`.

## QEMU with the install patches

`patches/qemu-m2b/` has three patches that apply on top of `patches/qemu/` (see `pins.env`). `snapintosh
build` does not apply them yet, so its release-v2 md5s stay the same. To get the USB device:

```sh
git clone "$QEMU_REPO" qemu && cd qemu && git checkout "$QEMU_REF"
git am ../../arm64/patches/qemu/*.patch ../../arm64/patches/qemu-m2b/*.patch
./configure --target-list=aarch64-softmmu --enable-pvg --enable-slirp ... && make -j"$(nproc)"
```

## What remains

The restore is proven in the lab; this packaged path has not run end to end. After it runs, the first boot
still needs the LocalPolicy: `mkpolicy-hack.py` builds one with the restore's vuid, nsih and a boot nonce
from the restored aux, signed with `hackkey.pem`, for the patched `AppleVPBootPolicy` kext. Then a normal
Ambrosia boot, Setup Assistant, `make-disk golden`.

## Licenses

The ported files carry their original notices: GPL-2.0-or-later for `m2bhost.py`, `tss.py`, `vusbhost.py`,
`qmp.py`, `mkbundle.py`, `mkpolicy-hack.py` and `mkpolicy-sep.py`. `hackkey.pem` is a P-384 key generated
for these stand-in policies, not a secret. No Apple object is shipped here; the IPSW, aux, ramdisk, bundle
and KDK are runtime inputs.
