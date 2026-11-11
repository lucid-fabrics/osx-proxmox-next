#!/bin/sh
# osx-proxmox-next: first boots of a macOS guest that has an NVIDIA card passed through (NullMoth driver).
# A LaunchDaemon (root) runs this on every boot until it is done:
#   install: turn on Remote Login and Screen Sharing, keep the Mac awake, install the driver, restart
#   verify:  wait for the driver to load, report to the host, remove itself and power off
# Every result goes to result.txt on the payload disk, where the host reads it. One attempt only: a failed
# install is reported and left alone (the driver's own installer rolls itself back).
BASE="/Library/Application Support/OSXProxmoxNext/nullmoth"
STATE="$BASE/state"
PLIST="/Library/LaunchDaemons/com.osxproxmoxnext.nullmoth-firstboot.plist"
LOG="/var/log/osx-proxmox-next-nullmoth.log"
TARBALL="@TARBALL@"
WANT_SHA256="@TARBALL_SHA256@"
FW_RUN="@FW_RUN@"
PAYLOAD_LABEL="OSXPNIGPU"
MIN_KEXTS=4
LOAD_WAIT=300

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }

# hand a result to the host through the payload disk; true when the disk is attached
write_result() {
  log "result: $1"
  # the real mount point of the payload disk: a leftover /Volumes/OSXPNIGPU folder on the system volume is no disk
  vol=$(diskutil info "$PAYLOAD_LABEL" 2>/dev/null | sed -n 's/^ *Mount Point: *//p')
  if [ -z "$vol" ]; then
    diskutil mount "$PAYLOAD_LABEL" >/dev/null 2>&1
    vol=$(diskutil info "$PAYLOAD_LABEL" 2>/dev/null | sed -n 's/^ *Mount Point: *//p')
  fi
  [ -n "$vol" ] && [ -d "$vol" ] || return 1
  echo "$1" >"$vol/result.txt"
  sync
  diskutil unmount "$vol" >/dev/null 2>&1
  return 0
}

# report, then end this run; $2 = halt|restart. Only a host that is waiting for the report (the payload disk is
# attached) gets a power off: without it nobody is watching, so the guest stays up. A restart is needed to load the
# driver either way.
report() {
  if write_result "$1"; then waiting=yes; else waiting=no; fi
  case "$2" in
    halt) [ "$waiting" = yes ] && /sbin/shutdown -h now >/dev/null 2>&1 ;;
    restart) /sbin/shutdown -r now >/dev/null 2>&1 ;;
  esac
  exit 0
}

# a failure ends the job for good
fail() {
  log "failed: $*"
  rm -f "$PLIST"
  echo failed >"$STATE"
  report "failed: $*" halt
}

# keep the log tail of the driver installer on one short line for the host
tail_line() { tail -n 3 "$LOG" | tr '\n"' '  ' | cut -c1-300; }

remote_access() {
  /bin/launchctl enable system/com.openssh.sshd >>"$LOG" 2>&1
  /bin/launchctl load -w /System/Library/LaunchDaemons/ssh.plist >>"$LOG" 2>&1
  /bin/launchctl enable system/com.apple.screensharing >>"$LOG" 2>&1
  /bin/launchctl load -w /System/Library/LaunchDaemons/com.apple.screensharing.plist >>"$LOG" 2>&1
  /usr/bin/pmset -a sleep 0 >>"$LOG" 2>&1
  /usr/bin/pmset -a disksleep 0 >>"$LOG" 2>&1
}

guest_ip() {
  for i in en0 en1 en2 en3; do
    ip=$(/usr/sbin/ipconfig getifaddr "$i" 2>/dev/null) && [ -n "$ip" ] && { echo "$ip"; return; }
  done
}

install_driver() {
  /usr/sbin/ioreg -r -c IOPCIDevice -d 1 | grep -q '"vendor-id" = <de100000>' || fail "macOS does not see the NVIDIA card"
  got=$(/usr/bin/shasum -a 256 "$BASE/$TARBALL" 2>/dev/null | awk '{print $1}')
  [ "$got" = "$WANT_SHA256" ] || fail "the driver package checksum does not match"
  remote_access
  work=$(mktemp -d /var/tmp/nullmoth-pkg.XXXXXX) || fail "cannot create a work folder"
  /usr/bin/tar -xzf "$BASE/$TARBALL" -C "$work" >>"$LOG" 2>&1 || fail "cannot unpack the driver package"
  [ -f "$work/pkgroot/install.sh" ] || fail "the driver package has no installer"
  if [ -n "$FW_RUN" ] && [ -f "$BASE/$FW_RUN" ]; then
    NULLMOTH_FW_RUN="$BASE/$FW_RUN"; export NULLMOTH_FW_RUN
  fi
  log "running the NullMoth installer"
  if /bin/bash "$work/pkgroot/install.sh" >>"$LOG" 2>&1; then
    rm -rf "$work" "${BASE:?}/$TARBALL"
    [ -z "$FW_RUN" ] || rm -f "$BASE/$FW_RUN"
    echo verify >"$STATE"
    report "installed" restart
  fi
  rm -rf "$work"
  fail "the driver installer stopped: $(tail_line)"
}

verify_driver() {
  write_result "verifying" || true   # tells the host the restart worked, so it does not stop the VM while the driver loads
  waited=0
  kexts=0
  while [ "$waited" -lt "$LOAD_WAIT" ]; do
    kexts=$(/usr/bin/kmutil showloaded --list-only 2>/dev/null | grep -ci nullmoth)
    [ "$kexts" -ge "$MIN_KEXTS" ] && break
    sleep 10
    waited=$((waited + 10))
  done
  if [ "$kexts" -lt "$MIN_KEXTS" ]; then
    fail "the driver did not load after the restart (kexts loaded: $kexts, needed $MIN_KEXTS)"
  fi
  # the network comes up a little after the extensions: give the guest up to a minute to get its address
  ip=$(guest_ip)
  tries=0
  while [ -z "$ip" ] && [ "$tries" -lt 12 ]; do
    sleep 5
    tries=$((tries + 1))
    ip=$(guest_ip)
  done
  displays=$(/usr/sbin/ioreg -l -w0 2>/dev/null | grep -c IODisplayEDID)
  shown=no; [ "$displays" -gt 0 ] && shown=yes
  rm -f "$PLIST"
  echo "done" >"$STATE"
  report "verified: kexts=$kexts display=$shown ip=$ip" halt
}

case "$(cat "$STATE" 2>/dev/null)" in
  verify) verify_driver ;;
  done|failed) rm -f "$PLIST" ;;
  *) install_driver ;;
esac
