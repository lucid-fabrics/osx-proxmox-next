#!/bin/bash
# Boot the restored macOS disk for the firstboot flow.
#
# usage: boot.sh WORK POLICY [SECS]      (MODE=single|desktop, 0 SECS means no timeout)
# env: ECID, DISK, AUX, QEMU, QIMG, QNBD, AMBROSIA, PATCH_FIND, PATCH_REPL, RAM, CPUS, PORT, VNC_PORT
#
# single:  raw disk over the single-user serial console, RAM backend ram (or memfd when SINGLE_MEMFD=1).
# desktop: qcow2 overlay served over NBD, memfd RAM, local-policy.img4 and KeepAppleGfx. MODE=desktop leaves QEMU
#          running and returns; the caller waits for ssh, the Dock and Finder, and takes a screendump.
set -eu
W=${1:?work directory}; POL=${2:?policy}; SECS=${3:-1500}
MODE=${MODE:-single}
SNAP_HOME=${SNAPINTOSH_HOME:-$HOME/.local/share/snapintosh}
PREFIX=${SNAPINTOSH_PREFIX:-$SNAP_HOME/prefix}
LIB=${SNAPINTOSH_LIB:-$HOME/.local/share/snapintosh/lib}
QEMU=${QEMU:-$PREFIX/bin/qemu-system-aarch64}
QIMG=${QIMG:-$(dirname "$QEMU")/qemu-img}
QNBD=${QNBD:-$(dirname "$QEMU")/qemu-nbd}
AMBROSIA=${AMBROSIA:-$PREFIX/share/snapintosh/ambrosia/ambrosia.bin}
ECID=${ECID:?ECID is not set}; DISK=${DISK:?DISK is not set}; AUX=${AUX:?AUX is not set}
RAM=${RAM:-6G}; CPUS=${CPUS:-8}; PORT=${PORT:-2222}; VNC_PORT=${VNC_PORT:-5900}
PATCH_FIND=${PATCH_FIND:?PATCH_FIND is not set}; PATCH_REPL=${PATCH_REPL:?PATCH_REPL is not set}
export SNAP_IOFENCE_PATCH=${SNAP_IOFENCE_PATCH:-1}
export LP_NUM_THREADS=${LP_NUM_THREADS:-8} REIMS_VGPU_WINDOW=0 M2V_INPLACE_RT=${M2V_INPLACE_RT:-1} REIMS_VGPU_LOG=${REIMS_VGPU_LOG:-warn}
MESA=${SNAPINTOSH_MESA:-$SNAP_HOME/mesa}
for f in "$MESA"/share/vulkan/icd.d/lvp_icd*.json; do
  [ -f "$f" ] && { export VK_ICD_FILENAMES=$f; export LD_LIBRARY_PATH="$MESA/lib/x86_64-linux-gnu:$MESA/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; break; }
done
mkdir -p "$W/b"
b64() { python3 -I -c 'import base64,sys;print(base64.b64encode(bytes.fromhex(sys.argv[1])).decode())' "$1"; }
cat > "$W/b/config.plist" <<EOP
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>BootArgs</key>
  <string>$(if [ "$MODE" = single ]; then echo "-s -v serial=11"; else echo "-v serial=11"; fi)</string>
  <key>Chosen</key><dict><key>internal-use-only-unit</key><integer>1</integer></dict>
  <key>Asmb</key><dict><key>lp-sip0</key><integer>127</integer></dict>
  <key>Graphics</key><dict><key>KeepAppleGfx</key><true/></dict>
  <key>Kernel</key><dict><key>Patch</key><array>
  <dict>
    <key>Identifier</key><string>com.apple.security.AppleVPBootPolicy</string>
    <key>Find</key><data>$(b64 "$PATCH_FIND")</data>
    <key>Replace</key><data>$(b64 "$PATCH_REPL")</data>
    <key>Count</key><integer>1</integer>
    <key>Expect</key><integer>1</integer>
  </dict>
  </array></dict>
</dict>
</plist>
EOP
cp "$POL" "$W/b/local-policy.img4"
(cd "$W/b" && printf '%s\n' config.plist local-policy.img4 | cpio -o -H newc --quiet > "$W/bundle.cpio")

RNDM=""
if [ "${NO_RND:-0}" != 1 ]; then
  set +e
  timeout 8 "$QEMU" -machine "vmapple,rnd=0x4d32b1a7" -accel tcg -display none -S -nodefaults -serial none -monitor none >"$W/rndtest.out" 2>&1
  set -e
  grep -q "Property 'vmapple-machine.rnd' not found" "$W/rndtest.out" || RNDM=",rnd=0x4d32b1a7"
fi
echo "mode=$MODE qemu=$QEMU ram=$RAM cpus=$CPUS rnd='${RNDM}'" > "$W/boot.log"
rm -f "$W/serial.sock" "$W/qmp.sock" "$W/serial.log" "$W/nbd.sock" "$W/memfd.path"
date +%s.%N > "$W/start"
cmd=("$QEMU" -accel "tcg,tb-size=2047"
  -machine "vmapple,uuid=$ECID,memory-backend=ram0,ambrosia=on$RNDM"
  -bios "$AMBROSIA"
  -device "loader,file=$W/bundle.cpio,addr=0x120000000,force-raw=on"
  -cpu max -smp "$CPUS" -m "$RAM"
  -display none -monitor none -parallel none)
if [ "$MODE" = single ]; then
  if [ "${SINGLE_MEMFD:-0}" = 1 ]; then
    python3 -I "$LIB/memfdhold.py" "$RAM" "$W/memfd.path" "$((SECS + 120))" >/dev/null 2>&1 &
    memfd_pid=$!; echo "$memfd_pid" > "$W/memfd.pid"
    for _ in $(seq 1 200); do [ -s "$W/memfd.path" ] && break; sleep 0.1; done
    cmd+=(-object "memory-backend-file,id=ram0,size=$RAM,mem-path=$(cat "$W/memfd.path"),share=on,align=2M")
  else
    cmd+=(-object "memory-backend-ram,id=ram0,size=$RAM")
  fi
  cmd+=(-chardev "socket,id=ser0,path=$W/serial.sock,server=on,wait=off,logfile=$W/serial.log,logappend=off"
        -serial "chardev:ser0" -qmp "unix:$W/qmp.sock,server=on,wait=off")
else
  [ -f "$W/root.qcow2" ] || "$QIMG" create -q -f qcow2 -b "$DISK" -F raw "$W/root.qcow2"
  "$QNBD" -k "$W/nbd.sock" -f qcow2 --shared=0 -t "$W/root.qcow2" --fork --pid-file="$W/nbd.pid"
  for _ in $(seq 1 200); do [ -S "$W/nbd.sock" ] && break; sleep 0.1; done
  [ -S "$W/nbd.sock" ] || { echo "qemu-nbd did not make $W/nbd.sock" >&2; exit 1; }
  python3 -I "$LIB/memfdhold.py" "$RAM" "$W/memfd.path" "${MEMFD_HOLD:-86400}" >/dev/null 2>&1 &
  memfd_pid=$!; echo "$memfd_pid" > "$W/memfd.pid"
  for _ in $(seq 1 200); do [ -s "$W/memfd.path" ] && break; sleep 0.1; done
  cmd+=(-object "memory-backend-file,id=ram0,size=$RAM,mem-path=$(cat "$W/memfd.path"),share=on,align=2M"
        -serial "file:$W/serial.log" -qmp "unix:$W/qmp.sock,server=on,wait=off")
  if [ -n "${VNC_PORT:-}" ]; then
    python3 -I -c 'import secrets,string;print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(10)))' > "$W/vnc.pass"
    chmod 600 "$W/vnc.pass"
    cmd+=(-L "$PREFIX/share/qemu" -object "secret,id=vncsec,file=$W/vnc.pass"
          -vnc "127.0.0.1:$((VNC_PORT - 5900)),password-secret=vncsec")
  fi
fi
cmd+=(-drive "if=pflash,format=raw,file.filename=$AUX,file.locking=off"
      -drive "if=pflash,format=raw,file.filename=$DISK,file.locking=off"
      -drive "if=none,format=raw,file.filename=$AUX,file.locking=off,id=aux"
      -device "vmapple-virtio-blk-pci,variant=aux,drive=aux,share-rw=on"
      -drive "if=none,format=raw,file.filename=$DISK,file.locking=off,id=root"
      -device "vmapple-virtio-blk-pci,variant=root,drive=root,share-rw=on"
      -netdev "user,id=net0,ipv6=off,hostfwd=tcp:127.0.0.1:$PORT-:22"
      -device "virtio-net-pci,netdev=net0,romfile=,mac=52:54:00:76:61:70")
if [ "$MODE" = desktop ]; then
  # the disk is served over NBD and the RAM is a memfd: replace the raw root drive with the NBD drive
  cmd=("${cmd[@]:0:${#cmd[@]}}")
  # build the final argv explicitly for clarity: drop the raw root file entries and use the NBD entries
  cmd=("$QEMU" -accel "tcg,tb-size=2047"
    -machine "vmapple,uuid=$ECID,memory-backend=ram0,ambrosia=on$RNDM"
    -bios "$AMBROSIA" -device "loader,file=$W/bundle.cpio,addr=0x120000000,force-raw=on"
    -cpu max -smp "$CPUS" -m "$RAM"
    -object "memory-backend-file,id=ram0,size=$RAM,mem-path=$(cat "$W/memfd.path"),share=on,align=2M"
    -display none -monitor none -parallel none
    -serial "file:$W/serial.log" -qmp "unix:$W/qmp.sock,server=on,wait=off"
    -drive "if=pflash,format=raw,file.filename=$AUX,file.locking=off"
    -drive "if=pflash,format=raw,file.driver=nbd,file.server.type=unix,file.server.path=$W/nbd.sock"
    -drive "if=none,format=raw,file.filename=$AUX,file.locking=off,id=aux"
    -device "vmapple-virtio-blk-pci,variant=aux,drive=aux,share-rw=on"
    -drive "if=none,format=raw,file.driver=nbd,file.server.type=unix,file.server.path=$W/nbd.sock,id=root"
    -device "vmapple-virtio-blk-pci,variant=root,drive=root,share-rw=on"
    -netdev "user,id=net0,ipv6=off,hostfwd=tcp:127.0.0.1:$PORT-:22"
    -device "virtio-net-pci,netdev=net0,romfile=,mac=52:54:00:76:61:70")
  if [ -n "${VNC_PORT:-}" ]; then
    cmd+=(-L "$PREFIX/share/qemu" -object "secret,id=vncsec,file=$W/vnc.pass"
          -vnc "127.0.0.1:$((VNC_PORT - 5900)),password-secret=vncsec")
  fi
fi
if [ "$SECS" = 0 ]; then
  "${cmd[@]}" >"$W/qemu.out" 2>&1 &
else
  timeout "$SECS" "${cmd[@]}" >"$W/qemu.out" 2>&1 &
fi
echo $! > "$W/qemu.pid"
for _ in $(seq 1 240); do [ -S "$W/qmp.sock" ] && break; sleep 0.5; done
echo "pid $(cat "$W/qemu.pid") qmp $W/qmp.sock serial $W/serial.sock nbd $W/nbd.sock"
