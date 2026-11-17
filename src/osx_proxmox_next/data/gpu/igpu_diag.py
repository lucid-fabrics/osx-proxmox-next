#!/usr/bin/env python3
"""osx-proxmox-next iGPU crash diagnostics (host side): serial log, incident snapshots, report bundle.

Usage: igpu_diag.py report <vmid> [--out DIR]    bundle the latest incidents and versions into one tar.gz
       igpu_diag.py incident <vmid> [reason]     take an incident snapshot of the running VM now
       igpu_diag.py versions <vmid>              what is installed in the VM (WhateverXe, Graphics, AMFIPass)

A hung macOS guest cannot tell anything, so everything is read from the host:

* SerialLog keeps a size-capped, rotated copy of the VM's serial console (the macOS kernel log, boot-arg
  serial=3) in /var/log/osx-proxmox-next/<vmid>/serial.log (10 MB, 5 files).
* collect_incident is called by the power watchdog right before it stops a panicked or hung VM. It writes a
  timestamped folder next to the serial log: the panic text, `info registers -a` of every vCPU (QMP), the iGPU's
  ring and fault registers (read-only, from BAR0), host CPU temperature and throttle counters, the vfio related
  dmesg tail, the VM config, the installed versions. It stays within BUDGET seconds and a few hundred KB.
* build_report packs the latest incidents into one tar.gz and writes a plain summary the user can paste into a
  WhateverXe issue. Nothing is ever sent anywhere.

The Python CLI and the standalone bash installer use this exact text (the bash script embeds it verbatim;
tests/test_igpu_parity.py diffs the two). Standard library only.
"""
import io
import json
import mmap
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.parse

LOG_BASE = '/var/log/osx-proxmox-next'
STATE_BASE = '/var/lib/osx-proxmox-next/igpu'
SOCK_DIR = '/var/run/qemu-server'
SYSFS = '/sys'
IGD_BDF = '0000:00:02.0'
SERIAL_MAX = 10 * 1024 * 1024   # bytes per serial log file
SERIAL_FILES = 5                # serial.log plus four rotated files: 50 MB at most
INCIDENT_KEEP = 10
BUDGET = 8.0                    # seconds for a whole incident snapshot: recovery is never held up longer
FILE_MAX = 256 * 1024           # bytes per text file in an incident
SERIAL_TAIL = 192 * 1024
REPORT_INCIDENTS = 3
REPORT_SERIAL_TAIL = 1024 * 1024
RECENT_LINES = 400
ISSUES_URL = 'https://github.com/b00t0x/WhateverXe/issues'
BAR0_SIZE = 16 << 20

