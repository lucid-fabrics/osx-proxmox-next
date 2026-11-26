#!/usr/bin/env python3
# M3: build a LocalPolicy for BootPolicy's hacktivate scheme once the pinned root
# certificate's public point is patched to the hackkey's public point (patch 1 in
# the lab's verifier findings). The two chain certificates use hackkey, the
# embedded root's exact subject DER as the intermediate issuer, and a verbatim
# copy of the golden leaf's Apple Img4 extension.
#
# SPDX-License-Identifier: GPL-2.0-or-later
# usage: mkpolicy-hack.py --template T.img4 --vuid UUID --nsih HEX --nonce HEX --out FILE [--kdk PATH]
#
# hackkey.pem (in this directory) is a P-384 key made for these stand-in policies, not a secret:
# the patched kext verifies against its public point. The template and the KDK are Apple objects
# from the machine that made the golden policy; they stay on the runner and out of this repository.
import argparse
import datetime
import hashlib
import importlib.util
import os
import sys

sys.path.append(os.path.expanduser("~/.local/lib/python3.14/site-packages"))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.extensions import UnrecognizedExtension  # noqa: E402
from cryptography.x509.name import _ASN1Type  # noqa: E402
from cryptography.x509.oid import NameOID, ObjectIdentifier  # noqa: E402

EXT_OID = ObjectIdentifier("1.2.840.113635.100.6.1.15")
HERE = os.path.dirname(os.path.abspath(__file__))
KEXT = os.path.join("KDK_27.0_26A428.kdk", "System", "Library", "Extensions",
                    "AppleVPBootPolicy.kext", "Contents", "MacOS", "AppleVPBootPolicy")
KDK = os.environ.get("SNAPINTOSH_POLICY_KDK", os.path.join(HERE, "kdk", KEXT))
ANCHOR_OFF, ANCHOR_LEN = 0xB390, 610
ROOT_POINT = bytes.fromhex(
    "04668902cd8adfcc1cb13298dca0b5745d62f67657dba329b1bbf173c7b3cd797917"
    "adc5f6c07b4494dcb38a434c6fa66efb4540c0447745c8dcd14a42b14d8da205418e"
    "6a9070970cdf665526254e0945713d01347060d72829f55cacbd011677")


def load_mkp(path):
    spec = importlib.util.spec_from_file_location("mkp", path)
    mod = importlib.util.module_from_spec(spec)
    src = open(spec.origin).read().replace("\nmain()", "\n")
    exec(compile(src, spec.origin, "exec"), mod.__dict__)
    return mod


def cert_name(value):
    return x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, value,
                           _ASN1Type.PrintableString),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Apple, Inc.",
                           _ASN1Type.PrintableString),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California",
                           _ASN1Type.PrintableString),
    ])


def make_cert(key, subject, issuer, serial, ext):
    start = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2035, 1, 1, tzinfo=datetime.timezone.utc)
    return (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key()).serial_number(serial)
            .not_valid_before(start).not_valid_after(end)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                           critical=True)
            .add_extension(UnrecognizedExtension(EXT_OID, ext), critical=True)
            .sign(key, hashes.SHA384()))


def patched_anchor(point):
    raw = open(KDK, "rb").read()
    o = int.from_bytes(raw[16:20], "big")
    der = bytearray(raw[o + ANCHOR_OFF:o + ANCHOR_OFF + ANCHOR_LEN])
    assert der.count(ROOT_POINT) == 1, der.count(ROOT_POINT)
    return x509.load_der_x509_certificate(bytes(der).replace(ROOT_POINT, point))


def golden_ext(tpl, mkp):
    tag, cs, ce, _ = mkp.tlv(tpl, 0)
    a0 = next(e for e in mkp.elems(tpl[cs:ce]) if e[0] == b"\xa0")
    kids = mkp.elems(mkp.split(a0[1])[1])
    gold = [x509.load_der_x509_certificate(mkp.encode(t, c))
            for t, c in mkp.elems(mkp.split(mkp.encode(*kids[4]))[1])]
    return gold[1].extensions.get_extension_for_oid(EXT_OID).value.value


