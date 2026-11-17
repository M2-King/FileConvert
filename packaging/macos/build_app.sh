#!/bin/bash
# Builds dist/FileConvert.app and dist/FileConvert.dmg on a Mac (GitHub Actions runs this).
#   bash packaging/macos/build_app.sh [version]
# Needs: Python 3 with `pip install -r requirements.txt pyinstaller`, and Xcode or the Command Line Tools.
set -euo pipefail
cd "$(dirname "$0")/../.."
VERSION="${1:-1.0.0}"
PY="${PYTHON:-python3}"
rm -rf build/mac dist/FileConvert.app dist/FileConvert.dmg
mkdir -p build/mac dist

echo "== 1. Glass helper (Swift)"
cat > build/mac/Info.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.fileconvert.ui</string>
  <key>CFBundleName</key><string>FileConvert</string>
  <key>CFBundleDisplayName</key><string>FileConvert</string>
  <key>LSUIElement</key><true/>
  <key>NSAppleEventsUsageDescription</key><string>FileConvert asks Finder which file you're holding.</string>
</dict></plist>
EOF
xcrun swiftc -O -parse-as-library macos/FileConvertUI.swift -o build/mac/FileConvertUI \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker build/mac/Info.plist

echo "== 2. ffmpeg (static build, optional)"
EXTRA=()
ARCH="$(uname -m)"; [ "$ARCH" = "x86_64" ] && FFARCH=amd64 || FFARCH=arm64
if curl -fsSL --retry 3 -o build/mac/ffmpeg.zip \
     "https://ffmpeg.martin-riedl.de/redirect/latest/macos/${FFARCH}/release/ffmpeg.zip" \
   && unzip -o -q build/mac/ffmpeg.zip -d build/mac/ff && [ -f build/mac/ff/ffmpeg ]; then
  chmod +x build/mac/ff/ffmpeg
  if build/mac/ff/ffmpeg -hide_banner -version >/dev/null 2>&1; then
    EXTRA+=(--add-binary "build/mac/ff/ffmpeg:bin")
    echo "bundling ffmpeg"
  fi
fi
[ ${#EXTRA[@]} -eq 0 ] && echo "ffmpeg not bundled (audio/video will use Homebrew's ffmpeg if installed)"

echo "== 3. App bundle"
"$PY" -m PyInstaller --noconfirm --clean --windowed --name FileConvert \
  --icon assets/icon.icns --osx-bundle-identifier com.fileconvert.app \
  --collect-all pillow_heif --collect-all pypdfium2 --collect-all pypdfium2_raw \
  --collect-all pdf2docx --collect-all docx --collect-submodules fileconvert \
  --add-data "macos:macos" --add-binary "build/mac/FileConvertUI:." \
  ${EXTRA[@]+"${EXTRA[@]}"} --distpath dist --workpath build/mac/pyi launcher.py

PLIST=dist/FileConvert.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" || true
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null || true
codesign --force --deep --sign - dist/FileConvert.app

echo "== 4. Disk image"
mkdir -p build/mac/dmg
cp -R dist/FileConvert.app build/mac/dmg/
ln -s /Applications build/mac/dmg/Applications
hdiutil create -volname "FileConvert" -srcfolder build/mac/dmg -ov -format UDZO dist/FileConvert.dmg
echo "Built dist/FileConvert.dmg"
