"""Tests for scripts/provisioner_daemon.py — the host-side daemon that
processes flag files and runs `docker compose` to spin up / tear down
per-user engine containers.

Subprocess calls are mocked (we're not testing Docker, we're testing the
daemon logic). The audit log + filesystem state are real and verified.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_provisioner_daemon -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _setup_daemon_env(root: str) -> None:
    """Point the daemon's module-level constants at a temp dir.

    Has to mutate the imported module's attributes because the constants are
    read at import time. We re-bind them and re-derive the dependent paths.
    """
    os.environ["SWARM_ROOT"]    = root
    os.environ["IONIC_ROOT"] = str(Path(root) / "ionic")
    os.environ["SHARED_ENV_FILE"] = str(Path(root) / "ionic" / ".env")
    os.environ["PROVISIONER_POLL_SEC"] = "0.1"

    # Re-import (or rebind) the daemon module so constants pick up env
    import importlib
    if "provisioner_daemon" in sys.modules:
        importlib.reload(sys.modules["provisioner_daemon"])
    import provisioner_daemon  # noqa: F401
    return sys.modules["provisioner_daemon"]


class DaemonTestBase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foundation_daemon_test_")
        self._saved_env = dict(os.environ)
        self.daemon = _setup_daemon_env(self.tmpdir)

        # Materialize the expected directory structure
        self.tib_root   = Path(self.tmpdir) / "ionic"
        self.queue_dir  = self.tib_root / "data" / "users" / "_queue"
        self.fragments  = self.tib_root / "users.yml.d"
        self.user_data  = self.tib_root / "data" / "users"
        self.deleted    = self.user_data / "_deleted"
        self.queue_dir.mkdir(parents=True)
        # Touch a fake .env so the fragment template's env_file: line resolves
        (self.tib_root / ".env").write_text("# test env\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._saved_env)


def _fake_completed(rc: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["docker", "compose"], returncode=rc, stdout=stdout, stderr=stderr,
    )


# ─── render_fragment ───────────────────────────────────────────────────────


class RenderFragmentTests(DaemonTestBase):

    def test_basic_substitution(self):
        text = self.daemon.render_fragment(42)
        self.assertIn("ionic-engine-42", text)
        self.assertIn("USER_ID:           \"42\"", text)
        self.assertIn("/app/data/users/42/ionic.db", text)
        self.assertIn("/app/data/users/42/Config.yaml", text)

    def test_image_tag_overridable(self):
        os.environ["IONIC_ENGINE_IMAGE"] = "custom-engine:v9"
        # Re-import so constants pick up new env
        import importlib
        self.daemon = importlib.reload(self.daemon)
        text = self.daemon.render_fragment(1)
        self.assertIn("custom-engine:v9", text)

    def test_network_overridable(self):
        os.environ["IONIC_ENGINE_NETWORK"] = "foundation-net"
        import importlib
        self.daemon = importlib.reload(self.daemon)
        text = self.daemon.render_fragment(1)
        self.assertIn("foundation-net", text)


# ─── audit() ───────────────────────────────────────────────────────────────


class AuditLogTests(DaemonTestBase):

    def test_writes_json_line(self):
        self.daemon.audit("provision", 42, True, detail="spun up")
        log = self.queue_dir / "audit.log"
        self.assertTrue(log.exists())
        line = log.read_text().strip()
        record = json.loads(line)
        self.assertEqual(record["action"], "provision")
        self.assertEqual(record["user_id"], 42)
        self.assertTrue(record["ok"])
        self.assertEqual(record["detail"], "spun up")
        self.assertIn("T", record["ts"])  # ISO-formatted UTC timestamp

    def test_truncates_long_detail(self):
        long = "x" * 1000
        self.daemon.audit("teardown", 1, False, detail=long)
        record = json.loads((self.queue_dir / "audit.log").read_text().strip())
        self.assertEqual(len(record["detail"]), 500)

    def test_appends_multiple_records(self):
        self.daemon.audit("provision", 1, True)
        self.daemon.audit("provision", 2, True)
        self.daemon.audit("teardown", 1, True)
        lines = (self.queue_dir / "audit.log").read_text().strip().splitlines()
        self.assertEqual(len(lines), 3)
        actions = [json.loads(line)["action"] for line in lines]
        self.assertEqual(actions, ["provision", "provision", "teardown"])


# ─── provision() ───────────────────────────────────────────────────────────


class ProvisionTests(DaemonTestBase):

    def test_writes_fragment_and_runs_compose(self):
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            ok = self.daemon.provision(42)
        self.assertTrue(ok)
        # Fragment file materialized
        frag = self.fragments / "42.yml"
        self.assertTrue(frag.exists())
        self.assertIn("ionic-engine-42", frag.read_text())
        # docker compose up -d called with the right container name
        call_args = mock_run.call_args.args
        self.assertEqual(call_args, ("up", "-d", "ionic-engine-42"))

    def test_compose_failure_audits_but_returns_false(self):
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=1, stderr="image not found")
            ok = self.daemon.provision(7)
        self.assertFalse(ok)
        # Fragment was still written (so operator can inspect it)
        self.assertTrue((self.fragments / "7.yml").exists())
        # Audit captures the failure
        record = json.loads((self.queue_dir / "audit.log").read_text().strip())
        self.assertFalse(record["ok"])
        self.assertIn("image not found", record["detail"])

    def test_dry_run_writes_nothing(self):
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            ok = self.daemon.provision(99, dry_run=True)
        self.assertTrue(ok)
        # Fragment NOT materialized in dry-run
        self.assertFalse((self.fragments / "99.yml").exists())

    def test_idempotent_overwrites_existing_fragment(self):
        # Pre-existing fragment with stale content
        self.fragments.mkdir(parents=True)
        (self.fragments / "5.yml").write_text("STALE")
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            self.daemon.provision(5)
        new_text = (self.fragments / "5.yml").read_text()
        self.assertNotEqual(new_text, "STALE")
        self.assertIn("ionic-engine-5", new_text)


# ─── teardown() ────────────────────────────────────────────────────────────


class TeardownTests(DaemonTestBase):

    def _seed_user(self, user_id: int):
        """Materialize a user dir + fragment so teardown has work to do."""
        user_dir = self.user_data / str(user_id)
        user_dir.mkdir(parents=True)
        (user_dir / "ionic.db").write_text("data")
        self.fragments.mkdir(parents=True, exist_ok=True)
        (self.fragments / f"{user_id}.yml").write_text("fragment")

    def test_stops_removes_container_and_moves_dir(self):
        self._seed_user(42)
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            ok = self.daemon.teardown(42)
        self.assertTrue(ok)
        # Fragment removed
        self.assertFalse((self.fragments / "42.yml").exists())
        # User dir moved to _deleted/
        self.assertFalse((self.user_data / "42").exists())
        deleted_entries = list(self.deleted.iterdir())
        self.assertEqual(len(deleted_entries), 1)
        self.assertTrue(deleted_entries[0].name.startswith("42_"))
        self.assertTrue((deleted_entries[0] / "ionic.db").exists())
        # docker compose stop + rm both called
        self.assertGreaterEqual(mock_run.call_count, 2)
        calls = [c.args for c in mock_run.call_args_list]
        self.assertIn(("stop", "ionic-engine-42"), calls)
        self.assertIn(("rm", "-f", "ionic-engine-42"), calls)

    def test_idempotent_when_nothing_exists(self):
        """Tearing down a user_id with no fragment + no dir is a no-op."""
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            ok = self.daemon.teardown(404)
        self.assertTrue(ok)
        record = json.loads((self.queue_dir / "audit.log").read_text().strip())
        self.assertEqual(record["detail"], "no_user_dir")

    def test_dry_run_changes_nothing(self):
        self._seed_user(7)
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            ok = self.daemon.teardown(7, dry_run=True)
        self.assertTrue(ok)
        # Nothing actually moved or removed
        self.assertTrue((self.fragments / "7.yml").exists())
        self.assertTrue((self.user_data / "7").exists())
        self.assertFalse(self.deleted.exists() and any(self.deleted.iterdir()))


# ─── process_queue() ───────────────────────────────────────────────────────


class ProcessQueueTests(DaemonTestBase):

    def test_processes_provision_flag(self):
        (self.queue_dir / "42.provision").touch()
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            n = self.daemon.process_queue()
        self.assertEqual(n, 1)
        # Flag consumed
        self.assertFalse((self.queue_dir / "42.provision").exists())
        # Fragment materialized
        self.assertTrue((self.fragments / "42.yml").exists())

    def test_processes_teardown_flag(self):
        (self.queue_dir / "9.teardown").touch()
        # Seed a user dir + fragment so teardown has something to do
        (self.user_data / "9").mkdir(parents=True)
        (self.user_data / "9" / "ionic.db").write_text("data")
        self.fragments.mkdir(parents=True, exist_ok=True)
        (self.fragments / "9.yml").write_text("frag")
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            n = self.daemon.process_queue()
        self.assertEqual(n, 1)
        self.assertFalse((self.queue_dir / "9.teardown").exists())
        self.assertFalse((self.user_data / "9").exists())

    def test_processes_multiple_in_one_pass(self):
        (self.queue_dir / "1.provision").touch()
        (self.queue_dir / "2.provision").touch()
        (self.queue_dir / "3.provision").touch()
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            n = self.daemon.process_queue()
        self.assertEqual(n, 3)

    def test_audit_log_file_skipped(self):
        """audit.log lives in the same dir as flags but isn't a flag."""
        (self.queue_dir / "audit.log").write_text('{"existing": "log"}\n')
        (self.queue_dir / "1.provision").touch()
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            n = self.daemon.process_queue()
        self.assertEqual(n, 1)
        # audit.log preserved + appended to
        text = (self.queue_dir / "audit.log").read_text()
        self.assertIn("existing", text)
        self.assertIn("provision", text)

    def test_malformed_flag_name_skipped_gracefully(self):
        (self.queue_dir / "notanint.provision").touch()
        (self.queue_dir / "1.provision").touch()
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            n = self.daemon.process_queue()
        # Both processed (one as a skip, one as a real provision)
        self.assertEqual(n, 2)
        # Real one materialized a fragment
        self.assertTrue((self.fragments / "1.yml").exists())
        # Bad one didn't
        self.assertFalse((self.fragments / "notanint.yml").exists())

    def test_unknown_suffix_ignored(self):
        (self.queue_dir / "1.unknown").touch()
        (self.queue_dir / "audit.log").touch()  # filtered above
        n = self.daemon.process_queue()
        self.assertEqual(n, 0)
        # Flag NOT consumed (no match)
        self.assertTrue((self.queue_dir / "1.unknown").exists())

    def test_provision_exception_doesnt_block_other_flags(self):
        (self.queue_dir / "1.provision").touch()
        (self.queue_dir / "2.provision").touch()
        def maybe_raise(*args, **kwargs):
            if args[2] == "ionic-engine-1":
                raise RuntimeError("simulated docker hiccup")
            return _fake_completed(rc=0)
        with patch("provisioner_daemon._run_compose", side_effect=maybe_raise):
            n = self.daemon.process_queue()
        self.assertEqual(n, 2)
        # Both flags consumed regardless of which one blew up
        self.assertFalse((self.queue_dir / "1.provision").exists())
        self.assertFalse((self.queue_dir / "2.provision").exists())


# ─── CLI smoke ─────────────────────────────────────────────────────────────


class CLISmokeTests(DaemonTestBase):

    def test_once_dry_run(self):
        (self.queue_dir / "5.provision").touch()
        rc = self.daemon.main(["--once", "--dry-run"])
        self.assertEqual(rc, 0)
        # In dry-run + --once, the flag is still consumed (since process_queue
        # always unlinks) but no fragment file is written
        self.assertFalse((self.queue_dir / "5.provision").exists())
        self.assertFalse((self.fragments / "5.yml").exists())

    def test_once_real_call_writes_fragment(self):
        (self.queue_dir / "6.provision").touch()
        with patch("provisioner_daemon._run_compose") as mock_run:
            mock_run.return_value = _fake_completed(rc=0)
            rc = self.daemon.main(["--once"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.fragments / "6.yml").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
