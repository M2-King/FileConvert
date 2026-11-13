# FileConvert

A silent file converter for **Windows** and **macOS**. It has no window and no tray icon. To use
it, **press and hold a file**: glass branches grow out of the file, one for each format it can
become. Slide onto a format and let go. FileConvert saves the converted copy next to the original,
and an animated glass pill shows the progress.

## Download

The newest builds are on this repo's **Releases** page:

| File | For | How to install |
|---|---|---|
| `FileConvert-Setup.exe` | Windows 10/11 | Run it. If SmartScreen appears, click *More info*, then *Run anyway*. No admin rights are needed, and ffmpeg is included. |
| `FileConvert.dmg` | Mac (Apple silicon) | Drag FileConvert to Applications and open it there. If macOS blocks it, go to *System Settings → Privacy & Security* and click *Open Anyway*. |
| `FileConvert.ipa` | iPhone / iPad | It's unsigned: install it with Sideloadly or AltStore using your Apple ID, or sign it with a developer account. |

GitHub builds these automatically on every push (`.github/workflows/build.yml`). You can also start a
build by hand: open the *Actions* tab, choose *Build*, then *Run workflow*.

### iPhone and iPad

iOS doesn't let apps watch for a press-and-hold on other apps' files, so the iPhone version works
from the Share sheet instead. In the Files app (or Photos), press and hold a file, tap **Share**,
then **Convert with FileConvert**. The same glass branches grow out of the file: slide onto a format
and let go, then tap *Save to Files* or *Share*. It uses Apple's built-in converters, so it handles
photos (JPG, PNG, HEIC, TIFF, GIF, BMP, PDF), videos (MP4, MOV, animated GIF, or just the sound as
M4A/WAV) and audio (M4A, WAV, AIFF). Word, Excel and PowerPoint files are desktop-only.

## Press and hold

1. Hold the left mouse button still on a file (in File Explorer, Finder, or on the desktop).
2. A ring fills around the pointer. After about half a second, curvy glass branches grow out of the
   file. Each one ends in a glass label (MP4, MP3, PDF…) with a soft mirror reflection.
3. Keep holding, slide onto a format and let go. The other branches pull back, the label flies into
   the file, and the conversion starts.
   - If you let go somewhere else, the branches stay open. Click a format, or click anywhere else
     to close them.
   - Clicking quickly, or moving before the ring fills, works exactly as before: normal clicks and
     drags are untouched.
4. The branches fan out toward the side of the screen with the most room, so files at the edge of
   the screen work too. Several selected files get the formats that work for all of them.

This needs a small helper that runs in the background, because Windows and macOS don't tell apps
when you press and hold a file. It starts with your computer and uses almost no CPU.
- **Windows:** it watches the mouse buttons and asks File Explorer which file you're holding.
- **macOS:** the first time, allow FileConvert in *System Settings → Privacy & Security →
  Accessibility* (so it can see the hold), and let it control Finder (so it knows which file).
  After a reinstall, you may need to turn the Accessibility switch off and on again.

The right-click menu (Windows: *Convert to ▸*, Mac: *Quick Actions ▸ Convert To…*) is still there
as a backup. On Mac it opens the same branches.

```
Windows                                   macOS
Right-click photo.png                     Right-click photo.png
  └─ Convert to  ▸  JPG                     └─ Quick Actions ▸ Convert To…
                    WEBP                          ┌──────────────────────┐
                    GIF                           │ Convert photo.png to:│
                    ICO                           │  JPG                 │
                    PDF  …                        │  WEBP   …            │
                                                  └──────────────────────┘
```

## Glass look and animations

- **macOS:** after you pick a format, the branches turn into a pill with a gradient progress ring.
  When the conversion finishes, a check mark draws itself and the pill folds away. Click the pill
  to show the file in Finder. macOS 26 and later get Apple's real Liquid Glass; macOS 12–15 get
  frosted glass.
- **Windows:** after you pick a format in the right-click menu, a frosted-glass island drops in at
  the top of the screen. It opens with a spring, shows live progress with a shine across the
  glass, then pops a check mark and folds away. It follows your light or dark theme. Click it to
  show the file in Explorer. Converting several files at once stacks one island per file.
- If something fails, you get a red cross, a small shake and the reason.
- To go back to plain system menus and notifications, set `"glass_ui": false` in `settings.json`.

The menu only lists formats that the selected file can actually be converted to. On a Mac you can
select several files at once, and the list shows the formats that work for all of them.

## What converts to what

| You right-click… | You can get… | Needs |
|---|---|---|
| **Images:** PNG, JPG, WEBP, GIF, BMP, TIFF, ICO, HEIC (iPhone) | PNG, JPG, WEBP, GIF, BMP, TIFF, ICO, PDF (+ animated GIF → MP4/WEBM) | built in |
| **Audio:** MP3, WAV, FLAC, AAC, M4A, OGG, OPUS, WMA, AIFF, AMR | MP3, WAV, FLAC, M4A, OGG, OPUS, AIFF | ffmpeg |
| **Video:** MP4, MOV, MKV, AVI, WEBM, WMV, FLV, M4V, MPG, 3GP, TS, MTS | MP4, MOV, MKV, WEBM, AVI, animated GIF, or audio only (MP3/WAV/M4A) | ffmpeg |
| **Documents:** DOC, DOCX, ODT, RTF, TXT | PDF, DOCX, ODT, RTF, TXT | LibreOffice |
| **Spreadsheets:** XLS, XLSX, ODS, CSV | PDF, XLSX, ODS, CSV | LibreOffice |
| **Slides:** PPT, PPTX, ODP | PDF, PPTX, ODP | LibreOffice |

