#!/bin/sh
# Run as root inside the guest (over ssh), then shut the guest down cleanly and save the disk as the golden.
# P83: launchd treats a job without ProcessType as a throttled
# daemon, so during the login storm sshd answers late. Interactive makes ssh-driven steps (snapshot, resume) start
# tens of seconds earlier. Optional, safe to repeat.
set -eu
P=/Library/LaunchDaemons/org.qemu.27on86.sshd.plist
plutil -replace ProcessType -string Interactive "$P"
plutil -p "$P"
echo "done: shut the guest down (shutdown -h now) so the change lands in the golden"
