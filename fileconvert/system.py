"""Platform helpers: where things live, running tools without flashing windows, settings, locks."""
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

APP_NAME = "FileConvert"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

DEFAULT_SETTINGS = {
    "hold_to_convert": True, # press and hold a file to open the branch menu
    "hold_ms": 550,          # how long to hold still before the branches grow
    "glass_ui": True,        # animated glass picker/progress (off = plain menus + notifications)
    "notifications": True,   # notification when done (used when glass_ui is off/unavailable)
    "mac_glass": "auto",     # "auto" = Liquid Glass on macOS 26+, "classic" = frosted glass everywhere
    "jpg_quality": 92,
    "webp_quality": 90,
    "ffmpeg_path": "",       # leave empty to auto-detect
    "soffice_path": "",      # leave empty to auto-detect
}


class ConversionError(Exception):
    pass


def app_dir():
    """Folder containing the app (the .exe when built, the project folder when run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir():
    if IS_WIN:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        d = base / APP_NAME
    elif IS_MAC:
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "fileconvert"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings():
    path = data_dir() / "settings.json"
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        try:
            path.write_text(json.dumps(DEFAULT_SETTINGS, indent=2), encoding="utf-8")
        except OSError:
            pass
    except (OSError, ValueError):
        pass
    return s


def get_logger():
    log = logging.getLogger(APP_NAME)
    if not log.handlers:
        try:
            h = logging.handlers.RotatingFileHandler(
                data_dir() / "fileconvert.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
        except OSError:
            log.addHandler(logging.NullHandler())
        log.setLevel(logging.INFO)
    return log


def run_quiet(cmd, timeout=None, env=None):
    """Run a command with no console window. Raises ConversionError with the tool's error text."""
    kwargs = {}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, env=env, **kwargs)
    except subprocess.TimeoutExpired:
        raise ConversionError(f"{Path(cmd[0]).name} took too long and was stopped")
    except OSError as e:
        raise ConversionError(f"Could not start {Path(cmd[0]).name}: {e}")
    if proc.returncode != 0:
        raise ConversionError(friendly_error(proc.stderr.decode("utf-8", "replace"), Path(cmd[0]).name))
    return proc


_KNOWN_ERRORS = [
    ("Invalid data found", "The file looks damaged, or isn't really the format its name says."),
    ("does not contain any stream", "No audio or video found in this file."),
    ("Output file does not contain any stream", "No audio or video found in this file."),
    ("matches no streams", "No audio or video found in this file."),
    ("Permission denied", "Permission denied: the file or folder is protected."),
    ("No space left", "The disk is full."),
    ("Unknown encoder", "This ffmpeg build is missing a needed encoder. Try a full ffmpeg build."),
]


def friendly_error(stderr, tool):
    for needle, nice in _KNOWN_ERRORS:
        if needle in stderr:
            return nice
    import re
    lines = [re.sub(r"\[[^\]]* @ 0x[0-9a-f]+\]\s*", "", l).strip() for l in stderr.splitlines()]
    lines = [l for l in lines if l]
    return (lines[-1] if lines else f"{tool} failed")[:200]


def spawn_detached(cmd, env=None):
    """Start a helper (e.g. a notification) without waiting and without a window."""
    kwargs = {}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env, **kwargs)
    except OSError:
        pass


def _first_existing(paths):
    for p in paths:
        if p and Path(p).is_file():
            return str(p)
    return None


def find_ffmpeg():
    exe = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    configured = settings().get("ffmpeg_path")
    bundled = Path(getattr(sys, "_MEIPASS", app_dir())) / "bin" / exe
    candidates = [configured, app_dir() / "bin" / exe, bundled, shutil.which("ffmpeg")]
    if IS_MAC:
        candidates += ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/local/bin/ffmpeg"]
    if IS_WIN:
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates += [local / "Microsoft" / "WinGet" / "Links" / exe,
                       Path(os.environ.get("ProgramData", "C:/ProgramData")) / "chocolatey" / "bin" / exe,
                       Path.home() / "scoop" / "shims" / exe,
                       Path("C:/ffmpeg/bin") / exe]
        # winget sometimes only unpacks into Packages\ without a link
        pkgs = local / "Microsoft" / "WinGet" / "Packages"
        if pkgs.is_dir():
            candidates += sorted(pkgs.glob(f"*FFmpeg*/*/bin/{exe}"))
    return _first_existing(candidates)


def find_soffice():
    configured = settings().get("soffice_path")
    candidates = [configured, shutil.which("soffice"), shutil.which("libreoffice")]
    if IS_MAC:
        candidates += ["/Applications/LibreOffice.app/Contents/MacOS/soffice",
                       str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice")]
    if IS_WIN:
        for var in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    return _first_existing(candidates)


def available_engines():
    engines = set()
    try:
        import PIL  # noqa: F401
        engines.add("pillow")
        try:
            import pillow_heif  # noqa: F401
            engines.add("heif")
        except ImportError:
            pass
    except ImportError:
        pass
    if find_ffmpeg():
        engines.add("ffmpeg")
    if find_soffice():
        engines.add("soffice")
    return engines


def installed_executable():
    """For the built app: copy it to a permanent per-user folder (so the menu keeps working
    even if the download folder is deleted) and return the path of the copied executable."""
    exe = Path(sys.executable).resolve()
    if ".app/Contents/MacOS" in str(exe):            # a Mac app bundle stays where the user put it
        return exe
    if IS_WIN:
        home = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / APP_NAME
    else:
        home = data_dir() / "app"
    src_dir = exe.parent
    if home.exists() and src_dir == home.resolve():   # already running from the installed copy
        return exe
    if home.exists():
        shutil.rmtree(home, ignore_errors=True)
    shutil.copytree(src_dir, home, symlinks=True, dirs_exist_ok=True)
    return home / exe.name


def reserve_output(src, ext):
    """Pick 'name.ext' next to the source (or 'name (1).ext' ...) and claim it atomically,
    so parallel conversions never overwrite each other or any existing file."""
    src = Path(src)
    folders = [src.parent, Path.home() / "Downloads"]
    last_err = None
    for folder in folders:
        i = 0
        while True:
            name = f"{src.stem}.{ext}" if i == 0 else f"{src.stem} ({i}).{ext}"
            p = folder / name
            try:
                with open(p, "x"):
                    return p
            except FileExistsError:
                i += 1
            except OSError as e:  # e.g. read-only folder -> fall back to Downloads
                last_err = e
                break
    raise ConversionError(f"Can't write the converted file: {last_err}")


@contextmanager
def file_lock(name):
    """Cross-process lock (used so only one LibreOffice conversion runs at a time)."""
    path = data_dir() / f"{name}.lock"
    f = open(path, "a+")
    try:
        if IS_WIN:
            import msvcrt
            import time
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.5)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if IS_WIN:
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()
