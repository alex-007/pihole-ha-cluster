"""Signed lease replication tests. Only the FTL state boundary is stubbed."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LeaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lease-sync-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'key').write_text('fixture-only-cluster-key-0123456789')
        (self.root / 'state').write_text('true')
        (self.root / 'leases').write_text('0 02:00:00:00:00:01 192.0.2.100 fixture *\n')

    def shell(self, commands, local='192.0.2.1', peers=''):
        env = dict(os.environ, TEST_ROOT=str(self.root), SOURCE_ROOT=str(ROOT),
                   LOCAL_IP=local, PEER_STATE=peers,
                   LEASE_SYNC_DIR=str(self.root / 'runtime'),
                   LEASE_SYNC_KEY=str(self.root / 'key'),
                   LEASE_SYNC_FILE=str(self.root / 'leases'),
                   LEASE_SYNC_UID=str(os.getuid()), LEASE_SYNC_GID=str(os.getgid()))
        script = '''set -uo pipefail
source "$SOURCE_ROOT/pihole-ha-platform"
source "$SOURCE_ROOT/pihole-ha-lease-sync"
get_dhcp() { cat "$TEST_ROOT/state"; }
set_dhcp() { printf '%s' "$1" > "$TEST_ROOT/state"; }
log_info() { :; }; log_warn() { :; }
NODES=(192.0.2.1 192.0.2.2)
declare -A peer_dhcp peer_sid
peer_dhcp[192.0.2.1]="$PEER_STATE"
peer_dhcp[192.0.2.2]="$PEER_STATE"
LEASE_SYNC_ENABLED=true
LEASE_SYNC_READY=true
''' + commands
        return subprocess.run(['bash', '-c', script], env=env, text=True, capture_output=True)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def publish(self):
        self.assert_success(self.shell('lease_sync_publish'))
        return self.root / 'runtime' / 'published'

    def test_yield_request_holds_owner_off_until_ready_peer_takes_over(self):
        import time
        self.assert_success(self.shell('LOCAL_IP=192.0.2.2; lease_sync_publish'))
        (self.root / 'runtime/yield').write_text('192.0.2.1 ' + str(int(time.time()) + 30) + '\n')
        result = self.shell('declare -A peer_api peer_dns; peer_api[192.0.2.1]=true; peer_dns[192.0.2.1]=true; '
                            'peer_dhcp[192.0.2.1]=false; remove_vip() { :; }; lease_sync_cycle; '
                            '[[ "${LEASE_SYNC_YIELDING:-false}" == true ]]', local='192.0.2.2')
        self.assert_success(result)
        self.assertEqual((self.root / 'state').read_text(), 'false')

    def test_startup_catches_survivor_state_even_after_survivor_stood_down(self):
        import http.server
        import threading
        import time
        self.assert_success(self.shell('LOCAL_IP=127.0.0.1; lease_sync_publish'))
        old = (self.root / 'runtime/cache').read_bytes()
        time.sleep(1.05)
        (self.root / 'leases').write_text('0 02:00:00:00:00:02 192.0.2.101 survivor *\n')
        self.assert_success(self.shell('LOCAL_IP=127.0.0.2; lease_sync_publish'))
        latest = (self.root / 'runtime/cache').read_bytes()
        (self.root / 'runtime/cache').write_bytes(old)
        (self.root / 'runtime/published').write_bytes(old)
        (self.root / 'runtime/last-owner').write_text('127.0.0.1\n')
        (self.root / 'leases').write_text('old-local-lease-file')

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(latest)

        server = http.server.HTTPServer(('127.0.0.2', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.shell('NODES=(127.0.0.1 127.0.0.2); LEASE_SYNC_READY=false; '
                                'peer_dhcp[127.0.0.2]=false; LEASE_SYNC_PORT=' + str(server.server_port) + '; lease_sync_cycle',
                                local='127.0.0.1')
            self.assert_success(result)
            self.assertEqual((self.root / 'state').read_text(), 'false')
            self.assertIn('192.0.2.101', (self.root / 'leases').read_text())
            self.assertEqual((self.root / 'runtime/cache').read_bytes(), latest)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_tampered_snapshot_does_not_replace_receiver_file(self):
        snapshot = self.publish()
        snapshot.write_bytes(snapshot.read_bytes() + b'tamper\n')
        (self.root / 'state').write_text('false')
        (self.root / 'leases').write_text('keep-existing-file')
        self.assertNotEqual(self.shell('lease_sync_import "$LEASE_SYNC_DIR/published" 192.0.2.1').returncode, 0)
        self.assertEqual((self.root / 'leases').read_text(), 'keep-existing-file')

    def test_wrong_cluster_key_is_rejected(self):
        self.publish()
        (self.root / 'state').write_text('false')
        (self.root / 'key').write_text('another-fixture-cluster-key-0123456789')
        self.assertNotEqual(self.shell('lease_sync_import "$LEASE_SYNC_DIR/published" 192.0.2.1').returncode, 0)

    def test_active_receiver_is_never_overwritten(self):
        self.publish()
        (self.root / 'leases').write_text('active-owner-file')
        self.assertNotEqual(self.shell('lease_sync_import "$LEASE_SYNC_DIR/published" 192.0.2.1').returncode, 0)
        self.assertEqual((self.root / 'leases').read_text(), 'active-owner-file')

    def test_wrong_owner_is_rejected_even_with_valid_signature(self):
        self.publish()
        (self.root / 'state').write_text('false')
        self.assertNotEqual(self.shell('lease_sync_import "$LEASE_SYNC_DIR/published" 192.0.2.2').returncode, 0)

    def test_empty_authoritative_snapshot_clears_released_leases(self):
        (self.root / 'leases').write_text('')
        self.publish()
        (self.root / 'state').write_text('false')
        (self.root / 'leases').write_text('old-released-record')
        self.assert_success(self.shell('lease_sync_import "$LEASE_SYNC_DIR/published" 192.0.2.1'))
        self.assertEqual((self.root / 'leases').read_bytes(), b'')

    def test_missing_cache_never_claims_vip_or_enables_dhcp(self):
        (self.root / 'state').write_text('false')
        result = self.shell('add_vip() { echo claimed > "$TEST_ROOT/claimed"; }; lease_sync_activate')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'claimed').exists())
        self.assertEqual((self.root / 'state').read_text(), 'false')

    def test_feature_is_disabled_without_configuration(self):
        self.assert_success(self.shell('lease_sync_load; lease_sync_cycle'))
        self.assertFalse((self.root / 'runtime/published').exists())

    def test_failed_role_catchup_blocks_reuse_of_own_old_snapshot(self):
        self.publish()
        result = self.shell('curl() { return 1; }; peer_dhcp[192.0.2.2]=true; lease_sync_cycle || true; '
                            'peer_dhcp[192.0.2.2]=false; lease_sync_prepare')
        self.assertNotEqual(result.returncode, 0)

    def test_activation_loads_leases_before_claiming_vip_and_enabling_dhcp(self):
        self.publish()
        (self.root / 'state').write_text('false')
        (self.root / 'leases').write_text('')
        result = self.shell('add_vip() { test -s "$LEASE_SYNC_FILE" && echo vip >> "$TEST_ROOT/events"; }; '
                            'set_dhcp() { echo "$1" >> "$TEST_ROOT/events"; }; lease_sync_activate')
        self.assert_success(result)
        self.assertEqual((self.root / 'events').read_text(), 'vip\ntrue\n')

    def test_dash_serves_snapshot_only_after_pihole_authentication(self):
        self.publish()
        config = self.root / 'config'
        config.write_text('LEASE_SYNC_ENABLED=true\n')
        env = dict(os.environ, LEASE_SYNC_DIR=str(self.root / 'runtime'), LEASE_SYNC_CONF=str(config),
                   HA_NODES='192.0.2.1,192.0.2.2')
        script = '''curl() {
case "$*" in
 *fixture-session*) printf '{"blocking":"enabled"}';;
 *) printf '{"error":{"key":"unauthorized"}}';;
esac
}
export -f curl
exec bash ./pihole-ha-dash --handle
'''
        def request(header, method='GET', path='/api/leases/snapshot'):
            return subprocess.run(['bash', '-c', script], input=method + ' ' + path + ' HTTP/1.1\r\n' + header + '\r\n',
                                  cwd=ROOT, env=env, text=True, capture_output=True)
        authorized = request('sid: fixture-session\r\n')
        self.assertIn('HTTP/1.1 200 OK', authorized.stdout, authorized.stderr)
        self.assertIn('PHALEASE1', authorized.stdout)
        unauthorized = request('')
        self.assertIn('HTTP/1.1 401 Unauthorized', unauthorized.stdout)
        self.assertNotIn('PHALEASE1', unauthorized.stdout)
        handover = request('sid: fixture-session\r\n', 'POST', '/api/leases/yield?target=192.0.2.2')
        self.assertIn('HTTP/1.1 202 Accepted', handover.stdout, handover.stderr)
        self.assertTrue((self.root / 'runtime/yield').read_text().startswith('192.0.2.2 '))
        denied = request('', 'POST', '/api/leases/yield?target=192.0.2.2')
        self.assertIn('HTTP/1.1 401 Unauthorized', denied.stdout)

    def test_snapshot_older_than_last_role_is_rejected(self):
        import time
        old = self.publish().read_bytes()
        time.sleep(1.05)
        self.assert_success(self.shell('LOCAL_IP=192.0.2.2; lease_sync_publish'))
        (self.root / 'old').write_bytes(old)
        (self.root / 'state').write_text('false')
        result = self.shell('lease_sync_import "$TEST_ROOT/old" 192.0.2.1', local='192.0.2.2')
        self.assertNotEqual(result.returncode, 0)

    def test_snapshot_from_removed_member_is_not_applied(self):
        self.publish()
        (self.root / 'state').write_text('false')
        result = self.shell('NODES=(192.0.2.2); lease_sync_prepare', local='192.0.2.2')
        self.assertNotEqual(result.returncode, 0)

    def test_former_owner_can_resume_from_its_own_latest_snapshot(self):
        self.publish()
        (self.root / 'state').write_text('false')
        (self.root / 'leases').write_text('')
        self.assert_success(self.shell('lease_sync_prepare'))
        self.assertIn('192.0.2.100', (self.root / 'leases').read_text())

    def test_competing_owner_cannot_publish_a_fresh_stale_snapshot(self):
        result = self.shell('peer_dhcp[192.0.2.2]=true; lease_sync_publish')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'runtime/published').exists())

    def test_returning_active_node_stands_down_before_receiving_peer_state(self):
        result = self.shell('curl() { return 1; }; peer_dhcp[192.0.2.2]=true; lease_sync_cycle')
        self.assertNotEqual(result.returncode, 0)  # Transfer deliberately unavailable.
        self.assertEqual((self.root / 'state').read_text(), 'false')

    def test_takeover_restores_verified_snapshot_before_activation(self):
        self.publish()
        (self.root / 'state').write_text('false')
        self.assert_success(self.shell('lease_sync_import "$LEASE_SYNC_DIR/published" 192.0.2.1', local='192.0.2.2'))
        (self.root / 'leases').write_text('')
        self.assert_success(self.shell('lease_sync_prepare', local='192.0.2.2'))
        self.assertIn('192.0.2.100', (self.root / 'leases').read_text())

    def test_cycle_fetches_from_actual_owner_not_first_config_node(self):
        import http.server
        import threading
        # Publish as the lower-priority node, then receive as the configured primary.
        self.assert_success(self.shell('LOCAL_IP=127.0.0.2; lease_sync_publish'))
        snapshot = (self.root / 'runtime/published').read_bytes()
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                seen.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(snapshot)

        server = http.server.HTTPServer(('127.0.0.2', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            (self.root / 'state').write_text('false')
            (self.root / 'leases').write_text('')
            result = self.shell('NODES=(127.0.0.1 127.0.0.2); peer_dhcp[127.0.0.2]=true; '
                                'LEASE_SYNC_PORT=' + str(server.server_port) + '; lease_sync_cycle; lease_sync_cycle', local='127.0.0.1')
            self.assert_success(result)
            self.assertEqual(seen, ['/api/leases/snapshot'])
            self.assertIn('192.0.2.100', (self.root / 'leases').read_text())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_expired_records_are_removed_without_extending_live_leases(self):
        (self.root / 'leases').write_text('1 02:00:00:00:00:02 192.0.2.101 expired *\n'
                                           '0 02:00:00:00:00:01 192.0.2.100 fixture *\n')
        self.publish()
        (self.root / 'state').write_text('false')
        self.assert_success(self.shell('lease_sync_import "$LEASE_SYNC_DIR/published" 192.0.2.1', local='192.0.2.2'))
        self.assertEqual((self.root / 'leases').read_text(), '0 02:00:00:00:00:01 192.0.2.100 fixture *\n')

    def test_stale_signed_snapshot_is_rejected(self):
        import hashlib
        import hmac
        snapshot = self.publish()
        lines = snapshot.read_text().splitlines()
        lines[3] = '1'
        body = ('\n'.join(lines[1:]) + '\n').encode()
        signature = hmac.new((self.root / 'key').read_bytes(), body, hashlib.sha256).hexdigest()
        snapshot.write_bytes(signature.encode() + b'\n' + body)
        (self.root / 'state').write_text('false')
        self.assertNotEqual(self.shell('lease_sync_import "$LEASE_SYNC_DIR/published" 192.0.2.1',
                                       local='192.0.2.2').returncode, 0)

    def test_signed_snapshot_installs_on_inactive_receiver(self):
        published = self.publish()
        (self.root / 'state').write_text('false')
        original = (self.root / 'leases').read_bytes()
        (self.root / 'leases').write_text('')
        self.assert_success(self.shell('lease_sync_import "$LEASE_SYNC_DIR/published" 192.0.2.1',
                                       local='192.0.2.2'))
        self.assertEqual((self.root / 'leases').read_bytes(), original)
        self.assertTrue(published.exists())


if __name__ == '__main__':
    unittest.main()
