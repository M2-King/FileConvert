Optional: put an ffmpeg program here to bundle it with FileConvert.

  Windows: ffmpeg.exe   (from https://www.gyan.dev/ffmpeg/builds/ - "release essentials", the bin folder)
  macOS:   ffmpeg       (from https://evermeet.cx/ffmpeg/ or copy $(brew --prefix)/bin/ffmpeg)

If this folder is empty, FileConvert uses the ffmpeg already installed on the computer
(winget / Homebrew / PATH). Audio & video menus only appear when ffmpeg is found.
