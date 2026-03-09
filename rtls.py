#!/usr/bin/env python3
"""
rtls — small GitHub installer (user-mode by default)

Features:
- rtls install <repo> [--bin] [--target-dir <dir>]
  where <repo> can be:
    - full URL: https://github.com/user/repo
    - short: user/repo
- reads realtools.txt from the repo root
- installs `requirements` using `pip --user`
- if --bin: ensures PyInstaller is installed (--user) and builds a onefile binary
- by default installs into ~/.local/bin (no sudo)
- supports uninstall <name>
- tracks installed packages in ~/.rtls/installed.txt
"""
from __future__ import annotations
import os
import sys
import subprocess
import tarfile
import urllib.request
import shutil
import tempfile
import textwrap

HOME = os.path.expanduser("~")
CACHE_DIR = os.path.join(HOME, ".rtls", "cache")
STATE_DIR = os.path.join(HOME, ".rtls")
INSTALLED_DB = os.path.join(STATE_DIR, "installed.txt")
DEFAULT_TARGET_DIR = os.path.join(HOME, ".local", "bin")
GITHUB_ARCHIVE_PATH = "archive/refs/heads/main.tar.gz"  # classic default

# ---------------- utilities ----------------
def ensure_dirs():
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(DEFAULT_TARGET_DIR, exist_ok=True)

