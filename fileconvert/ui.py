"""The only UI this app has: system notifications, the macOS format picker, and message boxes."""
import os
import shutil
import subprocess
import sys

from .system import APP_NAME, IS_MAC, IS_WIN, settings, spawn_detached

# Windows toast via built-in PowerShell (no extra installs). Text is passed through env vars
# so file names with quotes or symbols can't break the script.
_WIN_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text')
$n.Item(0).AppendChild($t.CreateTextNode($env:FC_TITLE)) > $null
$n.Item(1).AppendChild($t.CreateTextNode($env:FC_MSG)) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($toast)
"""

_MAC_NOTIFY = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"]

_MAC_PICK = [
    "on run argv",
    "try",
    "activate",
    "end try",
    "set thePrompt to item 1 of argv",
    "set theOptions to rest of argv",
    'set picked to choose from list theOptions with title "Convert To" with prompt thePrompt '
    'OK button name "Convert" default items {item 1 of theOptions}',
    'if picked is false then return ""',
    "return item 1 of picked",
    "end run",
]


def _osascript(lines, *args):
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    return cmd + list(args)


def notify(message, title=APP_NAME, force=False):
    if not (force or settings().get("notifications", True)):
        return
    if IS_MAC:
        spawn_detached(_osascript(_MAC_NOTIFY, title, message))
    elif IS_WIN:
        env = dict(os.environ, FC_TITLE=title, FC_MSG=message)
        spawn_detached(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-WindowStyle", "Hidden", "-Command", _WIN_TOAST], env=env)
    elif shutil.which("notify-send"):
        spawn_detached(["notify-send", title, message])


def pick_format(labels, prompt):
    """macOS: pop up a list of formats next to the cursor. Returns the chosen index or None."""
    if not IS_MAC:
        raise RuntimeError("The pop-up picker is for macOS; Windows uses the right-click submenu.")
    proc = subprocess.run(_osascript(_MAC_PICK, prompt, *labels), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    choice = proc.stdout.strip()
    return labels.index(choice) if choice in labels else None


def message(text, title=APP_NAME):
    """Show a result to the user even when there's no console (double-clicked .exe)."""
    if sys.stdout is not None:
        print(text)
        return
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)


def ask_yes_no_cancel(text, title=APP_NAME):
    """Windows message box. Returns 'yes', 'no' or 'cancel'."""
    import ctypes
    r = ctypes.windll.user32.MessageBoxW(None, text, title, 0x03 | 0x20)
    return {6: "yes", 7: "no"}.get(r, "cancel")
