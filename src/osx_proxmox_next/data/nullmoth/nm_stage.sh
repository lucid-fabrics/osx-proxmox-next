#!/bin/sh
# osx-proxmox-next: runs in the macOS recovery Terminal, from the payload disk (volume OSXPNIGPU).
# Copies the NullMoth driver package, the first-boot script and its LaunchDaemon into the installed macOS
# (offline), turns off the "allow this extension" prompt, writes result.txt next to this script and powers off.
SRC="$(cd "$(dirname "$0")" && pwd)"
TARBALL="@TARBALL@"
FW_RUN="@FW_RUN@"
# tells the host the typed command arrived intact (it retypes when it never sees this)
echo started >"$SRC/result.txt"; sync
say() { echo "osx-proxmox-next: $*"; }
fail() { say "FAILED: $*"; echo "failed: $*" >"$SRC/result.txt"; sync; /sbin/halt; exit 1; }

diskutil mount "MACOS - Data" >/dev/null 2>&1
D="/Volumes/MACOS - Data"
[ -d "$D/Library" ] || D="/Volumes/Data"
[ -d "$D/Library" ] || fail "the installed macOS data volume is not mounted"

BASE="$D/Library/Application Support/OSXProxmoxNext/nullmoth"
PLIST="com.osxproxmoxnext.nullmoth-firstboot.plist"
mkdir -p "$BASE" "$D/Library/LaunchDaemons" || fail "cannot create the target folders"
rm -f "$BASE/state"
cp "$SRC/$TARBALL" "$BASE/$TARBALL" || fail "cannot copy the driver package"
[ -z "$FW_RUN" ] || cp "$SRC/$FW_RUN" "$BASE/$FW_RUN" || fail "cannot copy NVIDIA's firmware package"
cp "$SRC/firstboot.sh" "$BASE/firstboot.sh" || fail "cannot copy the first-boot script"
cp "$SRC/$PLIST" "$D/Library/LaunchDaemons/$PLIST" || fail "cannot copy the LaunchDaemon"
chmod 755 "$BASE/firstboot.sh"
chmod 644 "$BASE/$TARBALL" "$D/Library/LaunchDaemons/$PLIST"
[ -z "$FW_RUN" ] || chmod 644 "$BASE/$FW_RUN"
chown -R 0:0 "$BASE" "$D/Library/LaunchDaemons/$PLIST"

# The payload disk was built from the verified download. Compare the copies with it (byte for byte where cmp
# exists, by size otherwise); firstboot.sh checks the SHA-256 again inside the full macOS.
size_of() { ls -ln "$1" | awk '{print $5}'; }
same() {
  if command -v cmp >/dev/null 2>&1; then cmp -s "$1" "$2"; else [ "$(size_of "$1")" = "$(size_of "$2")" ]; fi
}
same "$SRC/$TARBALL" "$BASE/$TARBALL" || fail "the copied driver package differs from the original"
[ -z "$FW_RUN" ] || same "$SRC/$FW_RUN" "$BASE/$FW_RUN" || fail "the copied firmware package differs from the original"

# macOS must load the extensions without asking the user to allow them in System Settings.
spctl kext-consent disable >"$BASE/kext-consent.log" 2>&1 || fail "could not turn off the extension approval prompt ($(tr '\n' ' ' <"$BASE/kext-consent.log" | cut -c1-120))"

say "staged OK"
# flush the copied files first: the host stops the VM as soon as it reads "staged" (the halt below can wedge Tahoe's recovery)
sync
echo "staged" >"$SRC/result.txt"
sync
/sbin/halt
