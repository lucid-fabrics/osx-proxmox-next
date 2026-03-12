from __future__ import annotations

import base64
import copy
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shlex import join, quote as shquote

from .assets import resolve_opencore_path, resolve_recovery_or_installer_path
from .defaults import CpuInfo, detect_cpu_info
from .domain import SUPPORTED_MACOS, VmConfig, validate_config
from .infrastructure import ProxmoxAdapter
from .smbios import generate_mac, generate_rom_from_mac, generate_smbios, generate_vmgenid, model_for_macos


_APPLE_OSK = "ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc"


def _partprobe_retry_snippet(loop_var: str) -> str:
    """Return bash snippet that retries partprobe up to 10 times for slow storage."""
    return (
        f"partprobe ${loop_var} 2>/dev/null; "
        f"for _i in $(seq 1 10); do ls ${{{loop_var}}}p* &>/dev/null && break; "
        f"sleep 1; partprobe ${loop_var} 2>/dev/null; "
        f"blockdev --rereadpt ${loop_var} 2>/dev/null; done"
    )


def _sanitize_smbios(val: str, *, allow_comma: bool = False) -> str:
    """Strip anything that isn't alphanumeric, hyphen, colon, or period.

    Only *model* names need commas (e.g. ``MacPro7,1``).
    """
    if allow_comma:
        return re.sub(r"[^a-zA-Z0-9\-:,.]", "", val)
    return re.sub(r"[^a-zA-Z0-9\-:.]", "", val)


@dataclass
class PlanStep:
    title: str
    argv: list[str]
    risk: str = "safe"

    @property
    def command(self) -> str:
        return join(self.argv)


def _cpu_args(cpu: CpuInfo, override: str = "") -> str:
    """Return QEMU -cpu flag tailored to host CPU.

    If *override* is provided (e.g. ``Skylake-Server-IBRS``), it is used
    directly as the -cpu model with standard KVM flags.

    AMD always uses Cascadelake-Server emulation (no native macOS support).
    Intel hybrid CPUs (12th gen+) also need Cascadelake-Server because macOS
    hardware validation fails on P+E core topology with correct SMBIOS.

    Non-hybrid Intel uses ``-cpu host`` for native passthrough.

    Ref: luchina-gabriel/OSX-PROXMOX (battle-tested on ~5k installs).
    """
    if override:
        return (
            f"-cpu {override},"
            "kvm=on,"
            "vendor=GenuineIntel,"
            "+invtsc,"
            "vmware-cpuid-freq=on"
        )
    if cpu.needs_emulated_cpu:
        return (
            "-cpu Cascadelake-Server,"
            "vendor=GenuineIntel,"
            "+invtsc,"
            "-pcid,"
            "-hle,-rtm,"
            "-avx512f,-avx512dq,-avx512cd,-avx512bw,-avx512vl,-avx512vnni,"
            "kvm=on,"
            "vmware-cpuid-freq=on"
        )
    return "-cpu host,kvm=on,vendor=GenuineIntel,+kvm_pv_unhalt,+kvm_pv_eoi,+hypervisor,+invtsc,vmware-cpuid-freq=on"


