"""tools/check_python_deps.py against a stand-in .venv whose python logs its calls."""
import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import check_python_deps as deps  # noqa: E402


@unittest.skipIf(os.name == "nt", "the stand-in python is a shell script")
class DependencyCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backend = Path(self.tmp.name)
        (self.backend / "requirements-core.txt").write_text("fastapi==1.0\n")
        (self.backend / "requirements.txt").write_text("-r requirements-core.txt\nsentence-transformers==6.0\n")
        self.log = self.backend / "calls.log"
        self.make_python(pip_exit=0, heavy_installed=True)

    def tearDown(self):
        self.tmp.cleanup()

    def make_python(self, pip_exit, heavy_installed):
        python = self.backend / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text(f'#!/bin/sh\nif [ "$1" = "-c" ]; then exit {0 if heavy_installed else 1}; fi\necho "$@" >> "{self.log}"\nexit {pip_exit}\n')
        python.chmod(python.stat().st_mode | stat.S_IEXEC)

    def run_check(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = deps.main(["--backend", str(self.backend), *args])
        return code, out.getvalue()

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_first_run_installs_then_normal_runs_skip_pip(self):
        self.assertEqual(self.run_check()[0], 0)
        self.assertEqual(len(self.calls()), 1)
        code, output = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn("Skipping pip", output)
        self.assertEqual(len(self.calls()), 1)

    def test_changed_requirement_installs_without_upgrade_flags(self):
        self.run_check()
        with (self.backend / "requirements.txt").open("a") as handle:
            handle.write("newpackage==1\n")
        self.run_check()
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("pip install -r", self.calls()[1])
        self.assertNotIn("--upgrade", self.calls()[1])
        self.assertNotIn("--force-reinstall", self.calls()[1])

    def test_change_in_an_included_file_is_detected(self):
        self.run_check()
        (self.backend / "requirements-core.txt").write_text("fastapi==2.0\n")
        self.run_check()
        self.assertEqual(len(self.calls()), 2)

    def test_comments_line_endings_and_source_files_do_not_trigger_pip(self):
        self.run_check()
        (self.backend / "requirements.txt").write_bytes(b"# note\r\n-r requirements-core.txt\r\n\r\nsentence-transformers==6.0  # search\r\n")
        (self.backend / "new_module.py").write_text("import fastapi\n")
        self.run_check()
        self.assertEqual(len(self.calls()), 1)

    def test_failed_install_stops_and_keeps_the_old_fingerprint(self):
        self.run_check()
        stamp = (self.backend / ".venv" / deps.STAMP_NAME).read_text()
        self.make_python(pip_exit=9, heavy_installed=True)
        (self.backend / "requirements.txt").write_text("-r requirements-core.txt\nbroken==1\n")
        code, output = self.run_check()
        self.assertEqual(code, 9)
        self.assertIn("Startup stopped", output)
        self.assertEqual((self.backend / ".venv" / deps.STAMP_NAME).read_text(), stamp)

    def test_missing_venv_explains_setup_and_creates_nothing(self):
        import shutil
        shutil.rmtree(self.backend / ".venv")
        code, output = self.run_check()
        self.assertEqual(code, deps.EXIT_NO_VENV)
        self.assertIn("Initial setup is required", output)
        self.assertFalse((self.backend / ".venv").exists())

    def test_existing_environment_without_pytorch_stays_on_the_core_set(self):
        self.make_python(pip_exit=0, heavy_installed=False)
        self.run_check()
        self.assertIn("requirements-core.txt", self.calls()[0])
        self.assertNotIn("/requirements.txt", self.calls()[0])
