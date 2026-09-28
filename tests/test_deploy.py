"""deploy/deploy.sh: config parsing, rendering, and a full run with fake ssh/compose."""

import os
import re
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "deploy", "deploy.sh")

# Runs the remote command locally, like ssh would on the host.
FAKE_SSH = """#!/bin/sh
for a; do last=$a; done
case " $* " in *" -O exit "*) exit 0 ;; esac
exec sh -c "$last"
"""

# Records its arguments; stands for both `docker compose` and `docker`.
# `exec` answers like `seedbox check`; UP_FAIL makes the recreate time out;
# RUNNING is what `inspect` reports; `ps` lists one leftover renamed container.
FAKE_COMPOSE = """#!/bin/sh
echo "$*" >> "$COMPOSE_LOG"
case " $* " in
  *" exec "*) echo "ok      qBittorrent reachable"; exit "${CHECK_RC:-0}" ;;
  *" --force-recreate "*) [ -z "${UP_FAIL:-}" ] || exit 1 ;;
  " inspect "*) echo "${RUNNING:-true}" ;;
  " ps "*) printf '0123456789ab_seedbox\\nseedbox\\nother_seedbox\\n' ;;
esac
"""


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)
    os.chmod(path, mode)


class DeployScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name
        self.home = os.path.join(self.t, "home")
        self.remote = os.path.join(self.t, "remote", "seedbox")
        bin_dir = os.path.join(self.t, "bin")
        write(os.path.join(bin_dir, "ssh"), FAKE_SSH, 0o755)
        write(os.path.join(bin_dir, "compose"), FAKE_COMPOSE, 0o755)
        write(os.path.join(self.home, "dotfiles", "seedbox.toml"), '[library]\nroots = ["/media/movies"]\n')
        # Private file in the "dotfiles", included from the main config.
        write(
            os.path.join(self.home, "dotfiles", "private.conf"),
            "DEPLOY_HOST=nas.lan\n"
            "SEEDBOX_TOML=seedbox.toml\n"
            "QBT_PASSWORD_CMD=printf 'p@ss w0rd'\n"
            "PROWLARR_API_KEY=abc123\n"
            "TMDB_API_KEY_CMD=printf tmdb-key\n"
            "MEDIA_ROOT=/srv/media\n"
            "TZ=UTC\n",
        )
        self.conf = os.path.join(self.home, ".config", "seedbox", "deploy.conf")
        write(
            self.conf,
            "# comment\n"
            "include ~/dotfiles/private.conf\n"
            f'DEPLOY_DIR="{self.remote}"\n'
            f"DEPLOY_COMPOSE={os.path.join(bin_dir, 'compose')}\n"
            f"DEPLOY_DOCKER={os.path.join(bin_dir, 'compose')}\n"
            "TZ = Europe/Paris\n",
        )
        self.env = {
            "PATH": bin_dir + os.pathsep + os.environ["PATH"],
            "HOME": self.home,
            "NO_COLOR": "1",
            "COMPOSE_LOG": os.path.join(self.t, "compose.log"),
            "SEEDBOX_DEPLOY_RETRY_DELAY": "0",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args, env=None):
        return subprocess.run(
            ["sh", SCRIPT, *args], capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=60
        )

    def test_print_config_resolves_includes_and_masks_secrets(self):
        result = self.run_script("--print-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if re.match(r"^[A-Z_]+=", line))
        self.assertEqual(lines["DEPLOY_HOST"], "nas.lan")
        self.assertEqual(lines["TZ"], "Europe/Paris")  # later line wins over the include
        self.assertEqual(lines["SEEDBOX_TOML"], os.path.join(self.home, "dotfiles", "seedbox.toml"))
        self.assertEqual(lines["DEPLOY_DIR"], self.remote)
        self.assertEqual(lines["PROWLARR_API_KEY"], "<set>")
        self.assertEqual(lines["TMDB_API_KEY"], "<empty>")
        self.assertEqual(lines["QBT_PASSWORD"], "<empty>")
        self.assertNotIn("abc123", result.stdout)

    def test_unknown_key_and_missing_config(self):
        write(self.conf, "DEPLOY_HOTS=typo\n")
        result = self.run_script("--print-config")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown key 'DEPLOY_HOTS'", result.stderr)
        result = self.run_script("-c", os.path.join(self.t, "nope.conf"), "--print-config")
        self.assertEqual(result.returncode, 1)
        self.assertIn("config file not found", result.stderr)

    def test_render(self):
        out = os.path.join(self.t, "rendered")
        result = self.run_script("--render", out, env={})
        self.assertEqual(result.returncode, 1)  # PUID is only auto-detected when deploying
        with open(self.conf, "a") as handle:
            handle.write("PUID=1026\nPGID=100\n")
        result = self.run_script("--render", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(out, "secrets", "qbt_password")) as handle:
            self.assertEqual(handle.read(), "p@ss w0rd")
        with open(os.path.join(out, ".env")) as handle:
            env = handle.read()
        self.assertIn("PUID='1026'\n", env)
        self.assertIn("TZ='Europe/Paris'\n", env)
        self.assertRegex(env, r"SEEDBOX_VERSION='\d+\.\d+\.\d+'")
        for name in (
            ".env",
            "seedbox.toml",
            "secrets/qbt_password",
            "secrets/prowlarr_api_key",
            "secrets/tmdb_api_key",
        ):
            mode = stat.S_IMODE(os.stat(os.path.join(out, name)).st_mode)
            self.assertEqual(mode, 0o600, name)

    def test_full_deploy_with_fake_host(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(os.path.join(self.remote, "secrets", "prowlarr_api_key")) as handle:
            self.assertEqual(handle.read(), "abc123")
        with open(os.path.join(self.remote, "secrets", "tmdb_api_key")) as handle:
            self.assertEqual(handle.read(), "tmdb-key")
        self.assertTrue(os.path.isdir(os.path.join(self.remote, "data")))
        with open(os.path.join(self.remote, ".env")) as handle:
            self.assertIn(f"PUID='{os.getuid()}'", handle.read())
        with open(self.env["COMPOSE_LOG"]) as handle:
            calls = handle.read().splitlines()
        self.assertEqual(
            calls,
            [
                "-f compose.yaml pull -q",
                "-f compose.yaml up -d --force-recreate --remove-orphans",
                "ps -a --filter status=exited --filter status=created --format {{.Names}}",
                "rm 0123456789ab_seedbox",
                "exec seedbox python -m seedbox check",
            ],
        )
        self.assertIn("removed 0123456789ab_seedbox", result.stdout)
        self.assertIn("seedbox check passed", result.stdout)
        self.assertNotIn("p@ss", result.stdout + result.stderr)

    def calls(self):
        with open(self.env["COMPOSE_LOG"]) as handle:
            return handle.read().splitlines()

    def test_recreate_timeout_converges(self):
        result = self.run_script(env={"UP_FAIL": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        # The recreate is not retried: the daemon may still be doing it.
        self.assertEqual(sum("--force-recreate" in c for c in calls), 1)
        self.assertIn("inspect -f {{.State.Running}} seedbox", calls)
        self.assertIn("seedbox check passed", result.stdout)

    def test_recreate_timeout_gives_up(self):
        result = self.run_script(env={"UP_FAIL": "1", "RUNNING": "false"})
        self.assertEqual(result.returncode, 1)
        calls = self.calls()
        self.assertEqual(calls.count("-f compose.yaml up -d --remove-orphans"), 3)
        self.assertEqual(calls.count("start seedbox"), 3)
        self.assertNotIn("exec seedbox python -m seedbox check", calls)

    def test_status_only_runs_status(self):
        result = self.run_script("--status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(self.env["COMPOSE_LOG"]) as handle:
            self.assertEqual(handle.read().splitlines(), ["exec seedbox python -m seedbox status"])
        self.assertFalse(os.path.exists(self.remote))

    def test_deploy_fails_when_check_fails(self):
        result = self.run_script(env={"CHECK_RC": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("seedbox check reported problems", result.stderr)

    def test_uid_mismatch_refused(self):
        with open(self.conf, "a") as handle:
            handle.write(f"PUID={os.getuid() + 1}\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("deploy as that user", result.stderr)
        self.assertFalse(os.path.exists(self.remote))


if __name__ == "__main__":
    unittest.main()
