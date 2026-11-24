"""Install Python packages only when the requirements have changed.

Compares a fingerprint of the requirements with the one saved after the last
successful install. Same fingerprint: pip does not run. Changed: pip installs into
the existing .venv, which keeps packages that already satisfy the requirements, so
PyTorch and other large packages are not fetched again. Source code is not part of
the fingerprint. The environment is never deleted or recreated here.

    python tools/check_python_deps.py [--profile full|core]

Exit codes: 0 ready, 2 .venv missing, otherwise pip's exit code.
Standard library only, because it must run before packages are checked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PROFILES = {"full": "requirements.txt", "core": "requirements-core.txt"}
STAMP_NAME = ".opslens-python-deps.json"
EXIT_NO_VENV = 2


def requirement_lines(path: Path, seen: set | None = None) -> list[str]:
    """Meaningful lines, following -r includes. Comments, blank lines and CRLF do not count."""
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        include = re.match(r"^(?:-r|--requirement)\s+(.+)$", line)
        if include:
            lines += requirement_lines(path.parent / include.group(1).strip(), seen)
        else:
            lines.append(re.sub(r"\s+", "", line).lower())
    return lines


def fingerprint(path: Path) -> str:
    return hashlib.sha256("\n".join(requirement_lines(path)).encode("utf-8")).hexdigest()


def venv_python(venv: Path) -> Path | None:
    for candidate in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
        if candidate.exists():
            return candidate
    return None


def read_stamp(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_stamp(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def detect_profile(python: Path) -> str:
    """An environment from before this check. setup_windows.bat falls back to the
    core set when PyTorch cannot install, so do not force that install now."""
    code = "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('sentence_transformers') else 1)"
    return "full" if subprocess.run([str(python), "-c", code]).returncode == 0 else "core"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES))
    parser.add_argument("--backend", default=str(BACKEND), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    backend = Path(args.backend)
    venv = backend / ".venv"
    python = venv_python(venv)
    if python is None:
        print(f"[ERROR] Python environment not found at {venv}.")
        print("        Initial setup is required. Run setup_windows.bat once, then start again.")
        return EXIT_NO_VENV
    stamp_path = venv / STAMP_NAME
    stamp = read_stamp(stamp_path)
    profile = args.profile or stamp.get("profile") or detect_profile(python)
    requirements = backend / PROFILES[profile]
    if not requirements.is_file():
        print(f"[ERROR] {requirements} is missing.")
        return 1
    wanted = fingerprint(requirements)
    if stamp.get("fingerprint") == wanted and stamp.get("profile") == profile:
        print(f"[OK] Python dependencies unchanged ({requirements.name}). Skipping pip.")
        if profile == "core":
            print("[INFO] Core packages only: SOP search uses BM25, not Qdrant. To add Qdrant search")
            print("       (sentence-transformers), run once while online:")
            print("       edge_intelligence\\.venv\\Scripts\\python edge_intelligence\\tools\\check_python_deps.py --profile full")
        return 0
    print(f"{requirements.name} changed or is not recorded yet. Updating .venv (installed packages are kept)...", flush=True)
    code = subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], cwd=str(backend)).returncode
    if code != 0:
        print(f"\n[ERROR] pip failed with exit code {code}. The output above shows why.")
        print("        Startup stopped. The fingerprint was not updated, so the next start tries again.")
        return code
    write_stamp(stamp_path, {"fingerprint": wanted, "profile": profile, "file": requirements.name,
                             "installed_at": datetime.now(timezone.utc).isoformat()})
    print("[OK] Python dependencies updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
