#!/usr/bin/env python3
"""Exercise the production sync reload path across separate pull processes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "pihole-ha-sync-pull").read_text()
PREFLIGHT = SOURCE[SOURCE.index('mkdir -p "$SYNC_DIR"'):SOURCE.index("# Determine this node's index")]
GRAVITY = SOURCE[SOURCE.index("# Check if gravity.db changed"):SOURCE.index("# Apply FTL settings AFTER")]
SETTINGS = SOURCE[SOURCE.index("# Restart FTL after settings apply"):SOURCE.index("# Cleanup\n")]


class SyncReloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "staging").mkdir()
        (self.root / "gravity.db").write_text("old database")
        (self.root / "staging/gravity.db").write_text("new database")

    def pull(self, succeeds, apply_gravity=True, apply_settings=False):
        # Only privileged/process boundaries are replaced. Actual file copy,
        # checksum, atomic rename and the production retry logic run unchanged.
        script = r'''
set -uo pipefail
SYNC_DIR="$TEST_ROOT/run"
SYNC_BLOB_DIR="$TEST_ROOT/blobs"
FTL_RELOAD_PENDING="$TEST_ROOT/ftl-reload-pending"
STAGING_DIR="$TEST_ROOT/staging"
COMP_GRAVITY=true
REMOTE_GRAVITY_MD5=""
APPLY_FAILED=false
APPLIED_SOMETHING=false
log_info() { printf '%s\n' "$*"; }
log_error() { printf '%s\n' "$*"; }
log_warn() { printf '%s\n' "$*"; }
gravity_is_sane() { return 0; }
chown() { :; }
sync() { :; }
platform_ftl_restart() {
    printf 'reload\n' >> "$TEST_ROOT/calls"
    [[ "$RELOAD_OK" == true ]]
}
'''
        script += PREFLIGHT
        if apply_gravity:
            script += GRAVITY.replace("/etc/pihole/gravity.db", str(self.root / "gravity.db"))
        if apply_settings:
            script += '\nSETTINGS_APPLIED=true; HOSTS_APPLIED=false\n' + SETTINGS
        script += '\nprintf "APPLY_FAILED=%s\\n" "$APPLY_FAILED"\n'
        env = dict(os.environ, TEST_ROOT=str(self.root), RELOAD_OK=str(succeeds).lower())
        return subprocess.run(["bash"], input=script, text=True, capture_output=True, env=env)

    def test_failed_reload_is_retried_when_database_is_already_replaced(self):
        first = self.pull(False)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("APPLY_FAILED=true", first.stdout)
        self.assertEqual((self.root / "gravity.db").read_text(), "new database")
        second = self.pull(True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["reload", "reload"])
        self.assertIn("APPLY_FAILED=false", second.stdout)
        self.assertFalse((self.root / "ftl-reload-pending").exists())

    def test_failed_settings_reload_is_recorded_and_retried_without_payload(self):
        first = self.pull(False, apply_gravity=False, apply_settings=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("APPLY_FAILED=true", first.stdout)
        second = self.pull(True, apply_gravity=False)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["reload", "reload"])
        self.assertFalse((self.root / "ftl-reload-pending").exists())

    def test_retry_failure_preserves_debt_until_a_later_success(self):
        self.pull(False)
        second = self.pull(False, apply_gravity=False)
        self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
        self.assertTrue((self.root / "ftl-reload-pending").exists())
        third = self.pull(True, apply_gravity=False)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(len((self.root / "calls").read_text().splitlines()), 3)
        self.assertFalse((self.root / "ftl-reload-pending").exists())

    def test_unchanged_database_without_reload_debt_does_not_reload(self):
        (self.root / "gravity.db").write_text("new database")
        result = self.pull(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status=unchanged", result.stdout)
        self.assertFalse((self.root / "calls").exists())


if __name__ == "__main__":
    unittest.main()
