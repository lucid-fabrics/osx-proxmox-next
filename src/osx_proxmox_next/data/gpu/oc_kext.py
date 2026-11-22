"""Read or replace kext bundles on a VM's OpenCore disk, for "update the driver".

Usage: oc_kext.py <EFI/OC directory> versions <Name.kext>...
       oc_kext.py <EFI/OC directory> replace <Name.kext> <zip> <sha256>

The Python CLI/TUI and the standalone bash installer run this exact text (the bash script embeds it verbatim;
tests/test_driver_parity.py diffs the two). Standard library only.

versions  Prints a JSON object {bundle: CFBundleShortVersionString, or "" when the kext or the version is missing}.
          Read only.
replace   Checks the zip against its SHA-256, unpacks the bundle into Kexts/ (found by name, also inside one
          wrapping folder) in place of the old copy and prints `VERSION <new version>`. config.plist is left alone, byte for
          byte, unless its entry for the bundle names an executable that the new bundle does not have: then only that
          ExecutablePath is corrected. A bundle that config.plist does not list is reported and not added.

Exit codes: 0 ok, 2 refused (nothing changed), 64 usage.
"""
import hashlib
import json
import os
import plistlib
import shutil
import sys
import zipfile


def refuse(message, code=2):
    sys.stderr.write('ERROR: ' + message + '\n')
    sys.exit(code)


def file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def plain_name(bundle):
    return bool(bundle) and bundle.endswith('.kext') and '/' not in bundle and bundle not in ('.kext', '..kext')


def bundle_info(kexts_dir, bundle):
    try:
        with open(os.path.join(kexts_dir, bundle, 'Contents', 'Info.plist'), 'rb') as f:
            return plistlib.load(f)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}


def versions(oc_dir, bundles):
    kexts = os.path.join(oc_dir, 'Kexts')
    out = {}
    for bundle in bundles:
        if not plain_name(bundle):
            refuse('%r is not a kext name' % bundle, 64)
        out[bundle] = str(bundle_info(kexts, bundle).get('CFBundleShortVersionString') or '')
    sys.stdout.write(json.dumps(out, sort_keys=True) + '\n')


def find_bundle(names, bundle):
    """The path inside the zip that holds *bundle*: at most two folders deep, exactly one match, else None."""
    found = set()
    for name in names:
        parts = name.split('/')
        for i, part in enumerate(parts[:-1]):
            if i <= 2 and part == bundle:
                found.add('/'.join(parts[:i + 1]))
    return found.pop() if len(found) == 1 else None


def unpack(zip_path, bundle, tmp):
    with zipfile.ZipFile(zip_path) as z:
        prefix = find_bundle([i.filename for i in z.infolist()], bundle)
        if prefix is None:
            refuse('no %s inside %s' % (bundle, os.path.basename(zip_path)))
        count = 0
        for info in z.infolist():
            name = info.filename
            if info.is_dir() or not name.startswith(prefix + '/'):
                continue
            if name.startswith('/') or '..' in name.split('/'):
                refuse('unsafe path in the zip: %s' % name)
            out = os.path.join(tmp, name[len(prefix) + 1:])
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    if not count:
        refuse('no files for %s inside the zip' % bundle)


def sync_config(oc_dir, bundle, exe):
    """Correct the ExecutablePath of the bundle's entry when it names another file. Returns what was noted."""
    path = os.path.join(oc_dir, 'config.plist')
    try:
        with open(path, 'rb') as f:
            cfg = plistlib.load(f)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return 'config.plist could not be read, so it was not checked'
    for entry in cfg.get('Kernel', {}).get('Add', []):
        if entry.get('BundlePath') != bundle:
            continue
        want = 'Contents/MacOS/' + exe
        if entry.get('ExecutablePath') == want:
            return ''
        entry['ExecutablePath'] = want
        tmp = path + '.tmp'
        data = plistlib.dumps(cfg)
        for tag in ('array', 'dict', 'data'):
            data = data.replace(('<%s/>' % tag).encode(), ('<%s></%s>' % (tag, tag)).encode())
        with open(tmp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return 'the ExecutablePath of %s in config.plist was corrected to %s' % (bundle, want)
    return '%s is not listed in config.plist, so it was not added' % bundle


def replace(oc_dir, bundle, zip_path, expected):
    if not plain_name(bundle):
        refuse('%r is not a kext name' % bundle, 64)
    if not os.path.isfile(zip_path):
        refuse('the zip for %s is missing (%s)' % (bundle, zip_path))
    actual = file_sha(zip_path)
    if actual != expected:
        refuse('%s zip checksum mismatch: expected %s, got %s' % (bundle, expected, actual))
    kexts = os.path.join(oc_dir, 'Kexts')
    dest, tmp = os.path.join(kexts, bundle), os.path.join(kexts, bundle) + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    unpack(zip_path, bundle, tmp)
    try:
        with open(os.path.join(tmp, 'Contents', 'Info.plist'), 'rb') as f:
            info = plistlib.load(f)
    except (OSError, ValueError, plistlib.InvalidFileException):
        shutil.rmtree(tmp, ignore_errors=True)
        refuse('the %s in the zip has no readable Contents/Info.plist' % bundle)
    exe = info.get('CFBundleExecutable') or bundle[:-len('.kext')]
    if os.path.basename(exe) != exe or not os.path.exists(os.path.join(tmp, 'Contents', 'MacOS', exe)):
        shutil.rmtree(tmp, ignore_errors=True)
        refuse('the %s in the zip has no executable named %s' % (bundle, exe))
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    note = sync_config(oc_dir, bundle, exe)
    if note:
        sys.stderr.write('NOTE: ' + note + '\n')
    sys.stdout.write('VERSION ' + str(info.get('CFBundleShortVersionString') or '') + '\n')


def main(argv):
    if len(argv) >= 4 and argv[2] == 'versions':
        versions(argv[1], argv[3:])
    elif len(argv) == 6 and argv[2] == 'replace':
        replace(argv[1], argv[3], argv[4], argv[5])
    else:
        refuse('usage: oc_kext.py <EFI/OC dir> versions <Name.kext>... | replace <Name.kext> <zip> <sha256>', 64)


if __name__ == '__main__':
    main(sys.argv)
