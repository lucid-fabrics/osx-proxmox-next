#!/usr/bin/env python3
# M2b: the Mac-free restore host. Drives Apple's restored (protocol 15) in the vmapple guest over
# the virtio USB device port, answers its data requests from an IPSW and Apple's TSS, and
# streams the OS image over ASR.
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Written for this lab. Protocol facts (message flow, reply keys, ASR framing, IMG4 stitching)
# follow Stela's restore runtime (GPL-2.0) and idevicerestore/libtatsu behaviour as documented
# there; no code is copied. The USB host and usbmux layers are vusbhost.py in this directory.
#
# usage: m2bhost.py SOCKET QMP IPSW OUTDIR --ecid N [--rnd N] [--tss] [--cache DIR]
#
# Apple network use is limited to what the user approved on 2026-11-22: TSS requests to
# gs.apple.com for this VM's ECID, and the AEA image key fetch restored asks for (URLAsset).
# Every request is logged by tss.send() / fetch_url() in OUTDIR/apple-requests.jsonl.
import argparse
import hashlib
import json
import os
import plistlib
import struct
import sys
import threading
import time
import uuid
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qmp  # noqa: E402
import tss  # noqa: E402
import vusbhost as V  # noqa: E402
from vusbhost import log  # noqa: E402

ASR_PORT = 12345
KEY_HOSTS = ("wkms-public.apple.com",)  # the AEA key service restored used
ASR_CHUNK = 128 * 1024


def default_cache():
    """The TSS answer cache, under the runner's data directory unless SNAPINTOSH_HOME says otherwise."""
    home = os.environ.get("SNAPINTOSH_HOME") or os.path.expanduser("~/.local/share/snapintosh")
    return os.path.join(home, "install", "tsscache")


# --------------------------------------------------------------------------- IPSW
class Ipsw:
    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        self.f = open(path, "rb")
        self.lock = threading.Lock()
        self.info = {i.filename: i for i in self.z.infolist()}
        self.data_off = {}

    def size(self, name):
        return self.info[name].file_size

    def read(self, name, off=0, n=None):
        zi = self.info[name]
        if n is None:
            n = zi.file_size - off
        if zi.compress_type == 0:
            if name not in self.data_off:
                with self.lock:
                    self.f.seek(zi.header_offset)
                    h = self.f.read(30)
                    nl, el = struct.unpack("<HH", h[26:30])
                    self.data_off[name] = zi.header_offset + 30 + nl + el
            with self.lock:
                self.f.seek(self.data_off[name] + off)
                return self.f.read(n)
        data = self.z.read(name)
        return data[off : off + n]


# ------------------------------------------------------------------- image4 pieces
def der_len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def tlv(tag, body):
    return bytes([tag]) + der_len(len(body)) + body


def img4(im4p, im4m):
    return tlv(0x30, tlv(0x16, b"IMG4") + im4p + tlv(0xA0, im4m))


def im4m_entries(im4m):
    """{tag4: {key4: raw value}} of an IM4M, only what stitching needs (DGST by tag)."""

    def rd(b, o):
        first = b[o]
        o += 1
        tag = first & 31
        if tag == 31:
            tag = 0
            while True:
                x = b[o]
                o += 1
                tag = (tag << 7) | (x & 0x7F)
                if not x & 0x80:
                    break
        ln = b[o]
        o += 1
        if ln & 0x80:
            k = ln & 0x7F
            ln = int.from_bytes(b[o : o + k], "big")
            o += k
        return first, tag, o, ln

    def seq(b, o, end):
        while o < end:
            first, tag, bo, ln = rd(b, o)
            yield first, tag, bo, ln
            o = bo + ln

    first, _, o, ln = rd(im4m, 0)
    end = o + ln
    # IA5 "IM4M", INTEGER, SET{ private MANB }, OCTET (sig), SEQ (certs)
    out = {}
    for f, t, bo, l0 in seq(im4m, o, end):
        if f == 0x31:
            for f2, t2, bo2, l2 in seq(im4m, bo, bo + l0):  # MANB wrapper (private 0xff)
                # SEQ{ IA5 "MANB", SET{ entries } }
                f3, t3, bo3, l3 = rd(im4m, bo2)
                p = bo3
                for f4, t4, bo4, l4 in seq(im4m, p, bo3 + l3):
                    if f4 == 0x31:
                        for f5, t5, bo5, l5 in seq(im4m, bo4, bo4 + l4):
                            name = t5.to_bytes(4, "big")
                            f6, t6, bo6, l6 = rd(im4m, bo5)  # SEQ
                            props = {}
                            q = bo6
                            f7, t7, bo7, l7 = rd(im4m, q)  # IA5 name
                            q = bo7 + l7
                            f8, t8, bo8, l8 = rd(im4m, q)  # SET
                            for f9, t9, bo9, l9 in seq(im4m, bo8, bo8 + l8):
                                key = t9.to_bytes(4, "big")
                                f10, t10, bo10, l10 = rd(im4m, bo9)  # SEQ
                                f11, t11, bo11, l11 = rd(im4m, bo10)  # IA5 key
                                f12, t12, bo12, l12 = rd(im4m, bo11 + l11)
                                props[key] = im4m[bo12 : bo12 + l12]
                            out[name] = props
    return out


