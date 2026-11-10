#!/usr/bin/env python3
"""osx-proxmox-next power watchdog: finishes a macOS restart or shut down that hangs (host side, one VM).

Usage: igpu_power_watchdog.py <vmid>     (run by the systemd unit osx-next-power-watchdog@<vmid>)

macOS Tahoe with the Intel iGPU hangs at the very end of every restart and shut down (WhateverXe issue 62):
the GPU faults while macOS tears the desktop down and the kernel waits for it forever. Only the host can end
that, so this watches the VM's serial port, where the kernel console goes (boot-arg serial=3):

1. launchd prints "Will be calling reboot(2) with flags: 0x..." once every app and service has quit.
   Flags with RB_HALT (0x8) mean shut down, anything else means restart. Nothing else prints that line,
   and a healthy guest that prints it is about to reset or power off anyway.
2. A guest that resets prints the firmware's "BdsDxe:" lines within seconds, and
   one that powers off closes the serial socket. Either ends the watch.
3. Neither within GRACE seconds: the VM must also have done no disk I/O for QUIET seconds (a booting or
   working guest always does). Only then the host stops it, and starts it again for a restart.

The Python CLI and the standalone bash installer install this exact text (the bash script embeds it
verbatim; tests/test_igpu_parity.py diffs the two). Standard library only.
It disables its own unit and exits when the VM is gone or no longer has the iGPU and the serial port.
"""
import os
import re
import socket
import subprocess
import sys
import time

INTENT_RE = re.compile(r'Will be calling reboot\(2\) with flags: (0x[0-9a-fA-F]+)')
RESET_RE = re.compile(r'BdsDxe: ')  # the firmware: a kernel banner is no proof, panic logs repeat it
RB_HALT = 0x8
GRACE = 60        # launchd's reboot(2) line to a reset: a healthy guest needs under 10 s
QUIET = 15        # no disk I/O for this long before the host steps in
STOP_WAIT = 60    # qm stop, then the VM must be stopped within this long before qm start
POLL = 1.0
MAX_LINE = 65536
CONF_DIR = '/etc/pve/qemu-server'
SOCK_DIR = '/var/run/qemu-server'
UNIT = 'osx-next-power-watchdog@%s.service'
IGD_RE = re.compile(r'^hostpci\d+:\s*(0000:)?00:02(\.0)?(,|$)', re.M)
SERIAL_RE = re.compile(r'^serial0:\s*socket\s*$', re.M)
OPS_RE = re.compile(r'^\s+(?:rd|wr)_operations:\s*(\d+)\s*$', re.M)


def log(vmid, message):
    sys.stdout.write('VM %s: %s\n' % (vmid, message))
    sys.stdout.flush()


def intent_of(line):
    """'restart' or 'shutdown' for launchd's reboot(2) line, else None."""
    m = INTENT_RE.search(line)
    if not m:
        return None
    return 'shutdown' if int(m.group(1), 16) & RB_HALT else 'restart'


def main_section(text):
    """The VM's current config, without its snapshot sections."""
    return re.split(r'^\[', text, maxsplit=1, flags=re.M)[0]


def applies(conf_text):
    """True while the VM has the iGPU passed through and its serial port as a socket."""
    cur = main_section(conf_text)
    return bool(IGD_RE.search(cur)) and bool(SERIAL_RE.search(cur))


def io_ops(status_text):
    """Read plus write operations over every disk, from `qm status <vmid> --verbose`. None when absent."""
    found = OPS_RE.findall(status_text)
    return sum(int(n) for n in found) if found else None


class Tracker:
    """Fed with serial lines and the clock; says when the host has to step in.

    idle -> armed (launchd's reboot(2) line) -> quiet (GRACE passed without a reset, disk counter taken)
    -> act (QUIET passed and the counter did not move). A reset line, a closed socket or disk I/O go back to idle.
    """

    def __init__(self):
        self.intent = None
        self.since = 0.0
        self.ops = None
        self.quiet_since = None  # when the disk counter was taken (GRACE ran out), None before

    def feed(self, line, now):
        """One serial line. Returns 'armed' or 'reset' when the state changed, else None."""
        found = intent_of(line)
        if found:
            self.intent, self.since, self.ops, self.quiet_since = found, now, None, None
            return 'armed'
        if self.intent and RESET_RE.search(line):
            self.clear()
            return 'reset'
        return None

    def clear(self):
        self.intent, self.ops, self.quiet_since = None, None, None

    def due(self, now):
        """'check' when GRACE ran out (take the disk counter), 'decide' when QUIET ran out after it."""
        if not self.intent:
            return None
        if self.quiet_since is None:
            return 'check' if now - self.since >= GRACE else None
        return 'decide' if now - self.quiet_since >= QUIET else None
        return None

    def start_quiet(self, ops, now):
        self.ops, self.quiet_since = ops, now


