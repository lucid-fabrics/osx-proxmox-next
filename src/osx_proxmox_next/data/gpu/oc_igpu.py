"""Set up (or undo) a VM's OpenCore disk for Intel iGPU passthrough with WhateverXe.

Usage: oc_igpu.py <EFI/OC directory> attach|restore|fadt|stage|unstage|mark|serial|unserial

The Python CLI/TUI and the standalone bash installer run this exact text (the
bash script embeds it verbatim; tests/test_igpu_parity.py diffs the two).
Standard library only.

attach   Backs up config.plist to config.plist.pre-igpu (kept if it already exists), unpacks
         WhateverXe.kext and AMFIPass.kext from the two zips named in the environment
         (IGPU_WEX_ZIP / IGPU_WEX_SHA256 and IGPU_AMFI_ZIP / IGPU_AMFI_SHA256, each checked
         against its pinned SHA-256 first) into Kexts/, lists both right after Lilu, turns
         WhateverGreen off, sets ACPI > Quirks > FadtEnableReset and adds the boot-args
         -allow3d -disablegfxfirmware -wex-tgl-hold-render-wake. Running it twice changes
         nothing the second time. Refuses when config.plist still carries the NVIDIA
         (NullMoth) changes: the IONDRVSupport Block entry or the nvfb/nvaccel boot-args.
fadt     Only sets ACPI > Quirks > FadtEnableReset, with no backup. The i440fx machine type the iGPU
         needs hangs every guest restart without it, so a new VM gets it before the install starts.
stage    Makes the picker show only the recovery disk and wait for a key: Timeout 0 and ScanPolicy 0x201 (HFS+
         only, so the installed APFS macOS, which the picker would otherwise highlight as the startup disk,
         is hidden). Used right before the VM boots recovery to stage the graphics package.
unstage  Puts ScanPolicy back (the value saved by stage, else 0). The caller restores the picker timeout.
mark     Notes that the WhateverXe Graphics package is installed inside macOS. From then on restore keeps
         WhateverXe and AMFIPass in OpenCore: with the package installed and WhateverXe gone the guest hangs at boot.
serial   Adds the boot-arg serial=3 so the kernel console goes to the VM's serial port, where the host's power
         watchdog (igpu_power_watchdog.py) reads it. unserial removes it. Both keep the SHA-256 that restore
         compares in step, so undo still puts the original config back.
restore  Puts config.plist.pre-igpu back byte for byte and removes the two kexts, but only
         while config.plist still has the SHA-256 attach stored next to the backup. When the
         file changed since, the changed config and the backup stay and a KEPT line says so.
         With no backup it does nothing and succeeds, so an interrupted undo can be repeated.
The config is always written to a temporary file and renamed into place.

Exit codes: 0 ok, 2 refused (nothing changed), 64 usage.
"""
import hashlib
import os
import plistlib
import shutil
import sys
import time
import zipfile

BACKUP = 'config.plist.pre-igpu'
PATCHED_SHA = BACKUP + '.patched-sha256'
KEXTS = (('WhateverXe.kext', 'WhateverXe', 'IGPU_WEX'), ('AMFIPass.kext', 'AMFIPass', 'IGPU_AMFI'))
BOOT_ARGS = ('-allow3d', '-disablegfxfirmware', '-wex-tgl-hold-render-wake')
NVRAM_GUID = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
NULLMOTH_ARGS = ('nvfb', 'nvaccel', 'nvfbheads', '-nvkmsnosmooth')
IONDRV = 'com.apple.iokit.IONDRVSupport'


def refuse(message, code=2):
    sys.stderr.write('ERROR: ' + message + '\n')
    sys.exit(code)


def file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_plist(cfg, path):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        data = plistlib.dumps(cfg)
        # plistlib emits self-closing tags that OpenCore's OcXmlLib rejects
        for tag in ('array', 'dict', 'data'):
            data = data.replace(('<%s/>' % tag).encode(), ('<%s></%s>' % (tag, tag)).encode())
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def merge_boot_args(current, wanted):
    keys = set(a.split('=', 1)[0] for a in wanted)
    kept = [t for t in current.split() if t.split('=', 1)[0] not in keys]
    return ' '.join(kept + list(wanted))


