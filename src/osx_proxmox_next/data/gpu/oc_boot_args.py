"""Add boot-args to (or restore) a VM's OpenCore config for a passed-through GPU.

Usage: oc_boot_args.py <EFI/OC directory> attach|detach

The Python CLI/TUI and the standalone bash installer run this exact text (the
bash script embeds it verbatim; tests/test_gpu_parity.py diffs the two).
Standard library only.

attach  Backs up config.plist to config.plist.pre-gpu (kept if it already
        exists) and appends the boot-args named in the environment variable
        GPU_BOOT_ARGS (space separated; an arg that is already there is
        replaced, never duplicated). Refuses when config.plist.pre-nullmoth
        exists: the NVIDIA profile owns the config then.
detach  Puts config.plist.pre-gpu back byte for byte and removes it. With no backup
        it does nothing and succeeds, so an interrupted undo can be repeated.

Exit codes: 0 ok, 2 refused (nothing changed).
"""
import os
import plistlib
import shutil
import sys

BACKUP = 'config.plist.pre-gpu'
OTHER_BACKUP = 'config.plist.pre-nullmoth'
NVRAM_GUID = '7C436110-AB2A-4BBB-A880-FE41995C9F82'


def refuse(message, code=2):
    sys.stderr.write('ERROR: ' + message + '\n')
    sys.exit(code)


def merge_boot_args(current, wanted):
    keys = set(a.split('=', 1)[0] for a in wanted)
    kept = [t for t in current.split() if t.split('=', 1)[0] not in keys]
    return ' '.join(kept + list(wanted))


def write_plist(cfg, path):
    with open(path, 'wb') as f:
        plistlib.dump(cfg, f)
    # plistlib emits self-closing tags that OpenCore's OcXmlLib rejects
    with open(path, 'rb') as f:
        data = f.read()
    for tag in ('array', 'dict', 'data'):
        data = data.replace(('<%s/>' % tag).encode(), ('<%s></%s>' % (tag, tag)).encode())
    with open(path, 'wb') as f:
        f.write(data)


def attach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    if os.path.exists(os.path.join(oc_dir, OTHER_BACKUP)):
        refuse('the NVIDIA (NullMoth) setup is active on this disk; run nvidia-detach first.')
    wanted = os.environ.get('GPU_BOOT_ARGS', '').split()
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
    nv = cfg.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault(NVRAM_GUID, {})
    nv['boot-args'] = merge_boot_args(nv.get('boot-args', ''), wanted)
    write_plist(cfg, path)
    sys.stderr.write('GPU boot-args applied (backup: ' + BACKUP + ')\n')


def detach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    if not os.path.exists(backup):
        # already restored (an earlier undo got this far): nothing left to do
        sys.stderr.write('no ' + BACKUP + ' found, nothing to restore\n')
        return
    os.replace(backup, path)
    sys.stderr.write('OpenCore config restored from ' + BACKUP + '\n')


def main(argv):
    if len(argv) != 3 or argv[2] not in ('attach', 'detach'):
        refuse('usage: oc_boot_args.py <EFI/OC dir> attach|detach', 64)
    (attach if argv[2] == 'attach' else detach)(argv[1])


if __name__ == '__main__':
    main(sys.argv)
