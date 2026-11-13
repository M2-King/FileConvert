"""Self-test: makes sample files in a temp folder and converts each into every format it offers.

    Windows:  .venv\\Scripts\\python.exe tests\\selftest.py
    macOS:    .venv/bin/python tests/selftest.py
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fileconvert.cli import convert_one  # noqa: E402
from fileconvert.formats import targets_for  # noqa: E402
from fileconvert.system import available_engines, find_ffmpeg  # noqa: E402


def make_samples(d):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (301, 199), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((20, 20, 280, 180), fill=(255, 80, 0, 200))
    im.save(d / "logo.png")
    frames = [Image.new("RGB", (120, 80), (i * 40, 100, 200)) for i in range(5)]
    frames[0].save(d / "anim.gif", save_all=True, append_images=frames[1:], duration=120, loop=0)
    Image.new("RGB", (200, 100), (30, 120, 60)).save(d / "photo.jpg")
    ff = find_ffmpeg()
    if ff:
        q = [ff, "-loglevel", "error", "-y"]
        subprocess.run(q + ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-f", "lavfi", "-i",
                            "sine=f=440", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest", str(d / "clip.mov")], check=True)
        subprocess.run(q + ["-f", "lavfi", "-i", "sine=f=330", "-t", "2", str(d / "song.wav")], check=True)
    (d / "notes.txt").write_text("Hello FileConvert\nLine two\n", encoding="utf-8")
    (d / "list.csv").write_text("name,qty\napple,3\npear,5\n", encoding="utf-8")


def verify(out, engine):
    if out.stat().st_size == 0:
        raise AssertionError("empty output")
    if engine == "ffmpeg" or out.suffix in (".mp4", ".webm", ".mp3", ".wav"):
        r = subprocess.run([find_ffmpeg(), "-v", "error", "-i", str(out), "-f", "null", "-"],
                           capture_output=True)
        if r.returncode:
            raise AssertionError("ffmpeg can't read the result")
    elif engine == "pillow" and out.suffix != ".pdf":
        from PIL import Image
        with Image.open(out) as im:
            im.load()


def main():
    engines = available_engines()
    print("Engines found:", ", ".join(sorted(engines)) or "none")
    d = Path(tempfile.mkdtemp(prefix="fileconvert-test-"))
    ok = bad = 0
    try:
        make_samples(d)
        for src in sorted(d.iterdir()):
            for t in targets_for(src.suffix, engines):
                t0 = time.time()
                try:
                    out = convert_one(src, t.ext, engines)
                    verify(out, t.engine)
                    print(f"  ok    {src.name:10} -> {t.label:18} {time.time() - t0:5.1f}s")
                    ok += 1
                except Exception as e:
                    print(f"  FAIL  {src.name:10} -> {t.label:18} {e}")
                    bad += 1
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{ok} conversions ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
