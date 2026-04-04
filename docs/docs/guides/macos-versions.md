---
sidebar_position: 7
title: macOS Version Comparison
---

# macOS Version Comparison

OSX Proxmox Next supports four macOS versions. This guide helps you pick the right one for your setup.

## Overview

| Version | Stability | Apple Services | Performance | Recommended For |
|---------|-----------|----------------|-------------|-----------------|
| Ventura 13 | Excellent | Full (iCloud, iMessage, FaceTime) | Good | Older hardware, maximum compatibility |
| Sonoma 14 | Excellent | Full (iCloud, iMessage, FaceTime) | Very Good | Best all-around choice |
| Sequoia 15 | Good | Full with `--apple-services` (kernel patch auto-applied) | Very Good | Users who need latest features |
| Tahoe 26 | Beta | Full with `--apple-services` (kernel patch auto-applied) | Good | Early adopters, testing |

## Version Details

### Ventura 13

The most mature and battle-tested option.

**Pros:**
- Rock-solid stability in virtualized environments
- Full Apple Services support (iCloud, iMessage, FaceTime)
- Works well on older or limited hardware configurations
- Widest compatibility with existing software

**Cons:**
- No longer receiving feature updates from Apple
- Missing newer macOS features (iPhone mirroring, window tiling, etc.)
- Security updates will eventually stop

### Sonoma 14

The recommended default for most users.

**Pros:**
- Full Apple Services support (iCloud, iMessage, FaceTime)
- Modern feature set (widgets on desktop, screen sharing improvements)
- Excellent stability in Proxmox VMs

**Cons:**
- Slightly higher resource usage than Ventura
- Requires CryptexFixup kext to avoid `EXITBS:START` hang at boot (included automatically)

### Sequoia 15

For users who want the latest stable release.

**Pros:**
- Latest stable macOS with newest features
- iPhone mirroring, native window tiling
- Good VM performance

**Cons:**
- Apple Services require the kernel patch applied by `--apple-services` (community-attested, not officially verified)
- Requires CryptexFixup kext

### Tahoe 26

The bleeding-edge option, currently in beta.

**Pros:**
- Access to the newest macOS features before general availability
- Good for developers targeting the next macOS release

**Cons:**
- Beta software with potential bugs and instability
- Apple Services require the kernel patch applied by `--apple-services` (community-attested, not officially verified)
- Not recommended for production or daily-driver use
- May require updates to OpenCore configuration as betas progress

## Which Should I Choose?

Follow this decision path:

1. **Do you need Apple Services (iCloud, iMessage, FaceTime)?**
   - Yes, on Sequoia or Tahoe: Use `--apple-services` — a kernel patch is applied automatically and is community-attested to work. Sonoma 14 remains the safest choice with full verified support.
   - Yes, on Sonoma: **Sonoma 14** with `--apple-services` gives fully verified Apple Services support.
   - No: Continue to step 2.

2. **Do you want the latest macOS features?**
   - Yes, stable: **Sequoia 15** gives you the newest stable release.
   - Yes, bleeding-edge: **Tahoe 26** if you accept beta instability.
   - No: Continue to step 3.

3. **Are you running older or limited hardware?**
   - Yes: **Ventura 13** has the lightest resource footprint and widest compatibility.
   - No: **Sonoma 14** remains the best overall choice.

**Best overall: Sonoma 14.** It strikes the right balance between modern features, stability, and full Apple Services support.

## Hardware Attestation (Sequoia and Tahoe)

Starting with Sequoia 15, Apple performs hardware attestation checks during Apple ID sign-in. These checks use the `hv_vmm_present` sysctl — which normally returns `1` in a VM — to detect virtualized environments.

When `--apple-services` is enabled, an OpenCore `Kernel/Patch` is automatically injected that redirects `hv_vmm_present` to `hibernatecount` (always `0`), making DeviceCheck see what appears to be a physical machine.

Community reports indicate this resolves Apple ID, iCloud, iMessage, and FaceTime sign-in on Sequoia 15 and Tahoe 26. This is **not officially verified** — use Sonoma 14 if you need a guaranteed-working baseline.

Notes:
- `RestrictEvents.kext` with `revpatch=sbvmm` alone does **not** fix this
- App Store downloads and purchases work regardless
- The OS itself runs fine; only Apple account services are affected by attestation

## In-Place Upgrade Path

If you want Sequoia or Tahoe but prefer to establish your Apple Services session on Sonoma first (the fully-verified baseline), use this approach:

1. Install **Sonoma 14** using the wizard
2. Sign in to your Apple ID, activate iCloud and iMessage
3. Once signed in, upgrade in-place to **Sequoia 15** or **Tahoe 26** via System Settings > Software Update
4. Your Apple Services session carries over from the Sonoma sign-in

This is an alternative to using `--apple-services` directly on Sequoia or Tahoe. Both approaches work — use this path if you prefer the Sonoma-verified baseline before upgrading.

:::note
In-place upgrades preserve your data but take longer than a fresh install. Back up your VM (snapshot) before upgrading.
:::
