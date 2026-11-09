#!/bin/bash
set -euo pipefail
mkdir -p /ceph/run /ceph/log /etc/ceph
ln -sf /ceph/ceph.conf /etc/ceph/ceph.conf
case "$1" in
mon)
  if [ ! -f /ceph/mon/ready ]; then
    mkdir -p /ceph/mon
    monmaptool --create --clobber --fsid "$FSID" --add a "$MON_A" --add b "$MON_B" --add c "$MON_C" /ceph/monmap
    ceph-mon --mkfs -i "$MON_ID" --monmap /ceph/monmap --mon-data /ceph/mon
    touch /ceph/mon/ready
  fi
  exec ceph-mon -f -i "$MON_ID" --mon-data /ceph/mon
  ;;
mgr)
  mkdir -p /ceph/mgr
  exec ceph-mgr -f -i "$MON_ID" --mgr-data /ceph/mgr
  ;;
osd)
  until ceph status >/dev/null 2>&1; do sleep 1; done
  if [ ! -f /ceph/osd/ready ]; then
    mkdir -p /ceph/osd
    truncate -s 16G /ceph/block
    ceph osd create "$OSD_UUID" "$OSD_ID"
    ceph-osd -i "$OSD_ID" --mkfs --osd-uuid "$OSD_UUID"
    ceph osd crush add "osd.$OSD_ID" 1 root=default host="$STORAGE_HOST"
    touch /ceph/osd/ready
  fi
  exec ceph-osd -f -i "$OSD_ID"
  ;;
rgw*) exec radosgw -f --name "client.$1" ;;
*) echo 'Unknown role' >&2; exit 1 ;;
esac