Image conversion works straight away. Audio/video menus appear once ffmpeg is installed, and
document menus once LibreOffice is installed. Install those, then run the installer again.

Some details:
- Phone photos keep their correct rotation and colour profile.
- Transparent images get a white background when converted to JPG.
- MOV→MP4 and similar container changes finish in seconds when the video doesn't need
  re-encoding.
- Existing files are never overwritten. A second conversion is saved as `photo (1).jpg`.

## Install on Windows

1. Install **Python 3** from python.org and tick *"Add python.exe to PATH"*.
2. Optional: `winget install Gyan.FFmpeg` (audio/video) and
   `winget install TheDocumentFoundation.LibreOffice` (documents).
3. Double-click **`install_windows.bat`**.

To use it, right-click a file, then go to **Convert to ▸** and pick a format.

On **Windows 11** the menu is under *Show more options* (or Shift + right-click). To put it in the
first menu, run `install_windows.bat --classic-menu`. This restores the full Windows 10-style
right-click menu everywhere, and uninstalling switches it back.

## Install on macOS

1. Optional: `brew install ffmpeg` (audio/video) and `brew install --cask libreoffice` (documents).
   For the glass picker you need Apple's Command Line Tools (`xcode-select --install`). Most Macs
   with Python already have them.
2. Double-click **`install_macos.command`**. The first install takes about a minute because it
   builds the glass picker. If macOS blocks it, right-click it and choose Open,
   or run `bash install_macos.command` in Terminal.

To use it, right-click (or two-finger click) a file and go to **Quick Actions ▸ Convert To…**. A
format list pops up. While a long video converts, a small gear spins in the menu bar.

- If **Convert To…** doesn't appear: go to *System Settings → Privacy & Security → Extensions →
  Finder* and tick *Convert To…*.
- The first time, macOS may ask whether Python can access your Desktop/Documents/Downloads. Click
  **Allow**.
- If .mkv or other unusual files don't show the menu, run
  `bash install_macos.command --all-files`.

Keep the FileConvert folder where it is after installing, because the menu runs from it.

## Build it yourself (no Python needed on other computers)

- **Windows:** run `build_windows.bat`. You get `dist\FileConvert\FileConvert.exe`. Double-click it
  and choose **Yes**. It copies itself to `%LOCALAPPDATA%\Programs\FileConvert` and adds the menu.
  Choose **No** to remove the menu.
- **macOS:** run `bash build_macos.sh`, then `dist/FileConvert/FileConvert install`.

To bundle ffmpeg in the build, put `ffmpeg.exe` (Windows) or `ffmpeg` (Mac) in the `bin/` folder
first. A build can only be made on the system it's for: build the Windows version on Windows and
the Mac version on a Mac. If you'll give the Mac build to other people, sign it with an Apple
Developer ID, or Gatekeeper will block it.

## Settings, logs, uninstall

- Settings file: `settings.json` in `%LOCALAPPDATA%\FileConvert` (Windows) or
  `~/Library/Application Support/FileConvert` (macOS). You can turn the glass UI on or off
  (`glass_ui`), turn off press and hold (`"hold_to_convert": false`), change how long to hold
  (`"hold_ms": 550`), force frosted glass instead of Liquid Glass on macOS 26 (`"mac_glass": "classic"`),
  set JPG/WEBP quality, and set custom ffmpeg/LibreOffice paths.
- Every conversion is logged to `fileconvert.log` in the same folder.
- `python launcher.py doctor` shows which converters were found.
- `python tests/selftest.py` creates sample files and runs every conversion as a test.
- To uninstall, run `uninstall_windows.bat` / `uninstall_macos.command`, or `FileConvert uninstall`.

## How it works

| File | Purpose |
|---|---|
| `fileconvert/formats.py` | Conversion table. Edit this to add formats. |
| `fileconvert/engines.py` | Converters (Pillow, ffmpeg, LibreOffice). |
| `fileconvert/install_windows.py` | Registers the cascading menu under `HKCU\Software\Classes\SystemFileAssociations\.ext\shell`. No admin rights needed. |
| `fileconvert/install_macos.py` | Writes a Finder Quick Action to `~/Library/Services`. |
| `fileconvert/tree.py` | Branch layout: which way the branches fan out and where each label goes. |
| `fileconvert/win_agent.py` + `tree_window.py` + `glass_tree.py` | Windows press-and-hold helper and its glass branches. |
| `fileconvert/glass_hud.py` | Windows glass island. It's drawn with Pillow and shown with Tk. |
| `macos/FileConvertUI.swift` + `fileconvert/glass_mac.py` | Mac press-and-hold helper, branches and progress pill (SwiftUI). |
| `fileconvert/ui.py` | Notifications and the plain fallback picker. |
| `fileconvert/cli.py` | Commands the menus call: `convert --to FMT FILE`, `pick FILES`. |
