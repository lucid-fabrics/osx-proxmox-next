"""The host GPU list for the optional pass-through: parsed from `lspci -Dnnk`."""
from __future__ import annotations

import subprocess

from osx_proxmox_next import stela_gpus
from osx_proxmox_next.stela_gpus import HostGpu, list_host_gpus, parse_lspci

LSPCI = """\
0000:00:00.0 Host bridge [0600]: Advanced Micro Devices, Inc. [AMD] Starship/Matisse Root Complex [1022:1480]
\tKernel driver in use: ccp
0000:0a:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA104 [GeForce RTX 3060 Ti] [10de:2486] (rev a1)
\tSubsystem: eVga.com. Corp. Device [3842:3663]
\tKernel driver in use: vfio-pci
0000:0a:00.1 Audio device [0403]: NVIDIA Corporation GA104 High Definition Audio Controller [10de:228b] (rev a1)
\tKernel driver in use: vfio-pci
0000:0c:00.0 Display controller [0380]: Advanced Micro Devices, Inc. [AMD/ATI] Device [1002:73ff]
0000:0d:00.0 3D controller [0302]: NVIDIA Corporation GA102 [10de:2204]
\tKernel driver in use: nouveau
0000:0e:00.1 VGA compatible controller [0300]: Example Corp second function [1234:5678]
"""


def test_cards_are_found_by_class_with_the_host_driver_that_holds_them():
    gpus = parse_lspci(LSPCI)
    assert gpus == [
        HostGpu("0000:0a:00", "NVIDIA Corporation GA104 [GeForce RTX 3060 Ti]", "vfio-pci"),
        HostGpu("0000:0c:00", "Advanced Micro Devices, Inc. [AMD/ATI] Device", ""),
        HostGpu("0000:0d:00", "NVIDIA Corporation GA102", "nouveau"),
    ]


def test_the_label_names_the_slot_and_the_driver():
    gpus = parse_lspci(LSPCI)
    assert gpus[0].label == "0000:0a:00 NVIDIA Corporation GA104 [GeForce RTX 3060 Ti] (vfio-pci)"
    assert gpus[1].label == "0000:0c:00 Advanced Micro Devices, Inc. [AMD/ATI] Device"


def test_no_gpu_in_the_output_gives_an_empty_list():
    assert parse_lspci("0000:00:00.0 Host bridge [0600]: X [1:2]\n") == []


def test_list_host_gpus_reads_lspci(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=LSPCI, stderr="")

    monkeypatch.setattr(stela_gpus.subprocess, "run", fake_run)
    assert [g.slot for g in list_host_gpus()] == ["0000:0a:00", "0000:0c:00", "0000:0d:00"]
    assert calls == [["lspci", "-Dnnk"]]


def test_a_host_without_lspci_has_no_gpu_choices(monkeypatch):
    def broken(*a, **kw):
        raise FileNotFoundError("lspci")

    monkeypatch.setattr(stela_gpus.subprocess, "run", broken)
    assert list_host_gpus() == []
