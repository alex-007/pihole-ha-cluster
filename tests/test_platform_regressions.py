import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1] / "pihole-ha-platform"

class PlatformRegressions(unittest.TestCase):
    def run_bash(self, body, **env):
        return subprocess.run(["bash", "-c", f"source '{PLATFORM}';\n{body}"], text=True, capture_output=True,
                              env=dict(os.environ, **env))

    def test_sane_port_warning_does_not_contaminate_stdout(self):
        result = self.run_bash('got="$(sane_port \'80 # inline\')"; printf "<%s>\\n" "$got"')
        self.assertEqual(result.stdout, "<80>\n")
        self.assertIn("event=port_trimmed", result.stderr)

    def test_all_dhcp_and_dns_api_urls_use_sane_port(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
            log = Path(tmp) / "urls"
            result = self.run_bash('''
PIHOLE_HA_PLATFORM=docker; PIHOLE_WEB_PORT="81 # note"; PIHOLE_PASSWORD=x
pihole-FTL() { :; }
curl() { printf "%s\\n" "${!#}" >> "$API_LOG"; case "$*" in *"/auth"*) printf '{"sid":"s"}';; esac; return 0; }
platform_set_dhcp true
platform_ftl_restart_dns
''', API_LOG=str(log))
            self.assertEqual(log.read_text().splitlines(), [
                "http://127.0.0.1:81/api/" + endpoint for endpoint in (
                    "auth", "config", "auth", "auth", "action/restartdns", "auth")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("event=port_trimmed", result.stderr)

    def test_reload_succeeds_when_any_signalled_live_pid_remains(self):
        result = self.run_bash('''
PIHOLE_HA_PLATFORM=docker; pgrep() { printf "101\\n202\\n"; }; kill() { if [[ "$1" == -0 ]]; then [[ "$2" == 202 ]]; else [[ "$2" == 101 ]] && return 1; return 0; fi; }; sleep() { :; }; platform_ftl_restart
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reload_fails_when_all_signalled_pids_are_dead(self):
        result = self.run_bash('''
PIHOLE_HA_PLATFORM=docker; pgrep() { printf "101\\n202\\n"; }; kill() { [[ "$1" == -0 ]] && return 1; return 0; }; sleep() { :; }; platform_ftl_restart
''')
        self.assertNotEqual(result.returncode, 0)

if __name__ == "__main__":
    unittest.main()
