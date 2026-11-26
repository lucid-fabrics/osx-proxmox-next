#!/bin/sh
# Runs in the VMApple single-user serial shell, after persist-sshd.py has installed the key and the sshd LaunchDaemon
#. Adds what a golden needs on top of that, all offline (no normal boot):
#   1. sshd job ProcessType Interactive: sshd answers early in the login storm
#   2. timed UseFilesystemTimestamp=NO: removes a boot clock stall
#   3. automatic login fallback: only if the installed disk has none (kcpassword staged next to this script)
# Idempotent. Needs the Data volume mounted (persist-sshd.py leaves it mounted).
set -eu
stage=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
P=/Library/LaunchDaemons/org.qemu.27on86.sshd.plist
test -f "$P" || { echo "sshd plist missing: persist-sshd.py did not run or the Data volume is not mounted" >&2; exit 1; }
/usr/bin/plutil -replace ProcessType -string Interactive "$P"
echo "sshd ProcessType: $(/usr/bin/plutil -extract ProcessType raw "$P")"

F=/Library/Preferences/com.apple.timed.plist
test -e "$F" || /usr/bin/plutil -create xml1 "$F"
/usr/bin/plutil -replace UseFilesystemTimestamp -bool NO "$F"
chown root:wheel "$F"; chmod 644 "$F"
echo "timed UseFilesystemTimestamp: $(/usr/bin/plutil -extract UseFilesystemTimestamp raw "$F")"

# no cfprefsd in single-user mode: `defaults` fails, so the plist is read and written with plutil
LW=/Library/Preferences/com.apple.loginwindow.plist
cur=$(/usr/bin/plutil -extract autoLoginUser raw "$LW" 2>/dev/null || true)
if test -n "$cur" && test -f /private/etc/kcpassword; then
  echo "autologin: already set for $cur (kept)"
elif test -f "$stage/kcpassword" && test -n "${AUTOLOGIN_USER:-}"; then
  cp "$stage/kcpassword" /private/etc/kcpassword
  chown root:wheel /private/etc/kcpassword; chmod 600 /private/etc/kcpassword
  test -e "$LW" || /usr/bin/plutil -create xml1 "$LW"
  /usr/bin/plutil -replace autoLoginUser -string "$AUTOLOGIN_USER" "$LW"
  chown root:wheel "$LW"; chmod 644 "$LW"
  echo "autologin: set for $AUTOLOGIN_USER (fallback, the install had none)"
else
  echo "autologin: not set and no fallback staged (the desktop needs a logged-in user)" >&2
fi
sync
echo "golden phase 1 done"
