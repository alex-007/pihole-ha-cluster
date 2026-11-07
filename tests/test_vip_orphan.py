#!/usr/bin/env python3
"""Run the real DNS-only election through orphan claim and handback cycles."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "pihole-ha").read_text()


def function(name):
    match = re.search(r"^" + name + r"\(\) \{\n.*?^\}", SOURCE, re.M | re.S)
    if not match:
        raise AssertionError(f"Missing production function: {name}")
    return match.group()


class OrphanCycleTests(unittest.TestCase):
    def run_cycles(self, cycles):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as state:
            setup = r'''
set -uo pipefail
unset EPOCHSECONDS
EPOCHSECONDS=100
VIP=10.0.0.9; IFACE=eth0; VIP_ENABLED=true; HA_ENABLED=true
GATEWAY=10.0.0.254; HEALTH_TIMEOUT=1
MY_IDX=1; NODES=(10.0.0.1 10.0.0.2); LOCAL_IP=10.0.0.2
ROLE=SECONDARY; ACTIVATE_AFTER=2; DEACTIVATE_AFTER=3
activate_count=0; deactivate_count=0
VIP_ORPHAN_AFTER=3; vip_orphan_count=0
VIP_PROBE_EVERY=10; vip_probe_next=0; vip_claimed_stranded=false
HEIR_ALIVE=false; HEIR_READY=false; ANSWERED=false
declare -A peer_ping peer_dns
for ip in "${NODES[@]}"; do peer_ping[$ip]=true; peer_dns[$ip]=true; done
ping() { return 0; }
log_info() { :; }; log_warn() { :; }; notify() { :; }
write_status() { printf 'STATUS %s\n' "$1"; }
has_vip() { [[ -f "$STATE/vip" ]]; }
add_vip() { touch "$STATE/vip"; echo CLAIM >> "$STATE/events"; }
remove_vip() { rm -f "$STATE/vip"; echo RELEASE >> "$STATE/events"; }
vip_is_answered() { echo PROBE >> "$STATE/events"; [[ "$ANSWERED" == true ]]; }
peer_ha_alive() { [[ "$HEIR_ALIVE" == true ]]; }
peer_answers_dns() { [[ "$HEIR_READY" == true ]]; }
build_dns_reason() { echo test; }
tick() { EPOCHSECONDS="$1"; dns_only_vip_cycle; wait; }
check() { if ! eval "$1"; then echo "CHECK FAILED: $1" >&2; exit 1; fi; }
'''
            script = setup + "\n" + "\n".join(function(name) for name in (
                "is_dns_healthy", "should_i_hold_vip", "dns_only_vip_cycle")) + "\n" + cycles
            result = subprocess.run(["bash"], input=script, text=True, capture_output=True,
                                    env=dict(os.environ, STATE=state))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_claim_hold_until_ha_and_dns_ready_then_debounced_handback(self):
        self.run_cycles(r'''
tick 100; check '(( vip_orphan_count == 1 ))'; check '! has_vip'
tick 101; tick 109
check '(( vip_orphan_count == 1 ))'
check '[[ $(grep -c PROBE "$STATE/events") == 1 ]]'
tick 110; check '(( vip_orphan_count == 2 ))'; check '! has_vip'
tick 120; check has_vip; check '[[ $vip_claimed_stranded == true ]]'
for t in 121 122 123 124; do tick "$t"; done
check has_vip; check '(( deactivate_count == 0 ))'
HEIR_ALIVE=true
tick 125; check has_vip; check '(( deactivate_count == 0 ))'
HEIR_READY=true
tick 126; tick 127; check has_vip
tick 128; check '! has_vip'; check '[[ $vip_claimed_stranded == false ]]'
check '[[ $(grep -c CLAIM "$STATE/events") == 1 ]]'
check '[[ $(grep -c RELEASE "$STATE/events") == 1 ]]'
''')

    def test_answer_resets_silence_count_and_probes_remain_rate_limited(self):
        self.run_cycles(r'''
tick 100; ANSWERED=true; tick 110
check '(( vip_orphan_count == 0 ))'
ANSWERED=false
tick 111; tick 119; check '(( vip_orphan_count == 0 ))'
tick 120; tick 130; check '! has_vip'
tick 140; check has_vip
check '[[ $(grep -c PROBE "$STATE/events") == 5 ]]'
''')

    def test_disabled_probe_does_not_claim_or_probe(self):
        self.run_cycles(r'''
VIP_PROBE_EVERY=0
for t in 100 110 120 130; do tick "$t"; done
check '! has_vip'; check '[[ ! -e "$STATE/events" ]]'
''')

    def test_unhealthy_local_dns_does_not_claim_or_probe(self):
        self.run_cycles(r'''
peer_dns[$LOCAL_IP]=false
for t in 100 110 120 130; do tick "$t"; done
check '! has_vip'; check '[[ ! -e "$STATE/events" ]]'
''')


if __name__ == "__main__":
    unittest.main()
