"""Behavioral tests for the real Docker injection entrypoint."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DockerBootGuardTests(unittest.TestCase):
    def run_entrypoint(self, dhcp_ha='true', guard_fails=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / 'nodes.conf'
            conf.write_text(f'HA_ENABLED=true\nDHCP_HA={dhcp_ha}\n')
            events = root / 'events'
            binary = root / 'pihole-FTL'
            binary.write_text('#!/bin/sh\nprintf "guard:%s\\n" "$*" >> "$EVENTS"\n' + ('exit 1\n' if guard_fails else 'exit 0\n'))
            binary.chmod(0o755)
            entry = root / 'start'
            entry.write_text('#!/bin/sh\nprintf "ftl-start\\n" >> "$EVENTS"\n')
            entry.chmod(0o755)
            # Keep the actual guard/handoff paths; prevent unrelated background UI work.
            source = (ROOT / 'docker/pihole-ha-inject-docker.sh').read_text().replace('inject_ha &', ': # UI injection omitted in isolated entrypoint test')
            script = root / 'entrypoint.sh'
            script.write_text(source)
            env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                       PIHOLE_HA_NODES_CONF=str(conf), PIHOLE_HA_ENTRYPOINT=str(entry), EVENTS=str(events))
            result = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True)
            return result, events.read_text().splitlines() if events.exists() else []

    def test_ha_dhcp_is_disabled_before_docker_entrypoint(self):
        result, events = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['guard:--config dhcp.active false', 'ftl-start'])

    def test_dns_only_does_not_change_dhcp(self):
        result, events = self.run_entrypoint(dhcp_ha='false')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['ftl-start'])

    def test_failed_guard_does_not_start_ftl(self):
        result, events = self.run_entrypoint(guard_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('ftl-start', events)


if __name__ == '__main__':
    unittest.main()