def build_plan(config: VmConfig) -> list[PlanStep]:
    issues = validate_config(config)
    if issues:
        raise ValueError(f"Invalid VM config: {'; '.join(issues)}")

    # Work on a copy so callers don't see SMBIOS side effects.
    config = copy.copy(config)

    meta = SUPPORTED_MACOS[config.macos]
    vmid = str(config.vmid)

    recovery_raw = resolve_recovery_or_installer_path(config)
    opencore_path = resolve_opencore_path(config.macos)
    oc_disk = opencore_path.parent / f"opencore-{config.macos}-vm{vmid}.img"

    macos_label = meta["label"]
    cpu = detect_cpu_info()
    cpu_flag = _cpu_args(cpu, override=config.cpu_model)
    # AMD needs kernel patches (AppleCpuPmCfgLock / AppleXcpmCfgLock).
    # Hybrid Intel does NOT — it only needs Cascadelake-Server emulation.
    is_amd = cpu.vendor == "AMD"

    # Pre-generate SMBIOS identity so downstream steps just read config fields.
    _populate_smbios(config)

    steps = [
        PlanStep(
            title="Create VM shell",
            argv=[
                "qm", "create", vmid,
                "--name", config.name,
                "--ostype", "other",
                "--machine", "q35",
                "--bios", "ovmf",
                "--cores", str(config.cores),
                "--sockets", "1",
                "--memory", str(config.memory_mb),
                "--cpu", "host",
                "--balloon", "0",
                "--agent", "enabled=1",
                "--net0", f"vmxnet3,bridge={config.bridge},firewall=0",
            ],
        ),
        PlanStep(
            title="Apply macOS hardware profile",
            argv=[
                "qm", "set", vmid,
                "--args",
                f'-device isa-applesmc,osk="{_APPLE_OSK}" '
                "-smbios type=2 -device qemu-xhci -device usb-kbd -device usb-tablet "
                "-global nec-usb-xhci.msi=off -global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off "
                f"{cpu_flag}",
                "--vga", "std",
                "--tablet", "1",
                "--scsihw", "virtio-scsi-pci",
            ],
        ),
        *_smbios_steps(config, vmid),
        *_apple_services_steps(config, vmid),
        PlanStep(
            title="Attach EFI + TPM",
            argv=[
                "qm", "set", vmid,
                "--efidisk0", f"{config.storage}:0,efitype=4m,pre-enrolled-keys=0",
                "--tpmstate0", f"{config.storage}:0,version=v2.0",
            ],
        ),
        PlanStep(
            title="Create main disk",
            argv=["qm", "set", vmid, "--virtio0", f"{config.storage}:{config.disk_gb}"],
        ),
        PlanStep(
            title="Build OpenCore boot disk",
            argv=[
                "bash", "-c",
                _build_oc_disk_script(
                    opencore_path, recovery_raw, oc_disk, config.macos,
                    is_amd, config.cores, config.verbose_boot,
                    apple_services=config.apple_services,
                    smbios_serial=config.smbios_serial,
                    smbios_uuid=config.smbios_uuid,
                    smbios_mlb=config.smbios_mlb,
                    smbios_rom=config.smbios_rom,
                    smbios_model=config.smbios_model,
                ),
            ],
        ),
        PlanStep(
            title="Import and attach OpenCore disk",
            argv=[
                "bash", "-c",
                "if qm disk import --help >/dev/null 2>&1; then IMPORT_CMD='qm disk import'; else IMPORT_CMD='qm importdisk'; fi && "
                f'REF=$($IMPORT_CMD {shquote(vmid)} {shquote(str(oc_disk))} {shquote(config.storage)} 2>&1 | '
                "grep 'successfully imported' | grep -oP \"'\\K[^']+\") && "
                f'qm set {shquote(vmid)} --ide0 "$REF",media=disk && '
                # Fix GPT header corruption from thin-provisioned LVM importdisk
                'DEV=$(pvesm path "$REF") && '
                f'dd if={shquote(str(oc_disk))} of="$DEV" bs=512 count=2048 conv=notrunc 2>/dev/null',
            ],
        ),
        PlanStep(
            title="Stamp recovery with Apple icon flavour",
            argv=[
                "bash", "-c",
                # Trap to clean up loop device and temp dir on failure
                "RLOOP=''; OC_REC=$(mktemp -d) && "
                "trap '[ -n \"$RLOOP\" ] && { umount $OC_REC 2>/dev/null; losetup -d $RLOOP 2>/dev/null; }; rm -rf $OC_REC' EXIT; "
                # Fix HFS+ dirty/lock flags so Linux mounts read-write,
                # then write OpenCore .contentFlavour + .contentDetails
                "python3 -c '"
                "import struct,subprocess; "
                f'img="{recovery_raw}"; '
                "out=subprocess.check_output([\"sgdisk\",\"-i\",\"1\",img],text=True); "
                "start=int([l for l in out.splitlines() if \"First sector\" in l][0].split(\":\")[1].split(\"(\")[0].strip()); "
                "off=start*512+1024+4; "
                "f=open(img,\"r+b\"); f.seek(off); "
                "a=struct.unpack(\">I\",f.read(4))[0]; "
                "a=(a|0x100)&~0x800; "
                "f.seek(off); f.write(struct.pack(\">I\",a)); "
                "f.close(); print(\"HFS+ flags fixed\")' && "
                # Cleanup stale loops from previous failed runs
                f'for lo in $(losetup -j {shquote(str(recovery_raw))} -O NAME --noheadings 2>/dev/null); do umount -l $lo* 2>/dev/null; losetup -d $lo 2>/dev/null; done; '
                f'RLOOP=$(losetup -fP --show {shquote(str(recovery_raw))}) && '
                "{ [ -b \"$RLOOP\" ] || { echo 'ERROR: losetup failed for recovery image. Hints: modprobe loop; losetup -a; ls /dev/loop*'; false; }; } && "
                # Retry partprobe up to 5 times for slow storage (partprobe first, then check)
                f"{_partprobe_retry_snippet('RLOOP')} && "
                "{ [ -b \"${RLOOP}p1\" ] || { echo \"ERROR: ${RLOOP}p1 not found after partprobe. Hint: Try running the script again (slow storage)\"; false; }; } && "
                "mount -t hfsplus -o rw ${RLOOP}p1 $OC_REC && "
                "{ mountpoint -q $OC_REC || { echo \"ERROR: $OC_REC is not mounted. Hints: file ${RLOOP}p1; blkid ${RLOOP}p1; dmesg | tail -5\"; false; }; } && "
                # Set custom name via .contentDetails in blessed directory
                "mkdir -p $OC_REC/System/Library/CoreServices && "
                "rm -f $OC_REC/System/Library/CoreServices/.contentDetails 2>/dev/null; "
                f"printf '%s' '{macos_label}' > $OC_REC/System/Library/CoreServices/.contentDetails && "
                # Copy macOS installer icon as .VolumeIcon.icns for boot picker
                "ICON=$(find $OC_REC -path '*/Install macOS*/Contents/Resources/InstallAssistant.icns' 2>/dev/null | head -1) && "
                "if [ -n \"$ICON\" ]; then "
                "rm -f $OC_REC/.VolumeIcon.icns; "
                "cp \"$ICON\" $OC_REC/.VolumeIcon.icns && "
                "echo \"Volume icon set from $ICON\"; "
                "else echo \"No InstallAssistant.icns found, using default icon\"; fi && "
                "{ umount $OC_REC || umount -l $OC_REC; } && losetup -d $RLOOP",
            ],
        ),
        PlanStep(
            title="Import and attach macOS recovery",
            argv=[
                "bash", "-c",
                "if qm disk import --help >/dev/null 2>&1; then IMPORT_CMD='qm disk import'; else IMPORT_CMD='qm importdisk'; fi && "
                f'REF=$($IMPORT_CMD {shquote(vmid)} {shquote(str(recovery_raw))} {shquote(config.storage)} 2>&1 | '
                "grep 'successfully imported' | grep -oP \"'\\K[^']+\") && "
                f'qm set {shquote(vmid)} --ide2 "$REF",media=disk',
            ],
        ),
        PlanStep(
            title="Set boot order",
            argv=["qm", "set", vmid, "--boot", "order=ide2;virtio0;ide0"],
        ),
        PlanStep(
            title="Start VM",
            argv=["qm", "start", vmid],
            risk="action",
        ),
    ]

    if meta["channel"] == "preview":
        steps.insert(
            0,
            PlanStep(
                title="Preview warning",
                argv=[
                    "echo",
                    f"Notice: {meta['label']} uses preview assets. Verify OpenCore and recovery sources before production use.",
                ],
                risk="warn",
            ),
        )
    return steps