def verify(inter, leaf, sig, signed, anchor):
    anchor.public_key().verify(inter.signature, inter.tbs_certificate_bytes,
                               ec.ECDSA(hashes.SHA384()))
    inter.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes,
                              ec.ECDSA(hashes.SHA384()))
    leaf.public_key().verify(sig, signed, ec.ECDSA(hashes.SHA384()))
    assert inter.issuer.public_bytes() == anchor.subject.public_bytes()
    assert leaf.issuer.public_bytes() == inter.subject.public_bytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vuid", required=True)
    ap.add_argument("--nsih", required=True)
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--rpnh", help="recoveryOS policy nonce hash (48 bytes)")
    ap.add_argument("--spih", help="Cryptex1 splat manifest SHA-384 (48 bytes)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--template", default=os.environ.get("SNAPINTOSH_POLICY_TEMPLATE"),
                    help="a golden LocalPolicy img4 (an Apple object; keep it on the runner)")
    ap.add_argument("--key", default=os.path.join(HERE, "hackkey.pem"))
    ap.add_argument("--point", help="hex public point; default: the --key's own point")
    ap.add_argument("--kdk", help="AppleVPBootPolicy kext binary from the KDK (default: $SNAPINTOSH_POLICY_KDK)")
    a = ap.parse_args()
    if not a.template:
        ap.error("--template is required (or set SNAPINTOSH_POLICY_TEMPLATE)")
    global KDK
    if a.kdk:
        KDK = a.kdk
    if not os.path.isfile(KDK):
        ap.error(f"no AppleVPBootPolicy kext at {KDK}; pass --kdk or set SNAPINTOSH_POLICY_KDK")

    mkp = load_mkp(os.path.join(HERE, "mkpolicy-sep.py"))
    tpl = open(a.template, "rb").read()
    ext = bytes(golden_ext(tpl, mkp))
    key = serialization.load_pem_private_key(open(a.key, "rb").read(), None)
    point = (bytes.fromhex(open(a.point).read().strip()) if a.point
             else key.public_key().public_bytes(serialization.Encoding.X962,
                                                serialization.PublicFormat.UncompressedPoint))
    anchor = patched_anchor(point)

    inter = make_cert(key,
                      cert_name("Hacktivation secp384r1 Intermediate "
                                "Certificate"),
                      cert_name("img4 test secp384r1 Root Certificate "
                                "Authority"), 0x2000, ext)
    leaf = make_cert(key,
                     cert_name("Hacktivation secp384r1 Leaf Certificate"),
                     cert_name("Hacktivation secp384r1 Intermediate "
                               "Certificate"), 0x2001, ext)
    der = serialization.Encoding.DER
    chain = mkp.encode(mkp.SEQ,
                       inter.public_bytes(der) + leaf.public_bytes(der))

    nonce = bytes.fromhex(a.nonce)
    if len(nonce) != 48:
        nonce = hashlib.sha384(nonce).digest()
    overrides = {b"lpnh": nonce, b"nsih": bytes.fromhex(a.nsih),
                 b"vuid": bytes.fromhex(a.vuid.replace("-", ""))}
    if a.rpnh:
        overrides[b"rpnh"] = bytes.fromhex(a.rpnh)
    if a.spih:
        overrides[b"spih"] = bytes.fromhex(a.spih)
    signed = mkp.signed_manb(tpl, overrides)
    sig = key.sign(signed, ec.ECDSA(hashes.SHA384()))
    verify(inter, leaf, sig, signed, anchor)
    im4m = mkp.encode(mkp.SEQ, mkp.encode(b"\x16", b"IM4M")
                      + mkp.encode(b"\x02", b"\x00") + signed
                      + mkp.encode(b"\x04", sig) + chain)
    img4 = mkp.encode(mkp.SEQ, mkp.encode(b"\x16", b"IMG4") + mkp.PAYLOAD
                      + mkp.encode(b"\xa0", im4m))
    open(a.out, "wb").write(img4)
    print("wrote", a.out, len(img4), "bytes, chain verified")


main()
