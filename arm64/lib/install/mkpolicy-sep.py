#!/usr/bin/env python3
# M3: build a macOS LocalPolicy whose MANB is signed with our own P-384 chain,
# mirroring a Mac's "Hacktivation secp384r1" policy (an intermediate and a leaf
# certificate, hrlp true). Properties, tag bytes and the lpol element come
# from a template policy; vuid, nsih and lpnh are replaced.
#
# SPDX-License-Identifier: GPL-2.0-or-later
# usage: mkpolicy-sep.py --template T.img4 --vuid UUID --nsih HEX \
#        --nonce HEX --out OUT.img4 [--keydir DIR]
import argparse
import datetime
import hashlib
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

HERE = os.path.dirname(os.path.abspath(__file__))

PAYLOAD = bytes.fromhex("30141604494d345016046c706f6c1603312e30040100")
NAMES = ("BORD", "CEPO", "CHIP", "CPRO", "CSEC", "ECID", "SDOM", "hrlp",
         "kuid", "lobo", "love", "lpnh", "nsih", "rpnh", "spih", "stng", "vuid")
SET, SEQ = b"\x31", b"\x30"


def tlv(b, o):
    t0 = o
    o += 1
    if b[t0] & 0x1F == 0x1F:
        while b[o] & 0x80:
            o += 1
        o += 1
    tag = b[t0:o]
    ln = b[o]
    o += 1
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(b[o:o + n], "big")
        o += n
    return tag, o, o + ln, o + ln


def enc_len(n):
    if n < 0x80:
        return bytes([n])
    ln = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(ln)]) + ln


def encode(tag, content):
    return tag + enc_len(len(content)) + content


def split(el):
    """(tag, content) of one DER element."""
    tag, cs, ce, _ = tlv(el, 0)
    return tag, el[cs:ce]


def elems(raw):
    """[(tag, content)] of a byte string of consecutive elements."""
    out, o = [], 0
    while o < len(raw):
        tag, cs, ce, _ = tlv(raw, o)
        out.append((tag, raw[cs:ce]))
        o = ce
    return out


def props_of(raw):
    """{name: DER} of the named elements in a SET's content."""
    out = {}
    for tag, content in elems(raw):
        _, seq = split(content)
        name = elems(seq)[0][1].decode()
        out[name] = encode(tag, content)
    return out


def valued(element, value):
    """The template property with its value element replaced."""
    tag, content = split(element)
    seq_tag, inner = split(content)
    kids = elems(inner)
    return encode(tag, encode(seq_tag, encode(*kids[0])
                              + encode(b"\x04", value)))


def named(wrapper_tag, seq_tag, name, body):
    """A named Image4 element: [tag] SEQ { 'name', body }."""
    return encode(wrapper_tag, encode(seq_tag, encode(b"\x16", name.encode())
                                      + body))


def cn(value):
    return x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Apple, Inc."),
        x509.NameAttribute(NameOID.COMMON_NAME, value),
    ])