def render_script(config: VmConfig, steps: list[PlanStep]) -> str:
    meta = SUPPORTED_MACOS[config.macos]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# Generated by osx-proxmox-next on {now}",
        f"# Target: {meta['label']} (channel={meta['channel']})",
        f"# VMID: {config.vmid}",
        "",
    ]
    for idx, step in enumerate(steps, start=1):
        lines.append(f"echo '[{idx}/{len(steps)}] {step.title}'")
        lines.append(step.command)
        lines.append("")
    return "\n".join(lines)


def _plist_patch_script(
    verbose_boot: bool = False,
    is_amd: bool = False,
    apple_services: bool = False,
    smbios_serial: str = "",
    smbios_uuid: str = "",
    smbios_mlb: str = "",
    smbios_rom: str = "",
    smbios_model: str = "",
) -> str:
    """Return an inline python3 -c script that patches OpenCore's config.plist."""
    # AMD: flip power management locks for Cascadelake-Server emulation
    amd_patch = ""
    if is_amd:
        amd_patch = (
            "kq=p[\"Kernel\"][\"Quirks\"]; "
            "kq[\"AppleCpuPmCfgLock\"]=True; "
            "kq[\"AppleXcpmCfgLock\"]=True; "
        )

    # PlatformInfo for Apple Services (iMessage, FaceTime, iCloud)
    platforminfo = ""
    if apple_services and smbios_serial:
        s_serial = _sanitize_smbios(smbios_serial)
        s_model = _sanitize_smbios(smbios_model, allow_comma=True)
        s_uuid = _sanitize_smbios(smbios_uuid)
        s_mlb = _sanitize_smbios(smbios_mlb)
        s_rom = _sanitize_smbios(smbios_rom)
        platforminfo = (
            "pi=p.setdefault(\"PlatformInfo\",{}).setdefault(\"Generic\",{}); "
            f"pi[\"SystemSerialNumber\"]=\"{s_serial}\"; "
            f"pi[\"SystemProductName\"]=\"{s_model}\"; "
            f"pi[\"SystemUUID\"]=\"{s_uuid}\"; "
            f"pi[\"MLB\"]=\"{s_mlb}\"; "
            f"pi[\"ROM\"]=bytes.fromhex(\"{s_rom}\"); "
            "p[\"PlatformInfo\"][\"UpdateSMBIOS\"]=True; "
            "p[\"PlatformInfo\"][\"UpdateDataHub\"]=True; "
        )

    return (
        "python3 -c '"
        "import plistlib; "
        "import os; oc_dest=os.environ[\"OC_DEST\"]; "
        "f=open(oc_dest+\"/EFI/OC/config.plist\",\"rb\"); p=plistlib.load(f); f.close(); "
        "p[\"Misc\"][\"Security\"][\"ScanPolicy\"]=0; "
        "p[\"Misc\"][\"Security\"][\"DmgLoading\"]=\"Any\"; "
        "p[\"Misc\"][\"Security\"][\"SecureBootModel\"]=\"Disabled\"; "
        "p[\"Misc\"][\"Boot\"][\"Timeout\"]=15; "
        "p[\"Misc\"][\"Boot\"][\"PickerAttributes\"]=17; "
        "p[\"Misc\"][\"Boot\"][\"HideAuxiliary\"]=True; "
        "p[\"Misc\"][\"Boot\"][\"PickerMode\"]=\"External\"; "
        "p[\"Misc\"][\"Boot\"][\"PickerVariant\"]=\"Acidanthera\\\\Syrah\"; "
        "p[\"NVRAM\"][\"Add\"][\"7C436110-AB2A-4BBB-A880-FE41995C9F82\"][\"csr-active-config\"]=b\"\\x67\\x0f\\x00\\x00\"; "
        f"p[\"NVRAM\"][\"Add\"][\"7C436110-AB2A-4BBB-A880-FE41995C9F82\"][\"boot-args\"]=\"keepsyms=1 debug=0x100{' -v' if verbose_boot else ''}\"; "
        "p[\"NVRAM\"][\"Add\"][\"7C436110-AB2A-4BBB-A880-FE41995C9F82\"][\"prev-lang:kbd\"]=\"en-US:0\".encode(); "
        "nv_del=p.setdefault(\"NVRAM\",{}).setdefault(\"Delete\",{}); "
        "nv_del[\"7C436110-AB2A-4BBB-A880-FE41995C9F82\"]=[\"csr-active-config\",\"boot-args\",\"prev-lang:kbd\"]; "
        "p[\"NVRAM\"][\"WriteFlash\"]=True; "
        "[k.update(Enabled=True) for k in p.get(\"Kernel\",{}).get(\"Add\",[]) if \"VirtualSMC\" in k.get(\"BundlePath\",\"\")]; "
        + amd_patch
        + platforminfo +
        "f=open(oc_dest+\"/EFI/OC/config.plist\",\"wb\"); plistlib.dump(p,f); f.close(); "
        "print(\"config.plist patched\")'"
    )


