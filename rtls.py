#!/usr/bin/env python3
"""
rtls — GitHub installer (user-mode) with distro-aware dependency handling.

Features:
- rtls install <repo> [--bin] [--target-dir <dir>] [--force] [--debug]
- rtls uninstall <name>
- rtls list
- rtls update    # <-- runs the official install.sh via curl|bash
- rtls help

Notes:
- If a requirement cannot be satisfied, rtls will abort the install unless --force is used.
- To allow automatic system package manager installs, set RTLS_INSTALL_SYSTEM=1 or run as root.
"""
from __future__ import annotations
import os
import sys
import subprocess
import tarfile
import urllib.request
import urllib.error
import shutil
import tempfile
import textwrap
import importlib
import re
from typing import Optional

HOME = os.path.expanduser("~")
CACHE_DIR = os.path.join(HOME, ".rtls", "cache")
STATE_DIR = os.path.join(HOME, ".rtls")
INSTALLED_DB = os.path.join(STATE_DIR, "installed.txt")
DEFAULT_TARGET_DIR = os.path.join(HOME, ".local", "bin")
GITHUB_ARCHIVE_PATH = "archive/refs/heads/{branch}.tar.gz"
OFFICIAL_INSTALLER = "https://raw.githubusercontent.com/AleXDE54/realtools/main/install.sh"

# Global debug flag
DEBUG = False


# ---------------- utilities ----------------
def debug_print(*args, **kwargs):
    if DEBUG:
        print("[debug]", *args, **kwargs)


