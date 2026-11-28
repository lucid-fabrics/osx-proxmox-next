#!/usr/bin/env python3
"""Print the Kernel patch Find and Replace hex for the patched AppleVPBootPolicy verifier.

FIND is Apple's root public point (the same constant mkpolicy-hack.py patches in the KDK); REPLACE is the
public point of hackkey.pem. Neither is an Apple file: the point is a public key value.
"""
from __future__ import annotations

import os
import sys

from cryptography.hazmat.primitives import serialization

ROOT_POINT = bytes.fromhex(
    "04668902cd8adfcc1cb13298dca0b5745d62f67657dba329b1bbf173c7b3cd797917"
    "adc5f6c07b4494dcb38a434c6fa66efb4540c0447745c8dcd14a42b14d8da205418e"
    "6a9070970cdf665526254e0945713d01347060d72829f55cacbd011677")


def main():
    key_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hackkey.pem")
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), None)
    point = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    print("PATCH_FIND=%s" % ROOT_POINT.hex())
    print("PATCH_REPL=%s" % point.hex())


if __name__ == "__main__":
    main()