def make_keys(path):
    os.makedirs(path, exist_ok=True)
    leaf_key = ec.generate_private_key(ec.SECP384R1())
    root_key = ec.generate_private_key(ec.SECP384R1())
    utc = datetime.timezone.utc
    start = datetime.datetime(2020, 1, 1, tzinfo=utc)
    end = datetime.datetime(2035, 1, 1, tzinfo=utc)

    def build(subject, issuer, key, serial, ca):
        b = (x509.CertificateBuilder().subject_name(cn(subject))
             .issuer_name(cn(issuer)).public_key(key.public_key())
             .serial_number(serial).not_valid_before(start)
             .not_valid_after(end))
        if ca:
            b = b.add_extension(x509.BasicConstraints(ca=True, path_length=None),
                                critical=True)
        return b

    build("img4 test secp384r1 Root Certificate Authority",
          "img4 test secp384r1 Root Certificate Authority",
          root_key, 0x1000, True).sign(root_key, hashes.SHA384())
    inter = build("Hacktivation secp384r1 Intermediate Certificate",
                  "img4 test secp384r1 Root Certificate Authority",
                  leaf_key, 0x1001, True).sign(root_key, hashes.SHA384())
    leaf = build("Hacktivation secp384r1 Leaf Certificate",
                 "Hacktivation secp384r1 Intermediate Certificate",
                 leaf_key, 0x1002, False).sign(leaf_key, hashes.SHA384())
    with open(os.path.join(path, "leaf.pem"), "wb") as f:
        f.write(leaf_key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    der = serialization.Encoding.DER
    return leaf_key, inter.public_bytes(der), leaf.public_bytes(der)


def signed_manb(tpl, overrides):
    """The signed SET for the template with the overrides applied."""
    tag, cs, ce, _ = tlv(tpl, 0)
    outer = elems(tpl[cs:ce])
    a0 = next(e for e in outer if e[0][0] == 0xA0)
    _, a0_inner = split(a0[1])
    imkids = elems(a0_inner)
    manb_tag, manb_content = elems(imkids[2][1])[0]
    manb_seq, manb_inner = split(manb_content)
    _, manb_set = elems(manb_inner)
    manp_el, lpol_el = elems(manb_set[1])
    manp_tag, manp_content = manp_el
    manp_seq, manp_inner = split(manp_content)
    _, manp_props = elems(manp_inner)
    have = props_of(manp_props[1])
    props = [valued(have[nm], overrides[nm.encode()])
             if nm.encode() in overrides else have[nm] for nm in NAMES]
    new_manp = named(manp_tag, manp_seq, "MANP",
                     encode(manp_props[0], b"".join(sorted(props))))
    new_l1 = encode(manb_set[0], new_manp + encode(*lpol_el))
    return encode(SET, named(manb_tag, manb_seq, "MANB", new_l1))


def template_chain(tpl):
    """The template IMG4's certificate chain element."""
    tag, cs, ce, _ = tlv(tpl, 0)
    outer = elems(tpl[cs:ce])
    a0 = next(e for e in outer if e[0][0] == 0xA0)
    _, a0_inner = split(a0[1])
    return encode(*elems(a0_inner)[4])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--vuid", required=True)
    ap.add_argument("--nsih", required=True)
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keydir", default=os.path.join(HERE, "sepkey"))
    ap.add_argument("--keyfile", help="a PEM P-384 key to sign with")
    ap.add_argument("--template-chain", action="store_true",
                    help="reuse the template's certificate chain")
    a = ap.parse_args()

    nonce = bytes.fromhex(a.nonce)
    if len(nonce) != 48:
        nonce = hashlib.sha384(nonce).digest()
    overrides = {
        b"lpnh": nonce,
        b"nsih": bytes.fromhex(a.nsih),
        b"vuid": bytes.fromhex(a.vuid.replace("-", "")),
    }
    tpl = open(a.template, "rb").read()
    signed = signed_manb(tpl, overrides)
    if a.keyfile:
        key = serialization.load_pem_private_key(open(a.keyfile, "rb").read(),
                                                 None)
        chain = template_chain(tpl) if a.template_chain else b""
    else:
        key, inter_cert, leaf_cert = make_keys(a.keydir)
        chain = encode(SEQ, inter_cert + leaf_cert)
    sig = key.sign(signed, ec.ECDSA(hashes.SHA384()))
    key.public_key().verify(sig, signed, ec.ECDSA(hashes.SHA384()))
    im4m = encode(SEQ, encode(b"\x16", b"IM4M") + encode(b"\x02", b"\x00")
                  + signed + encode(b"\x04", sig) + chain)
    img4 = encode(SEQ, encode(b"\x16", b"IMG4") + PAYLOAD
                  + encode(b"\xa0", im4m))
    open(a.out, "wb").write(img4)
    print("wrote %s, %d bytes, signature %d bytes"
          % (a.out, len(img4), len(sig)))


main()