# Gen12 registers read from the host side of the passed-through iGPU's BAR0, read-only: ring state of the render
# (RCS) and copy (BCS) engines, their reset control, the display plane, the TLB fault registers, interrupt state
# and the power limit and frequency registers. The offsets are the ones the hang analysis of WhateverXe issue 62 used.
IGPU_REGS = {
    'RCS_HEAD': 0x2034, 'RCS_TAIL': 0x2030, 'RCS_CTL': 0x203C, 'RCS_ACTHD': 0x2074, 'RCS_IPEHR': 0x2068,
    'RCS_MI_MODE': 0x209C, 'RCS_RESET_CTRL': 0x20D0, 'RCS_EXECLIST_STATUS_LO': 0x2234, 'RCS_EXECLIST_STATUS_HI': 0x2238,
    'BCS_HEAD': 0x22034, 'BCS_TAIL': 0x22030, 'BCS_CTL': 0x2203C, 'BCS_ACTHD': 0x22074, 'BCS_IPEHR': 0x22068,
    'BCS_INSTDONE': 0x2206C, 'BCS_MI_MODE': 0x2209C, 'BCS_RESET_CTRL': 0x220D0,
    'BCS_EXECLIST_STATUS_LO': 0x22234, 'BCS_EXECLIST_STATUS_HI': 0x22238,
    'PLANE_CTL_1_A': 0x70180, 'PLANE_SURF_1_A': 0x7019C, 'PLANE_CTL_1_B': 0x71180, 'PLANE_SURF_1_B': 0x7119C,
    'TRANS_CONF_A': 0x70008, 'TRANS_CONF_B': 0x71008,
    'FAULT_TLB_DATA0': 0xCEB8, 'FAULT_TLB_DATA1': 0xCEBC, 'RING_FAULT_REG': 0xCEC4,
    'MASTER_IRQ': 0x190010, 'GEN11_GT_INTR_DW0': 0x190018, 'DISPLAY_INT_CTL': 0x44200,
    'GT0_PERF_LIMIT_REASONS': 0x1381A8, 'RPSTAT1': 0xA01C, 'RPNSWREQ': 0xA008, 'RP_STATE_CAP': 0x145998,
    'PWR_WELL_CTL2': 0x45404, 'DC_STATE_EN': 0x45504,
}
PANIC_RE = re.compile(r'panic\(cpu \d+ caller|Nested panic detected|^Double fault at')
KERNEL_RE = re.compile(r'vfio|i915|\bxe\b|DMAR|IOMMU|thermal|throttl|\bMCE\b|mce:|machine check|kvm|AER|pcieport|BAR 0',
                       re.I)
MAC_RE = re.compile(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}')
MACOS_RE = re.compile(r'\b(monterey|ventura|sonoma|sequoia|tahoe)\b', re.I)


def run(argv, timeout=5):
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return res.returncode, (res.stdout or '') + (res.stderr or '')
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def now_stamp(clock=time.gmtime):
    return time.strftime('%Y%m%dT%H%M%SZ', clock())


def vm_dir(vmid, base=None):
    return os.path.join(base or os.environ.get('IGPU_LOG_DIR') or LOG_BASE, str(int(vmid)))


# ── serial log ───────────────────────────────────────────────────────

class SerialLog:
    """Append-only copy of the serial console, rotated at max_bytes, `files` files at most. Never raises: a
    full disk must not take the watchdog down, it only stops the copy (failed becomes True)."""

    def __init__(self, vmid, base=None, max_bytes=SERIAL_MAX, files=SERIAL_FILES):
        self.dir = vm_dir(vmid, base)
        self.path = os.path.join(self.dir, 'serial.log')
        self.max_bytes, self.files = max_bytes, files
        self.failed = False
        self._fh = None
        self._size = 0

    def _open(self):
        os.makedirs(self.dir, exist_ok=True)
        self._fh = open(self.path, 'ab')
        self._size = self._fh.tell()

    def _rotate(self):
        self._fh.close()
        self._fh = None
        for n in range(self.files - 1, 0, -1):
            src = self.path if n == 1 else '%s.%d' % (self.path, n - 1)
            if os.path.exists(src):
                os.replace(src, '%s.%d' % (self.path, n))
        self._open()

    def write(self, data):
        if self.failed or not data:
            return
        try:
            if self._fh is None:
                self._open()
            if self._size + len(data) > self.max_bytes and self._size > 0:
                self._rotate()
            self._fh.write(data)
            self._fh.flush()
            self._size += len(data)
        except OSError:
            self.failed = True

    def mark(self, text):
        self.write(('--- osx-proxmox-next %s %s ---\n' % (now_stamp(), text)).encode())

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def tail_bytes(path, limit):
    try:
        with open(path, 'rb') as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - limit))
            return f.read()
    except OSError:
        return b''


# ── readers (every one is read-only and bounded) ─────────────────────

