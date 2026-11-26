#!/bin/bash
# M2b: boot the restore ramdisk through Ambrosia's restore mode with the vmapple virtio USB port.
# Packaged copy of the lab's tools/m2b/run-restore.sh: no lab paths, every input is an argument or
# an environment variable whose default lives under the snapintosh data directory.
#
# usage: run-restore.sh [--print] TAG [SECS] [CPUS]
# env:
#   QEMU       the vmapple QEMU (default: $SNAPINTOSH_PREFIX/bin/qemu-system-aarch64)
#   AUX        the full aux image (required; its first 16 KiB are dropped, the NVRAM bank)
#   DISK       the raw disk the restore writes (required; made when missing)
#   ECID       the machine uuid the guest sees (required, decimal)
#   BUNDLE     Ambrosia restore bundle, cpio (default: $BUNDLE_DIR/restore.cpio)
#   RAMDISK    the restore ramdisk im4p (default: $BUNDLE_DIR/restore-ramdisk.im4p)
#   AMBROSIA   Ambrosia build with restore mode (default: $BUNDLE_DIR/ambrosia-r4.bin)
#   EXTRA      more QEMU arguments (the USB chardev comes from here)
#   RND        the vmapple rnd value (0x...) when you need a fixed one
#   RAM        guest RAM (default 8G), DISK_GB the size to make DISK when it is missing (default 96)
#   KEEP=1     keep an existing run dir and disk
# --print writes nothing: it prints the QEMU command the run would start, then exits.
set -eu
PRINT=0
case ${1:-} in --print) PRINT=1; shift ;; esac
[ $# -ge 1 ] || { echo "usage: run-restore.sh [--print] TAG [SECS] [CPUS]" >&2; exit 2; }
TAG=$1; SECS=${2:-1800}; CPUS=${3:-6}
HOME_BASE=${SNAPINTOSH_HOME:-$HOME/.local/share/snapintosh}
RUNS=${SNAPINTOSH_INSTALL_RUNS:-$HOME_BASE/install/runs}
BUNDLE_DIR=${SNAPINTOSH_INSTALL_BUNDLE:-$HOME_BASE/install/bundle}
R=$RUNS/$TAG
QEMU=${QEMU:-${SNAPINTOSH_PREFIX:-$HOME_BASE/prefix}/bin/qemu-system-aarch64}
AUX=${AUX:-}; DISK=${DISK:-}; ECID=${ECID:-}
BUNDLE=${BUNDLE:-$BUNDLE_DIR/restore.cpio}
RAMDISK=${RAMDISK:-$BUNDLE_DIR/restore-ramdisk.im4p}
AMBROSIA=${AMBROSIA:-$BUNDLE_DIR/ambrosia-r4.bin}
RAM=${RAM:-8G}

[ -n "$AUX" ] || { echo "run-restore.sh: AUX is not set" >&2; exit 2; }
[ -n "$DISK" ] || { echo "run-restore.sh: DISK is not set" >&2; exit 2; }
case $ECID in ''|*[!0-9]*) echo "run-restore.sh: ECID must be decimal digits" >&2; exit 2 ;; esac

cmd=("$QEMU" -accel "tcg,tb-size=2047"
  -machine "vmapple,uuid=$ECID,memory-backend=ram0,ambrosia=on${RND:+,rnd=$RND}"
  -cpu max -smp "$CPUS" -m "$RAM"
  -object "memory-backend-memfd,id=ram0,size=$RAM,share=on"
  -display none -monitor none -parallel none
  -serial "file:$R/serial.log" -qmp "unix:$R/qmp.sock,server=on,wait=off"
  -bios "$AMBROSIA"
  -device "loader,file=$BUNDLE,addr=0x120000000,force-raw=on"
  -device "loader,file=$RAMDISK,addr=0x180000000,force-raw=on"
  -drive "if=pflash,format=raw,file.filename=$R/aux,file.locking=off"
  -drive "if=pflash,format=raw,file.filename=$DISK,file.locking=off"
  -drive "if=none,format=raw,file.filename=$R/aux,file.locking=off,id=aux"
  -device "vmapple-virtio-blk-pci,variant=aux,drive=aux,share-rw=on"
  -drive "if=none,format=raw,file.filename=$DISK,file.locking=off,id=root"
  -device "vmapple-virtio-blk-pci,variant=root,drive=root,share-rw=on"
  -netdev "user,id=net0,ipv6=off"
  -device "virtio-net-pci,netdev=net0,romfile=,mac=52:54:00:76:61:79")
# shellcheck disable=SC2206
[ -z "${EXTRA:-}" ] || cmd+=($EXTRA)

if [ "$PRINT" = 1 ]; then
  echo "run dir: $R"
  echo "aux:     $AUX (first 16384 bytes dropped into $R/aux)"
  echo "disk:    $DISK"
  echo "bundle:  $BUNDLE"
  echo "ramdisk: $RAMDISK"
  echo "qemu:"
  printf '%q ' "${cmd[@]}"; echo
  exit 0
fi

if [ -z "${KEEP:-}" ]; then
  rm -rf "$R"; mkdir -p "$R"
  dd if="$AUX" of="$R/aux" bs=16384 skip=1 status=none
fi
[ -d "$R" ] || mkdir -p "$R"
[ -n "${KEEP:-}" ] || [ -f "$R/aux" ] || dd if="$AUX" of="$R/aux" bs=16384 skip=1 status=none
if [ ! -s "$DISK" ]; then
  [ -e "$DISK" ] || [ -d "$(dirname "$DISK")" ] || mkdir -p "$(dirname "$DISK")"
  truncate -s "${DISK_GB:-96}G" "$DISK"
fi
date +%s.%N > "$R/start"
timeout "$SECS" "${cmd[@]}" > "$R/qemu.out" 2>&1 &
echo $! > "$R/qemu.pid"
echo "pid $(cat "$R/qemu.pid") run $R"
