"""macOS: installs a Finder Quick Action called "Convert To…".

Right-click (or Control-click / two-finger click) a file > Quick Actions > Convert To…
A small list pops up with only the formats that file can become; pick one and it converts
silently next to the original. Works on several selected files at once.

The Quick Action is a standard Automator workflow bundle written to ~/Library/Services,
so nothing runs in the background and no admin password is needed.
"""
import plistlib
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .glass_mac import build_helper, bundled_helper, helper_path
from .system import app_dir, installed_executable

MENU_TITLE = "Convert To…"
SERVICES = Path.home() / "Library" / "Services"
WORKFLOW = SERVICES / f"{MENU_TITLE}.workflow"

UTIS = {
    "pillow": ["public.image"],
    "ffmpeg": ["public.audio", "public.movie", "public.audiovisual-content", "com.compuserve.gif"],
    "soffice": [
        "com.microsoft.word.doc", "org.openxmlformats.wordprocessingml.document",
        "org.oasis-open.opendocument.text", "public.rtf", "public.plain-text",
        "com.microsoft.excel.xls", "org.openxmlformats.spreadsheetml.sheet",
        "org.oasis-open.opendocument.spreadsheet", "public.comma-separated-values-text",
        "com.microsoft.powerpoint.ppt", "org.openxmlformats.presentationml.presentation",
        "org.oasis-open.opendocument.presentation",
    ],
}


def launch_command():
    if getattr(sys, "frozen", False):
        return shlex.quote(str(installed_executable()))
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(app_dir() / 'launcher.py'))}"


def shell_script():
    return (
        "# FileConvert: pops up a list of formats for the selected files, then converts them.\n"
        "export LANG=en_US.UTF-8\n"
        'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
        f'{launch_command()} pick "$@" >/dev/null 2>&1\n'
    )


def info_plist(file_types):
    return {
        "NSServices": [{
            "NSBackgroundColorName": "background",
            "NSIconName": "NSActionTemplate",
            "NSMenuItem": {"default": MENU_TITLE},
            "NSMessage": "runWorkflowAsService",
            "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
            "NSSendFileTypes": file_types,
        }]
    }


