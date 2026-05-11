"""Freeze the camel-pet-agent sidecar with PyInstaller.

Outputs:
    dist/camel-agent-<triple>(.exe)

The binary is copied into ../src-tauri/binaries/ so that Tauri can pick
it up via the `externalBin` config entry.

Run from the `agent/` directory:
    poetry run python build.py
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path


def rust_target_triple() -> str:
    """Return the Rust target triple for the current host.

    Tauri's externalBin feature requires the binary name to end in the
    target triple (e.g. `camel-agent-x86_64-pc-windows-msvc.exe`).
    """
    try:
        out = subprocess.check_output(["rustc", "-Vv"], text=True)
        for line in out.splitlines():
            if line.startswith("host:"):
                return line.split(":", 1)[1].strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    machine = platform.machine().lower()
    system = platform.system().lower()
    if system == "windows":
        arch = "x86_64" if "64" in machine or machine == "amd64" else "i686"
        return f"{arch}-pc-windows-msvc"
    if system == "darwin":
        arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
        return f"{arch}-apple-darwin"
    if system == "linux":
        arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
        return f"{arch}-unknown-linux-gnu"
    raise RuntimeError(f"unsupported host: {system}/{machine}")


def main() -> int:
    here = Path(__file__).resolve().parent
    triple = rust_target_triple()
    out_name = f"camel-agent-{triple}"
    dist_dir = here / "dist"

    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    entry = here / "camel_pet_agent" / "__main__.py"
    if not entry.exists():
        entry.write_text(
            "from camel_pet_agent.server import main\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        out_name,
        "--noconfirm",
        "--clean",
        "--collect-all",
        "camel",
        str(entry),
    ]
    print(">>>", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=here)
    if rc != 0:
        return rc

    ext = ".exe" if platform.system().lower() == "windows" else ""
    built = dist_dir / f"{out_name}{ext}"
    if not built.exists():
        print(f"ERROR: expected output not found: {built}", file=sys.stderr)
        return 1

    target = here.parent / "src-tauri" / "binaries" / f"{out_name}{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, target)
    print(f"OK — copied to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
