from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shlex import quote as shquote

from .assets import resolve_opencore_path, resolve_recovery_or_installer_path
from .defaults import CpuInfo, detect_cpu_info
from .domain import SUPPORTED_MACOS, VmConfig, PlanStep, validate_config
from .infrastructure import ProxmoxAdapter
from .script_renderer import (
    _APPLE_OSK,
    _build_oc_disk_script,
    _partprobe_retry_snippet,
)
from .smbios_planner import (
    _encode_smbios_value,
    _populate_smbios,
)


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
    # +kvm_pv_unhalt and +kvm_pv_eoi are KVM paravirtualization hints that macOS
    # does not implement. They are no-ops at best and can cause instability on
    # some firmware versions. Omit them for a cleaner passthrough profile.
    return "-cpu host,kvm=on,vendor=GenuineIntel,+hypervisor,+invtsc,vmware-cpuid-freq=on"


def build_plan(config: VmConfig) -> list[PlanStep]:
    issues = validate_config(config)
    if issues:
        raise ValueError(f"Invalid VM config: {'; '.join(issues)}")

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
    config = _populate_smbios(config)

    ctx = _DiskBuildContext(
        config=config,
        vmid=vmid,
        is_amd=is_amd,
        recovery_raw=recovery_raw,
        opencore_path=opencore_path,
        oc_disk=oc_disk,
    )
    steps = [
        *_network_steps(config, vmid, cpu_flag),
        *_disk_steps(ctx, macos_label),
        *_boot_steps(config, vmid),
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


def _network_steps(config: VmConfig, vmid: str, cpu_flag: str) -> list[PlanStep]:
    """VM shell creation with network and CPU config."""
    return [
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
                "--net0", f"{config.net_model},bridge={config.bridge},firewall=0",
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
    ]


@dataclass
class _DiskBuildContext:
    """Shared parameters for disk-building plan steps."""
    config: VmConfig
    vmid: str
    is_amd: bool
    recovery_raw: Path
    opencore_path: Path
    oc_disk: Path


def _opencore_steps(ctx: _DiskBuildContext) -> list[PlanStep]:
    """Build and import the OpenCore EFI disk."""
    return [
        PlanStep(
            title="Build OpenCore boot disk",
            argv=[
                "bash", "-c",
                _build_oc_disk_script(
                    ctx.opencore_path, ctx.recovery_raw, ctx.oc_disk, ctx.config.macos,
                    ctx.is_amd, ctx.config.cores, ctx.config.verbose_boot,
                    apple_services=ctx.config.apple_services,
                    smbios_serial=ctx.config.smbios_serial,
                    smbios_uuid=ctx.config.smbios_uuid,
                    smbios_mlb=ctx.config.smbios_mlb,
                    smbios_rom=ctx.config.smbios_rom,
                    smbios_model=ctx.config.smbios_model,
                ),
            ],
        ),
        PlanStep(
            title="Import and attach OpenCore disk",
            argv=[
                "bash", "-c",
                "if qm disk import --help >/dev/null 2>&1; then IMPORT_CMD='qm disk import'; else IMPORT_CMD='qm importdisk'; fi && "
                f'REF=$($IMPORT_CMD {shquote(ctx.vmid)} {shquote(str(ctx.oc_disk))} {shquote(ctx.config.storage)} 2>&1 | '
                "grep 'successfully imported' | grep -oP \"'\\K[^']+\") && "
                f'qm set {shquote(ctx.vmid)} --ide0 "$REF",media=disk && '
                # Fix GPT header corruption from thin-provisioned LVM importdisk
                'DEV=$(pvesm path "$REF") && '
                f'dd if={shquote(str(ctx.oc_disk))} of="$DEV" bs=512 count=2048 conv=notrunc 2>/dev/null',
            ],
        ),
    ]


def _recovery_steps(
    config: VmConfig,
    vmid: str,
    recovery_raw: Path,
    macos_label: str,
) -> list[PlanStep]:
    """Stamp and import the macOS recovery image."""
    return [
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
    ]


def _disk_steps(ctx: _DiskBuildContext, macos_label: str) -> list[PlanStep]:
    """EFI/TPM disk, main disk, OpenCore build/import, and recovery import."""
    return [
        PlanStep(
            title="Attach EFI + TPM",
            argv=[
                "qm", "set", ctx.vmid,
                "--efidisk0", f"{ctx.config.storage}:0,efitype=4m,pre-enrolled-keys=0",
                "--tpmstate0", f"{ctx.config.storage}:0,version=v2.0",
            ],
        ),
        PlanStep(
            title="Create main disk",
            argv=["qm", "set", ctx.vmid, "--virtio0", f"{ctx.config.storage}:{ctx.config.disk_gb}"],
        ),
        *_opencore_steps(ctx),
        *_recovery_steps(ctx.config, ctx.vmid, ctx.recovery_raw, macos_label),
    ]


def _boot_steps(config: VmConfig, vmid: str) -> list[PlanStep]:
    """Hardware profile, boot order, and start VM."""
    return [
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
            argv=["qm", "set", vmid, "--net0", f"{config.net_model},bridge={config.bridge},macaddr={config.static_mac},firewall=0"],
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
    if adapter is None:
        from .services.proxmox_service import get_proxmox_adapter
        adapter = get_proxmox_adapter()
    runtime = adapter
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