def unpack_kext(zip_path, expected_sha, bundle, kexts_dir):
    """Unpack *bundle* from a verified zip into kexts_dir, replacing an older copy."""
    if not zip_path or not os.path.exists(zip_path):
        refuse('the %s zip is missing (%s)' % (bundle, zip_path or 'not set'))
    actual = file_sha(zip_path)
    if actual != expected_sha:
        refuse('%s zip checksum mismatch: expected %s, got %s' % (bundle, expected_sha, actual))
    dest = os.path.join(kexts_dir, bundle)
    tmp = dest + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            if info.is_dir() or not name.startswith(bundle + '/'):
                continue
            if name.startswith('/') or '..' in name.split('/'):
                refuse('unsafe path in the %s zip: %s' % (bundle, name))
            out = os.path.join(tmp, name[len(bundle) + 1:])
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    if count == 0:
        refuse('no %s inside the zip' % bundle)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)


def kext_entry(bundle, exe):
    return {'Arch': 'Any', 'BundlePath': bundle, 'Comment': 'iGPU passthrough', 'Enabled': True,
            'ExecutablePath': 'Contents/MacOS/' + exe, 'MaxKernel': '', 'MinKernel': '',
            'PlistPath': 'Contents/Info.plist'}


def nullmoth_leftovers(cfg):
    """What of the NVIDIA (NullMoth) setup config.plist still holds."""
    found = []
    if any(b.get('Identifier') == IONDRV for b in cfg.get('Kernel', {}).get('Block', [])):
        found.append('the IONDRVSupport Block entry')
    args = cfg.get('NVRAM', {}).get('Add', {}).get(NVRAM_GUID, {}).get('boot-args', '')
    if any(t.split('=', 1)[0] in NULLMOTH_ARGS for t in args.split()):
        found.append('the NullMoth boot-args')
    return found


def attach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    adds = cfg.get('Kernel', {}).get('Add', [])
    left = nullmoth_leftovers(cfg)
    if left:
        refuse('config.plist still has the NVIDIA (NullMoth) changes: ' + ', '.join(left) + '. '
               'Run nvidia-detach first.')
    kexts_dir = os.path.join(oc_dir, 'Kexts')
    for bundle, _exe, prefix in KEXTS:
        unpack_kext(os.environ.get(prefix + '_ZIP', ''), os.environ.get(prefix + '_SHA256', ''), bundle, kexts_dir)
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
    ours = [b for b, _e, _p in KEXTS]
    kexts = [k for k in adds if k.get('BundlePath') not in ours]
    pos = None
    for idx, k in enumerate(kexts):
        if k.get('BundlePath') == 'WhateverGreen.kext':
            k['Enabled'] = False
        if k.get('BundlePath') == 'Lilu.kext':
            pos = idx + 1
    if pos is None:
        refuse('config.plist has no Lilu.kext entry, so WhateverXe cannot be placed after it')
    for bundle, exe, _p in KEXTS:
        kexts.insert(pos, kext_entry(bundle, exe))
        pos += 1
    cfg['Kernel']['Add'] = kexts
    cfg.setdefault('ACPI', {}).setdefault('Quirks', {})['FadtEnableReset'] = True
    nv = cfg.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault(NVRAM_GUID, {})
    nv['boot-args'] = merge_boot_args(nv.get('boot-args', ''), BOOT_ARGS)
    write_plist(cfg, path)
    with open(os.path.join(oc_dir, PATCHED_SHA), 'w') as f:
        f.write(file_sha(path) + '\n')
    sys.stderr.write('iGPU OpenCore setup applied (backup: ' + BACKUP + ')\n')