def run(cmd, check=True, capture=False):
    """Run command list. Print if failing."""
    try:
        if capture:
            return subprocess.run(cmd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        else:
            return subprocess.run(cmd, check=check)
    except subprocess.CalledProcessError as e:
        print(f"[error] command failed: {' '.join(cmd)}")
        if e.stdout:
            print(e.stdout)
        if e.stderr:
            print(e.stderr)
        raise

def normalize_repo(repo: str) -> str:
    """Return full https://github.com/user/repo for both 'user/repo' and full urls."""
    repo = repo.strip()
    if repo.startswith("http://") or repo.startswith("https://"):
        # accept full URL; strip trailing slash
        return repo.rstrip("/")
    if "/" in repo:
        return f"https://github.com/{repo}"
    raise ValueError("Repo must be 'user/repo' or full GitHub URL.")

def download_repo_archive(repo_url: str) -> str:
    """
    Download the main branch archive and return local archive path.
    Archive cached in ~/.rtls/cache/<repo>-main.tar.gz
    """
    repo_name = repo_url.rstrip("/").split("/")[-1]
    local_archive = os.path.join(CACHE_DIR, f"{repo_name}-main.tar.gz")
    download_url = f"{repo_url}/{GITHUB_ARCHIVE_PATH}"
    print(f"[info] downloading {download_url} -> {local_archive}")
    try:
        urllib.request.urlretrieve(download_url, local_archive)
    except Exception as e:
        raise RuntimeError(f"Failed to download {download_url}: {e}")
    return local_archive

def extract_archive_to_temp(archive_path: str) -> str:
    """Extract archive into a tempdir. Return path to extracted repo root folder."""
    tmpdir = tempfile.mkdtemp(prefix="rtls-")
    try:
        with tarfile.open(archive_path, "r:*") as tar:
            tar.extractall(path=tmpdir)
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(f"Failed to extract archive {archive_path}: {e}")

    # archive usually extracts into <repo>-main
    entries = [p for p in os.listdir(tmpdir) if os.path.isdir(os.path.join(tmpdir, p))]
    if not entries:
        # fallback: just return tmpdir
        return tmpdir
    repo_root = os.path.join(tmpdir, entries[0])
    return repo_root

# ---------------- manifest parsing ----------------
def parse_manifest(manifest_text: str) -> dict:
    """
    Parse a small manifest with keys:
      entry = lofitty.py
      requirements = requests, rich
      build = pyinstaller
    Or multiline:
      requirements =
         requests
         rich
    Returns dict with keys: entry (str), requirements (list), build (str)
    """
    data = {"entry": None, "requirements": [], "build": None, "post_install": []}
    lines = manifest_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue

        if "=" in line:
            key, rest = line.split("=", 1)
            key = key.strip().lower()
            rest = rest.strip()
            if rest == "":  # multiline block expected
                # gather indented following lines
                block = []
                while i < len(lines) and (lines[i].strip() == "" or lines[i].startswith(" ") or lines[i].startswith("\t")):
                    l = lines[i].strip()
                    i += 1
                    if l and not l.startswith("#"):
                        block.append(l)
                if key == "requirements":
                    data["requirements"].extend(block)
                elif key == "post_install":
                    data["post_install"].extend(block)
                else:
                    if block:
                        data[key] = "\n".join(block)
            else:
                # single-line value; allow comma separated lists
                if key == "requirements":
                    parts = [p.strip() for p in rest.split(",") if p.strip()]
                    data["requirements"].extend(parts)
                elif key == "post_install":
                    parts = [p.strip() for p in rest.split(",") if p.strip()]
                    data["post_install"].extend(parts)
                else:
                    data[key] = rest
        else:
            # fallback: support lines like "entry lofitty.py"
            parts = line.split(None, 1)
            if len(parts) == 2:
                data[parts[0].lower()] = parts[1].strip()
    # normalize entry to string
    if data["entry"]:
        data["entry"] = data["entry"].strip().strip('"').strip("'")
    return data

# ---------------- installed DB ----------------
def read_installed() -> list:
    if not os.path.exists(INSTALLED_DB):
        return []
    with open(INSTALLED_DB, "r", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]

def write_installed(listing: list):
    with open(INSTALLED_DB, "w", encoding="utf-8") as f:
        for item in listing:
            f.write(item + "\n")

def add_installed(name: str):
    items = read_installed()
    if name not in items:
        items.append(name)
        write_installed(items)

def remove_installed(name: str):
    items = read_installed()
    if name in items:
        items.remove(name)
        write_installed(items)

# ---------------- pip helpers (user installs) ----------------
def pip_install_user(packages: list[str]):
    """
    Install packages into user site using pip --user.
    packages: list of package spec strings, e.g. ["requests", "pyinstaller"]
    """
    if not packages:
        return
    cmd = [sys.executable, "-m", "pip", "install", "--user"] + packages
    print(f"[pip] installing (user) -> {' '.join(packages)}")
    run(cmd)

def ensure_pyinstaller_user():
    """Ensure PyInstaller is available in the user's Python; install it with --user if not."""
    try:
        import PyInstaller  # type: ignore
        return
    except Exception:
        print("[info] PyInstaller not found in user environment — installing with pip --user")
        pip_install_user(["pyinstaller"])

# ---------------- build & install actions ----------------
def build_with_pyinstaller(entry_path: str, workdir: str) -> str:
    """
    Run PyInstaller to build a onefile binary of entry_path.
    Returns path to built binary.
    Note: PyInstaller writes dist/<name> inside workdir (we run it from workdir).
    """
    ensure_pyinstaller_user()
    entry_abspath = os.path.abspath(entry_path)
    entry_basename = os.path.splitext(os.path.basename(entry_abspath))[0]
    old_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm", entry_abspath]
        print(f"[pyinstaller] building {entry_abspath}")
        run(cmd)
        dist_bin = os.path.join(workdir, "dist", entry_basename)
        if sys.platform == "win32":
            dist_bin += ".exe"
        if not os.path.exists(dist_bin):
            raise RuntimeError(f"Expected built binary at {dist_bin} not found.")
        return dist_bin
    finally:
        os.chdir(old_cwd)

def install_to_target(src_path: str, name: str, target_dir: str = DEFAULT_TARGET_DIR):
    """Install the src_path to target_dir/name (create target_dir if needed)."""
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, name)
    print(f"[install] copying {src_path} -> {target}")
    try:
        shutil.copy(src_path, target)
        os.chmod(target, 0o755)
    except PermissionError:
        raise RuntimeError(f"Permission denied while installing to {target}.")
    return target

