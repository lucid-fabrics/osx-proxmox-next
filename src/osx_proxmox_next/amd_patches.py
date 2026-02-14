"""AMD kernel patches from AMD-OSX/AMD_Vanilla.

These 25 patches fix Intel-specific instructions in the XNU kernel that crash
on AMD CPUs.  The first 4 are cpuid_cores_per_package patches whose Replace
field needs the VM core count injected at byte offset 1.

Source: https://github.com/AMD-OSX/AMD_Vanilla (master, kernel 17.0.0–25.99.99)
"""

from __future__ import annotations

# Index of core-count byte inside each cpuid_cores_per_package Replace field.
_CORE_BYTE_OFFSET = 1

# Number of cpuid_cores_per_package patches (always the first N in the list).
_CORE_PATCH_COUNT = 4

_PATCHES: list[dict] = [
    # ── cpuid_cores_per_package (patches 0-3) ─────────────────────────
    {
        "Arch": "x86_64",
        "Base": "_cpuid_set_info",
        "Comment": "algrey | Force cpuid_cores_per_package to constant (user-specified) | 10.13-10.14",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xc1\xe8\x1a\x00\x00\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xfd\xff\x00\x00\x00',
        "MaxKernel": "18.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'\xb8\x00\x00\x00\x00\x00',
        "ReplaceMask": b'\xff\xff\xff\xff\xff\x00',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "_cpuid_set_info",
        "Comment": "algrey | Force cpuid_cores_per_package to constant (user-specified) | 10.15-11.0",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xc1\xe8\x1a\x00\x00\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xfd\xff\x00\x00\x00',
        "MaxKernel": "20.99.99",
        "MinKernel": "19.0.0",
        "Replace": b'\xba\x00\x00\x00\x00\x00',
        "ReplaceMask": b'\xff\xff\xff\xff\xff\x00',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "_cpuid_set_info",
        "Comment": "algrey | Force cpuid_cores_per_package to constant (user-specified) | 12.0-13.2",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xc1\xe8\x1a\x00\x00\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xfd\xff\x00\x00\x00',
        "MaxKernel": "22.3.99",
        "MinKernel": "21.0.0",
        "Replace": b'\xba\x00\x00\x00\x00\x90',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "_cpuid_set_info",
        "Comment": "algrey | Force cpuid_cores_per_package to constant (user-specified) | 13.3+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xc1\xe8\x1a\x00\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xfd\xff\x00\x00',
        "MaxKernel": "25.99.99",
        "MinKernel": "22.4.0",
        "Replace": b'\xba\x00\x00\x00\x00',
        "ReplaceMask": b'\xff\xff\xff\xff\xff',
        "Skip": 0,
    },
    # ── Remaining 21 patches (no core-count injection) ────────────────
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _commpage_populate | Remove rdmsr | 10.13+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xb9\xa0\x01\x00\x00\x0f2',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "25.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'f\x90f\x90f\x90\x90',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _cpuid_set_cache_info | Set CPUID proper instead of 4 | 10.13+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xb8\x04\x00\x00\x00D\x89\xf1D\x89',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "25.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'\xb8\x1d\x00\x00\x80D\x89\xf1D\x89',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _cpuid_set_generic_info | Remove wrmsr(0x8B) | 10.13+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xb9\x8b\x00\x00\x001\xc01\xd2\x0f0',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "25.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'f\x90f\x90f\x90f\x90f\x90\x90',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _cpuid_set_generic_info | Replace rdmsr(0x8B) with constant 186 | 10.13+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xb9\x8b\x00\x00\x00\x0f2',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "25.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'\xba\xba\x00\x00\x00f\x90',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _cpuid_set_generic_info | Set flag=1 | 10.13+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xb9\x17\x00\x00\x00\x0f2\xc1\xea\x12\x80\xe2\x07',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "25.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'\xb2\x01f\x0f\x1f\x84\x00\x00\x00\x00\x00f\x90',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _cpuid_set_generic_info | Disable check to allow leaf7 | 10.13+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\x00:\x0f\x82',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "23.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'\x00\x00\x0f\x82',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _cpuid_set_generic_info | Disable check to allow leaf7 | 15.0+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\x00\x05\x0f\x82',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "25.99.99",
        "MinKernel": "24.0.0",
        "Replace": b'\x00\x00\x0f\x82',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _cpuid_set_info | GenuineIntel to AuthenticAMD | 10.13-11.0",
        "Count": 1,
        "Enabled": True,
        "Find": b'GenuineIntel\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "20.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'AuthenticAMD\x00',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "_cpuid_set_info",
        "Comment": "Goldfish64, algrey | Bypass GenuineIntel check panic | 12.0+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\x00\x00\x00\x00\x00\x001\xd2\xb3\x01',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\x00\x00\x00\x00\x00\x00\xff\xff\xff\xff',
        "MaxKernel": "25.99.99",
        "MinKernel": "21.0.0",
        "Replace": b'\x90\x90\x90\x90\x90\x901\xd2\xb3\x01',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _cpuid_set_cpufamily | Force CPUFAMILY_INTEL_PENRYN | 10.13-11.2",
        "Count": 1,
        "Enabled": True,
        "Find": b'1\xdb\x80=\x00\x00\x00\x00\x06u\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xff\xff\xff\x00\x00\x00\xff\xff\xff\x00',
        "MaxKernel": "20.3.0",
        "MinKernel": "17.0.0",
        "Replace": b'\xbb\xbcO\xeax\xe9]\x00\x00\x00\x90',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "_cpuid_set_info ",
        "Comment": "algrey | _cpuid_set_cpufamily | Force CPUFAMILY_INTEL_PENRYN | 11.3+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\x80=\x00\x00\x00\x00\x06u',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xff\x00\x00\x00\x00\xff\xff',
        "MaxKernel": "25.99.99",
        "MinKernel": "20.4.0",
        "Replace": b'\xba\xbcO\xeax1\xdb\xeb',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _i386_init | Remove 3 rdmsr calls | 10.13+",
        "Count": 0,
        "Enabled": True,
        "Find": b'\xb9\x99\x01\x00\x00\x0f2H\xc1\xe2 \x89\xc6H\t\xd6\xb9\x98\x01\x00\x00\x0f2H\xc1\xe2 \x89\xc0H\t\xc2\xbfX\x021\x051\xc9E1\xc0',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "25.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'f\x0f\x1f\x84\x00\x00\x00\x00\x00f\x0f\x1f\x84\x00\x00\x00\x00\x00f\x0f\x1f\x84\x00\x00\x00\x00\x00f\x0f\x1f\x84\x00\x00\x00\x00\x00f\x0f\x1fD\x00\x00',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey, XLNC | Remove version check and panic | 10.13+",
        "Count": 1,
        "Enabled": True,
        "Find": b'%\xfc\x00\x00\x00\x83\xf8\x13',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'',
        "MaxKernel": "25.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'%\xfc\x00\x00\x00\x0f\x1f\x00',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "__ZN11IOPCIBridge13probeBusGatedEP14probeBusParams",
        "Comment": "CaseySJ | probeBusGated | Disable 10 bit tags | 12.0+",
        "Count": 1,
        "Enabled": True,
        "Find": b'\xe0\x11r\x00',
        "Identifier": "com.apple.iokit.IOPCIFamily",
        "Limit": 0,
        "Mask": b'\xf0\xff\xff\xf0',
        "MaxKernel": "25.99.99",
        "MinKernel": "21.0.0",
        "Replace": b'\x00\x00\x03\x00',
        "ReplaceMask": b'\x00\x00\x0f\x00',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "__ZN17IOPCIConfigurator18IOPCIIsHotplugPortEP16IOPCIConfigEntry",
        "Comment": "CaseySJ | IOPCIIsHotplugPort | Fix PCI bus enumeration on AM5 | 13.0+",
        "Count": 1,
        "Enabled": False,
        "Find": b'\x84\x00uK',
        "Identifier": "com.apple.iokit.IOPCIFamily",
        "Limit": 0,
        "Mask": b'\xff\x00\xff\xff',
        "MaxKernel": "25.99.99",
        "MinKernel": "22.0.0",
        "Replace": b'\x00\x00\xeb\x00',
        "ReplaceMask": b'\x00\x00\xff\x00',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "Visual | thread_quantum_expire, thread_unblock, thread_invoke | Remove non-monotonic time panic | 12.0+",
        "Count": 3,
        "Enabled": True,
        "Find": b'H\x00\x00\x00\x02\x00\x00H\x00\x00X\x00\x00\x00\x0f\x00\x00\x00\x00\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\x00\x00\x0f\xff\xff\xff\xff\x00\x00\xff\x00\x00\x00\xff\x00\x00\x00\x00\x00',
        "MaxKernel": "25.99.99",
        "MinKernel": "21.0.0",
        "Replace": b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00f\x90f\x90f\x90',
        "ReplaceMask": b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xff\xff\xff\xff\xff',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "Visual | thread_invoke, thread_dispatch | Remove non-monotonic time panic | 12.0+",
        "Count": 2,
        "Enabled": True,
        "Find": b'H\x00\x00\x80\x04\x00\x00\x0f\x00\x00\x00\x00\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'H\x00\x00\xf0\xff\xff\xff\xff\x00\x00\x00\x00\x00',
        "MaxKernel": "25.99.99",
        "MinKernel": "21.0.0",
        "Replace": b'\x00\x00\x00\x00\x00\x00\x00f\x90f\x90f\x90',
        "ReplaceMask": b'\x00\x00\x00\x00\x00\x00\x00\xff\xff\xff\xff\xff\xff',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "algrey | _mtrr_update_action | fix PAT | 10.13+",
        "Count": 0,
        "Enabled": True,
        "Find": b'\x89\xc0\x81\xe2\xff\xff\x00\xff\x81\xca\x00\x00\x01\x00\xb9w\x02\x00\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xff\xff\xff\xff\xff\x0f\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff',
        "MaxKernel": "23.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'\xb9w\x02\x00\x00\xb8\x06\x01\x07\x00\xba\x06\x01\x07\x00\x0f\x1f@\x00',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "Shaneee | _mtrr_update_action | Fix PAT | 10.13+",
        "Count": 0,
        "Enabled": False,
        "Find": b'\x89\xc0\x81\xe2\xff\xff\x00\xff\x81\xca\x00\x00\x01\x00\xb9w\x02\x00\x00',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xff\xff\xff\xff\xff\x0f\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff',
        "MaxKernel": "23.99.99",
        "MinKernel": "17.0.0",
        "Replace": b'\xb9w\x02\x00\x00\xb8\x06\x06\x06\x06\xba\x06\x06\x06\x06\x0f0\x0f\t',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "Algrey / Zormeister | _mtrr_update_action | Fix PAT | 15.0+",
        "Count": 0,
        "Enabled": True,
        "Find": b'\x89\xc0\x81\xe2\xff\xff\x00\xff\x81\xca\x00\x00\x00\x00\x0f0',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff',
        "MaxKernel": "25.99.99",
        "MinKernel": "24.0.0",
        "Replace": b'\x89\xc0\xb8\x06\x01\x07\x00\xba\x06\x01\x07\x00\x0f0\x90\x90',
        "ReplaceMask": b'',
        "Skip": 0,
    },
    {
        "Arch": "x86_64",
        "Base": "",
        "Comment": "Shaneee / Zormeister | _mtrr_update_action | Fix PAT | 15.0+",
        "Count": 0,
        "Enabled": False,
        "Find": b'\x89\xc0\x81\xe2\xff\xff\x00\xff\x81\xca\x00\x00\x00\x00\x0f0',
        "Identifier": "kernel",
        "Limit": 0,
        "Mask": b'\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff',
        "MaxKernel": "25.99.99",
        "MinKernel": "24.0.0",
        "Replace": b'\x89\xc0\xb8\x06\x06\x06\x06\xba\x06\x06\x06\x06\x0f0\x90\x90',
        "ReplaceMask": b'',
        "Skip": 0,
    },
]


