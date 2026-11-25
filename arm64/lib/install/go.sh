#!/bin/bash
# M2b: one restore attempt through the packaged pipeline.
# Packaged copy of the lab's tools/m2b/go.sh: no lab paths. Boots the restore OS with run-restore.sh,
# runs m2bhost.py against the guest's virtio USB port and stops QEMU.
#
# usage: go.sh TAG [m2bhost args...]   (env: IPSW, ECID, AUX, DISK, SECS, CPUS, RND, TSS=1)
# Exit status is m2bhost's: 0 the guest reported Status 0 with no AMRError, 4 the control stream
# ended, 5 a data request had no answer, 6 the guest reported an error.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 1 ] || { echo "usage: go.sh TAG [m2bhost args...]" >&2; exit 2; }
TAG=$1; shift
HOME_BASE=${SNAPINTOSH_HOME:-$HOME/.local/share/snapintosh}
RUNS=${SNAPINTOSH_INSTALL_RUNS:-$HOME_BASE/install/runs}
R=$RUNS/$TAG
export SNAPINTOSH_INSTALL_RUNS=$RUNS
IPSW=${IPSW:-}; ECID=${ECID:-}
[ -n "$IPSW" ] || { echo "go.sh: IPSW is not set" >&2; exit 2; }
[ -n "$ECID" ] || { echo "go.sh: ECID is not set" >&2; exit 2; }
export RND=${RND:-0x4d32b1a7}
export EXTRA="-chardev socket,id=vusb,path=$R/vusb.sock,server=on,wait=off -device vmapple-virtio-usb-pci,chardev=vusb ${EXTRA:-}"
bash "$HERE/run-restore.sh" "$TAG" "${SECS:-7500}" "${CPUS:-6}" >/dev/null || { echo "go.sh: run-restore.sh failed" >&2; exit 1; }
for _ in $(seq 1 100); do [ -S "$R/vusb.sock" ] && break; sleep 0.1; done
if [ ! -S "$R/vusb.sock" ]; then
  echo "go.sh: the guest never opened $R/vusb.sock (see $R/qemu.out)" >&2
  kill "$(cat "$R/qemu.pid" 2>/dev/null)" 2>/dev/null
  exit 4
fi
python3 -I "$HERE/m2bhost.py" "$R/vusb.sock" "$R/qmp.sock" "$IPSW" "$R/host" --ecid "$ECID" --rnd "$RND" "$@" > "$R/host.out" 2>&1
rc=$?
echo "host rc=$rc" >> "$R/host.out"
kill "$(cat "$R/qemu.pid" 2>/dev/null)" 2>/dev/null
grep -a "REQUEST\|WALL\|STATUS\|TSS\|TIME\|rc=" "$R/host.out" | cut -c1-400 | tail -"${TAILN:-30}"
exit $rc
