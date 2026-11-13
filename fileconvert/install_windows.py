"""Windows: adds a cascading 'Convert to >' submenu to Explorer's right-click menu.

Everything goes under HKEY_CURRENT_USER, so no admin rights are needed. For each supported
extension we write:

    HKCU\\Software\\Classes\\SystemFileAssociations\\.png\\shell\\FileConvert
        MUIVerb     = "Convert to"
        SubCommands = ""                      (tells Explorer the children are a submenu)
        shell\\01_jpg   MUIVerb = "JPG"   command = "<app>" convert --to jpg "%1"
        shell\\02_webp  ...
"""
import json
import sys
from pathlib import Path

from .formats import all_input_extensions, supported_extensions
from .system import app_dir, data_dir, installed_executable

ROOT = r"Software\Classes\SystemFileAssociations"
VERB = "FileConvert"
CLASSIC_MENU_KEY = r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "FileConvertHold"


def _start_hold_agent(winreg, command):
    """Press-and-hold helper: start now and at every sign-in."""
    from .win_agent import stop_agent
    import subprocess
    stop_agent()                                   # restart so updates take effect
    agent_cmd = f"{command} agent"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, agent_cmd)
    subprocess.Popen(agent_cmd, creationflags=0x00000008 | 0x08000000,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _stop_hold_agent(winreg):
    from .win_agent import stop_agent
    stop_agent()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except OSError:
        pass


def launch_command():
    """The program the menu runs, without a console window."""
    if getattr(sys, "frozen", False):                     # built FileConvert.exe (--windowed)
        return f'"{installed_executable()}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")                # pythonw = Python without a console
    exe = pythonw if pythonw.exists() else exe
    return f'"{exe}" "{app_dir() / "launcher.py"}"'


def build_entries(engines, command=None):
    """Return [(key_path, {value_name: value})] for every registry key we'll create."""
    command = command or launch_command()
    if getattr(sys, "frozen", False):
        icon = command.split('"')[1]                      # the installed .exe has the icon inside
    else:
        icon = str(app_dir() / "assets" / "icon.ico")
    entries = []
    for ext, targets in supported_extensions(engines).items():
        menu = rf"{ROOT}\.{ext}\shell\{VERB}"
        entries.append((menu, {"MUIVerb": "Convert to", "SubCommands": "", "Icon": icon}))
        for i, t in enumerate(targets, 1):
            item = rf"{menu}\shell\{i:02d}_{t.ext}"
            entries.append((item, {"MUIVerb": t.label}))
            entries.append((rf"{item}\command", {"": f'{command} convert --to {t.ext} "%1"'}))
    return entries


def _delete_tree(winreg, root, path):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(winreg, root, rf"{path}\{child}")
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def _refresh_explorer():
    import ctypes
    SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


def _restart_explorer():
    import subprocess
    flags = subprocess.CREATE_NO_WINDOW
    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], creationflags=flags, capture_output=True)
    subprocess.Popen(["explorer.exe"])


def _state_file():
    return data_dir() / "install_state.json"


def install(engines, classic_menu=False, all_files=False, dry_run=False):
    if dry_run:
        return "\n".join(f"[HKCU\\{k}] {v}" for k, v in build_entries(engines, '"FileConvert.exe"'))
    from .win_agent import stop_agent
    stop_agent()                                   # the running helper may lock the installed copy
    command = launch_command()
    entries = build_entries(engines, command)

    import winreg
    _remove_menus(winreg)
    for path, values in entries:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    state = {"classic_menu": False}
    if classic_menu:
        # Windows 11: makes right-click open the full menu directly (no "Show more options").
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, CLASSIC_MENU_KEY + r"\InprocServer32",
                                0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "")
        state["classic_menu"] = True
        _restart_explorer()
    _state_file().write_text(json.dumps(state))
    _refresh_explorer()

    from .cli import export_targets
    from .system import settings
    export_targets(engines)
    hold = "off (turn on with \"hold_to_convert\": true in settings.json)"
    if settings().get("hold_to_convert", True):
        try:
            import comtypes  # noqa: F401  (reads the selected files from Explorer)
            _start_hold_agent(winreg, command)
            hold = "on: hold a file still for half a second"
        except ImportError:
            hold = "off (missing comtypes: pip install comtypes, then run install again)"

    exts = sorted({p.split("\\")[3] for p, _ in entries if p.endswith(VERB)})
    note = ("" if classic_menu else
            "\n\nWindows 11: it's under 'Show more options' (or Shift + right-click).\n"
            "Re-run the installer with --classic-menu to put it straight in the first menu.")
    missing = []
    if "ffmpeg" not in engines:
        missing.append("ffmpeg (audio/video): winget install Gyan.FFmpeg")
    if "soffice" not in engines:
        missing.append("LibreOffice (documents): winget install TheDocumentFoundation.LibreOffice")
    extra = ("\n\nOptional extras not found:\n  " + "\n  ".join(missing) +
             "\nInstall them, then run install again.") if missing else ""
    return (f"Right-click menu added for {len(exts)} file types:\n{' '.join(exts)}\n\n"
            f"Press and hold menu: {hold}{note}{extra}")


def _remove_menus(winreg):
    for ext in all_input_extensions():
        _delete_tree(winreg, winreg.HKEY_CURRENT_USER, rf"{ROOT}\.{ext}\shell\{VERB}")


def uninstall():
    import winreg
    _stop_hold_agent(winreg)
    _remove_menus(winreg)
    restored = ""
    try:
        state = json.loads(_state_file().read_text())
    except (OSError, ValueError):
        state = {}
    if state.get("classic_menu"):
        _delete_tree(winreg, winreg.HKEY_CURRENT_USER, CLASSIC_MENU_KEY)
        _restart_explorer()
        restored = "\nWindows 11 compact menu restored."
    _state_file().unlink(missing_ok=True)
    _refresh_explorer()
    return "FileConvert removed from the right-click menu." + restored
