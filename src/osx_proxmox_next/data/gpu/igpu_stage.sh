#!/bin/sh
# osx-proxmox-next: runs in the macOS recovery Terminal, from the payload disk (volume OSXPNIGPU).
# Copies the graphics package, the first-boot script and its LaunchDaemon into the installed
# macOS (offline), checks the package, writes result.txt next to this script and powers off.
SRC="$(cd "$(dirname "$0")" && pwd)"
# tells the host the typed command arrived intact (it retypes when it never sees this)
echo started >"$SRC/result.txt"; sync
say() { echo "osx-proxmox-next: $*"; }
fail() { say "FAILED: $*"; echo "failed: $*" >"$SRC/result.txt"; sync; /sbin/halt; exit 1; }

diskutil mount "MACOS - Data" >/dev/null 2>&1
D="/Volumes/MACOS - Data"
[ -d "$D/Library" ] || D="/Volumes/Data"
[ -d "$D/Library" ] || fail "the installed macOS data volume is not mounted"

BASE="$D/Library/Application Support/OSXProxmoxNext"
mkdir -p "$BASE" "$D/Library/LaunchDaemons" || fail "cannot create the target folders"
cp "$SRC/WhateverXe-Graphics.pkg" "$BASE/WhateverXe-Graphics.pkg" || fail "cannot copy the package"
cp "$SRC/firstboot.sh" "$BASE/firstboot.sh" || fail "cannot copy the first-boot script"
cp "$SRC/com.osxproxmoxnext.igpu-firstboot.plist" "$D/Library/LaunchDaemons/" || fail "cannot copy the LaunchDaemon"
chmod 755 "$BASE/firstboot.sh"
chmod 644 "$BASE/WhateverXe-Graphics.pkg" "$D/Library/LaunchDaemons/com.osxproxmoxnext.igpu-firstboot.plist"
chown -R 0:0 "$BASE" "$D/Library/LaunchDaemons/com.osxproxmoxnext.igpu-firstboot.plist"
rm -f "$BASE/igpu-tries" "$BASE/igpu-status"

# The payload disk was built from the verified download. Compare the copy with it (byte for byte where cmp
# exists, by size otherwise); firstboot.sh checks the SHA-256 again inside the full macOS.
size_of() { ls -ln "$1" | awk '{print $5}'; }
if command -v cmp >/dev/null 2>&1; then
  cmp -s "$SRC/WhateverXe-Graphics.pkg" "$BASE/WhateverXe-Graphics.pkg" || \
    fail "the copied package differs from the original ($(size_of "$SRC/WhateverXe-Graphics.pkg") and $(size_of "$BASE/WhateverXe-Graphics.pkg") bytes)"
else
  [ "$(size_of "$SRC/WhateverXe-Graphics.pkg")" = "$(size_of "$BASE/WhateverXe-Graphics.pkg")" ] || fail "the copied package has the wrong size"
fi

say "staged OK"
echo "staged" >"$SRC/result.txt"
sync
/sbin/halt