def fadt(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    cfg.setdefault('ACPI', {}).setdefault('Quirks', {})['FadtEnableReset'] = True
    write_plist(cfg, path)
    sys.stderr.write('FadtEnableReset set\n')


STAGE_SCAN_POLICY = 0x201  # lock to file systems, allow HFS+ only
SCAN_SAVED = 'igpu-scanpolicy.orig'
PACKAGE_MARK = 'igpu-package-installed'


def stage(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    sec = cfg.setdefault('Misc', {}).setdefault('Security', {})
    saved = os.path.join(oc_dir, SCAN_SAVED)
    if not os.path.exists(saved) and sec.get('ScanPolicy', 0) != STAGE_SCAN_POLICY:
        with open(saved, 'w') as f:
            f.write(str(sec.get('ScanPolicy', 0)) + '\n')
    sec['ScanPolicy'] = STAGE_SCAN_POLICY
    cfg['Misc'].setdefault('Boot', {})['Timeout'] = 0
    write_plist(cfg, path)
    sys.stderr.write('picker shows recovery only and waits for a key\n')


def unstage(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    saved = os.path.join(oc_dir, SCAN_SAVED)
    sec = cfg.setdefault('Misc', {}).setdefault('Security', {})
    if sec.get('ScanPolicy') == STAGE_SCAN_POLICY:
        original = 0
        if os.path.exists(saved):
            with open(saved) as f:
                original = int(f.read().strip() or 0)
        sec['ScanPolicy'] = original
        write_plist(cfg, path)
    if os.path.exists(saved):
        os.remove(saved)
    sys.stderr.write('ScanPolicy restored\n')


def mark(oc_dir):
    with open(os.path.join(oc_dir, PACKAGE_MARK), 'w') as f:
        f.write('installed\n')
    sys.stderr.write('noted that the graphics package is installed\n')


SERIAL_ARG = 'serial=3'


def _set_serial(oc_dir, on):
    path = os.path.join(oc_dir, 'config.plist')
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    nv = cfg.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault(NVRAM_GUID, {})
    stamp = os.path.join(oc_dir, PATCHED_SHA)
    ours = False
    if os.path.exists(stamp):
        with open(stamp) as f:
            ours = f.read().strip() == file_sha(path)
    kept = [a for a in nv.get('boot-args', '').split() if a.split('=', 1)[0] != 'serial']
    nv['boot-args'] = ' '.join(kept + [SERIAL_ARG] if on else kept)
    write_plist(cfg, path)
    if ours:
        with open(stamp, 'w') as f:
            f.write(file_sha(path) + '\n')
    sys.stderr.write(('kernel console on the serial port (' + SERIAL_ARG + ')\n') if on
                     else 'kernel console back on the screen only\n')


def serial(oc_dir):
    _set_serial(oc_dir, True)


def unserial(oc_dir):
    _set_serial(oc_dir, False)


def restore(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    if os.path.exists(os.path.join(oc_dir, PACKAGE_MARK)):
        sys.stderr.write(
            'KEPT: the graphics package is installed inside macOS, so WhateverXe and AMFIPass stay in OpenCore. '
            'Without WhateverXe that package makes the guest hang at boot. Remove the package inside macOS first '
            'if you want the original OpenCore config back.\n')
        return
    backup = os.path.join(oc_dir, BACKUP)
    if not os.path.exists(backup):
        sys.stderr.write('no ' + BACKUP + ' found, nothing to restore\n')
        return
    stamp = os.path.join(oc_dir, PATCHED_SHA)
    if os.path.exists(stamp):
        with open(stamp) as f:
            wrote = f.read().strip()
        if os.path.exists(path) and file_sha(path) != wrote:
            kept = backup + '.kept-' + time.strftime('%Y%m%d%H%M%S')
            os.replace(backup, kept)
            os.remove(stamp)
            sys.stderr.write(
                'KEPT: config.plist was changed after the iGPU setup was added, so your version stays. '
                'The original is on the disk as ' + os.path.basename(kept) + '.\n')
            return
        os.remove(stamp)
    os.replace(backup, path)
    for bundle, _exe, _p in KEXTS:
        shutil.rmtree(os.path.join(oc_dir, 'Kexts', bundle), ignore_errors=True)
    sys.stderr.write('OpenCore config restored from ' + BACKUP + '\n')


def main(argv):
    actions = {'attach': attach, 'restore': restore, 'fadt': fadt, 'stage': stage, 'unstage': unstage, 'mark': mark,
               'serial': serial, 'unserial': unserial}
    if len(argv) != 3 or argv[2] not in actions:
        refuse('usage: oc_igpu.py <EFI/OC dir> attach|restore|fadt|stage|unstage|mark|serial|unserial', 64)
    actions[argv[2]](argv[1])


if __name__ == '__main__':
    main(sys.argv)
