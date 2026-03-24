---
sidebar_position: 4
title: Shared Storage
---

# Shared Storage

Use the `--iso-dir` flag to store OpenCore and recovery images on shared storage (NAS, NFS, CIFS) instead of each node's local filesystem.

## Why Shared Storage

By default, images are stored in `/var/lib/vz/template/iso` on each Proxmox node. In a multi-node cluster, every node downloads its own copy. With `--iso-dir`, you download once and reuse everywhere.

| Approach | Downloads per Version | Disk Usage |
|----------|----------------------|------------|
| Default (local) | 1 per node | ~1 GB per node |
| Shared (`--iso-dir`) | 1 total | ~1 GB total |

## Using `--iso-dir`

### CLI

```bash
osx-next-cli apply --execute \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm \
  --iso-dir /mnt/pve/nas/template/iso
```

### Download to Shared Storage

```bash
osx-next-cli download --macos sequoia --dest /mnt/pve/nas/template/iso
```

### TUI

The wizard auto-detects storage pools including NAS mounts under `/mnt/pve/*/template/iso`. Select your shared storage in step 3.

## Smart Caching

The tool checks for existing assets before downloading:

- `opencore-osx-proxmox-vm.iso` or `opencore-{version}.iso`
- `{version}-recovery.img` or `{version}-recovery.iso`

If the file already exists in the target directory, the download is skipped entirely. Creating a second VM with the same macOS version reuses cached images.

## Multi-Node Cluster Setup

1. **Mount shared storage** on every Proxmox node (NFS, CIFS, or GlusterFS)

   ```bash
   # Example NFS mount in /etc/fstab
   nas:/volume1/proxmox /mnt/pve/nas nfs defaults 0 0
   ```

2. **Add as Proxmox storage** in the web UI or via CLI:

   ```bash
   pvesm add dir nas --path /mnt/pve/nas --content iso,images
   ```

3. **Download once** from any node:

   ```bash
   osx-next-cli download --macos sequoia --dest /mnt/pve/nas/template/iso
   ```

4. **Create VMs** from any node using the shared path:

   ```bash
   osx-next-cli apply --execute \
     --vmid 910 --name macos-sequoia --macos sequoia \
     --cores 8 --memory 16384 --disk 128 \
     --bridge vmbr0 --storage local-lvm \
     --iso-dir /mnt/pve/nas/template/iso
   ```

:::note
The `--storage` flag controls where the VM disk is created (e.g., `local-lvm`). The `--iso-dir` flag controls where OpenCore and recovery images are read from. These are independent settings.
:::

## Typical NAS Paths

| Storage Type | Common Path |
|-------------|-------------|
| NFS mount | `/mnt/pve/nas/template/iso` |
| CIFS/SMB mount | `/mnt/pve/smb-share/template/iso` |
| Local default | `/var/lib/vz/template/iso` |

:::warning
Ensure the shared storage mount is available on every node before creating VMs. If the mount is missing at boot time, the VM will fail to start.
:::
