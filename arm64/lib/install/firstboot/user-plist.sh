#!/bin/sh
# Make the first macOS account directly in dslocal: DirectoryService is not running in the single-user shell.
# The password is set later, over ssh, on the first normal boot. Idempotent.
set -eu
U=${FIRSTBOOT_USER:-snap}
case "$U" in
  [a-z_]*) ;;
  *) echo "invalid FIRSTBOOT_USER: $U" >&2; exit 2 ;;
esac
case "$U" in *[!a-z0-9_-]*) echo "invalid FIRSTBOOT_USER: $U" >&2; exit 2 ;; esac
D=/var/db/dslocal/nodes/Default
F="$D/users/$U.plist"
if [ -f "$F" ]; then
  echo "user $U: exists (kept)"
else
  g=$(/usr/bin/uuidgen)
  cat > "$F" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>name</key><array><string>$U</string></array>
	<key>uid</key><array><string>501</string></array>
	<key>gid</key><array><string>20</string></array>
	<key>home</key><array><string>/Users/$U</string></array>
	<key>shell</key><array><string>/bin/zsh</string></array>
	<key>realname</key><array><string>$U</string></array>
	<key>passwd</key><array><string>*</string></array>
	<key>generateduid</key><array><string>$g</string></array>
</dict>
</plist>
PLIST
  chown root:wheel "$F"; chmod 600 "$F"
  mkdir -p "/Users/$U"; chown 501:20 "/Users/$U"; chmod 755 "/Users/$U"
  echo "user $U: created (uid 501, admin)"
fi
touch /var/db/.AppleSetupDone; chown root:wheel /var/db/.AppleSetupDone
echo "user pass done"
sync