def _loop_cleanup_script(opencore_path: Path, dest: Path) -> str:
    """Return bash snippet for loop device trap and stale cleanup."""
    return (
        "SRC_LOOP=''; DEST_LOOP=''; "
        "OC_SRC=$(mktemp -d) && OC_DEST=$(mktemp -d) && export OC_SRC OC_DEST && "
        "trap 'umount $OC_SRC 2>/dev/null; umount $OC_DEST 2>/dev/null; "
        "[ -n \"$SRC_LOOP\" ] && losetup -d $SRC_LOOP 2>/dev/null; "
        "[ -n \"$DEST_LOOP\" ] && losetup -d $DEST_LOOP 2>/dev/null; "
        "rm -rf $OC_SRC $OC_DEST' EXIT; "
        "umount $OC_SRC 2>/dev/null; umount $OC_DEST 2>/dev/null; "
        f'for lo in $(losetup -j {shquote(str(opencore_path))} -O NAME --noheadings 2>/dev/null); do umount -l $lo* 2>/dev/null; losetup -d $lo 2>/dev/null; done; '
        f'for lo in $(losetup -j {shquote(str(dest))} -O NAME --noheadings 2>/dev/null); do umount -l $lo* 2>/dev/null; losetup -d $lo 2>/dev/null; done; '
    )