def im4p_type(im4p):
    # SEQ { IA5 "IM4P", IA5 type, ...}
    o = 1
    ln = im4p[o]
    o += 1
    if ln & 0x80:
        k = ln & 0x7F
        o += k
    assert im4p[o] == 0x16 and im4p[o + 2 : o + 6] == b"IM4P", "not an IM4P"
    o += 6
    assert im4p[o] == 0x16 and im4p[o + 1] == 4
    return o + 2, im4p[o + 2 : o + 6]


def retype(im4p, digest, ticket_entries):
    """Type the IM4P as the tag under which the ticket covers `digest` (see Stela retype_covered)."""
    if hashlib.sha384(im4p).digest() == digest:
        return im4p
    at, orig = im4p_type(im4p)
    for tag, props in ticket_entries.items():
        if props.get(b"DGST") == digest:
            cand = bytearray(im4p)
            cand[at : at + 4] = tag
            if hashlib.sha384(bytes(cand)).digest() == digest:
                return bytes(cand)
    raise ValueError("ticket covers no retype of the component digest")


# ------------------------------------------------------------------------ host
class Host:
    def __init__(self, a):
        self.a = a
        self.out = a.out
        self.ipsw = Ipsw(a.ipsw)
        self.bm_idx, self.ident = tss.load_identity_from_ipsw(self.ipsw)
        self.ecid = a.ecid
        self.rnd = a.rnd
        self.boot_nonce = struct.pack("<I", self.rnd) * 8
        self.ap_nonce = hashlib.sha384(self.boot_nonce).digest()[:32] if a.nonce_mode == "sha384" else self.boot_nonce
        self.root_tickets = {}
        self.msgs = open(os.path.join(self.out, "messages.jsonl"), "a")
        self.t_start = time.time()
        self.times = {}
        self.failed = False

    def mark(self, name):
        self.times[name] = round(time.time() - self.t_start, 2)
        log("TIME", name, self.times[name])
        json.dump(self.times, open(os.path.join(self.out, "times.json"), "w"), indent=1)

    # -- TSS with an on-disk cache keyed by the request minus its random @UUID
    def tss_ticket(self, what, req, key="ApImg4Ticket"):
        body = {k: v for k, v in req.items() if k != "@UUID"}
        h = hashlib.sha256(plistlib.dumps(body, sort_keys=True)).hexdigest()[:24]
        cache = os.path.join(self.a.cache, "%s-%s.plist" % (what, h))
        if os.path.exists(cache):
            log("TSS %s: cached answer %s" % (what, os.path.basename(cache)))
            return plistlib.load(open(cache, "rb"))[key]
        if not self.a.tss:
            open(os.path.join(self.out, "unsent-%s.plist" % what), "wb").write(plistlib.dumps(req))
            raise SystemExit("TSS needed for %s and --tss not given" % what)
        status, message, answer, raw = tss.send(what, req)
        log("TSS %s -> STATUS=%s MESSAGE=%s" % (what, status, message))
        if answer is None or key not in answer:
            raise SystemExit("TSS refused %s: STATUS=%s MESSAGE=%s" % (what, status, message))
        os.makedirs(self.a.cache, exist_ok=True)
        plistlib.dump(answer, open(cache, "wb"))
        return answer[key]

    def root_ticket(self, recovery):
        k = "recovery" if recovery else "ap"
        if k not in self.root_tickets:
            req = tss.root_ticket_request(self.ident, self.ecid, self.ap_nonce, recovery)
            self.root_tickets[k] = self.tss_ticket("root-" + k, req)
            open(os.path.join(self.out, "ticket-%s.im4m" % k), "wb").write(self.root_tickets[k])
        return self.root_tickets[k]

    # -- control channel
    def jlog(self, direction, m):
        self.msgs.write(json.dumps({"t": round(time.time() - self.t_start, 3), "dir": direction,
                                    "msg": V.jsonable(m)}) + "\n")
        self.msgs.flush()

    def send(self, st, obj, binary=False):
        b = plistlib.dumps(obj, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML)
        st.send(struct.pack(">I", len(b)) + b)

    def recv(self, st, timeout=600):
        n = struct.unpack(">I", st.recv_exact(4, timeout))[0]
        return plistlib.loads(st.recv_exact(n, timeout))

    # -- identities and tickets
    def bm(self):
        if not hasattr(self, "_bm"):
            self._bm = plistlib.loads(self.ipsw.read("BuildManifest.plist"))
        return self._bm

    def recovery_ident(self):
        r = self.ident
        c = [
            i for i in self.bm()["BuildIdentities"]
            if i["Info"].get("Variant") == "macOS Customer"
            and i["ApChipID"] == r["ApChipID"] and i["ApBoardID"] == r["ApBoardID"]
        ]
        if len(c) != 1:
            raise ValueError("recovery identity missing or ambiguous (%d)" % len(c))
        return c[0]

    def global_manifest(self, ident, args, cryptex):
        info = ident["Info"]
        variant = args.get("Variant") or info.get("MacOSVariant") or info["Variant"]
        prefix = args.get("GlobalManifestPrefix", "apticket")
        suffix = args.get("GlobalManifestSuffix", "")
        path = "Firmware/Manifests/restore/%s%s/%s.%s%s.im4m" % (
            "cryptex1/" if cryptex else "", variant, prefix, info["DeviceClass"], suffix)
        return path

    def ticket_for(self, name, args, recovery):
        if name.startswith("Cryptex1,"):
            ident = self.recovery_ident() if recovery else self.ident
            return self.ipsw.read(self.global_manifest(ident, args, True))
        return self.root_ticket(recovery)

    def personalize(self, ident, name, args, recovery=None):
        """The component's IM4P stitched into an IMG4 with the right ticket."""
        recovery = bool(args.get("IsRecoveryOS", False)) if recovery is None else recovery
        e = ident["Manifest"][name]
        im4p = self.ipsw.read(e["Info"]["Path"])
        tk = self.ticket_for(name, args, recovery)
        ents = im4m_entries(tk)
        im4p = retype(im4p, e["Digest"], ents)
        return img4(im4p, tk)

    # -- file transfer: FileData frames then FileDataDone (the DataPort or the control stream)
    def send_file(self, conn, data, chunk=8192):
        for i in range(0, len(data), chunk):
            self.send(conn, {"FileData": data[i : i + chunk]}, binary=True)
        self.send(conn, {"FileDataDone": True}, binary=True)


    # -- ASR (port 12345 or the request's DataPort): raw image after an XML plist negotiation
    def asr_read_plist(self, conn, buf):
        while b"</plist>" not in buf:
            d = conn.recv_some(30)
            if not d and conn.closed:
                raise EOFError("asr peer closed")
            buf += d
        i = buf.index(b"</plist>") + len(b"</plist>")
        raw = bytes(buf[:i]).lstrip()
        try:
            return plistlib.loads(raw), buf[i:]
        except Exception:
            log("ASR: unparsable plist", repr(raw[:400]))
            raise

    def asr_serve(self, conn, path, what):
        size = self.ipsw.size(path)
        checksum = False
        buf = b""
        sent = 0
        t0 = time.time()
        last = t0
        log("ASR %s: serving %s (%d bytes)" % (what, path, size))
        while True:
            cmd, buf = self.asr_read_plist(conn, buf)
            c = cmd.get("Command")
            log("ASR %s <- %s %s" % (what, c, {k: v for k, v in cmd.items() if k != "Command"}))
            if c == "Initiate":
                checksum = bool(cmd.get("Checksum Chunks", False))
                pkt = {"Version": 1, "Stream ID": 1, "Payload": {"Port": 1, "Size": size},
                       "FEC Slice Stride": 40, "Packet Payload Size": 1450, "Packets Per FEC": 25}
                if checksum:
                    pkt["Checksum Chunk Size"] = ASR_CHUNK
                conn.send(plistlib.dumps(pkt, fmt=plistlib.FMT_XML))
            elif c == "OOBData":
                if "OOB Ranges" in cmd:
                    raise Unhandled("ASR OOB Ranges")
                off, ln = cmd["OOB Offset"], cmd["OOB Length"]
                if not (isinstance(off, int) and isinstance(ln, int) and 0 <= off and 0 <= ln <= (1 << 24)
                        and off + ln <= size):
                    raise Unhandled("ASR OOB range out of bounds %r %r" % (off, ln))
                conn.send(self.ipsw.read(path, off, ln))
            elif c == "Payload":
                break
            else:
                raise Unhandled("ASR command " + str(c))
        self.mark("asr_payload_start")
        pos = 0
        while pos < size:
            n = min(ASR_CHUNK, size - pos)
            chunk = self.ipsw.read(path, pos, n)
            if checksum:
                chunk += hashlib.sha1(chunk[:n]).digest()
            conn.send(chunk)
            pos += n
            now = time.time()
            if now - last > 10:
                log("ASR %s: %d / %d MB, %.1f MB/s" % (what, pos >> 20, size >> 20, (pos - sent) / (now - last) / 1e6))
                sent, last = pos, now
        log("ASR %s: done, %.1f s, %.2f MB/s" % (what, time.time() - t0, size / (time.time() - t0) / 1e6))
        self.mark("asr_payload_done")

    # -- URLAsset / StreamedImageDecryptionKey: the HTTP request restored wants made for it
    def http_fetch(self, kind, args):
        import urllib.parse
        import urllib.request

        url = args["RequestURL"]
        method = args["RequestMethod"]
        host = urllib.parse.urlsplit(url).hostname or ""
        if kind != "URLAsset" or method != "GET":
            raise Unhandled("only the URLAsset GET (the AEA key) is approved: %s %s" % (kind, method))
        if urllib.parse.urlsplit(url).scheme != "https" or host not in KEY_HOSTS:
            raise Unhandled("URLAsset to a host or scheme outside the approved key service: " + url[:80])
        body = args.get("RequestBody")
        hdrs = dict(args.get("RequestAdditionalHeaders", {}))
        rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "what": kind, "method": method, "url": url}
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        t0 = time.time()
        try:
            with opener.open(req, timeout=60) as r:
                data, code, rh = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            data, code, rh = e.read(), e.code, dict(e.headers)
        rec.update(final_url=url, http_status=code, response_bytes=len(data), seconds=round(time.time() - t0, 2))
        with open(os.path.join(self.out, "apple-requests.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
        log("HTTP %s %s -> %d (%d bytes)" % (method, url, code, len(data)))
        return {"ResponseBody": data, "ResponseBodyDone": True, "ResponseHeaders": rh,
                "ResponseStatus": code}

    def serve_async(self, m, mux):
        """AsyncDataRequestMsg: serve it on its DataPort (run in a thread)."""
        kind, args, port = m["DataType"], m.get("Arguments", {}), m["DataPort"]
        try:
            if kind in ("SystemImageData", "RecoveryOSASRImage"):
                conn = mux.connect(port)
                ai = self.recovery_ident() if args.get("IsRecoveryOS") else self.ident
                path = ai["Manifest"]["OS"]["Info"]["Path"]
                self.asr_serve(conn, path, kind)
            elif kind == "URLAsset":
                conn = None
                for attempt in range(12):
                    try:
                        conn = mux.connect(port, timeout=15)
                        break
                    except ConnectionError as e:
                        log("async %s connect retry %d: %r" % (kind, attempt + 1, e))
                if conn is None:
                    raise ConnectionError("no connect to port %d" % port)
                self.send(conn, self.http_fetch(kind, args), binary=True)
            else:
                raise Unhandled("async " + kind)
        except Exception as e:
            log("ASYNC %s FAILED: %r" % (kind, e))
            self.failed = True
            import traceback
            log(traceback.format_exc())

    # -- data request handlers; return a dict reply, or raise Unhandled
    def handle(self, st, m, mux):
        kind = m["DataType"]
        args = m.get("Arguments", {})
        recovery = bool(args.get("IsRecoveryOS", False))
        ident = self.recovery_ident() if recovery else self.ident
        port = m.get("DataPort")
        if kind in ("RootTicket", "RecoveryOSRootTicketData"):
            if kind == "RecoveryOSRootTicketData":
                self.mark("tss_recoveryos_root")
            return {"RootTicketData": self.root_ticket(kind == "RecoveryOSRootTicketData")}
        if kind == "HostSystemTime":
            return {"SetHostTimeOnDevice": int(time.time())}
        if kind == "RestoreAttestation":
            return {"RestoreShouldAttest": False}
        if kind == "RecoveryOSVersionData":
            info = self.recovery_ident()["Info"]
            d = {"BuildNumber": info["BuildNumber"], "Variant": info["Variant"],
                 "BuildTrain": info["BuildTrain"], "ProductVersion": info["ProductMarketingVersion"]}
            return {"RecoveryOSVersionData": plistlib.dumps(d, fmt=plistlib.FMT_XML)}
        if kind == "ReceiptManifest":
            return {"ReceiptManifest": ident["Manifest"]}
        if kind == "FDRTrustData":
            return {}
        if kind == "BuildIdentityDict":
            return {"BuildIdentityDict": ident, "Variant": args.get("Variant", "Erase")}
        if kind == "RecoveryOSLocalPolicy":
            req = tss.local_policy_request(ident, self.ecid, args)
            tk = self.tss_ticket("localpolicy-recovery", req)
            self.mark("tss_localpolicy")
            return {"Ap,LocalPolicy": img4(tss.LPOL, tk)}
        if kind == "FirmwareUpdaterData":
            if args.get("MessageArgUpdaterName") != "Cryptex1":
                raise Unhandled("FirmwareUpdaterData " + str(args.get("MessageArgUpdaterName")))
            req = tss.cryptex_request(ident, self.ecid, args)
            tk = self.tss_ticket("cryptex1", req, key="Cryptex1,Ticket")
            self.mark("tss_cryptex1")
            return {"FirmwareResponseData": {"Cryptex1,Ticket": tk}}
        if kind in ("BootabilityBundle", "BootabilityBundleV2"):
            if not port:
                raise Unhandled("BootabilityBundle without DataPort")
            prefix = "%s/Restore/Bootability/" % kind
            cache = "%s/Restore/Firmware/Bootability.dmg.trustcache" % kind
            out = b""

            def odc(name, mode, size):
                nb = name.encode() + b"\0"
                hd = "070707%06o%06o%06o%06o%06o%06o%06o%011o%06o%011o" % (
                    0, 0, mode, 0, 0, 1, 0, 0, len(nb), size)
                return hd.encode() + nb

            for zi in self.ipsw.z.infolist():
                if zi.filename == cache:
                    name = "Bootability.trustcache"
                elif zi.filename.startswith(prefix):
                    name = zi.filename[len(prefix):]
                else:
                    continue
                name = name.rstrip("/")
                if not name:
                    continue
                mode = zi.external_attr >> 16
                data = b"" if zi.filename.endswith("/") else self.ipsw.read(zi.filename)
                out += odc(name, mode, len(data)) + data
            out += odc("TRAILER!!!", 0, 0)
            conn = mux.connect(port)
            conn.send(out)
            conn.close()
            log("Bootability cpio sent: %d bytes" % len(out))
            return None
        if kind in ("SystemImageData", "RecoveryOSASRImage"):
            m2 = dict(m)
            m2["DataPort"] = port or ASR_PORT
            threading.Thread(target=self.serve_async, args=(m2, mux), daemon=True).start()
            return None
        if kind == "NORData":
            man = ident["Manifest"]
            names = []
            for n, e in man.items():
                i = e.get("Info", {})
                if (i.get("IsFirmwarePayload") or (i.get("IsSecondaryFirmwarePayload")
                                                   and i.get("IsLoadedByiBoot"))) \
                        and n not in ("LLB", "RestoreSEP"):
                    names.append(n)
            names.sort(key=lambda n: (not n.startswith("iBoot"), n))
            log("NORData: LLB + %s" % names)
            imgs = {n: self.personalize(ident, n, {}) for n in names}
            r = {"LlbImageData": self.personalize(ident, "LLB", {})}
            r["NorImageData"] = imgs if "FlashVersion1" in args else [imgs[n] for n in names]
            for comp, key in (("RestoreSEP", "RestoreSEPImageData"), ("SEP", "SEPImageData"),
                              ("SepStage1", "SEPPatchImageData")):
                if comp in man:
                    r[key] = self.personalize(ident, comp, {})
            return r
        if kind in ("PersonalizedData", "FUDData", "EANData"):
            lk, dk, flag = {
                "FUDData": ("FUDImageList", "FUDImageData", "IsFUDFirmware"),
                "EANData": ("EANImageList", "EANData", "IsEarlyAccessFirmware"),
            }.get(kind, ("ImageList", "ImageData", args.get("ImageType")))
            names = [n for n, e in ident["Manifest"].items() if e.get("Info", {}).get(flag, False)]
            if args.get(lk):
                return {lk: names}
            if "ImageName" in args:
                if args["ImageName"] not in names:
                    raise Unhandled("%s image %s not in manifest list" % (kind, args["ImageName"]))
                return {dk: self.personalize(ident, args["ImageName"], args), "ImageName": args["ImageName"]}
            return {dk: {n: self.personalize(ident, n, args) for n in names}}
        if kind in ("SourceBootObjectV4", "SourceBootObjectV5", "PersonalizedBootObjectV3"):
            name = args["ImageName"]
            if name == "__GlobalManifest__":
                path = self.global_manifest(ident, args, False)
                data = self.ipsw.read(path) if path in self.ipsw.info else b""
            elif name == "__RestoreVersion__":
                data = self.ipsw.read("RestoreVersion.plist")
            elif name == "__SystemVersion__":
                data = self.ipsw.read("SystemVersion.plist")
            elif kind == "PersonalizedBootObjectV3":
                data = self.personalize(ident, name, args)
            else:
                data = self.ipsw.read(ident["Manifest"][name]["Info"]["Path"])
            conn = mux.connect(port) if port else st
            self.send_file(conn, data)
            return None
        raise Unhandled(kind)


class Unhandled(Exception):
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sock")
    ap.add_argument("qmp")
    ap.add_argument("ipsw")
    ap.add_argument("out")
    ap.add_argument("--ecid", type=int, required=True, help="the VM's ECID (the machine uuid QEMU is given)")
    ap.add_argument("--rnd", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--tss", action="store_true", help="allow requests to Apple's TSS")
    ap.add_argument("--cache", default=default_cache())
    ap.add_argument("--nonce-mode", default="sha384", choices=["sha384", "raw"])
    ap.add_argument("--timeout", type=float, default=7200)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    V.LOG = open(os.path.join(a.out, "m2bhost.log"), "w")
    tss.LOG = os.path.join(a.out, "apple-requests.jsonl")
    tss.SAVE_DIR = os.path.join(a.out, "tss")
    wire = open(os.path.join(a.out, "wire.txt"), "w", buffering=1)

    h = Host(a)
    nonce = qmp.boot_nonce(a.qmp)
    if nonce != h.boot_nonce:
        raise SystemExit("guest boot-nonce %s differs from --rnd %s" % (nonce.hex(), h.boot_nonce.hex()))
    log("boot-nonce", nonce.hex(), "ApNonce", h.ap_nonce.hex(), "identity", h.bm_idx)

    link = V.VLink(a.sock, wire)
    link.query()
    deadline = time.time() + 180
    while time.time() < deadline and not (link.status is not None and link.status & 4):
        link.status_ev.wait(1)
        link.status_ev.clear()
    if not (link.status and link.status & 4):
        raise SystemExit("guest never set DRIVER_OK")
    h.mark("driver_ok")
    usb = V.UsbHost(link)
    time.sleep(1.0)
    for attempt in range(3):
        try:
            usb.reset()
            break
        except TimeoutError:
            log("reset attempt %d timed out" % (attempt + 1))
    else:
        raise SystemExit("no reset response")
    dev = usb.get_descriptor(1, 0, 18)
    ncfg = dev[17]
    cfg = None
    for i in range(ncfg):
        hd = usb.get_descriptor(2, i, 9)
        total = struct.unpack_from("<H", hd, 2)[0]
        c = V.parse_config(usb.get_descriptor(2, i, total))
        for itf in c["interfaces"]:
            if (itf["class"], itf["subclass"], itf["protocol"]) == (0xFF, 0xFE, 2) and len(itf["endpoints"]) >= 2:
                cfg = (c, itf)
                break
        if cfg:
            break
    c, itf = cfg
    ep_in = [e["bEndpointAddress"] for e in itf["endpoints"] if e["bEndpointAddress"] & 0x80][0]
    ep_out = [e["bEndpointAddress"] for e in itf["endpoints"] if not e["bEndpointAddress"] & 0x80][0]
    usb.control(0x00, 9, c["bConfigurationValue"], 0, 0)
    time.sleep(0.5)
    mux = V.Mux(usb, ep_out, ep_in)
    mux.handshake()
    stop = threading.Event()
    lst = mux.connect(5000)
    threading.Thread(target=V.log_reader, args=(lst, os.path.join(a.out, "restored-port5000.log"), stop),
                     daemon=True).start()
    st = mux.connect(62078)
    V.plist_send(st, {"Request": "QueryType", "Label": "m2b"})
    log("QueryType ->", V.plist_recv(st))
    V.plist_send(st, {"Request": "QueryValue", "QueryKey": "HardwareInfo", "Label": "m2b"})
    log("HardwareInfo ->", V.plist_recv(st))
    V.plist_send(st, {"Request": "Goodbye", "Label": "m2b"})
    V.plist_recv(st, 10)
    h.mark("restored_handshake")

    st = mux.connect(62078)
    V.plist_send(st, {"Request": "QueryType", "Label": "m2b"})
    V.plist_recv(st)
    info = h.ident["Info"]
    opts = {
        "AutoBootDelay": 0,
        "FlashNOR": False,
        "FormatForAPFS": True,
        "InstallRecoveryOS": True,
        "RecoveryOSUnpack": True,
        "ShouldRestoreSystemImage": True,
        "BootImageType": "UserOrInternal",
        "CreateFilesystemPartitions": True,
        "AllowUntetheredRestore": False,
        "SystemImageType": "User",
        "UpdateBaseband": False,
        "PersonalizedDuringPreflight": True,
        "RootToInstall": False,
        "UUID": str(uuid.uuid4()).upper(),
        "AuthInstallRestoreBehavior": info["RestoreBehavior"],
        "AuthInstallRestoreVariant": info["Variant"],
        "MacOSVariant": info["MacOSVariant"],
        "AuthInstallRecoveryOSVariant": info["MacOSVariant"],
        "SystemPartitionSize": info["MinimumSystemPartition"],
        "SupportedDataTypes": V.DATA_TYPES,
        "SupportedMessageTypes": V.MESSAGE_TYPES,
        "SupportedAsyncDataTypes": {k: True for k in V.ASYNC_DATA_TYPES},
    }
    h.send(st, {"Request": "StartRestore", "RestoreProtocolVersion": 15, "RestoreOptions": opts,
                "Label": "m2b"})
    h.mark("start_restore")
    t_end = time.time() + a.timeout
    rc = 0
    while time.time() < t_end:
        try:
            m = h.recv(st, max(5, t_end - time.time()))
        except Exception as e:
            log("control read ended:", repr(e))
            rc = 4
            break
        h.jlog("<-", m)
        mt = m.get("MsgType")
        if mt == "AsyncDataRequestMsg":
            log("REQUEST AsyncDataRequestMsg", m.get("DataType"), "port", m.get("DataPort"),
                json.dumps(V.jsonable(m.get("Arguments", {})))[:300])
            threading.Thread(target=h.serve_async, args=(m, mux), daemon=True).start()
        elif mt == "DataRequestMsg":
            log("REQUEST", mt, m.get("DataType"), json.dumps(V.jsonable(m.get("Arguments", {})))[:300])
            try:
                reply = h.handle(st, m, mux)
            except Unhandled as e:
                log("WALL: no answer implemented for", e)
                rc = 5
                break
            if reply is not None:
                h.jlog("->", {k: (len(v) if isinstance(v, bytes) else "...") for k, v in reply.items()})
                h.send(st, reply, binary=True)
        elif mt == "RestoreAttestation":
            log("REQUEST RestoreAttestation")
            h.send(st, {"RestoreShouldAttest": False}, binary=True)
        elif mt == "StatusMsg":
            log("STATUS", json.dumps(V.jsonable(m))[:1500])
            rc = 0 if m.get("Status") == 0 and not m.get("AMRError") else 6
            h.send(st, {"MsgType": "ReceivedFinalStatusMsg"})
            break
        else:
            log("<-", mt, json.dumps(V.jsonable(m))[:200])
    h.mark("end")
    stop.set()
    return rc


if __name__ == "__main__":
    sys.exit(main())
