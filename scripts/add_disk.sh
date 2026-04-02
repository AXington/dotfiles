#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "This script must be run as root." >&2
    exit 1
fi

DISK="${1:-/dev/sdc}"
PARTITION="${DISK}1"

echo "Formatting disk: $DISK"
sed -e 's/\s*\([\+0-9a-zA-Z]*\).*/\1/' << FDISK_EOF | fdisk "${DISK}"
  o # clear the in memory partition table
  n # new partition
  p # primary partition
  1 # partition number 1
    # default - start at beginning of disk
    # partition whole disk
  p # print the in-memory partition table
  w # write the partition table
  q # and we're done
FDISK_EOF

echo "New partition at ${PARTITION}"
mkfs -t ext4 "${PARTITION}"
mkdir -p /data
mount "${PARTITION}" /data

# Verify the mount succeeded before touching fstab
mountpoint -q /data || { echo "Mount verification failed — not writing to fstab." >&2; exit 1; }

DISK_UUID="$(blkid -o value -s UUID "${PARTITION}")"

# Idempotent: only append if this UUID isn't already in fstab
if grep -q "UUID=${DISK_UUID}" /etc/fstab; then
    echo "UUID=${DISK_UUID} already present in /etc/fstab, skipping."
else
    echo "UUID=${DISK_UUID}  /data  ext4  defaults,discard  1  2" >> /etc/fstab
    echo "Added ${PARTITION} (UUID=${DISK_UUID}) to /etc/fstab, mounted at /data."
fi
