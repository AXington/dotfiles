#! /bin/bash

DISK=${1:-/dev/sdc}


echo "formatting disk"
sed -e 's/\s*\([\+0-9a-zA-Z]*\).*/\1/' << EOF | fdisk ${DISK}
  o # clear the in memory partition table
  n # new partition
  p # primary partition
  1 # partition number 1
    # default - start at beginning of disk 
    # partition whole disk
  p # print the in-memory partition table
  w # write the partition table
  q # and we're done
EOF

PARTITION=${DISK}1

echo "New partition at ${PARTITION}"

mkfs -t ext4 ${PARTITION}

mkdir /data

mount ${PARTITION} /data 

DISK_UUID=$(blkid -o value -s UUID ${PARTITION})

echo "UUID=${DISK_UUID}  /data ext4 defaults,discard 1 2" >> /etc/fstab
