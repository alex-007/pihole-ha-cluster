#!/bin/bash
set -euo pipefail
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
source ./pihole-ha-lease-sync
key=$T/key; printf 'secret' >"$key"
leases=$T/dhcp.leases; printf '1 00:11:22:33:44:55 192.0.2.4 host *\n' >"$leases"
state=$T/state; printf 'active=1\n' >"$state"
out=$T/snap
lease_snapshot_create "$leases" "$out" "$key" node-a 7 1000 "$state"
lease_snapshot_verify "$out" "$key" node-a 7 1000
! lease_snapshot_verify "$out" "$key" node-b 7 1000
printf tamper >>"$out.data"; ! lease_snapshot_verify "$out" "$key" node-a 7 1000
printf 'active=0\n' >"$state"
# Recreate a clean snapshot for apply.
printf 'active=1\n' >"$state"
lease_snapshot_create "$leases" "$out" "$key" node-a 7 1000 "$state"
printf 'active=0\n' >"$state"
lease_snapshot_apply "$out" "$T/dest" "$state"; cmp "$leases" "$T/dest"
printf 'active=1\n' >"$state"; ! lease_snapshot_apply "$out" "$T/dest2" "$state"
printf 'active=1\n' >"$state"
printf '1 00:11:22:33:44:55 192.0.2.5 host *\n' >"$T/new"
lease_snapshot_create "$T/new" "$T/newsnap" "$key" node-a 6 1000 "$state"
! lease_snapshot_accept "$T/newsnap" "$T/last" "$key" node-a 7 1000
echo ok