# ---------------- main install flow ----------------
def install_repo(repo: str, build_bin: bool = False, target_dir: str | None = None):
    """
    repo: 'user/repo' or full url 'https://github.com/user/repo'
    build_bin: if True, build with PyInstaller
    target_dir: destination dir for the installed executable; default ~/.local/bin
    """
    target_dir = target_dir or DEFAULT_TARGET_DIR
    ensure_dirs()
    repo_url = normalize_repo(repo)
    repo_name = repo_url.rstrip("/").split("/")[-1]
    print(f"[info] installing {repo_url} (name: {repo_name})")

    # download and extract
    archive = download_repo_archive(repo_url)
    work_root = extract_archive_to_temp(archive)
    # read manifest
    manifest_path = os.path.join(work_root, "realtools.txt")
    if not os.path.exists(manifest_path):
        raise RuntimeError(f"realtools.txt manifest not found in repository root ({manifest_path}).")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = parse_manifest(f.read())

    entry = manifest.get("entry")
    if not entry:
        raise RuntimeError("Manifest does not define `entry` (main script path).")

    entry_path = os.path.join(work_root, entry)
    if not os.path.exists(entry_path):
        raise RuntimeError(f"Entry script {entry} not found in repo (expected at {entry_path}).")

    # install requirements into user site
    requirements = manifest.get("requirements") or []
    if requirements:
        pip_install_user(requirements)

    installed_target = None
    if build_bin:
        # Build a binary (user PyInstaller)
        built = build_with_pyinstaller(entry_path, workdir=work_root)
        installed_target = install_to_target(built, repo_name, target_dir=target_dir)
    else:
        # Install the script file into target_dir (make executable)
        installed_target = install_to_target(entry_path, os.path.basename(entry_path), target_dir=target_dir)

    # run post_install commands (optional)
    post_cmds = manifest.get("post_install") or []
    for cmd in post_cmds:
        # run in shell from work_root
        print(f"[post] running: {cmd}")
        run(cmd if isinstance(cmd, list) else ["bash", "-lc", cmd])

    add_installed(repo_name)
    print(f"✅ Installed {repo_name} -> {installed_target}")
    print(f"[note] if '{target_dir}' is not in your PATH, add it (for most systems add to ~/.profile or ~/.bashrc):")
    print(f"  export PATH=\"$PATH:{target_dir}\"")

# ---------------- uninstall ----------------
def uninstall(name: str, target_dir: str | None = None):
    target_dir = target_dir or DEFAULT_TARGET_DIR
    possible_paths = [
        os.path.join(target_dir, name),
        os.path.join(target_dir, f"{name}.py"),
        os.path.join(target_dir, f"{name}.exe"),
    ]
    removed = False
    for p in possible_paths:
        if os.path.exists(p):
            print(f"[remove] deleting {p}")
            try:
                os.remove(p)
                removed = True
            except Exception as e:
                raise RuntimeError(f"Failed to remove {p}: {e}")
    if removed:
        remove_installed(name)
        print(f"✅ Uninstalled {name}")
    else:
        print(f"[info] nothing found for {name} in {target_dir}")

# ---------------- CLI ----------------
USAGE = textwrap.dedent("""
    rtls — tiny GitHub installer (user-mode)

    Usage:
      rtls install <repo> [--bin] [--target-dir <dir>]
      rtls uninstall <name>
      rtls list
      rtls help

    <repo> can be 'user/repo' or a full https://github.com/... URL
    By default the script installs to ~/.local/bin (no sudo). Use --target-dir to override.
    --bin requests building a one-file binary with PyInstaller (PyInstaller will be installed in user site if missing).
""")

def cmd_list():
    ensure_dirs()
    items = read_installed()
    if not items:
        print("(no installed packages tracked)")
        return
    print("Installed packages (tracked):")
    for it in items:
        print(" -", it)

def main():
    if len(sys.argv) < 2:
        print(USAGE)
        return
    cmd = sys.argv[1].lower()
    try:
        if cmd in ("install", "i"):
            if len(sys.argv) < 3:
                print("Error: repo required.")
                print(USAGE)
                return
            repo = sys.argv[2]
            build_bin = "--bin" in sys.argv or "-b" in sys.argv
            # parse optional --target-dir value
            target_dir = None
            if "--target-dir" in sys.argv:
                idx = sys.argv.index("--target-dir")
                if idx + 1 < len(sys.argv):
                    target_dir = sys.argv[idx + 1]
            install_repo(repo, build_bin=build_bin, target_dir=target_dir)
        elif cmd in ("uninstall", "remove", "rm"):
            if len(sys.argv) < 3:
                print("Error: name required.")
                return
            uninstall(sys.argv[2])
        elif cmd == "list":
            cmd_list()
        else:
            print(USAGE)
    except Exception as e:
        print(f"[fatal] {e}")

if __name__ == "__main__":
    main()