def _mount_source_oc_script(opencore_path: Path) -> str:
    """Return bash snippet to loop-mount the source OpenCore ISO."""
    return (
        f'SRC_LOOP=$(losetup -fP --show {shquote(str(opencore_path))}) && '
        "{ [ -b \"$SRC_LOOP\" ] || { echo 'ERROR: losetup failed for OpenCore source ISO. Hints: modprobe loop; losetup -a; ls /dev/loop*'; false; }; } && "
        f"{_partprobe_retry_snippet('SRC_LOOP')} && "
        "SRC_PART=$(blkid -o device $SRC_LOOP ${SRC_LOOP}p* 2>/dev/null "
        "| xargs -I{} sh -c 'blkid -s TYPE -o value {} 2>/dev/null | grep -q vfat && echo {}' "
        "| head -1); "
        "if [ -n \"$SRC_PART\" ]; then mount \"$SRC_PART\" $OC_SRC; "
        "else echo 'WARN: No vfat partition found on source ISO via blkid, trying raw mount'; mount $SRC_LOOP $OC_SRC; fi && "
        "{ mountpoint -q $OC_SRC || { echo \"ERROR: $OC_SRC is not mounted. Hints: file $SRC_LOOP; blkid $SRC_LOOP; dmesg | tail -5\"; false; }; } && "
    )


def _format_dest_oc_script(dest: Path) -> str:
    """Return bash snippet to create, format, and mount the destination OpenCore disk."""
    qp = shquote(str(dest))
    return (
        f'dd if=/dev/zero of={qp} bs=1M count=1024 && '
        f'sgdisk -Z {qp} && '
        f'sgdisk -n 1:0:0 -t 1:EF00 -c 1:OPENCORE {qp} && '
        f'DEST_LOOP=$(losetup -fP --show {qp}) && '
        "{ [ -b \"$DEST_LOOP\" ] || { echo 'ERROR: losetup failed for OpenCore destination disk. Hints: modprobe loop; losetup -a; ls /dev/loop*'; false; }; } && "
        f"{_partprobe_retry_snippet('DEST_LOOP')} && "
        "{ [ -b \"${DEST_LOOP}p1\" ] || { echo \"ERROR: ${DEST_LOOP}p1 not found after partprobe. Hint: Try running the script again (slow storage)\"; false; }; } && "
        "mkfs.fat -F 32 -n OPENCORE ${DEST_LOOP}p1 && "
        "mount ${DEST_LOOP}p1 $OC_DEST && "
        "{ mountpoint -q $OC_DEST || { echo \"ERROR: $OC_DEST is not mounted. Hints: file ${DEST_LOOP}p1; blkid ${DEST_LOOP}p1; dmesg | tail -5\"; false; }; } && "
    )


def _build_oc_disk_script(
    opencore_path: Path, recovery_path: Path, dest: Path, macos: str,
    is_amd: bool = False, cores: int = 4, verbose_boot: bool = False,
    apple_services: bool = False, smbios_serial: str = "",
    smbios_uuid: str = "", smbios_mlb: str = "", smbios_rom: str = "",
    smbios_model: str = "",
) -> str:
    """Build a bash script that creates a GPT+ESP OpenCore disk with patched config."""
    plist_script = _plist_patch_script(
        verbose_boot=verbose_boot, is_amd=is_amd,
        apple_services=apple_services, smbios_serial=smbios_serial,
        smbios_uuid=smbios_uuid, smbios_mlb=smbios_mlb,
        smbios_rom=smbios_rom, smbios_model=smbios_model,
    )

    return (
        _loop_cleanup_script(opencore_path, dest)
        + _format_dest_oc_script(dest)
        + _mount_source_oc_script(opencore_path)
        # Copy OpenCore files (including hidden files)
        + "cp -a $OC_SRC/. $OC_DEST/ && "
        # Validate EFI structure was copied
        "{ [ -d $OC_DEST/EFI/OC ] || { echo 'ERROR: OpenCore ISO does not contain expected EFI/OC directory. ISO may be corrupt.'; false; }; } && "
        # Patch config.plist
        + plist_script + " && "
        # Fix plistlib self-closing tags that OpenCore's OcXmlLib rejects
        "sed -i 's|<array/>|<array></array>|g; s|<dict/>|<dict></dict>|g; s|<data/>|<data></data>|g' $OC_DEST/EFI/OC/config.plist && "
        # Hide OC partition from boot picker (shown only when user presses Space)
        "echo Auxiliary > $OC_DEST/.contentVisibility && "
        # Cleanup mounts (lazy unmount fallback for busy mounts)
        "{ umount $OC_SRC || umount -l $OC_SRC; } && losetup -d $SRC_LOOP && "
        "{ umount $OC_DEST || umount -l $OC_DEST; } && losetup -d $DEST_LOOP"
    )


