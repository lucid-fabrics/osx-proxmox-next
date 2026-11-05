"""Prepare (or restore) a VM's OpenCore config for the NullMoth NVIDIA driver.

Usage: oc_patch.py <EFI/OC directory> attach|detach|restore

The Python CLI/TUI and the standalone bash installer run this exact text (the
bash script embeds it verbatim; tests/test_nullmoth_parity.py diffs the two),
so both entry points edit the disk identically. Standard library only.

attach  Backs up config.plist to config.plist.pre-nullmoth (kept if it already
        exists), adds AMFIPass.kext right after Lilu, appends the NullMoth
        boot-args, blocks IONDRVSupport and makes sure the SMBIOS model is
        MacPro7,1. Environment AMFIPASS_ZIP names the pinned, already
        verified AMFIPass release zip to unpack into Kexts/.
detach  Puts config.plist.pre-nullmoth back byte for byte and removes it,
        together with the AMFIPass.kext directory. Refuses (exit 3) when there is
        no backup: a VM that was never attached keeps whatever it has.
restore Like detach, but succeeds when there is nothing to restore (the attach
        never got that far, or an earlier undo already finished).

attach stores the SHA-256 of the config it wrote next to the backup. detach and
restore put the backup back only while config.plist still has that hash. When
the file changed since (the NullMoth 1401 app edits it, or the user did), the
changed config and the kext stay, the backup stays, and a KEPT line says so.
The config is always written to a temporary file and renamed into place.

Environment AMFIPASS_SHA256, when set, is checked against the zip right before
it is unpacked.

Exit codes: 0 ok, 2 refused (precondition not met, nothing changed),
3 nothing to restore.
"""
import glob
import hashlib
import os
import plistlib
import shutil
import sys
import time
import zipfile

BACKUP = 'config.plist.pre-nullmoth'
PATCHED_SHA = BACKUP + '.patched-sha256'
NVRAM_GUID = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
BOOT_ARGS = (
    'nvfb=1', 'nvaccel=1', 'nvfbheads=4', '-nvkmsnosmooth',
    'amfi_get_out_of_my_way=0x1', 'amfi=0x80', 'ipc_control_port_options=0',
    'gfxrst=4',
)
REMOVE_ARGS = ('nv_disable=1', '-wegnoegpu')
CSR_REQUIRED = 0x0A43
IONDRV = 'com.apple.iokit.IONDRVSupport'
KEXT = 'AMFIPass.kext'
SMBIOS_MODEL = 'MacPro7,1'


def refuse(message, code=2):
    sys.stderr.write('ERROR: ' + message + '\n')
    sys.exit(code)


def merge_boot_args(current):
    """Append the NullMoth args, drop the disabling ones, never duplicate."""
    new_keys = set(a.split('=', 1)[0] for a in BOOT_ARGS)
    kept = [
        t for t in current.split()
        if t not in REMOVE_ARGS and t.split('=', 1)[0] not in new_keys
    ]
    return ' '.join(kept + list(BOOT_ARGS))


def check_preconditions(cfg):
    add = cfg.get('NVRAM', {}).get('Add', {}).get(NVRAM_GUID, {})
    csr = add.get('csr-active-config', b'')
    value = int.from_bytes(bytes(csr), 'little')
    if value & CSR_REQUIRED != CSR_REQUIRED:
        refuse('csr-active-config is 0x%04X but NullMoth needs 0x%04X set. '
               'This tool\'s default is 0x0F67; was the OpenCore disk edited?'
               % (value, CSR_REQUIRED))
    model = cfg.get('Misc', {}).get('Security', {}).get('SecureBootModel')
    if model != 'Disabled':
        refuse('SecureBootModel is %r but NullMoth needs Disabled.' % (model,))


def add_kext_entry(cfg):
    kexts = cfg.setdefault('Kernel', {}).setdefault('Add', [])
    kexts[:] = [k for k in kexts if k.get('BundlePath') != KEXT]
    pos = 0
    for i, k in enumerate(kexts):
        if k.get('BundlePath') == 'Lilu.kext':
            pos = i + 1
    kexts.insert(pos, {
        'Arch': 'Any', 'BundlePath': KEXT,
        'Comment': 'NullMoth: AMFI bypass', 'Enabled': True,
        'ExecutablePath': 'Contents/MacOS/AMFIPass', 'MaxKernel': '',
        'MinKernel': '', 'PlistPath': 'Contents/Info.plist',
    })


