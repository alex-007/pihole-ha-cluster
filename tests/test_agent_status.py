import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'pihole-ha-agent-status'


class AgentStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / 'pihole-ha'
        self.script.write_text('#!/bin/bash\ntrap "exit 0" TERM\nwhile true; do sleep 1 & wait $!; done\n')
        self.script.chmod(0o755)

    def probe(self, pid, age=0):
        (self.root / 'daemon.pid').write_text(str(pid) + '\n')
        status = self.root / 'status.json'
        status.write_text('{}')
        stamp = time.time() - age
        os.utime(status, (stamp, stamp))
        env = dict(os.environ, HA_STATUS_DIR=str(self.root), HA_DAEMON_SCRIPT=str(self.script), HA_HEARTBEAT_MAX_AGE='90')
        output = subprocess.check_output(['bash', '-c', 'source "$1"; ha_agent_status_json', 'probe', str(HELPER)], env=env, text=True)
        return json.loads(output)

    def start(self, *args):
        p = subprocess.Popen(['bash', str(self.script), *args])
        self.addCleanup(lambda: p.wait(timeout=3))
        self.addCleanup(p.terminate)
        time.sleep(0.05)
        return p

    def test_bare_docker_daemon_with_fresh_status_is_running(self):
        self.assertEqual(self.probe(self.start().pid)['state'], 'running')

    def test_explicit_daemon_mode_is_running(self):
        self.assertEqual(self.probe(self.start('daemon').pid)['state'], 'running')

    def test_status_cli_mode_is_not_daemon(self):
        self.assertEqual(self.probe(self.start('status').pid)['state'], 'stopped')

    def test_dead_pid_with_stale_status_is_stopped(self):
        self.assertEqual(self.probe(999999, age=3600)['state'], 'stopped')

    def test_pid_reused_by_other_process_is_stopped(self):
        self.assertEqual(self.probe(os.getpid())['state'], 'stopped')

    def test_live_daemon_stale_heartbeat_is_unknown(self):
        self.assertEqual(self.probe(self.start().pid, age=3600)['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
