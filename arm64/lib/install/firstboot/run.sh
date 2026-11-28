#!/bin/bash
# firstboot: restored macOS disk to a running desktop, no Mac and no manual step.
#
# usage: run.sh --disk FILE --aux FILE --ecid N --kdk DIR --template FILE [--user U] [--password-file F]
#                [--secs N] [--work DIR]
# The LocalPolicy is built from the disk and aux; a single-user offline pass installs the sshd job, creates the
# account and sets autologin; one normal boot then sets the password, waits for Dock/Finder and takes a screendump.
# QEMU is left running on success, with VNC on loopback and the tunnel line printed.
set -Eeuo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SNAP_HOME=${SNAPINTOSH_HOME:-$HOME/.local/share/snapintosh}
PREFIX=${SNAPINTOSH_PREFIX:-$SNAP_HOME/prefix}
LIB=${SNAPINTOSH_LIB:-$HOME/.local/share/snapintosh/lib}
DISK=""; AUX=""; ECID=""; KDK=""; TEMPLATE=""; GUEST_USER=snap; PASSWORD_FILE=""; SECS=1500; WORK=$SNAP_HOME/firstboot
while [ $# -gt 0 ]; do
  case $1 in
    --disk) DISK=$2; shift ;;
    --aux) AUX=$2; shift ;;
    --ecid) ECID=$2; shift ;;
    --kdk) KDK=$2; shift ;;
    --template) TEMPLATE=$2; shift ;;
    --user) GUEST_USER=$2; shift ;;
    --password-file) PASSWORD_FILE=$2; shift ;;
    --secs) SECS=$2; shift ;;
    --work) WORK=$2; shift ;;
    *) echo "firstboot: unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$DISK" ] && [ -n "$AUX" ] && [ -n "$ECID" ] && [ -n "$KDK" ] && [ -n "$TEMPLATE" ] \
  || { echo "firstboot: --disk --aux --ecid --kdk --template are required" >&2; exit 2; }
PW=${SNAPINTOSH_GUEST_PASSWORD:-snaplab}
if [ -n "$PASSWORD_FILE" ]; then IFS= read -r PW < "$PASSWORD_FILE" || { echo "cannot read $PASSWORD_FILE" >&2; exit 2; }; fi
[ -n "$PW" ] || { echo "firstboot: the guest password is empty" >&2; exit 2; }
QEMU=${FIRSTBOOT_QEMU:-$PREFIX/bin/qemu-system-aarch64}
SINGLE_QEMU=${FIRSTBOOT_SINGLE_QEMU:-$QEMU}
AMBROSIA=${FIRSTBOOT_AMBROSIA:-$LIB/install/ambrosia-m3.bin}
[ -s "$AMBROSIA" ] || AMBROSIA=$PREFIX/share/snapintosh/ambrosia/ambrosia.bin
HELPER=$PREFIX/share/snapintosh/27on86
[ -x "$QEMU" ] || { echo "QEMU not built at $QEMU (snapintosh build --m2b)" >&2; exit 2; }
[ -x "$SINGLE_QEMU" ] || { echo "single-user QEMU not executable: $SINGLE_QEMU" >&2; exit 2; }
[ -f "$DISK" ] || { echo "no disk: $DISK" >&2; exit 2; }
[ -f "$AUX" ] || { echo "no aux image: $AUX" >&2; exit 2; }
# Use the aux the caller supplied. It is the image the restore read; the run directory copy is rewritten by the
# restore and can carry boot objects that Ambrosia cannot map at the new xid (the fresh-runner wall).
[ -f "$TEMPLATE" ] || { echo "no policy template: $TEMPLATE" >&2; exit 2; }
[ -d "$KDK" ] || [ -f "$KDK" ] || { echo "no KDK at $KDK" >&2; exit 2; }
KEXT=$(find "$KDK" -type f -path '*/AppleVPBootPolicy.kext/Contents/MacOS/AppleVPBootPolicy' -print -quit 2>/dev/null || true)
[ -n "$KEXT" ] || { [ -f "$KDK" ] && KEXT=$KDK; }
[ -n "$KEXT" ] || { echo "no AppleVPBootPolicy kext under $KDK" >&2; exit 2; }
mkdir -p "$WORK/single" "$WORK/desktop" "$WORK/ssh-state"
chmod 700 "$WORK"
SSH_PORT=${SSH_PORT:-2222}; VNC_PORT=${VNC_PORT:-5900}

say() { echo "firstboot: $*"; }
fail() { say "$*" >&2; exit 1; }
ssh_root() {
  ssh -p "$SSH_PORT" -i "$WORK/ssh-state/id_ed25519" -o BatchMode=yes -o IdentitiesOnly=yes \
      -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$WORK/known_hosts" -o ConnectTimeout=8 root@127.0.0.1 "$@"
}
kill_pid() { local p=${1:-}; [ -n "$p" ] && kill "$p" 2>/dev/null || true; }
stop_all() {
  kill_pid "$(cat "$WORK/single/qemu.pid" 2>/dev/null || true)"
  kill_pid "$(cat "$WORK/single/memfd.pid" 2>/dev/null || true)"
  kill_pid "$(cat "$WORK/desktop/qemu.pid" 2>/dev/null || true)"
  kill_pid "$(cat "$WORK/desktop/memfd.pid" 2>/dev/null || true)"
  kill_pid "$(cat "$WORK/desktop/nbd.pid" 2>/dev/null || true)"
}
trap 'stop_all' ERR