def ensure_dirs():
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(DEFAULT_TARGET_DIR, exist_ok=True)


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    if capture:
        return subprocess.run(cmd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    else:
        return subprocess.run(cmd, check=check)


def normalize_repo(repo: str) -> str:
    repo = repo.strip()
    if repo.startswith("http://") or repo.startswith("https://"):
        return repo.rstrip("/")
    if "/" in repo:
        return f"https://github.com/{repo}"
    raise ValueError("Repo must be 'user/repo' or a full GitHub URL.")


def download_repo_archive(repo_url: str) -> tuple[str, str]:
    """
    Downloads repository archive. Tries main branch first, then master.
    Returns tuple of (archive_path, branch_used)
    """
    repo_name = repo_url.rstrip("/").split("/")[-1]
    
    # Try branches in order: main, master
    for branch in ["main", "master"]:
        local_archive = os.path.join(CACHE_DIR, f"{repo_name}-{branch}.tar.gz")
        download_url = f"{repo_url}/{GITHUB_ARCHIVE_PATH.format(branch=branch)}"
        print(f"[info] attempting {branch} branch: {download_url}")
        
        try:
            urllib.request.urlretrieve(download_url, local_archive)
            print(f"[info] successfully downloaded {branch} branch")
            return local_archive, branch
        except urllib.error.HTTPError as e:
            if e.code == 404:
                debug_print(f"{branch} branch not found (404)")
                continue
            else:
                debug_print(f"HTTP error {e.code} for {branch}: {e}")
                continue
        except Exception as e:
            debug_print(f"Failed to download {branch}: {e}")
            continue
    
    raise RuntimeError(f"Failed to download from {repo_url} (tried main/master branches)")


def extract_archive_to_temp(archive_path: str, branch: str) -> str:
    """Extracts tarball and returns path to the extracted repo root directory."""
    tmpdir = tempfile.mkdtemp(prefix="rtls-")
    debug_print(f"Extracting to temp dir: {tmpdir}")
    
    try:
        with tarfile.open(archive_path, "r:*") as tar:
            tar.extractall(path=tmpdir)
            debug_print(f"Extracted contents: {os.listdir(tmpdir)}")
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(f"Failed to extract {archive_path}: {e}")
    
    # Find the extracted directory (should be repo-name-branch)
    entries = [p for p in os.listdir(tmpdir) if os.path.isdir(os.path.join(tmpdir, p))]
    debug_print(f"Directories found: {entries}")
    
    if not entries:
        # No subdirectory, files are directly in tmpdir
        return tmpdir
    
    # Return the first subdirectory (the repo root)
    repo_root = os.path.join(tmpdir, entries[0])
    debug_print(f"Repo root set to: {repo_root}")
    return repo_root


# ---------------- manifest parsing ----------------
def parse_manifest(manifest_text: str) -> dict:
    data = {"entry": None, "requirements": [], "build": None, "post_install": []}
    lines = manifest_text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, rest = line.split("=", 1)
            key = key.strip().lower()
            rest = rest.strip()
            if rest == "":
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
                    data[key] = "\n".join(block) if block else None
            else:
                if key == "requirements":
                    parts = [p.strip() for p in rest.split(",") if p.strip()]
                    data["requirements"].extend(parts)
                elif key == "post_install":
                    parts = [p.strip() for p in rest.split(",") if p.strip()]
                    data["post_install"].extend(parts)
                else:
                    data[key] = rest
        else:
            parts = line.split(None, 1)
            if len(parts) == 2:
                data[parts[0].lower()] = parts[1].strip()
    
    if data["entry"]:
        data["entry"] = data["entry"].strip('"').strip("'")
    
    debug_print(f"Parsed manifest: entry={data['entry']}, requirements={data['requirements']}")
    return data


# ---------------- installed DB ----------------
def read_installed() -> list[str]:
    if not os.path.exists(INSTALLED_DB):
        return []
    with open(INSTALLED_DB, "r", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


def write_installed(listing: list[str]):
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


# ---------------- distro detection & suggestions ----------------
def detect_distro() -> str:
    plat = sys.platform
    if plat == "darwin":
        return "macos"
    if plat.startswith("win"):
        return "windows"
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            data = f.read().lower()
            if "arch" in data or "manjaro" in data:
                return "arch"
            if "debian" in data:
                return "debian"
            if "ubuntu" in data:
                return "ubuntu"
            if "fedora" in data or "rhel" in data or "centos" in data:
                return "fedora"
            if "alpine" in data:
                return "alpine"
    except Exception:
        pass
    return "unknown"


def suggest_system_command(import_name: str, distro: str) -> str:
    base = import_name.lower()
    pkg_variants = []
    if base.startswith("python-"):
        pkg_variants.append(base)
        pkg_variants.append(base.replace("python-", ""))
    if "-" in base:
        pkg_variants.append(base)
        pkg_variants.append(base.replace("-", "_"))
    else:
        pkg_variants.append(base)
    
    if distro in ("arch",):
        candidate = f"python-{pkg_variants[0]}"
        return f"sudo pacman -S {candidate}"
    if distro in ("debian", "ubuntu"):
        candidate = f"python3-{pkg_variants[0]}"
        return f"sudo apt update && sudo apt install -y {candidate}"
    if distro in ("fedora",):
        candidate = f"python3-{pkg_variants[0]}"
        return f"sudo dnf install -y {candidate}"
    if distro == "alpine":
        candidate = f"py3-{pkg_variants[0]}"
        return f"sudo apk add {candidate}"
    if distro == "macos":
        return f"python3 -m pip install --user {import_name}"
    if distro == "windows":
        return f"pip install {import_name}"
    return f"python3 -m pip install --user {import_name}"


# ---------------- requirement helpers ----------------
def canonical_import_name(req: str) -> str:
    name = re.split(r"[<>=!~]", req, maxsplit=1)[0].strip()
    if name.startswith("python-"):
        return name[len("python-") :].replace("-", "_")
    if "-" in name:
        return name.replace("-", "_")
    return name


def pip_install_user(req: str) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--user", req]
    # Add --break-system-packages for newer pip versions
    try:
        import subprocess
        result = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, text=True)
        if "24.0" in result.stdout or "25." in result.stdout:
            cmd.append("--break-system-packages")
    except:
        pass
    
    print(f"[pip] trying: {' '.join(cmd)}")
    try:
        run(cmd)
        return True
    except Exception:
        print("[pip] pip --user install failed.")
        return False


def try_system_install(system_cmd: str) -> bool:
    auto_allowed = os.environ.get("RTLS_INSTALL_SYSTEM", "") == "1" or (hasattr(os, "geteuid") and os.geteuid() == 0)
    print(f"[system] suggested: {system_cmd}")
    if not auto_allowed:
        print("[system] automatic system install not allowed. To enable, set RTLS_INSTALL_SYSTEM=1 or run as root.")
        return False
    print("[system] executing suggested system command (this may require network / sudo)...")
    try:
        subprocess.run(system_cmd, shell=True, check=True)
        return True
    except Exception as e:
        print(f"[system] system install failed: {e}")
        return False


def ensure_requirement(req: str) -> bool:
    import_name = canonical_import_name(req)
    tried_names = [import_name]
    if "_" in import_name:
        tried_names.append(import_name.replace("_", ""))
    if "-" in import_name:
        tried_names.append(import_name.replace("-", "_"))

    for name in tried_names:
        try:
            importlib.import_module(name)
            print(f"[ok] requirement '{req}' satisfied (import '{name}')")
            return True
        except Exception:
            pass

    distro = detect_distro()
    suggestion = suggest_system_command(import_name, distro)
    print(f"[warn] requirement '{req}' not importable.")
    print(f"[info] detected platform: {distro}")
    print(f"[info] suggestion: {suggestion}")

    pip_ok = pip_install_user(req)
    if pip_ok:
        try:
            importlib.invalidate_caches()
            importlib.import_module(import_name)
            print(f"[ok] requirement '{req}' installed via pip --user and import works.")
            return True
        except Exception:
            print(f"[warn] pip installed '{req}' but import still fails for '{import_name}'.")

    sys_ok = try_system_install(suggestion)
    if sys_ok:
        try:
            importlib.invalidate_caches()
            importlib.import_module(import_name)
            print(f"[ok] requirement '{req}' installed via system package manager.")
            return True
        except Exception:
            print(f"[warn] system package manager claimed success but import still fails for '{import_name}'.")

    print(f"[error] Could not satisfy requirement '{req}'. Please install it manually ({suggestion})")
    return False


def ensure_requirements_list(reqs: list[str]) -> bool:
    all_ok = True
    for r in reqs:
        if not r:
            continue
        ok = ensure_requirement(r)
        if not ok:
            all_ok = False
    return all_ok


# ---------------- build & install ----------------
def build_with_pyinstaller(entry_path: str, workdir: str) -> str:
    if not ensure_requirement("pyinstaller"):
        raise RuntimeError("PyInstaller not available and could not be installed automatically.")
    
    entry_abspath = os.path.abspath(entry_path)
    entry_basename = os.path.splitext(os.path.basename(entry_abspath))[0]
    old_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm", entry_abspath]
        print(f"[pyinstaller] running: {' '.join(cmd)}")
        run(cmd)
        dist_bin = os.path.join(workdir, "dist", entry_basename)
        if sys.platform == "win32":
            dist_bin += ".exe"
        if not os.path.exists(dist_bin):
            raise RuntimeError(f"Expected built binary not found at {dist_bin}")
        return dist_bin
    finally:
        os.chdir(old_cwd)


def install_to_target(src_path: str, name: str, target_dir: str = DEFAULT_TARGET_DIR) -> str:
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, name)
    print(f"[install] copying {src_path} -> {target}")
    shutil.copy(src_path, target)
    os.chmod(target, 0o755)
    return target