def document_wflow(script):
    u = lambda: str(uuid.uuid4()).upper()  # noqa: E731
    action = {
        "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
        "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {"COMMAND_STRING": {}, "CheckedForUserDefaultShell": {},
                                  "inputMethod": {}, "shell": {}, "source": {}},
        "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {
            "COMMAND_STRING": script,
            "CheckedForUserDefaultShell": True,
            "inputMethod": 1,            # 1 = pass selected files as arguments ("$@")
            "shell": "/bin/bash",
            "source": "",
        },
        "BundleIdentifier": "com.apple.RunShellScript",
        "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False,
        "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"],
        "Class Name": "RunShellScriptAction",
        "InputUUID": u(),
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
        "OutputUUID": u(),
        "UUID": u(),
        "UnlocalizedApplications": ["Automator"],
        "arguments": {
            "0": {"default value": 0, "name": "inputMethod", "required": "0", "type": "0", "uuid": "0"},
            "1": {"default value": "", "name": "source", "required": "0", "type": "0", "uuid": "0"},
            "2": {"default value": False, "name": "CheckedForUserDefaultShell", "required": "0",
                  "type": "0", "uuid": "0"},
            "3": {"default value": "", "name": "COMMAND_STRING", "required": "0", "type": "0", "uuid": "0"},
            "4": {"default value": "/bin/sh", "name": "shell", "required": "0", "type": "0", "uuid": "0"},
        },
        "isViewVisible": True,
        "location": "309.000000:253.000000",
        "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib",
    }
    return {
        "AMApplicationBuild": "523",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [{"action": action, "isViewVisible": True}],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleID": "com.apple.finder",
            "applicationBundleIDsByPath": {"/System/Library/CoreServices/Finder.app": "com.apple.finder"},
            "applicationPath": "/System/Library/CoreServices/Finder.app",
            "applicationPaths": ["/System/Library/CoreServices/Finder.app"],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15,
            "processesInput": 0,
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": 0,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def write_workflow(dest, engines, all_files=False):
    if all_files:
        types = ["public.item"]
    else:
        types = [u for eng, utis in UTIS.items() if eng in engines for u in utis]
    contents = Path(dest) / "Contents"
    if Path(dest).exists():
        shutil.rmtree(dest)
    contents.mkdir(parents=True)
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(info_plist(types), f)
    with open(contents / "document.wflow", "wb") as f:
        plistlib.dump(document_wflow(shell_script()), f)
    return types


AGENT_LABEL = "com.fileconvert.hold"
AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def _converter_command():
    if getattr(sys, "frozen", False):
        return [str(installed_executable())]
    return [sys.executable, str(app_dir() / "launcher.py")]


def _start_hold_agent(engines):
    """Press-and-hold helper: a LaunchAgent that starts at login and restarts if it stops."""
    import os
    from .cli import export_targets
    from .system import settings
    table = export_targets(engines)
    _stop_hold_agent(remove=False)
    AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
    with open(AGENT_PLIST, "wb") as f:
        plistlib.dump({
            "Label": AGENT_LABEL,
            "ProgramArguments": [str(helper_path()), "--agent", str(table)] + _converter_command(),
            "EnvironmentVariables": {"FILECONVERT_HOLD_MS": str(settings().get("hold_ms", 550)),
                                     "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Interactive",
            "LimitLoadToSessionType": "Aqua",
        }, f)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootstrap", domain, str(AGENT_PLIST)], capture_output=True, timeout=30)


def _stop_hold_agent(remove=True):
    import os
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{AGENT_LABEL}"], capture_output=True, timeout=30)
    if remove:
        AGENT_PLIST.unlink(missing_ok=True)


def _refresh_services():
    pbs = "/System/Library/CoreServices/pbs"
    for flag in ("-flush", "-update"):
        try:
            subprocess.run([pbs, flag], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass


def install(engines, classic_menu=False, all_files=False, dry_run=False):
    if dry_run:
        return shell_script()
    SERVICES.mkdir(parents=True, exist_ok=True)
    write_workflow(WORKFLOW, engines, all_files=all_files)
    _refresh_services()

    print("Building the glass picker (first time can take a minute)...", flush=True)
    glass_ok, glass_msg = build_helper()
    glass = ("Glass menu: on (Liquid Glass on macOS 26, frosted glass on older macOS)" if glass_ok else
             f"Glass menu: off, using the plain list ({glass_msg})")
    from .system import settings
    if glass_ok and settings().get("hold_to_convert", True):
        _start_hold_agent(engines)
        hold = ("Press and hold: on. Hold a file still in Finder for half a second.\n"
                "  macOS will ask you to allow FileConvert in Privacy & Security > Accessibility\n"
                "  (to see the hold) and to let it control Finder (to know which file). Allow both.")
    elif not glass_ok:
        hold = "Press and hold: off (needs the glass menu, see above)"
    else:
        hold = "Press and hold: off (turn on with \"hold_to_convert\": true in settings.json)"

    missing = []
    if "ffmpeg" not in engines:
        missing.append("ffmpeg (audio/video):     brew install ffmpeg")
    if "soffice" not in engines:
        missing.append("LibreOffice (documents):  brew install --cask libreoffice")
    if "heif" not in engines:
        missing.append(f"iPhone HEIC photos:       {shlex.quote(sys.executable)} -m pip install pillow-heif")
    extra = ("\n\nOptional extras not found:\n  " + "\n  ".join(missing) +
             "\nInstall them, then run the installer again.") if missing else ""
    return (f"Installed the '{MENU_TITLE}' Quick Action.\n"
            f"Right-click a file in Finder > Quick Actions > {MENU_TITLE}\n"
            "(If you don't see it: System Settings > Privacy & Security > Extensions > Finder,"
            f" and tick '{MENU_TITLE}'.)\n{glass}\n{hold}{extra}")


def uninstall():
    _stop_hold_agent()
    if WORKFLOW.exists():
        shutil.rmtree(WORKFLOW)
    if not bundled_helper():
        helper_path().unlink(missing_ok=True)
    _refresh_services()
    return f"Removed the '{MENU_TITLE}' Quick Action and the press-and-hold helper."