def add_block_entry(cfg):
    block = cfg.setdefault('Kernel', {}).setdefault('Block', [])
    block[:] = [b for b in block if b.get('Identifier') != IONDRV]
    block.append({
        'Arch': 'Any', 'Comment': 'NullMoth: keep IONDRVSupport out',
        'Enabled': True, 'Identifier': IONDRV, 'MaxKernel': '',
        'MinKernel': '', 'Strategy': 'Exclude',
    })


def file_sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def unpack_kext(zip_path, oc_dir):
    dest = os.path.join(oc_dir, 'Kexts')
    expected = os.environ.get('AMFIPASS_SHA256', '')
    if expected and file_sha(zip_path) != expected:
        refuse('the AMFIPass zip does not match its pinned SHA-256; it changed after it was verified.')
    with zipfile.ZipFile(zip_path) as z:
        members = [i for i in z.infolist()
                   if i.filename.startswith(KEXT + '/') and not i.is_dir()]
        for info in members:
            if info.filename.startswith('/') or '..' in info.filename.split('/'):
                refuse('unsafe archive member: ' + info.filename)
        if not members:
            refuse('no ' + KEXT + ' inside ' + zip_path)
        # Validated first: a refused archive must not remove a working kext.
        shutil.rmtree(os.path.join(dest, KEXT), ignore_errors=True)
        for info in members:
            out = os.path.join(dest, info.filename)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, 'wb') as dst:
                shutil.copyfileobj(src, dst)


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


def attach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    check_preconditions(cfg)
    zip_path = os.environ.get('AMFIPASS_ZIP', '')
    if zip_path:
        unpack_kext(zip_path, oc_dir)
    elif not os.path.isdir(os.path.join(oc_dir, 'Kexts', KEXT)):
        refuse('AMFIPASS_ZIP is not set and ' + KEXT + ' is not on the disk.')
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
    add_kext_entry(cfg)
    add_block_entry(cfg)
    nv = cfg.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault(NVRAM_GUID, {})
    nv['boot-args'] = merge_boot_args(nv.get('boot-args', ''))
    generic = cfg.setdefault('PlatformInfo', {}).setdefault('Generic', {})
    if generic.get('SystemProductName') != SMBIOS_MODEL:
        generic['SystemProductName'] = SMBIOS_MODEL
    write_plist(cfg, path)
    with open(os.path.join(oc_dir, PATCHED_SHA), 'w') as f:
        f.write(file_sha(path) + '\n')
    sys.stderr.write('NullMoth OpenCore changes applied (backup: ' + BACKUP + ')\n')


def detach(oc_dir, soft=False):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    stamp = os.path.join(oc_dir, PATCHED_SHA)
    if not os.path.exists(backup):
        if soft and (not os.path.isdir(os.path.join(oc_dir, 'Kexts', KEXT))
                     or glob.glob(backup + '.kept-*')):
            sys.stderr.write('no ' + BACKUP + ' found, nothing to restore\n')
            return
        refuse('no ' + BACKUP + ' found, nothing to restore.', 3)
    if os.path.exists(stamp):
        with open(stamp) as f:
            wrote = f.read().strip()
        if os.path.exists(path) and file_sha(path) != wrote:
            kept = backup + '.kept-' + time.strftime('%Y%m%d%H%M%S')
            os.replace(backup, kept)
            os.remove(stamp)
            sys.stderr.write(
                'KEPT: config.plist was changed after NullMoth was prepared, so your version stays (and so does '
                + KEXT + ', which it may use). The original is on the disk as ' + os.path.basename(kept) + '.\n')
            return
    os.replace(backup, path)
    if os.path.exists(stamp):
        os.remove(stamp)
    shutil.rmtree(os.path.join(oc_dir, 'Kexts', KEXT), ignore_errors=True)
    sys.stderr.write('OpenCore config restored from ' + BACKUP + '\n')


def main(argv):
    if len(argv) != 3 or argv[2] not in ('attach', 'detach', 'restore'):
        refuse('usage: oc_patch.py <EFI/OC dir> attach|detach|restore', 64)
    if argv[2] == 'attach':
        attach(argv[1])
    else:
        detach(argv[1], soft=argv[2] == 'restore')


if __name__ == '__main__':
    main(sys.argv)