# ---------------- update (self-update) ----------------
def update_rtls():
    """
    Self-update by running the official installer script via curl|bash.
    This will re-download and re-install rtls using the repo installer.
    """
    cmd = f"curl -sSL {OFFICIAL_INSTALLER} | bash"
    print("[info] running self-update command:")
    print("  " + cmd)
    try:
        subprocess.run(cmd, shell=True, check=True)
        print("rtls updated successfully")
    except subprocess.CalledProcessError as e:
        print("[error] update failed:", e)
        raise


# ---------------- main install flow ----------------
def install_repo(repo: str, build_bin: bool = False, target_dir: Optional[str] = None, force: bool = False):
    target_dir = target_dir or DEFAULT_TARGET_DIR
    ensure_dirs()
    repo_url = normalize_repo(repo)
    repo_name = repo_url.rstrip("/").split("/")[-1]
    print(f"[info] installing {repo_url} (name: {repo_name})")

    # Download archive (tries main/master automatically)
    archive, branch_used = download_repo_archive(repo_url)
    work_root = extract_archive_to_temp(archive, branch_used)
    
    # Look for manifest
    manifest_path = os.path.join(work_root, "realtools.txt")
    debug_print(f"Looking for manifest at: {manifest_path}")
    
    # If not found, try to search for it in subdirectories
    if not os.path.exists(manifest_path):
        debug_print("Manifest not in root, searching recursively...")
        for root, dirs, files in os.walk(work_root):
            if "realtools.txt" in files:
                manifest_path = os.path.join(root, "realtools.txt")
                debug_print(f"Found manifest at: {manifest_path}")
                work_root = root  # Update work_root to the directory containing manifest
                break
    
    if not os.path.exists(manifest_path):
        # Debug output to help diagnose
        print(f"[error] Manifest realtools.txt not found in repo root")
        print(f"[debug] Looking in: {work_root}")
        print(f"[debug] Contents:")
        for f in os.listdir(work_root):
            print(f"  - {f}")
        raise RuntimeError(f"Manifest realtools.txt not found in repo root")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = parse_manifest(f.read())

    entry = manifest.get("entry")
    if not entry:
        raise RuntimeError("Manifest missing 'entry'")

    entry_path = os.path.join(work_root, entry)
    if not os.path.exists(entry_path):
        raise RuntimeError(f"Entry script {entry} not found in repo (expected {entry_path})")

    # Ensure requirements
    requirements = manifest.get("requirements") or []
    if requirements:
        print("[info] checking requirements...")
        ok = ensure_requirements_list(requirements)
        if not ok:
            if not force:
                print("[fatal] Some requirements are missing and could not be installed.")
                print("        Install dependencies first or rerun with --force to override.")
                raise RuntimeError("Unsatisfied requirements — aborting installation.")
            else:
                print("[warn] continuing installation despite unsatisfied requirements due to --force")

    # Build or copy
    installed_target = None
    if build_bin:
        built = build_with_pyinstaller(entry_path, workdir=work_root)
        installed_target = install_to_target(built, repo_name, target_dir=target_dir)
    else:
        installed_target = install_to_target(entry_path, os.path.basename(entry_path), target_dir=target_dir)

    # post_install commands
    for cmd in manifest.get("post_install") or []:
        print(f"[post] running: {cmd}")
        try:
            run(cmd if isinstance(cmd, list) else ["bash", "-lc", cmd])
        except Exception as e:
            print(f"[warn] post-install command failed: {e}")
            if not force:
                raise

    add_installed(repo_name)
    print(f"[success] Installed {repo_name} -> {installed_target}")
    
    # Check PATH
    if target_dir not in os.environ.get("PATH", ""):
        print(f"[note] Add to your shell profile ({os.path.join(HOME, '.bashrc')}) if not present:")
        print(f"  export PATH=\"$PATH:{target_dir}\"")
    
    # Cleanup temp directory
    shutil.rmtree(os.path.dirname(work_root), ignore_errors=True)
    print(f"[info] Installation complete!")


