"""Add boot-args to (or restore) a VM's OpenCore config for a passed-through GPU.

Usage: oc_boot_args.py <EFI/OC directory> attach|detach|restore

The Python CLI/TUI and the standalone bash installer run this exact text (the
bash script embeds it verbatim; tests/test_gpu_parity.py diffs the two).
Standard library only.

attach  Backs up config.plist to config.plist.pre-gpu (kept if it already
        exists) and appends the boot-args named in the environment variable
        GPU_BOOT_ARGS (space separated; an arg that is already there is
        replaced, never duplicated). Refuses when config.plist itself still carries
        the NVIDIA (NullMoth) changes (AMFIPass kext entry, IONDRVSupport Block entry
        or the NullMoth boot-args), whatever backup files exist.
detach  Puts config.plist.pre-gpu back byte for byte and removes it, but only while
        config.plist still has the SHA-256 attach stored next to the backup. When the
        file changed since, the changed config and the backup stay and a KEPT line says
        so. With no backup it does nothing and succeeds, so an interrupted undo can be
        repeated. (restore is the same as detach.)
The config is always written to a temporary file and renamed into place.

Exit codes: 0 ok, 2 refused (nothing changed).
"""
import hashlib
import os
import plistlib
import shutil
import sys
import time

BACKUP = 'config.plist.pre-gpu'
PATCHED_SHA = BACKUP + '.patched-sha256'
NULLMOTH_ARGS = ('nvfb', 'nvaccel', 'nvfbheads', '-nvkmsnosmooth')
IONDRV = 'com.apple.iokit.IONDRVSupport'
NVRAM_GUID = '7C436110-AB2A-4BBB-A880-FE41995C9F82'


def refuse(message, code=2):
    sys.stderr.write('ERROR: ' + message + '\n')
    sys.exit(code)


def merge_boot_args(current, wanted):
    keys = set(a.split('=', 1)[0] for a in wanted)
    kept = [t for t in current.split() if t.split('=', 1)[0] not in keys]
    return ' '.join(kept + list(wanted))


def file_sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_plist(cfg, path):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        plistlib.dump(cfg, f)
    # plistlib emits self-closing tags that OpenCore's OcXmlLib rejects
    with open(tmp, 'rb') as f:
        data = f.read()
    for tag in ('array', 'dict', 'data'):
        data = data.replace(('<%s/>' % tag).encode(), ('<%s></%s>' % (tag, tag)).encode())
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def nullmoth_leftovers(cfg):
    """What of the NVIDIA (NullMoth) setup config.plist still holds."""
    found = []
    if any(k.get('BundlePath') == 'AMFIPass.kext' for k in cfg.get('Kernel', {}).get('Add', [])):
        found.append('the AMFIPass kext entry')
    if any(b.get('Identifier') == IONDRV for b in cfg.get('Kernel', {}).get('Block', [])):
        found.append('the IONDRVSupport Block entry')
    args = cfg.get('NVRAM', {}).get('Add', {}).get(NVRAM_GUID, {}).get('boot-args', '')
    if any(t.split('=', 1)[0] in NULLMOTH_ARGS for t in args.split()):
        found.append('the NullMoth boot-args')
    return found


def attach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    wanted = os.environ.get('GPU_BOOT_ARGS', '').split()
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    left = nullmoth_leftovers(cfg)
    if left:
        refuse('config.plist still has the NVIDIA (NullMoth) changes: ' + ', '.join(left) + '. '
               'Run nvidia-detach first. If that keeps your config, put back the original from the '
               'config.plist.pre-nullmoth.kept-* file on the OpenCore disk, or remove those entries by hand.')
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
    nv = cfg.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault(NVRAM_GUID, {})
    nv['boot-args'] = merge_boot_args(nv.get('boot-args', ''), wanted)
    write_plist(cfg, path)
    with open(os.path.join(oc_dir, PATCHED_SHA), 'w') as f:
        f.write(file_sha(path) + '\n')
    sys.stderr.write('GPU boot-args applied (backup: ' + BACKUP + ')\n')


def detach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    if not os.path.exists(backup):
        # already restored (an earlier undo got this far): nothing left to do
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
                'KEPT: config.plist was changed after the boot-args were added, so your version stays. '
                'The original is on the disk as ' + os.path.basename(kept) + '.\n')
            return
        os.remove(stamp)
    os.replace(backup, path)
    sys.stderr.write('OpenCore config restored from ' + BACKUP + '\n')


def main(argv):
    if len(argv) != 3 or argv[2] not in ('attach', 'detach', 'restore'):
        refuse('usage: oc_boot_args.py <EFI/OC dir> attach|detach|restore', 64)
    (attach if argv[2] == 'attach' else detach)(argv[1])


if __name__ == '__main__':
    main(sys.argv)