def run(argv, timeout=120):
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return res.returncode, (res.stdout or '') + (res.stderr or '')
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def vm_running(vmid, runner=run):
    return 'status: running' in runner(['qm', 'status', str(vmid)])[1]


def vm_ops(vmid, runner=run):
    rc, out = runner(['qm', 'status', str(vmid), '--verbose'])
    return io_ops(out) if rc == 0 else None


def recover(vmid, intent, runner=run, clock=time.monotonic, sleep=time.sleep):
    """Stop the hung VM, and start it again when macOS asked for a restart. True when that worked."""
    rc, out = runner(['qm', 'stop', str(vmid)])
    if rc != 0:
        # a 'qm shutdown' from the Proxmox UI holds the VM lock while it waits for the hung guest
        rc, out = runner(['qm', 'stop', str(vmid), '--overrule-shutdown', '1'])
    if rc != 0:
        log(vmid, 'qm stop failed: ' + out.strip()[-200:])
        return False
    if intent != 'restart':
        return True
    deadline = clock() + STOP_WAIT
    while vm_running(vmid, runner):
        if clock() >= deadline:
            log(vmid, 'still running %d s after qm stop, not starting it' % STOP_WAIT)
            return False
        sleep(2)
    rc, out = runner(['qm', 'start', str(vmid)])
    if rc != 0:
        log(vmid, 'qm start failed: ' + out.strip()[-200:])
    return rc == 0


def step(vmid, tracker, now, runner=run, clock=time.monotonic, sleep=time.sleep):
    """Act on what the tracker says is due. Returns True when the host stopped (and maybe started) the VM."""
    due = tracker.due(now)
    if due == 'check':
        if not vm_running(vmid, runner):
            tracker.clear()
            return False
        tracker.start_quiet(vm_ops(vmid, runner), now)
        return False
    if due != 'decide':
        return False
    before, intent = tracker.ops, tracker.intent
    after = vm_ops(vmid, runner)
    tracker.clear()
    if not vm_running(vmid, runner):
        return False
    if before is None or after is None or after != before:
        log(vmid, 'macOS asked for a %s %d s ago and did not reset, but its disks are still busy (%s -> %s '
            'operations), so it is left alone' % (intent, GRACE + QUIET, before, after))
        return False
    log(vmid, 'macOS asked for a %s, then hung with no disk activity for %d s: %s' % (
        intent, QUIET, 'stopping and starting it' if intent == 'restart' else 'stopping it'))
    ok = recover(vmid, intent, runner, clock, sleep)
    log(vmid, ('%s finished' % intent) if ok else ('%s could not be finished' % intent))
    return True


def disable(vmid, why):
    log(vmid, why + '; disabling ' + UNIT % vmid)
    run(['systemctl', 'disable', UNIT % vmid])


def still_applies(vmid):
    """None while the watch should go on, else the reason to stop."""
    try:
        with open(os.path.join(CONF_DIR, '%s.conf' % vmid)) as f:
            text = f.read()
    except OSError:
        return 'the VM config is gone'
    if not applies(text):
        return 'the VM no longer has the iGPU and a serial socket'
    return None


def watch(vmid, sock_path):
    """Read one QEMU run of the serial socket until it closes."""
    s = socket.socket(socket.AF_UNIX)
    s.connect(sock_path)
    s.settimeout(POLL)
    tracker, buf = Tracker(), b''
    try:
        while True:
            try:
                data = s.recv(4096)
                if not data:
                    if tracker.intent:
                        log(vmid, 'powered off by itself' if tracker.intent == 'shutdown' else 'QEMU exited')
                    return
                buf = (buf + data)[-MAX_LINE:]  # a line longer than this is not one the watch looks for
                while b'\n' in buf:
                    raw, buf = buf.split(b'\n', 1)
                    state = tracker.feed(raw.decode('utf-8', 'replace'), time.monotonic())
                    if state == 'armed':
                        log(vmid, 'macOS asked for a %s, watching for the reset' % tracker.intent)
                    elif state == 'reset':
                        log(vmid, 'the guest reset by itself')
            except socket.timeout:
                pass
            if step(vmid, tracker, time.monotonic(), run):
                return
    finally:
        s.close()


def main(argv):
    if len(argv) != 2 or not argv[1].isdigit():
        sys.stderr.write('usage: igpu_power_watchdog.py <vmid>\n')
        return 64
    vmid = argv[1]
    sock_path = os.path.join(SOCK_DIR, '%s.serial0' % vmid)
    log(vmid, 'power watchdog started')
    last_check = None
    while True:
        now = time.monotonic()
        if last_check is None or now - last_check >= 30:
            last_check = now
            why = still_applies(vmid)
            if why:
                disable(vmid, why)
                return 0
        try:
            watch(vmid, sock_path)
        except OSError:
            time.sleep(3)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