# ---------------- uninstall ----------------
def uninstall(name: str, target_dir: Optional[str] = None):
    target_dir = target_dir or DEFAULT_TARGET_DIR
    candidates = [
        os.path.join(target_dir, name),
        os.path.join(target_dir, f"{name}.py"),
        os.path.join(target_dir, f"{name}.exe")
    ]
    removed = False
    for p in candidates:
        if os.path.exists(p):
            print(f"[remove] deleting {p}")
            os.remove(p)
            removed = True
    
    # Also check for binary without .py extension in PATH
    for cmd_path in os.environ.get("PATH", "").split(":"):
        p = os.path.join(cmd_path, name)
        if os.path.exists(p) and p not in candidates:
            print(f"[remove] deleting {p}")
            os.remove(p)
            removed = True
    
    if removed:
        remove_installed(name)
        print(f"[success] Uninstalled {name}")
    else:
        print(f"[info] Nothing found for {name} in {target_dir}")


# ---------------- CLI ----------------
USAGE = textwrap.dedent(f"""
╔══════════════════════════════════════════════════════════════╗
║                       rtls - RealTools                       ║
║              GitHub installer with dependency management     ║
╚══════════════════════════════════════════════════════════════╝

Usage:
  rtls install <repo> [--bin] [--target-dir <dir>] [--force] [--debug]
  rtls uninstall <name>
  rtls list
  rtls update
  rtls help

Examples:
  rtls install AleXDE54/lofitty
  rtls install AleXDE54/lofitty --bin --force
  rtls install https://github.com/AleXDE54/realtools --debug
  rtls uninstall lofitty

Options:
  --bin                 Build binary with PyInstaller (requires pyinstaller)
  --target-dir <dir>    Install to custom directory (default: ~/.local/bin)
  --force               Force install even if requirements fail
  --debug               Show debug output for troubleshooting

Self-update:
  rtls update           Update rtls to latest version

Notes:
  - Repository must contain 'realtools.txt' manifest file
  - Uses pip --user for Python dependencies (safe, no sudo)
  - Set RTLS_INSTALL_SYSTEM=1 to allow system package manager installs
  - Set RTLS_FORCE_INSTALL=1 to force installation (same as --force)
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
    global DEBUG
    
    if len(sys.argv) < 2:
        print(USAGE)
        return
    
    # Check for debug flag
    if "--debug" in sys.argv:
        DEBUG = True
        sys.argv.remove("--debug")
        print("[debug] Debug mode enabled")
    
    cmd = sys.argv[1].lower()
    
    try:
        if cmd in ("install", "i"):
            if len(sys.argv) < 3:
                print("Error: repo required.")
                print(USAGE)
                return
            repo = sys.argv[2]
            build_bin = "--bin" in sys.argv or "-b" in sys.argv
            force = ("--force" in sys.argv or "-f" in sys.argv) or os.environ.get("RTLS_FORCE_INSTALL", "") == "1"
            target_dir = None
            if "--target-dir" in sys.argv:
                idx = sys.argv.index("--target-dir")
                if idx + 1 < len(sys.argv):
                    target_dir = sys.argv[idx + 1]
            install_repo(repo, build_bin=build_bin, target_dir=target_dir, force=force)
        elif cmd in ("uninstall", "remove", "rm"):
            if len(sys.argv) < 3:
                print("Error: name required.")
                return
            uninstall(sys.argv[2])
        elif cmd == "list":
            cmd_list()
        elif cmd == "update":
            update_rtls()
        elif cmd in ("help", "-h", "--help"):
            print(USAGE)
        else:
            print(f"Unknown command: {cmd}")
            print(USAGE)
    except KeyboardInterrupt:
        print("\n[info] Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"[fatal] {e}")
        if DEBUG:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
