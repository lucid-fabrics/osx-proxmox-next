#!/usr/bin/env python3
# M2b: TSS requests for a vma2macosap restore, and the one place that talks to Apple.
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Written for this lab from the protocol as Stela (GPL-2.0,
# crates/stela-runtime/src/restore/tss.rs) documents libtatsu and idevicerestore:
# request keys, the recoveryOS tag exclusions, the restore request rules, the
# Cryptex1 and LocalPolicy requests. No Stela code is copied.
#
# Every request to Apple goes through send(), which appends one line to the
# request log (LOG, JSON lines: time, endpoint, what, request/response file
# names, HTTP status, STATUS and MESSAGE of the answer). The plan approves
# exactly: TSS to gs.apple.com for our VirtualMac2,1 VM installs. Nothing else
# is contacted from here.
import hashlib
import http.client
import json
import os
import plistlib
import time
import uuid

HOST = "gs.apple.com"
PATH = "/TSS/controller?action=2"
HEADERS = {
    "User-Agent": "InetURL/1.0",
    "Cache-Control": "no-cache",
    "Content-Type": 'text/xml; charset="utf-8"',
}
VERSION = "libauthinstall-1104.0.9"
LPOL = bytes.fromhex("30141604494d3450" "16046c706f6c" "1603312e30" "040100")

IDENTITY_STRINGS = [
    "Ap,OSLongVersion",
    "Ap,OSReleaseType",
    "Ap,ProductMarketingVersion",
    "Ap,ProductType",
    "Ap,SDKPlatform",
    "Ap,Target",
    "Ap,TargetType",
]
AP_SKIPPED = {
    "BasebandFirmware", "SE,UpdatePayload", "BaseSystem", "Diags", "Ap,ExclaveOS",
}
RECOVERY_SKIPPED = {
    "BasebandFirmware", "SE,UpdatePayload", "BaseSystem", "ANS", "Ap,AudioBootChime",
    "Ap,CIO", "Ap,RestoreCIO", "Ap,RestoreTMU", "Ap,TMU", "Ap,rOSLogo1", "Ap,rOSLogo2",
    "AppleLogo", "DCP", "LLB", "RecoveryMode", "RestoreANS", "RestoreDCP",
    "RestoreDeviceTree", "RestoreKernelCache",
    "RestoreLogo", "RestoreRamDisk", "RestoreSEP", "SEP", "ftap", "ftsp",
    "iBEC", "iBSS", "rfta", "rfts", "Diags",
}


def num(v):
    if isinstance(v, int):
        return v
    return int(v, 16) if str(v).startswith("0x") else int(v)


def load_identity(manifest_path, variant="Customer Erase Install (IPSW)",
                  device="vma2macosap"):
    return _pick(plistlib.load(open(manifest_path, "rb")), variant, device)


def load_identity_from_ipsw(ipsw, variant="Customer Erase Install (IPSW)",
                            device="vma2macosap"):
    return _pick(plistlib.loads(ipsw.read("BuildManifest.plist")), variant, device)


def _pick(bm, variant, device):
    for i, ident in enumerate(bm["BuildIdentities"]):
        info = ident.get("Info", {})
        if info.get("Variant") == variant and info.get("DeviceClass") == device:
            return i, ident
    raise SystemExit("no identity %r for %s" % (variant, device))


def _rules(entry_out, rules):
    for rule in rules:
        cond = rule["Conditions"]
        ok = all(
            k in ("ApRawProductionMode", "ApCurrentProductionMode", "ApRawSecurityMode",
                  "ApRequiresImage4") and v is True
            for k, v in cond.items()
        )
        if ok:
            for k, v in rule["Actions"].items():
                if isinstance(v, bool):
                    entry_out[k] = v


def _component(entry, recovery):
    info = entry.get("Info")
    if info is None:
        return None
    trusted = bool(entry.get("Trusted", False))
    rules = info.get("RestoreRequestRules")
    if not recovery:
        if (rules is None and not trusted) or info.get("IsFTAB", False):
            return None
    out = {k: v for k, v in entry.items() if k != "Info"}
    if rules is not None:
        _rules(out, rules)
    elif not recovery:
        out["EPRO"] = True
        out["ESEC"] = True
    if trusted and "Digest" not in entry:
        out["Digest"] = b""
    if not recovery and not out:
        return None
    return out


def _uuid(req):
    # libtatsu sends a random GUID that only names the request
    return str(uuid.uuid4()).upper()


def root_ticket_request(ident, ecid, ap_nonce, recovery):
    req = {
        "@HostPlatformInfo": "mac",
        "@VersionInfo": VERSION,
        "@ApImg4Ticket": True,
        "ApECID": ecid,
        "UniqueBuildID": ident["UniqueBuildID"],
        "ApChipID": num(ident["ApChipID"]),
        "ApBoardID": num(ident["ApBoardID"]),
        "ApSecurityDomain": num(ident["ApSecurityDomain"]),
        "ApNonce": ap_nonce,
        "ApSecurityMode": True,
        "ApProductionMode": True,
    }
    for k in IDENTITY_STRINGS:
        if k in ident:
            req[k] = ident[k]
    # Info.RequestManifestProperties: this identity lists Ap,Timestamp (Info, an
    # integer) and Ap,VM
    props = ident["Info"].get("RequestManifestProperties", [])
    for k in props:
        if k in ident:
            req[k] = ident[k]
        elif k in ident["Info"]:
            req[k] = ident["Info"][k]
    skipped = RECOVERY_SKIPPED if recovery else AP_SKIPPED
    for name, entry in ident["Manifest"].items():
        if name in skipped:
            continue
        c = _component(entry, recovery)
        if c is not None:
            req[name] = c
    req["@UUID"] = _uuid(req)
    return req


def local_policy_request(ident, ecid, args):
    req = {
        "@HostPlatformInfo": "mac",
        "@VersionInfo": VERSION,
        "@ApImg4Ticket": True,
        "Ap,LocalBoot": True,
        "Ap,LocalPolicy": {"Digest": hashlib.sha384(LPOL).digest(), "Trusted": True},
        "Ap,NextStageIM4MHash": args["Ap,NextStageIM4MHash"],
        "ApECID": ecid,
        "ApChipID": num(ident["ApChipID"]),
        "ApBoardID": num(ident["ApBoardID"]),
        "ApSecurityDomain": num(ident["ApSecurityDomain"]),
        "ApSecurityMode": True,
        "ApProductionMode": True,
    }
    for k in ("Ap,RecoveryOSPolicyNonceHash", "Ap,LocalPolicyNonceHash",
              "Ap,RemotePolicyNonceHash", "ApNonce"):
        if k in args:
            req[k] = args[k]
    if "Ap,VolumeUUID" in args:
        v = args["Ap,VolumeUUID"]
        req["Ap,VolumeUUID"] = uuid.UUID(v).bytes if isinstance(v, str) else v
    for k, v in args.items():
        req.setdefault(k, v)
    req["@UUID"] = _uuid(req)
    return req


def cryptex_request(ident, ecid, args):
    params = dict(args["MessageArgInfo"])
    for tag in args["DeviceGeneratedTags"]["BuildIdentityTags"]:
        if tag in ident:
            params[tag] = ident[tag]
    params.setdefault("ApProductionMode", True)
    params.setdefault("ApSecurityMode", True)
    for k in ("ApChipID", "ApBoardID"):
        if k in ident:
            params[k] = ident[k]
    params.update(args["DeviceGeneratedRequest"])
    if num(params["ApECID"]) != ecid:
        raise ValueError("cryptex request names another ECID")
    req = {
        "@HostPlatformInfo": "mac",
        "@VersionInfo": VERSION,
        "@Cryptex1,Ticket": True,
        "ApECID": ecid,
    }
    for k in ("ApChipID", "ApBoardID", "ApSecurityDomain"):
        if k in params:
            req[k] = num(params[k])
    if "UniqueBuildID" in params:
        req["UniqueBuildID"] = params["UniqueBuildID"]
    for k in ("ApSecurityMode", "ApProductionMode"):
        req[k] = bool(params[k])
    n = 0
    for k, v in params.items():
        if k.startswith("Cryptex1"):
            req[k] = v
            n += 1
    if not n:
        raise ValueError("cryptex request names no cryptex")
    req["@UUID"] = _uuid(req)
    return req


# ------------------------------------------------------------------ the wire
LOG = None  # path of the request log (JSON lines), set by the caller
# where request/response bodies are kept (data/m2b only, they carry the ECID)
SAVE_DIR = None
_count = 0


def parse_response(body):
    text = body.decode("utf-8", "replace")
    fields = {}
    for part in text.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            fields[k] = v
    status = fields.get("STATUS")
    message = fields.get("MESSAGE", "").split("&REQUEST_STRING")[0]
    answer = None
    if status == "0" and "MESSAGE=SUCCESS" in text:
        i = text.find("<?xml")
        answer = plistlib.loads(body[i:].replace(b"\x00", b""))
    return status, message, answer


def send(what, req):
    """POST one TSS request to Apple.

    Returns (status, message, answer_plist, raw_body).
    """
    global _count
    _count += 1
    n = _count
    body = plistlib.dumps(req, fmt=plistlib.FMT_XML)
    rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "what": what, "host": HOST,
           "path": PATH, "request_bytes": len(body)}
    if SAVE_DIR:
        os.makedirs(SAVE_DIR, exist_ok=True)
        rq = os.path.join(SAVE_DIR, "%02d-%s-request.plist" % (n, what))
        open(rq, "wb").write(body)
        rec["request_file"] = rq
    t0 = time.time()
    try:
        c = http.client.HTTPConnection(HOST, 80, timeout=60)
        c.request("POST", PATH, body, HEADERS)
        r = c.getresponse()
        raw = r.read()
        rec["http_status"] = r.status
        c.close()
    except Exception as e:  # network failure is a result too
        rec["error"] = repr(e)
        raw = b""
    rec["seconds"] = round(time.time() - t0, 2)
    status = message = answer = None
    if raw:
        status, message, answer = parse_response(raw)
        rec["tss_status"], rec["tss_message"] = status, message
        rec["response_bytes"] = len(raw)
        if SAVE_DIR:
            rs = os.path.join(SAVE_DIR, "%02d-%s-response.txt" % (n, what))
            open(rs, "wb").write(raw)
            rec["response_file"] = rs
    if LOG:
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    return status, message, answer, raw


if __name__ == "__main__":
    import sys

    # tss.py MANIFEST ECID NONCEHEX ap|recovery  -> print the request (no network)
    i, ident = load_identity(sys.argv[1])
    r = root_ticket_request(ident, int(sys.argv[2]), bytes.fromhex(sys.argv[3]),
                            sys.argv[4] == "recovery")
    sys.stdout.buffer.write(plistlib.dumps(r))
    sys.stderr.write("identity %d, %d keys\n" % (i, len(r)))
