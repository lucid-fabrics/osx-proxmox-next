#!/usr/bin/env python3
# R9 M2a: userspace USB host for QEMU's vmapple-virtio-usb-pci device port.
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Layers, all written for this lab (no code copied from usbmuxd, libimobiledevice
# or Stela; their protocol behaviour was used as a reference, see the lab's
# r9 restore notes):
#   VLink     chardev framing of hw/vmapple/virtio-usb.c (le32 n, kind, body)
#   UsbHost   enumeration and transfers over the guest's request/response queues
#   Mux       usbmux (version 2 header, TCP-like streams) on the mux bulk pipes
#   restored  plist service on port 62078: QueryType, QueryValue, Goodbye
#
# Nothing here talks to Apple or to any network: the only peer is the QEMU
# chardev socket given on the command line.
#
# usage: vusbhost.py SOCKET [--out DIR] [--timeout S] [--keys K1,K2,...]

import argparse
import json
import os
import plistlib
import queue
import socket
import struct
import sys
import threading
import time

T0 = time.time()
LOG = None


def log(*a):
    line = "%8.3f %s" % (time.time() - T0, " ".join(str(x) for x in a))
    print(line, flush=True)
    if LOG:
        LOG.write(line + "\n")
        LOG.flush()


# ---------------------------------------------------------------- chardev link
K_REQUEST, K_QUERY, K_RESPONSE, K_STATUS = 0x01, 0x02, 0x81, 0x82
T_XFER, T_RESET, T_PAUSE = 1, 2, 3
R_XFER, R_STALL, R_ABORT, R_RESET, R_PAUSE = 1, 2, 3, 4, 5
RNAME = {1: "xfer", 2: "stall", 3: "abort", 4: "reset", 5: "pause"}


class VLink:
    def __init__(self, path, wire):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.wire = wire
        self.lock = threading.Lock()
        self.status = None
        self.status_ev = threading.Event()
        self.rsp = {}  # ep -> Queue of (type, data)
        self.rsp_lock = threading.Lock()
        self.dead = False
        threading.Thread(target=self._reader, daemon=True).start()

    def q(self, ep):
        with self.rsp_lock:
            return self.rsp.setdefault(ep, queue.Queue())

    def send(self, ep, typ, payload=b"", length=None):
        if length is None:
            length = len(payload)
        msg = struct.pack("<IBB", length, ep, typ) + payload
        frame = struct.pack("<IB", len(msg) + 1, K_REQUEST) + msg
        if self.wire:
            self.wire.write(
                "> ep=%02x type=%d len=%d %s\n" % (ep, typ, length, payload[:64].hex())
            )
        with self.lock:
            self.s.sendall(frame)

    def query(self):
        with self.lock:
            self.s.sendall(struct.pack("<IB", 1, K_QUERY))

    def _recvn(self, n):
        b = b""
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise EOFError
            b += c
        return b

    def _reader(self):
        try:
            while True:
                n = struct.unpack("<I", self._recvn(4))[0]
                f = self._recvn(n)
                if f[0] == K_STATUS:
                    self.status = f[1]
                    log("virtio status 0x%02x" % f[1])
                    self.status_ev.set()
                elif f[0] == K_RESPONSE:
                    typ, ep, data = f[1], f[2], f[3:]
                    if self.wire:
                        self.wire.write(
                            "< %s ep=%02x len=%d %s\n"
                            % (RNAME.get(typ, typ), ep, len(data), data[:64].hex())
                        )
                    self.q(ep).put((typ, data))
                else:
                    log("unknown frame kind", hex(f[0]))
        except (EOFError, OSError) as e:
            self.dead = True
            log("link closed", repr(e))


# ------------------------------------------------------------------- USB host
class Stall(Exception):
    pass


class UsbHost:
    def __init__(self, link):
        self.l = link

    def wait(self, ep, timeout=10.0):
        try:
            return self.l.q(ep).get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("no response on ep %02x" % ep)

    def reset(self):
        self.l.send(0, T_RESET)
        deadline = time.time() + 10
        while time.time() < deadline:
            for ep in list(self.l.rsp):
                try:
                    typ, d = self.l.q(ep).get_nowait()
                except queue.Empty:
                    continue
                if typ == R_RESET:
                    log("reset done (response ep %02x)" % ep)
                    return
                log(
                    "during reset: %s ep=%02x len=%d"
                    % (RNAME.get(typ, typ), ep, len(d))
                )
            time.sleep(0.02)
        raise TimeoutError("no reset response")

    def control(
        self, bmRequestType, bRequest, wValue, wIndex, wLength, data=b"", timeout=10.0
    ):
        setup = struct.pack("<BBHHH", bmRequestType, bRequest, wValue, wIndex, wLength)
        self.l.send(0, T_XFER, setup + data)
        typ, d = self.wait(0, timeout)
        if typ == R_STALL:
            raise Stall("stall on request %02x/%02x" % (bmRequestType, bRequest))
        if typ != R_XFER:
            raise RuntimeError("control: unexpected %s" % RNAME.get(typ, typ))
        return d

    def get_descriptor(self, dtype, index, length, lang=0):
        return self.control(0x80, 6, (dtype << 8) | index, lang, length)

    def get_string(self, index):
        if not index:
            return None
        d = self.get_descriptor(3, index, 255, 0x0409)
        return d[2 : d[0]].decode("utf-16-le", "replace")

    def bulk_out(self, ep, data, timeout=10.0):
        self.l.send(ep, T_XFER, data)
        typ, d = self.wait(ep, timeout)
        if typ != R_XFER:
            raise RuntimeError("bulk out ep %02x: %s" % (ep, RNAME.get(typ, typ)))

    def in_token(self, ep, length):
        self.l.send(ep, T_XFER, b"", length)


def parse_config(d):
    cfg = {
        "wTotalLength": struct.unpack_from("<H", d, 2)[0],
        "bNumInterfaces": d[4],
        "bConfigurationValue": d[5],
        "iConfiguration": d[6],
        "bmAttributes": d[7],
        "bMaxPower": d[8],
        "interfaces": [],
    }
    o, cur = 0, None
    while o + 2 <= len(d):
        ln, ty = d[o], d[o + 1]
        if ln == 0:
            break
        if ty == 4:
            cur = {
                "bInterfaceNumber": d[o + 2],
                "bAlternateSetting": d[o + 3],
                "bNumEndpoints": d[o + 4],
                "class": d[o + 5],
                "subclass": d[o + 6],
                "protocol": d[o + 7],
                "iInterface": d[o + 8],
                "endpoints": [],
            }
            cfg["interfaces"].append(cur)
        elif ty == 5 and cur is not None:
            cur["endpoints"].append(
                {
                    "bEndpointAddress": d[o + 2],
                    "bmAttributes": d[o + 3],
                    "wMaxPacketSize": struct.unpack_from("<H", d, o + 4)[0],
                }
            )
        o += ln
    return cfg


# --------------------------------------------------------------------- usbmux
MUX_VERSION, MUX_CONTROL, MUX_SETUP, MUX_TCP = 0, 1, 2, 6
TH_FIN, TH_SYN, TH_RST, TH_PUSH, TH_ACK = 1, 2, 4, 8, 0x10
MUX_MAGIC = 0xFEEDFACE
OUT_MAX = 16384  # the guest's mux reads are 32 KiB; never exceed a posted read
WINDOW = 131072


class Stream:
    def __init__(self, mux, sport, dport):
        self.mux, self.sport, self.dport = mux, sport, dport
        self.tx_seq, self.tx_ack = 0, 0
        self.state = "syn"
        self.ev = threading.Event()
        self.rx = bytearray()
        self.cv = threading.Condition()
        self.closed = False
        self.peer_ack = 0  # highest ack the guest has sent (our bytes it has taken)
        self.peer_win = 65535 << 8  # the guest's advertised receive window, bytes
        self.sent_total = 0
        self.acked_total = 0

    def send(self, data):
        mtu = OUT_MAX - 16 - 20
        for i in range(0, len(data), mtu):
            chunk = data[i : i + mtu]
            # flow control: never put more than the guest's window in flight
            deadline = time.time() + 60
            with self.cv:
                while (
                    self.sent_total - self.acked_total + len(chunk) > self.peer_win
                    and not self.closed
                ):
                    left = deadline - time.time()
                    if left <= 0:
                        raise TimeoutError("stream %d: guest window stayed full" % self.dport)
                    self.cv.wait(min(left, 1.0))
            self.mux.send_tcp(self, TH_ACK, chunk)
            self.tx_seq = (self.tx_seq + len(chunk)) & 0xFFFFFFFF
            self.sent_total += len(chunk)

    def close(self):
        """Half-close our side (FIN) so the device sees EOF on a raw transfer."""
        self.mux.send_tcp(self, TH_FIN | TH_ACK)
        self.tx_seq = (self.tx_seq + 1) & 0xFFFFFFFF

    def recv_exact(self, n, timeout=20.0):
        deadline = time.time() + timeout
        with self.cv:
            while len(self.rx) < n:
                left = deadline - time.time()
                if self.closed or left <= 0:
                    raise TimeoutError(
                        "stream %d: wanted %d bytes, have %d (closed=%s)"
                        % (self.dport, n, len(self.rx), self.closed)
                    )
                self.cv.wait(left)
            b = bytes(self.rx[:n])
            del self.rx[:n]
            return b

    def recv_some(self, timeout):
        with self.cv:
            if not self.rx and not self.closed:
                self.cv.wait(timeout)
            b = bytes(self.rx)
            self.rx.clear()
            return b


class Mux:
    def __init__(self, usb, ep_out, ep_in):
        self.usb, self.ep_out, self.ep_in = usb, ep_out, ep_in
        self.version = 0
        self.tx_seq, self.rx_seq = 0, 0xFFFF
        self.buf = bytearray()
        self.streams = {}
        self.next_sport = 1
        self.version_ev = threading.Event()
        self.out_lock = threading.Lock()
        self.stop = False
        self.ctrl_msgs = []
        threading.Thread(target=self._in_pump, daemon=True).start()

    # -- bulk IN: keep two IN tokens outstanding, feed everything to the parser
    def _in_pump(self):
        q = self.usb.l.q(self.ep_in)
        outstanding = 0
        while not self.stop and not self.usb.l.dead:
            while outstanding < 2:
                self.usb.in_token(self.ep_in, 65536)
                outstanding += 1
            try:
                typ, d = q.get(timeout=0.5)
            except queue.Empty:
                continue
            outstanding -= 1
            if typ != R_XFER:
                log("mux in: %s" % RNAME.get(typ, typ))
                continue
            self.buf += d
            self._parse()

    def _parse(self):
        while True:
            hl = 16 if self.version >= 2 else 8
            if len(self.buf) < 8:
                return
            proto, length = struct.unpack_from(">II", self.buf, 0)
            if self.version < 2 and proto != MUX_VERSION:
                hl = 16
            if length < hl or length > 1 << 20:
                log("mux: bad packet header", self.buf[:16].hex())
                self.buf.clear()
                return
            if len(self.buf) < length:
                return
            pkt = bytes(self.buf[:length])
            del self.buf[:length]
            if hl == 16:
                magic, txs, rxs = struct.unpack_from(">IHH", pkt, 8)
                self.rx_seq = txs
            self._packet(proto, pkt[hl:])

    def _packet(self, proto, body):
        if proto == MUX_VERSION:
            major, minor, _ = struct.unpack_from(">III", body)
            log("mux: device version %d.%d" % (major, minor))
            self.version = major
            if major >= 2:
                self.send_packet(MUX_SETUP, b"\x07")
            self.version_ev.set()
        elif proto == MUX_CONTROL:
            t = body[0] if body else -1
            msg = body[1:].decode("utf-8", "replace").rstrip("\0")
            self.ctrl_msgs.append((t, msg))
            log("mux control type %d: %s" % (t, msg))
        elif proto == MUX_TCP:
            sport, dport, seq, ack, off, flags, win = struct.unpack_from(
                ">HHIIBBH", body
            )
            data = body[(off >> 4) * 4 :]
            st = self.streams.get(dport)
            if st is not None and flags & TH_ACK:
                # ack counts from our ISN+1 (the SYN took one); bytes of ours taken
                with st.cv:
                    delta = (ack - 1 - st.acked_total) & 0xFFFFFFFF
                    if delta < 0x80000000:
                        st.acked_total += delta
                    st.peer_win = max(win << 8, 1)
                    st.cv.notify_all()
            if st is None:
                log("mux: tcp for unknown stream", sport, dport, hex(flags))
                return
            if flags & TH_RST:
                log("mux: RST on port %d" % st.dport)
                st.state, st.closed = "rst", True
                st.ev.set()
                with st.cv:
                    st.cv.notify_all()
                return
            if st.state == "syn":
                if flags == (TH_SYN | TH_ACK):
                    st.tx_seq = (st.tx_seq + 1) & 0xFFFFFFFF
                    st.tx_ack = (seq + 1) & 0xFFFFFFFF
                    st.state = "open"
                    self.send_tcp(st, TH_ACK)
                    st.ev.set()
                else:
                    log("mux: unexpected flags %#x during connect" % flags)
                return
            if data:
                st.tx_ack = (seq + len(data)) & 0xFFFFFFFF
                with st.cv:
                    st.rx += data
                    st.cv.notify_all()
                self.send_tcp(st, TH_ACK)
            if flags & TH_FIN:
                st.closed = True
                with st.cv:
                    st.cv.notify_all()
        else:
            log("mux: proto %d, %d bytes" % (proto, len(body)))

    def send_packet(self, proto, body):
        # the sequence number is assigned under the lock so packets from several
        # threads leave in sequence order (M2b: the guest drops "duplicates")
        with self.out_lock:
            if self.version >= 2:
                hdr = struct.pack(
                    ">IIIHH", proto, 16 + len(body), MUX_MAGIC, self.tx_seq, self.rx_seq
                )
                self.tx_seq = (self.tx_seq + 1) & 0xFFFF
            else:
                hdr = struct.pack(">II", proto, 8 + len(body))
            pkt = hdr + body
            assert len(pkt) <= OUT_MAX
            self.usb.bulk_out(self.ep_out, pkt)

    def send_tcp(self, st, flags, data=b""):
        th = struct.pack(
            ">HHIIBBHHH",
            st.sport,
            st.dport,
            st.tx_seq,
            st.tx_ack,
            5 << 4,
            flags,
            WINDOW >> 8,
            0,
            0,
        )
        self.send_packet(MUX_TCP, th + data)

    def handshake(self, timeout=10):
        self.send_packet(MUX_VERSION, struct.pack(">III", 2, 0, 0))
        if not self.version_ev.wait(timeout):
            raise TimeoutError("no mux version reply")

    def connect(self, port, timeout=10):
        sport = self.next_sport
        self.next_sport += 1
        st = Stream(self, sport, port)
        self.streams[sport] = st
        self.send_tcp(st, TH_SYN)
        if not st.ev.wait(timeout) or st.state != "open":
            raise ConnectionError("connect to device port %d: %s" % (port, st.state))
        log("mux: connected to device port %d (local %d)" % (port, sport))
        return st


# ------------------------------------------------------------------ restored
def plist_send(st, obj):
    b = plistlib.dumps(obj, fmt=plistlib.FMT_XML)
    st.send(struct.pack(">I", len(b)) + b)


def plist_recv(st, timeout=20):
    n = struct.unpack(">I", st.recv_exact(4, timeout))[0]
    return plistlib.loads(st.recv_exact(n, timeout))


def jsonable(o):
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [jsonable(v) for v in o]
    if isinstance(o, bytes):
        return {"bytes_hex": o.hex()}
    if isinstance(o, (int, float, str, bool)) or o is None:
        return o
    return str(o)


# Host vocabulary for restored's is_host_compatible check: names and values as the
# public host sends them (idevicerestore restore_supported_{data,message}_types, LGPL,
# as listed in Stela session.rs/answers.rs, GPL-2.0). A missing required name fails
# StartRestore with AMRError 10 "This host version is unsupported" (seen in run m2a4).
DATA_TYPES = {
    "AuthInstallCACert": True,
    "BasebandBootData": False,
    "BasebandData": False,
    "BasebandStackData": False,
    "BasebandUpdaterOutputData": False,
    "BootabilityBundle": False,
    "BootabilityBundleV2": False,
    "BuildIdentityDict": False,
    "BuildIdentityDictV2": False,
    "Cryptex1LocalPolicy": True,
    "DataType": False,
    "DeviceRestoreInfoPreflight": False,
    "DiagData": False,
    "EANData": False,
    "FDRMemoryCommit": False,
    "FDRTrustData": False,
    "FUDData": False,
    "FileData": False,
    "FileDataDone": False,
    "FirmwareUpdaterData": False,
    "FirmwareUpdaterDataV2": False,
    "FirmwareUpdaterDataV3": True,
    "FirmwareUpdaterPreflight": True,
    "GrapeFWData": False,
    "HPMFWData": False,
    "HostSystemTime": True,
    "KernelCache": False,
    "MessageUseStreamedImageFile": True,
    "NORData": False,
    "NitrogenFWData": True,
    "OpalFWData": False,
    "OverlayRootDataCount": False,
    "OverlayRootDataForKey": True,
    "OverlayRootDataForKeyIndex": True,
    "PeppyFWData": True,
    "PersonalizedBootObjectV3": False,
    "PersonalizedData": True,
    "ProvisioningData": False,
    "RamdiskFWData": True,
    "ReceiptManifest": True,
    "RecoveryOSASRImage": True,
    "RecoveryOSAppleLogo": True,
    "RecoveryOSDeviceTree": True,
    "RecoveryOSFileAssetImage": True,
    "RecoveryOSIBEC": True,
    "RecoveryOSIBootFWFilesImages": True,
    "RecoveryOSImage": True,
    "RecoveryOSKernelCache": True,
    "RecoveryOSLocalPolicy": True,
    "RecoveryOSOverlayRootDataCount": False,
    "RecoveryOSRootTicketData": True,
    "RecoveryOSStaticTrustCache": True,
    "RecoveryOSVersionData": True,
    "RestoreLocalPolicy": True,
    "RootData": False,
    "RootTicket": False,
    "S3EOverride": False,
    "SourceBootObjectV3": False,
    "SourceBootObjectV4": False,
    "SourceBootObjectV5": False,
    "SsoServiceTicket": False,
    "StockholmPostflight": False,
    "SystemImageCanonicalMetadata": False,
    "SystemImageData": False,
    "SystemImageRootHash": False,
    "URLAsset": True,
    "USBCFWData": False,
    "USBCOverride": False,
    "UpdateVolumeOverlayRootDataCount": True,
}
MESSAGE_TYPES = {
    "AsyncDataRequestMsg": True,
    "AsyncWait": True,
    "BBUpdateStatusMsg": False,
    "CheckpointMsg": True,
    "CrashLog": True,
    "DataRequestMsg": False,
    "FDRSubmit": True,
    "MsgType": False,
    "PreviousRestoreLogMsg": False,
    "ProgressMsg": False,
    "ProvisioningAck": False,
    "ProvisioningInfo": False,
    "ProvisioningStatusMsg": False,
    "ReceivedFinalStatusMsg": False,
    "RestoreAttestation": True,
    "RestoreProtocol": True,
    "RestoredCrash": True,
    "StatusMsg": False,
}
ASYNC_DATA_TYPES = ["RecoveryOSASRImage", "SystemImageData", "URLAsset"]


def probe_start(mux, step):
    """StartRestore, then record every message until the first data request."""
    import uuid

    st = mux.connect(62078)
    plist_send(st, {"Request": "QueryType", "Label": "vusbhost"})
    log("QueryType (2nd) ->", plist_recv(st))
    opts = {
        "AutoBootDelay": 0,
        "BootImageType": "UserOrInternal",
        "CreateFilesystemPartitions": True,
        "FlashNOR": False,
        "SystemImageType": "User",
        "UpdateBaseband": False,
        "PersonalizedDuringPreflight": True,
        "RootToInstall": False,
        "UUID": str(uuid.uuid4()).upper(),
        "AuthInstallRestoreVariant": "Customer Erase Install (IPSW)",
        "MacOSVariant": "macOS Customer",
        "AuthInstallRecoveryOSVariant": "macOS Customer",
        "SystemPartitionSize": 13100,
        "SupportedDataTypes": DATA_TYPES,
        "SupportedMessageTypes": MESSAGE_TYPES,
        "SupportedAsyncDataTypes": {k: True for k in ASYNC_DATA_TYPES},
    }
    plist_send(
        st,
        {
            "Request": "StartRestore",
            "RestoreProtocolVersion": 15,
            "RestoreOptions": opts,
            "Label": "vusbhost",
        },
    )
    msgs = []
    deadline = time.time() + 150
    while time.time() < deadline:
        try:
            m = plist_recv(st, max(1, deadline - time.time()))
        except Exception as e:
            log("probe: read ended (%r)" % e)
            break
        msgs.append(m)
        log("probe <- %s" % json.dumps(jsonable(m))[:1500])
        if m.get("MsgType") in ("DataRequestMsg", "AsyncDataRequestMsg"):
            log("probe: first data request %r, stopping here" % m.get("DataType"))
            break
    step("probe_start", ok=True, messages=msgs)


def log_reader(st, path, stop):
    with open(path, "ab") as f:
        while not stop.is_set() and not st.closed:
            b = st.recv_some(0.5)
            if b:
                f.write(b)
                f.flush()


def main():
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("sock")
    ap.add_argument("--out", default=".")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--keys", default="HardwareInfo,SavedDebugInfo")
    ap.add_argument("--no-logport", action="store_true")
    ap.add_argument(
        "--probe-start",
        action="store_true",
        help="after the handshake, send StartRestore and log restored's messages up to "
        "its first DataRequestMsg, answering nothing (no ticket, no image is sent)",
    )
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    LOG = open(os.path.join(a.out, "vusbhost.log"), "w")
    wire = open(os.path.join(a.out, "wire.txt"), "w", buffering=1)
    result = {"steps": []}

    def step(name, **kw):
        result["steps"].append(dict(name=name, t=round(time.time() - T0, 3), **kw))
        with open(os.path.join(a.out, "result.json"), "w") as f:
            json.dump(jsonable(result), f, indent=1)

    link = VLink(a.sock, wire)
    link.query()
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        if link.status is not None and link.status & 4:
            break
        link.status_ev.wait(1)
        link.status_ev.clear()
    if link.status is None or not link.status & 4:
        log("guest never set DRIVER_OK")
        step("driver_ok", ok=False)
        return 2
    log("guest driver is up (DRIVER_OK)")
    step("driver_ok", ok=True, status=link.status)
    usb = UsbHost(link)
    usb.reset()
    step("reset", ok=True)

    dev = usb.get_descriptor(1, 0, 18)
    (
        bl,
        bt,
        bcdUSB,
        dcls,
        dsub,
        dproto,
        mps0,
        vid,
        pid,
        bcdDev,
        iMan,
        iProd,
        iSer,
        ncfg,
    ) = struct.unpack("<BBHBBBBHHHBBBB", dev)
    info = dict(
        bcdUSB=hex(bcdUSB),
        cls=dcls,
        sub=dsub,
        proto=dproto,
        mps0=mps0,
        idVendor=hex(vid),
        idProduct=hex(pid),
        bcdDevice=hex(bcdDev),
        bNumConfigurations=ncfg,
    )
    log("device descriptor", info)
    # No SET_ADDRESS: the virtual controller has no address phase and never
    # answers one (handleSetAddressPre/PostStatusPhase are empty in the guest).
    info["manufacturer"] = usb.get_string(iMan)
    info["product"] = usb.get_string(iProd)
    info["serial"] = usb.get_string(iSer)
    log("strings", info["manufacturer"], "|", info["product"], "|", info["serial"])
    cfgs = []
    for i in range(ncfg):
        h = usb.get_descriptor(2, i, 9)
        total = struct.unpack_from("<H", h, 2)[0]
        c = parse_config(usb.get_descriptor(2, i, total))
        c["name"] = usb.get_string(c["iConfiguration"])
        for itf in c["interfaces"]:
            itf["name"] = usb.get_string(itf["iInterface"])
        cfgs.append(c)
        log(
            'config %d value %d "%s": %s'
            % (
                i,
                c["bConfigurationValue"],
                c["name"],
                ", ".join(
                    'if%d alt%d %02x/%02x/%02x "%s" eps %s'
                    % (
                        x["bInterfaceNumber"],
                        x["bAlternateSetting"],
                        x["class"],
                        x["subclass"],
                        x["protocol"],
                        x["name"],
                        [hex(e["bEndpointAddress"]) for e in x["endpoints"]],
                    )
                    for x in c["interfaces"]
                ),
            )
        )
    info["configurations"] = cfgs
    step("enumerate", ok=True, device=info)

    pick = None
    for c in cfgs:
        for itf in c["interfaces"]:
            if (itf["class"], itf["subclass"], itf["protocol"]) == (
                0xFF,
                0xFE,
                2,
            ) and len(itf["endpoints"]) >= 2:
                pick = (c, itf)
                break
        if pick:
            break
    if not pick:
        log("no usbmux interface (ff/fe/02) found")
        step("find_mux", ok=False)
        return 3
    c, itf = pick
    ep_in = [
        e["bEndpointAddress"]
        for e in itf["endpoints"]
        if e["bEndpointAddress"] & 0x80 and e["bmAttributes"] & 3 == 2
    ][0]
    ep_out = [
        e["bEndpointAddress"]
        for e in itf["endpoints"]
        if not e["bEndpointAddress"] & 0x80 and e["bmAttributes"] & 3 == 2
    ][0]
    usb.control(0x00, 9, c["bConfigurationValue"], 0, 0)  # SET_CONFIGURATION
    if itf["bAlternateSetting"]:
        usb.control(0x01, 11, itf["bAlternateSetting"], itf["bInterfaceNumber"], 0)
    log(
        "configuration %d set; mux interface %d, bulk out %02x in %02x"
        % (c["bConfigurationValue"], itf["bInterfaceNumber"], ep_out, ep_in)
    )
    step(
        "set_configuration",
        ok=True,
        config=c["bConfigurationValue"],
        ep_out=ep_out,
        ep_in=ep_in,
    )
    time.sleep(0.5)

    mux = Mux(usb, ep_out, ep_in)
    mux.handshake()
    step("mux_version", ok=True, version=mux.version)
    stop = threading.Event()
    if not a.no_logport:
        try:
            lst = mux.connect(5000)
            threading.Thread(
                target=log_reader,
                args=(lst, os.path.join(a.out, "restored-port5000.log"), stop),
                daemon=True,
            ).start()
            step("logport_connect", ok=True)
        except Exception as e:
            log("log port 5000:", repr(e))
            step("logport_connect", ok=False, error=repr(e))

    st = mux.connect(62078)
    step("restored_connect", ok=True)
    plist_send(st, {"Request": "QueryType", "Label": "vusbhost"})
    r = plist_recv(st)
    log("QueryType ->", r)
    step("QueryType", ok=r.get("Type") == "com.apple.mobile.restored", reply=r)
    for k in [x for x in a.keys.split(",") if x]:
        plist_send(st, {"Request": "QueryValue", "QueryKey": k, "Label": "vusbhost"})
        r = plist_recv(st)
        log("QueryValue %s -> %s" % (k, json.dumps(jsonable(r))[:2000]))
        step("QueryValue:" + k, ok="Error" not in r, reply=r)
    plist_send(st, {"Request": "Goodbye", "Label": "vusbhost"})
    try:
        r = plist_recv(st, 10)
        log("Goodbye ->", r)
        step("Goodbye", ok=True, reply=r)
    except Exception as e:
        log("Goodbye: no reply (%r)" % e)
        step("Goodbye", ok=False, error=repr(e))
    if a.probe_start:
        probe_start(mux, step)
    time.sleep(3)
    stop.set()
    step("done", ok=True, mux_control=mux.ctrl_msgs)
    log("M2a handshake complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