values() { # DISK AUX MOUNT OUTFILE
  python3 -I "$HERE/read-values.py" "$1" "$2" "$ECID" "$3" > "$4"
  # shellcheck disable=SC1090
  . "$4"
}
policy() { # OUT TEMPLATE
  python3 -I "$LIB/install/mkpolicy-hack.py" --vuid "$VUID" --nsih "$NSIH" --nonce "$LPNH" --rpnh "$RPNH" \
    --spih "$SPIH" --template "$2" --key "$LIB/install/hackkey.pem" --kdk "$KEXT" --out "$1"
}

# The policy nonces and the guest pflash must come from the same aux: BootPolicy panics when the NVRAM
# nonces and the LocalPolicy disagree. Prefer the live run-directory aux, which the restore rewrote with this
# disk's nonces and volume group; fall back to the aux the caller supplied.
LIVE_AUX=$SNAP_HOME/install/runs/install/aux
AUX_EFF=$AUX
[ -s "$LIVE_AUX" ] && AUX_EFF=$LIVE_AUX
say "reading the policy inputs from $DISK and $AUX_EFF"
values "$DISK" "$AUX_EFF" "$WORK/single/preboot" "$WORK/single/values.sh"
say "building the LocalPolicy with $TEMPLATE"
policy "$WORK/policy.img4" "$TEMPLATE"
# The aux passed by the one-command path is the full 32 MiB image; the guest pflash wants the first 16 KiB dropped.
# A caller that already passes the dropped live aux is used as it is.
if [ "$(stat -c %s "$AUX_EFF")" -gt 33562780 ]; then
  dd if="$AUX_EFF" of="$WORK/single/aux" bs=16384 skip=1 status=none
else
  cp "$AUX_EFF" "$WORK/single/aux"
fi
eval "$(python3 -I "$HERE/patch-points.py" "$LIB/install/hackkey.pem")"

say "single-user offline pass (bound ${SECS}s)"
MODE=single ECID="$ECID" DISK="$DISK" AUX="$WORK/single/aux" QEMU="$SINGLE_QEMU" AMBROSIA="$AMBROSIA" RAM="${FIRSTBOOT_SINGLE_RAM:-6G}" \
  PATCH_FIND="$PATCH_FIND" PATCH_REPL="$PATCH_REPL" SINGLE_MEMFD="${FIRSTBOOT_SINGLE_MEMFD:-1}" \
  SNAPINTOSH_LIB="$LIB" VNC_PORT="" bash "$HERE/boot.sh" "$WORK/single" "$WORK/policy.img4" "$SECS"
qemu_pid=$(cat "$WORK/single/qemu.pid")
for _ in $(seq 1 300); do
  [ -S "$WORK/single/serial.sock" ] && break
  kill -0 "$qemu_pid" 2>/dev/null || fail "single-user QEMU exited before the serial socket (see $WORK/single/qemu.out)"
  sleep 1
done
[ -S "$WORK/single/serial.sock" ] || fail "no serial socket after 300s"
python3 -I "$HELPER/persist-sshd.py" "$WORK/single/serial.sock" "$WORK/ssh-state" \
  || fail "persist-sshd.py failed"
python3 -I - "$HERE/user-plist.sh" "$WORK/single/serial.sock" "$GUEST_USER" "$HELPER" "$WORK/single/offline.log" <<'PY' \
  || fail "user-plist.sh failed"
import importlib.util, os, shlex, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("ps", os.path.join(sys.argv[4], "persist-sshd.py"))
ps = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps)
sock = Path(sys.argv[2]); stage = ps.discover_stage(sock)
ps.upload(sock, stage, "user-plist.sh", open(sys.argv[1], "rb").read(), 0o700)
out = ps.run_serial(sock, "FIRSTBOOT_USER=%s %s" % (shlex.quote(sys.argv[3]), shlex.quote(stage + "/user-plist.sh")), timeout=300)
print(out)
open(sys.argv[5], "a").write(out)
if "user pass done" not in out: raise SystemExit(1)
PY

SNAPINTOSH_GUEST_PASSWORD="$PW" SNAPINTOSH_AUTOLOGIN_FORCE=1 python3 -I "$LIB/goldenctl.py" extra "$WORK/single/serial.sock" "$HELPER" "$LIB/guest" --user "$GUEST_USER" \
  || fail "golden phase 1 (autologin, sshd) failed"
# the guest halts itself; give it a bound, then ask QMP to quit
for _ in $(seq 1 180); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 1; done
if kill -0 "$qemu_pid" 2>/dev/null; then
  python3 -I - "$WORK/single/qmp.sock" <<'PY' || true
