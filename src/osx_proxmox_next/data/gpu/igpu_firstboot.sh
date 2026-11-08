#!/bin/sh
# osx-proxmox-next: first boot of a macOS guest that has the Intel iGPU passed through.
# Installs the WhateverXe Graphics package, but only once WhateverXe is loaded: installing it
# without WhateverXe makes the guest panic at boot. Started by the LaunchDaemon
# com.osxproxmoxnext.igpu-firstboot (root). At most 3 tries; the guest stays usable without it.
BASE="/Library/Application Support/OSXProxmoxNext"
PKG="$BASE/WhateverXe-Graphics.pkg"
TRIES_FILE="$BASE/igpu-tries"
STATUS_FILE="$BASE/igpu-status"
PLIST="/Library/LaunchDaemons/com.osxproxmoxnext.igpu-firstboot.plist"
LOG="/var/log/osx-proxmox-next-igpu.log"
WANT_SHA256="@PKG_SHA256@"
PAYLOAD_LABEL="OSXPNIGPU"
WAIT_SECONDS=120
MAX_TRIES=3

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }

# status, then hand it to the host through the payload disk when that is attached
finish() {
  echo "$1" >"$STATUS_FILE"
  log "status: $1"
  vol="/Volumes/$PAYLOAD_LABEL"
  [ -d "$vol" ] || diskutil mount "$PAYLOAD_LABEL" >/dev/null 2>&1
  if [ -d "$vol" ]; then
    echo "$1" >"$vol/result.txt"
    sync
    diskutil unmount "$vol" >/dev/null 2>&1
    # The host waits for the guest to stop, then reads result.txt and starts it again.
    log "stopping so the host can restart the guest"
    /sbin/shutdown -h now >/dev/null 2>&1
  elif [ "$1" = "installed" ]; then
    log "no payload disk, restarting once to load the graphics drivers"
    /sbin/shutdown -r now >/dev/null 2>&1
  fi
  exit 0
}

tries=$(cat "$TRIES_FILE" 2>/dev/null || echo 0)
if [ "$tries" -ge "$MAX_TRIES" ]; then
  log "already tried $tries times, leaving the package alone"
  rm -f "$PLIST"
  exit 0
fi
echo $((tries + 1)) >"$TRIES_FILE"

if [ ! -f "$PKG" ]; then
  log "package is not there"
  rm -f "$PLIST"
  finish "failed: package missing"
fi

got=$(/usr/bin/shasum -a 256 "$PKG" | awk '{print $1}')
if [ "$got" != "$WANT_SHA256" ]; then
  log "package checksum mismatch: $got"
  rm -f "$PKG" "$PLIST"
  finish "failed: package checksum mismatch"
fi

waited=0
while [ "$waited" -lt "$WAIT_SECONDS" ]; do
  if /usr/bin/kmutil showloaded 2>/dev/null | grep -q 'org\.b00t0x\.WhateverXe '; then
    break
  fi
  sleep 5
  waited=$((waited + 5))
done
if ! /usr/bin/kmutil showloaded 2>/dev/null | grep -q 'org\.b00t0x\.WhateverXe '; then
  log "WhateverXe did not load within ${WAIT_SECONDS}s (try $((tries + 1)) of $MAX_TRIES)"
  finish "not installed: WhateverXe not loaded (try $((tries + 1)) of $MAX_TRIES)"
fi

log "WhateverXe is loaded, installing the graphics package"
if /usr/sbin/installer -pkg "$PKG" -target / >>"$LOG" 2>&1; then
  rm -f "$PKG" "$PLIST" "$BASE/firstboot.sh" "$TRIES_FILE"
  finish "installed"
fi
finish "failed: installer error (try $((tries + 1)) of $MAX_TRIES)"