def _encode_smbios_value(value: str) -> str:
    """Base64-encode a value for Proxmox smbios1 fields."""
    return base64.b64encode(value.encode()).decode()


def _populate_smbios(config: VmConfig) -> None:
    """Pre-generate SMBIOS identity and Apple services fields on config.

    Called once at the top of build_plan so downstream helpers just read fields.
    """
    if config.no_smbios:
        return
    if not config.smbios_serial:
        identity = generate_smbios(config.macos, config.apple_services)
        config.smbios_serial = identity.serial
        config.smbios_uuid = identity.uuid
        config.smbios_model = identity.model
        config.smbios_mlb = identity.mlb
        config.smbios_rom = identity.rom
        if identity.mac and not config.static_mac:
            config.static_mac = identity.mac
    if not config.smbios_model:
        config.smbios_model = model_for_macos(config.macos)
    if config.apple_services:
        if not config.vmgenid:
            config.vmgenid = generate_vmgenid()
        if not config.static_mac:
            config.static_mac = generate_mac()
        config.smbios_rom = generate_rom_from_mac(config.static_mac)


def _smbios_steps(config: VmConfig, vmid: str) -> list[PlanStep]:
    if config.no_smbios:
        return []
    smbios_value = (
        f"uuid={config.smbios_uuid},"
        f"base64=1,"
        f"serial={_encode_smbios_value(config.smbios_serial)},"
        f"manufacturer={_encode_smbios_value('Apple Inc.')},"
        f"product={_encode_smbios_value(config.smbios_model)},"
        f"family={_encode_smbios_value('Mac')}"
    )
    return [
        PlanStep(
            title="Set SMBIOS identity",
            argv=["qm", "set", vmid, "--smbios1", smbios_value],
        ),
    ]


def _apple_services_steps(config: VmConfig, vmid: str) -> list[PlanStep]:
    """Configure vmgenid and static MAC for Apple services (iMessage, FaceTime, etc.)."""
    if not config.apple_services:
        return []
    return [
        PlanStep(
            title="Configure vmgenid for Apple services",
            argv=["qm", "set", vmid, "--vmgenid", config.vmgenid],
        ),
        PlanStep(
            title="Configure static MAC for Apple services",
            argv=["qm", "set", vmid, "--net0", f"vmxnet3,bridge={config.bridge},macaddr={config.static_mac},firewall=0"],
        ),
    ]


# ── VM Destroy ──────────────────────────────────────────────────────


@dataclass
class VmInfo:
    vmid: int
    name: str
    status: str  # "running" | "stopped"
    config_raw: str


def fetch_vm_info(vmid: int, adapter: ProxmoxAdapter | None = None) -> VmInfo | None:
    runtime = adapter or ProxmoxAdapter()
    status_result = runtime.run(["qm", "status", str(vmid)])
    if not status_result.ok:
        return None
    # Parse status line like "status: running" or "status: stopped"
    status = "stopped"
    for line in status_result.output.splitlines():
        if "running" in line.lower():
            status = "running"
            break
    config_result = runtime.run(["qm", "config", str(vmid)])
    config_raw = config_result.output if config_result.ok else ""
    # Parse name from config
    name = ""
    for line in config_raw.splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
            break
    return VmInfo(vmid=vmid, name=name, status=status, config_raw=config_raw)


def build_destroy_plan(vmid: int, purge: bool = False) -> list[PlanStep]:
    vid = str(vmid)
    destroy_argv = ["qm", "destroy", vid]
    if purge:
        destroy_argv.append("--purge")
    return [
        PlanStep(title="Stop VM", argv=["qm", "stop", vid], risk="warn"),
        PlanStep(title="Destroy VM", argv=destroy_argv, risk="warn"),
    ]