import json, socket, sys
s = socket.socket(socket.AF_UNIX); s.settimeout(10); s.connect(sys.argv[1]); f = s.makefile("rw"); f.readline()
for c in ("qmp_capabilities", "quit"):
    f.write(json.dumps({"execute": c}) + "\n"); f.flush(); f.readline()
PY
  sleep 3; kill "$qemu_pid" 2>/dev/null || true
fi
wait "$qemu_pid" 2>/dev/null || true
say "offline pass done"

say "normal desktop boot (bound ${SECS}s)"
values "$DISK" "$AUX_EFF" "$WORK/desktop/preboot" "$WORK/desktop/values.sh"
policy "$WORK/desktop/policy.img4" "$TEMPLATE"
# Same pflash image as the single-user pass, so the desktop boot matches the policy nonces it was built with.
cp "$WORK/single/aux" "$WORK/desktop/aux"
MODE=desktop ECID="$ECID" DISK="$DISK" AUX="$WORK/desktop/aux" QEMU="$QEMU" AMBROSIA="$AMBROSIA" RAM="${FIRSTBOOT_RAM:-6G}" \
  CPUS="${FIRSTBOOT_CPUS:-8}" PATCH_FIND="$PATCH_FIND" PATCH_REPL="$PATCH_REPL" VNC_PORT="$VNC_PORT" \
  SNAPINTOSH_LIB="$LIB" MEMFD_HOLD=86400 bash "$HERE/boot.sh" "$WORK/desktop" "$WORK/desktop/policy.img4" 0
qemu_pid=$(cat "$WORK/desktop/qemu.pid")
deadline=$(( $(date +%s) + SECS ))
up=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  kill -0 "$qemu_pid" 2>/dev/null || fail "desktop QEMU exited (see $WORK/desktop/qemu.out)"
  if ssh_root 'echo up' 2>/dev/null | grep -q up; then up=1; break; fi
  sleep 3
done
[ "$up" = 1 ] || fail "no guest ssh within ${SECS}s (see $WORK/desktop/qemu.out)"
say "ssh up"
passcmd=$(python3 -I -c 'import shlex,sys; print("dscl . -passwd /Users/%s %s" % (shlex.quote(sys.argv[1]), shlex.quote(sys.argv[2])))' "$GUEST_USER" "$PW")
ssh_root "$passcmd" || say "warning: could not set the guest password"
# shellcheck disable=SC2016  # expands in the guest shell, not here
console=$(ssh_root 'who | grep console | awk "{print \$1}"' 2>/dev/null || true)
if [ "$console" != "$GUEST_USER" ]; then
  say "typing the login password at the login window"
  for _ in 1 2 3; do
    # shellcheck disable=SC2016  # expands in the guest shell, not here
    [ "$(ssh_root 'who | grep console | awk "{print \$1}"' 2>/dev/null || true)" = "$GUEST_USER" ] && break
    # the login window can be late: typing into nothing leaves the session at login
    ssh_root 'pgrep -x loginwindow >/dev/null' 2>/dev/null || sleep 2
    python3 -I "$HERE/qmp-login.py" "$WORK/desktop/qmp.sock" "$PW" || say "warning: QMP login keys failed"
    sleep 5
  done
fi
desk=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  # shellcheck disable=SC2016  # expands in the guest shell, not here
  status=$(ssh_root 'echo C=$(who | grep console | awk "{print \$1}"); echo W=$(pgrep -x WindowServer); echo D=$(pgrep -x Dock); echo F=$(pgrep -x Finder)' 2>/dev/null || true)
  echo "$status" >> "$WORK/desktop/desktop.log"
  if echo "$status" | grep -qx "C=$GUEST_USER" && echo "$status" | grep -qE 'D=[0-9]+' && echo "$status" | grep -qE 'F=[0-9]+'; then desk=1; break; fi
  sleep 3
done
[ "$desk" = 1 ] || fail "Dock and Finder did not start within ${SECS}s (see $WORK/desktop/desktop.log)"
say "desktop session ready, taking a frame"
# The first screendump can land on the blank frame the login transition leaves behind; retry before giving up.
frame_ok=0
for _ in $(seq 1 12); do
  if python3 -I "$HERE/frame.py" "$WORK/desktop/qmp.sock" "$WORK/desktop/shot.ppm"; then frame_ok=1; break; fi
  sleep 5
done
[ "$frame_ok" = 1 ] || fail "the captured frame is black"
cat > "$WORK/desktop/vnc.txt" <<EOF
VNC: ssh -L $VNC_PORT:127.0.0.1:$VNC_PORT -i <runner key> snapintosh@<runner address>
     then open vnc://127.0.0.1:$VNC_PORT or vncviewer 127.0.0.1:$VNC_PORT
password: $WORK/desktop/vnc.pass
guest:    user $GUEST_USER, password from the password file (default snaplab)
EOF
cat "$WORK/desktop/vnc.txt"
say "desktop ready; QEMU left running in $WORK/desktop"
