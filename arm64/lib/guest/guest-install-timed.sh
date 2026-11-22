#!/bin/sh
# single-user shell: mount Data (as p56 guest-install-diag.sh) and turn off timed FilesystemTimestamp source
set -eu
stage=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
boot_util=/System/Library/Filesystems/apfs.fs/Contents/Resources/apfs_boot_util
"$boot_util" 2 >"$stage/apfs-boot.log" 2>&1 &
boot_pid=$!
count=0; stitched=0
while test "$count" -lt 180; do
  if test -d /System/Volumes/Data/private/var && test -d /private/var; then
    if test "$(/usr/bin/stat -f %d /System/Volumes/Data/private)" = "$(/usr/bin/stat -f %d /private)"; then stitched=1; break; fi
  fi
  count=$((count + 1)); sleep 1
done
test "$stitched" = 1 || { echo "no firmlinks"; tail -n 40 "$stage/apfs-boot.log"; exit 1; }
sleep 30
kill -INT "$boot_pid" 2>/dev/null || true; wait "$boot_pid" 2>/dev/null || true
F=/Library/Preferences/com.apple.timed.plist
test -e $F || /usr/bin/plutil -create xml1 $F
/usr/bin/plutil -replace UseFilesystemTimestamp -bool NO $F
chown root:wheel $F; chmod 644 $F
/usr/bin/plutil -p $F
sync
echo "p58 timed pref installed"