def read_igpu_registers(sysfs=None):
    """{name: value} from BAR0 of the iGPU, read-only. Raises OSError when it cannot be read."""
    path = os.path.join(sysfs or os.environ.get('GPU_SYSFS') or SYSFS, 'bus/pci/devices', IGD_BDF, 'resource0')
    fd = os.open(path, os.O_RDONLY | os.O_SYNC)
    try:
        size = min(os.fstat(fd).st_size or BAR0_SIZE, BAR0_SIZE)
        m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ)
        try:
            # 32-bit accesses: the iGPU's registers are not meant to be read a byte at a time
            words = memoryview(m).cast('I')
            try:
                return {k: words[o // 4] for k, o in IGPU_REGS.items() if o + 4 <= size}
            finally:
                words.release()
        finally:
            m.close()
    finally:
        os.close(fd)


def format_registers(regs):
    lines = ['%-26s 0x%08x' % (k, v) for k, v in regs.items()]
    if regs and all(v == 0xFFFFFFFF for v in regs.values()):
        lines.append('ALL ONES: the iGPU did not answer (powered down or reset).')
    return '\n'.join(lines) + '\n'


def qmp_hmp(vmid, command, timeout, sock_dir=SOCK_DIR, clock=time.monotonic):
    """The text a QEMU human monitor command prints, through the VM's QMP socket, within *timeout* seconds."""
    deadline = clock() + timeout
    s = socket.socket(socket.AF_UNIX)
    try:
        s.settimeout(max(0.1, timeout))
        s.connect(os.path.join(sock_dir, '%s.qmp' % vmid))
        buf = b''

        def line():
            """The next line from QEMU, within the deadline (events that keep arriving do not extend it)."""
            nonlocal buf
            while b'\n' not in buf:
                if clock() >= deadline:
                    raise OSError('QMP did not answer in time')
                s.settimeout(max(0.1, deadline - clock()))
                chunk = s.recv(65536)
                if not chunk:
                    raise OSError('QMP closed')
                buf += chunk
            text, buf = buf.split(b'\n', 1)
            return json.loads(text)

        def call(obj):
            s.sendall((json.dumps(obj) + '\n').encode())
            while True:
                msg = line()
                if 'return' in msg or 'error' in msg:
                    return msg

        line()  # the greeting
        call({'execute': 'qmp_capabilities'})
        res = call({'execute': 'human-monitor-command', 'arguments': {'command-line': command}})
        if 'error' in res:
            raise OSError(str(res['error'].get('desc', res['error'])))
        return res['return']
    finally:
        s.close()


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def host_snapshot(sysfs=None, loadavg='/proc/loadavg', cpuinfo='/proc/cpuinfo'):
    """Host CPU temperatures, throttle counters, frequency, load and the CPU and iGPU identity, as text."""
    sysfs = sysfs or os.environ.get('GPU_SYSFS') or SYSFS
    out = []
    model = next((ln.split(':', 1)[1].strip() for ln in (_read(cpuinfo) or '').splitlines()
                  if ln.startswith('model name')), 'unknown')
    out.append('cpu: %s' % model)
    out.append('igpu device id: 8086:%s' % (_read(os.path.join(sysfs, 'bus/pci/devices', IGD_BDF, 'device')) or '?').replace('0x', ''))
    out.append('load average: %s' % (_read(loadavg) or '?'))
    hw = os.path.join(sysfs, 'class/hwmon')
    for name in sorted(os.listdir(hw)) if os.path.isdir(hw) else []:
        d = os.path.join(hw, name)
        chip = _read(os.path.join(d, 'name')) or name
        for f in sorted(os.listdir(d)):
            m = re.fullmatch(r'temp(\d+)_input', f)
            raw = _read(os.path.join(d, f)) if m else None
            if raw and raw.lstrip('-').isdigit():
                label = _read(os.path.join(d, 'temp%s_label' % m.group(1))) or f
                out.append('temperature %s %s: %.1f C' % (chip, label, int(raw) / 1000.0))
    core = package = 0
    cpus = os.path.join(sysfs, 'devices/system/cpu')
    for name in sorted(os.listdir(cpus)) if os.path.isdir(cpus) else []:
        if re.fullmatch(r'cpu\d+', name):
            t = os.path.join(cpus, name, 'thermal_throttle')
            c, p = _read(os.path.join(t, 'core_throttle_count')), _read(os.path.join(t, 'package_throttle_count'))
            core += int(c) if c and c.isdigit() else 0
            package = max(package, int(p)) if p and p.isdigit() else package
    out.append('thermal throttle events: cores %d, package %d' % (core, package))
    freq = _read(os.path.join(cpus, 'cpu0/cpufreq/scaling_cur_freq'))
    out.append('cpu0 frequency: %s' % ('%d MHz' % (int(freq) // 1000) if freq and freq.isdigit() else 'unknown'))
    return '\n'.join(out) + '\n'


def redact_config(text):
    """The VM's own config section with what identifies the machine blanked: MAC addresses, SMBIOS serial and
    uuid, the OSK. Snapshot sections are dropped."""
    main = re.split(r'^\[', text, maxsplit=1, flags=re.M)[0]
    out = []
    for line in main.splitlines():
        key = line.split(':', 1)[0]
        if key in ('smbios1', 'vmgenid', 'cipassword', 'sshkeys', 'cicustom') or key.startswith('ipconfig'):
            line = key + ': (redacted)'
        elif key == 'description':
            m = MACOS_RE.search(urllib.parse.unquote(line))
            line = 'description: macOS ' + m.group(1).capitalize() if m else 'description: (redacted)'
        line = MAC_RE.sub('xx:xx:xx:xx:xx:xx', line)
        line = re.sub(r'osk="[^"]*"', 'osk="redacted"', line)
        line = re.sub(r'\b(serial|uuid)=[^,\s]+', r'\1=redacted', line)
        out.append(line)
    return '\n'.join(out) + '\n'


def macos_of(config_text):
    for line in config_text.splitlines():
        if line.startswith(('description:', 'name:')):
            m = MACOS_RE.search(urllib.parse.unquote(line))
            if m:
                return m.group(1).capitalize()
    return 'unknown'


def read_versions(vmid, base=None):
    try:
        with open(os.path.join(base or os.environ.get('IGPU_STATE_DIR') or STATE_BASE, '%d.json' % int(vmid))) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def describe_versions(state):
    lines = []
    for key, label in (('whateverxe', 'WhateverXe'), ('graphics', 'WhateverXe Graphics')):
        if state.get(key + '_version'):
            how = 'latest when installed' if state.get(key + '_source') == 'latest' else 'chosen by the user'
            lines.append('%s %s (%s)' % (label, state[key + '_version'], how))
    if state.get('amfipass_version'):
        lines.append('AMFIPass %s (pinned)' % state['amfipass_version'])
    return lines


# ── incident snapshot ────────────────────────────────────────────────

def panic_text(lines):
    """From the first line of the last panic (a nested panic reports itself within a few hundred lines of the panic
    it follows, so that one counts as part of it) to the end, at most 200 lines; else the last 60 lines."""
    idx = [i for i, ln in enumerate(lines) if PANIC_RE.search(ln)]
    if not idx:
        return '\n'.join(ln.rstrip('\r\n') for ln in lines[-60:]) + '\n'
    start = idx[-1]
    for i in reversed(idx[:-1]):
        if start - i <= 300:
            start = i
    return '\n'.join(ln.rstrip('\r\n') for ln in lines[start:][:200]) + '\n'


def _cap(text):
    data = text.encode('utf-8', 'replace')
    return data if len(data) <= FILE_MAX else data[:FILE_MAX] + b'\n[cut]\n'


def prune(parent, keep=INCIDENT_KEEP):
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return
    for name in names[:-keep] if keep else names:
        path = os.path.join(parent, name)
        shutil.rmtree(path, ignore_errors=True)


def collect_incident(vmid, kind, reason, lines, base=None, runner=run, clock=time.monotonic, budget=BUDGET,
                     sysfs=None, sock_dir=SOCK_DIR, stamp=None):
    """Write the incident folder for a VM that is about to be stopped and return its path.

    *kind* is panic, hang-restart or hang-shutdown, *reason* the watchdog's plain sentence, *lines* the last
    serial lines. Each part is skipped (and listed under "skipped") once the budget is used up."""
    started = clock()
    deadline = started + budget
    root = os.path.join(vm_dir(vmid, base), 'incidents')
    path = os.path.join(root, '%s-%s' % (stamp or now_stamp(), kind))
    os.makedirs(path, exist_ok=True)
    done, skipped = [], []

    def left():
        return deadline - clock()

    def put(name, content, binary=False):
        with open(os.path.join(path, name), 'wb') as f:
            f.write(content if binary else _cap(content))
        done.append(name)

    def part(name, fn, need=0.3):
        if left() < need:
            skipped.append(name + ': out of time')
            return
        try:
            fn()
        except Exception as exc:  # a failed read must never keep the VM from being recovered
            skipped.append('%s: %s' % (name, str(exc)[:120]))

    part('panic.txt', lambda: put('panic.txt', panic_text(lines)))
    part('serial-tail.txt', lambda: put('serial-tail.txt', tail_bytes(os.path.join(vm_dir(vmid, base), 'serial.log'),
                                                                        SERIAL_TAIL), binary=True))
    part('igpu-registers.txt', lambda: put('igpu-registers.txt', format_registers(read_igpu_registers(sysfs))))
    part('host.txt', lambda: put('host.txt', host_snapshot(sysfs)))
    conf = {}

    def config():
        rc, out = runner(['qm', 'config', str(vmid)], max(0.5, min(2.0, left())))
        if rc != 0:
            raise OSError('qm config failed')
        conf['text'] = out
        put('vm.conf', redact_config(out))

    part('vm.conf', config, need=1.0)

    def dmesg():
        rc, out = runner(['dmesg'], max(0.5, min(2.0, left())))
        if rc != 0:
            raise OSError('dmesg failed')
        put('dmesg.txt', '\n'.join([ln for ln in out.splitlines() if KERNEL_RE.search(ln)][-300:]) + '\n')

    part('dmesg.txt', dmesg, need=1.0)
    state = read_versions(vmid)
    part('versions.json', lambda: put('versions.json', json.dumps(
        dict(state, macos_detected=macos_of(conf.get('text', ''))), indent=2, sort_keys=True) + '\n'))
    # last: if QEMU's monitor is stuck behind a vCPU that cannot be kicked, the wait costs only what is left of
    # the budget and everything above is already on disk
    part('vcpu-registers.txt', lambda: put('vcpu-registers.txt', qmp_hmp(vmid, 'info registers -a', min(4.0, left()),
                                                                          sock_dir, clock)), need=1.0)
    meta = {'vmid': int(vmid), 'kind': kind, 'reason': reason, 'time': stamp or now_stamp(),
            'seconds': round(clock() - started, 2), 'files': done, 'skipped': skipped}
    put('incident.json', json.dumps(meta, indent=2, sort_keys=True) + '\n')
    prune(root)
    return path


# ── report bundle ────────────────────────────────────────────────────

def list_incidents(vmid, base=None):
    root = os.path.join(vm_dir(vmid, base), 'incidents')
    try:
        return [os.path.join(root, n) for n in sorted(os.listdir(root))]
    except OSError:
        return []


def _first_line(path):
    text = (_read(path) or '').strip()
    return text.splitlines()[0][:200] if text else ''


def summarize(vmid, state, incidents, host_text, config_text, bundle=''):
    """The plain text a user can paste into an issue."""
    out = ['osx-proxmox-next iGPU report for VM %s' % vmid, 'macOS: %s' % (macos_of(config_text) if config_text else 'unknown')]
    out += [ln for ln in host_text.splitlines() if ln.startswith(('cpu:', 'igpu device id:'))]
    versions = describe_versions(state)
    out.append('Installed: ' + ('; '.join(versions) if versions else 'unknown (this VM was set up before versions were recorded)'))
    out.append('Crashes recorded: %d' % len(incidents))
    for path in incidents[-REPORT_INCIDENTS:][::-1]:
        meta = {}
        try:
            with open(os.path.join(path, 'incident.json')) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            pass
        out.append('  %s  %s: %s' % (os.path.basename(path), meta.get('kind', '?'), meta.get('reason', '')))
        panic = next((ln.strip() for ln in (_read(os.path.join(path, 'panic.txt')) or '').splitlines()
                      if PANIC_RE.search(ln)), '')
        if panic:
            out.append('    ' + panic[:240])
    if not incidents:
        out.append('No crash was recorded yet. The bundle still has the versions, the VM config and the end of the '
                   'serial log.')
    out += ['', 'Bundle: %s' % bundle,
            'Look through it before you share it: the VM config has MAC addresses, serial numbers, the OSK and keys '
            'blanked, but the serial log and the host kernel messages are copied as they are. Nothing was sent anywhere.',
            'To report it, open an issue at %s, paste this summary and attach the file.' % ISSUES_URL]
    return '\n'.join(out) + '\n'


def _add(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size, info.mtime = len(data), int(time.time())
    tar.addfile(info, io.BytesIO(data))


def build_report(vmid, out_dir=None, base=None, runner=run, sysfs=None, stamp=None):
    """Pack the latest incidents, versions, config and serial tail into a tar.gz. Returns (path, summary)."""
    vmid = int(vmid)
    stamp = stamp or now_stamp()
    out_dir = out_dir or vm_dir(vmid, base)
    os.makedirs(out_dir, exist_ok=True)
    top = 'igpu-report-%d-%s' % (vmid, stamp)
    path = os.path.join(out_dir, top + '.tar.gz')
    state = read_versions(vmid)
    incidents = list_incidents(vmid, base)
    rc, out = runner(['qm', 'config', str(vmid)], 5)
    config_text = out if rc == 0 else ''
    if not config_text and incidents:
        config_text = _read(os.path.join(incidents[-1], 'vm.conf')) or ''
    host_text = host_snapshot(sysfs)
    with tarfile.open(path, 'w:gz') as tar:
        _add(tar, top + '/versions.json', (json.dumps(state, indent=2, sort_keys=True) + '\n').encode())
        _add(tar, top + '/vm.conf', redact_config(config_text).encode())
        _add(tar, top + '/host.txt', host_text.encode())
        _add(tar, top + '/serial-tail.txt', tail_bytes(os.path.join(vm_dir(vmid, base), 'serial.log'), REPORT_SERIAL_TAIL))
        for inc in incidents[-REPORT_INCIDENTS:]:
            for fn in sorted(os.listdir(inc)):
                with open(os.path.join(inc, fn), 'rb') as f:
                    _add(tar, '%s/incidents/%s/%s' % (top, os.path.basename(inc), fn), f.read())
        summary = summarize(vmid, state, incidents, host_text, config_text, path)
        _add(tar, top + '/summary.txt', summary.encode())
    return path, summary


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ''
    if cmd in ('report', 'incident', 'versions') and len(argv) >= 3 and argv[2].isdigit():
        vmid = int(argv[2])
        if cmd == 'report':
            out = argv[4] if len(argv) == 5 and argv[3] == '--out' else None
            path, summary = build_report(vmid, out)
            sys.stdout.write(summary)
            return 0
        if cmd == 'incident':
            sys.stdout.write(collect_incident(vmid, 'manual', ' '.join(argv[3:]) or 'taken by hand', []) + '\n')
            return 0
        sys.stdout.write('\n'.join(describe_versions(read_versions(vmid))) + '\n')
        return 0
    sys.stderr.write('usage: igpu_diag.py report <vmid> [--out DIR] | incident <vmid> [reason] | versions <vmid>\n')
    return 64


if __name__ == '__main__':
    sys.exit(main(sys.argv))