def get_kernel_patches(cores: int) -> list[dict]:
    """Return AMD kernel patches with core count injected.

    The first 4 patches encode cpuid_cores_per_package — the core count
    byte is at Replace[1] (little-endian 32-bit immediate operand).
    """
    import copy

    patches = copy.deepcopy(_PATCHES)
    for i in range(_CORE_PATCH_COUNT):
        replace = bytearray(patches[i]["Replace"])
        replace[_CORE_BYTE_OFFSET] = cores & 0xFF
        patches[i]["Replace"] = bytes(replace)
    return patches


def serialize_patches(cores: int) -> str:
    """Return patches as a Python expression string for embedding in bash scripts.

    Uses ``import base64; base64.b64decode(...)`` for bytes fields so the output
    survives bash shell quoting (no backslash escapes).
    """
    import base64 as _b64

    patches = get_kernel_patches(cores)
    parts = []
    for p in patches:
        items = []
        for k, v in p.items():
            if isinstance(v, bytes):
                encoded = _b64.b64encode(v).decode()
                items.append(f'"{k}": __b64("{encoded}")')
            elif isinstance(v, bool):
                items.append(f'"{k}": {v}')
            elif isinstance(v, int):
                items.append(f'"{k}": {v}')
            elif isinstance(v, str):
                items.append(f'"{k}": "{v}"')
        parts.append("{" + ", ".join(items) + "}")
    return "[" + ", ".join(parts) + "]"


def serialize_preamble() -> str:
    """Return the Python preamble needed before eval-ing ``serialize_patches()`` output.

    Defines the ``__b64`` helper used by the serialized patch dicts.
    """
    return "import base64 as _b64; __b64=lambda s: _b64.b64decode(s); "
