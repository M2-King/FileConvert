"""The actual converters: Pillow for images, ffmpeg for audio/video, LibreOffice for documents."""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .system import (ConversionError, data_dir, file_lock, find_ffmpeg, find_soffice,
                     friendly_error, get_logger, run_quiet, settings)


# ----------------------------------------------------------------------------- images (Pillow)

def _flatten(im, background=(255, 255, 255)):
    """Drop transparency onto a white background (for formats without alpha)."""
    from PIL import Image
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, background)
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im.convert("RGB") if im.mode != "RGB" else im


def _prepare(im, target):
    from PIL import Image
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):          # 16-bit greyscale -> 8-bit
        im = im.point(lambda v: v / 256).convert("L")
    if target in ("jpg", "pdf", "bmp"):
        return _flatten(im)
    if target == "ico":
        im = im.convert("RGBA")
        side = max(im.size)
        if im.size[0] != im.size[1]:                         # icons must be square
            sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            sq.paste(im, ((side - im.size[0]) // 2, (side - im.size[1]) // 2))
            im = sq
        return im
    if im.mode == "CMYK" or im.mode == "YCbCr":
        return im.convert("RGB")
    if target == "webp" and im.mode not in ("RGB", "RGBA"):
        return im.convert("RGBA" if "transparency" in im.info or "A" in im.mode else "RGB")
    return im


def convert_image(src, dst, target, progress=None):
    from PIL import Image, ImageOps, ImageSequence
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    s = settings()
    try:
        im = Image.open(src)
    except Exception as e:
        raise ConversionError(f"Can't open this image: {e}")

    with im:
        icc = im.info.get("icc_profile")
        n_frames = getattr(im, "n_frames", 1)
        fmt = {"jpg": "JPEG", "tiff": "TIFF", "ico": "ICO", "pdf": "PDF", "png": "PNG",
               "webp": "WEBP", "gif": "GIF", "bmp": "BMP"}[target]
        opts = {}
        if target == "jpg":
            opts = {"quality": int(s["jpg_quality"]), "optimize": True}
        elif target == "webp":
            opts = {"quality": int(s["webp_quality"]), "method": 4}
        elif target == "tiff":
            opts = {"compression": "tiff_lzw"}
        elif target == "pdf":
            dpi = im.info.get("dpi", (150, 150))[0] or 150
            opts = {"resolution": float(dpi)}
        elif target == "ico":
            side = max(im.size)
            opts = {"sizes": [(n, n) for n in (16, 24, 32, 48, 64, 128, 256) if n <= side] or [(side, side)]}
        if icc and target in ("jpg", "png", "webp", "tiff"):
            opts["icc_profile"] = icc

        keeps_frames = target in ("gif", "webp", "png", "pdf", "tiff")
        if n_frames > 1 and keeps_frames:
            frames = [_prepare(f.copy(), target) for f in ImageSequence.Iterator(im)]
            if target == "png":                               # animated PNG wants full-colour frames
                frames = [f.convert("RGBA") for f in frames]
            if target in ("gif", "webp", "png"):
                opts.update(loop=im.info.get("loop", 0), duration=im.info.get("duration", 100))
            frames[0].save(dst, fmt, save_all=True, append_images=frames[1:], **opts)
        else:
            frame = ImageOps.exif_transpose(im)     # respect phone camera rotation
            frame = _prepare(frame, target)
            frame.save(dst, fmt, **opts)


# ----------------------------------------------------------------------------- audio/video (ffmpeg)

AUDIO_ARGS = {
    "mp3":  ["-c:a", "libmp3lame", "-q:a", "2"],
    "wav":  ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "m4a":  ["-c:a", "aac", "-b:a", "256k"],
    "ogg":  ["-c:a", "libvorbis", "-q:a", "6"],
    "opus": ["-c:a", "libopus", "-b:a", "160k"],
    "aiff": ["-c:a", "pcm_s16be"],
}

EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"     # H.264 needs even width/height
GIF_FILTER = ("fps=12,scale='min(720,iw)':-1:flags=lanczos,"
              "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")

ENCODE_ARGS = {
    "mp4":  ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-vf", EVEN, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    "mov":  ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-vf", EVEN, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    "mkv":  ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-vf", EVEN, "-c:a", "aac", "-b:a", "192k"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
             "-deadline", "good", "-cpu-used", "4", "-c:a", "libopus", "-b:a", "128k"],
    "avi":  ["-c:v", "mpeg4", "-q:v", "3", "-vtag", "xvid", "-c:a", "libmp3lame", "-q:a", "3"],
}


def _ffmpeg():
    ff = find_ffmpeg()
    if not ff:
        raise ConversionError("ffmpeg isn't installed (needed for audio/video). See README.")
    return ff


def _probe(ff, src):
    """Return (video_codec, audio_codec, duration_seconds) from ffmpeg's stream listing."""
    # ffmpeg exits with an error when given no output, but prints the stream list to stderr first.
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        proc = subprocess.run([ff, "-hide_banner", "-nostdin", "-i", str(src)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=60, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None, None, None
    text = proc.stderr.decode("utf-8", "replace")
    video = audio = duration = None
    d = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if d:
        duration = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    for line in text.splitlines():
        m = re.search(r"Stream #\S+.*?: (Video|Audio): (\w+)", line)
        if not m:
            continue
        if m.group(1) == "Video" and video is None and "attached pic" not in line:
            video = m.group(2)
        elif m.group(1) == "Audio" and audio is None:
            audio = m.group(2)
    return video, audio, duration


def _run_ffmpeg(cmd, duration, progress):
    """Run ffmpeg and report 0..1 progress from its -progress output."""
    if not (progress and duration):
        run_quiet(cmd)
        return
    cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=err, **kwargs)
        except OSError as e:
            raise ConversionError(f"Could not start ffmpeg: {e}")
        for raw in proc.stdout:
            key, _, value = raw.decode("ascii", "replace").strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and value.isdigit():   # both are microseconds
                progress(min(1.0, int(value) / 1e6 / duration))
        proc.wait()
        if proc.returncode != 0:
            err.seek(0)
            raise ConversionError(friendly_error(err.read().decode("utf-8", "replace"), "ffmpeg"))
    progress(1.0)


def convert_media(src, dst, target, progress=None):
    ff = _ffmpeg()
    base = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src)]
    video, audio, duration = _probe(ff, src)

    if target in AUDIO_ARGS:
        _run_ffmpeg(base + ["-vn", "-sn", "-map_metadata", "0"] + AUDIO_ARGS[target] + [str(dst)],
                    duration, progress)
        return
    if target == "gif":
        _run_ffmpeg(base + ["-an", "-sn", "-vf", GIF_FILTER, "-loop", "0", str(dst)], duration, progress)
        return

    # Fast path: change the container without re-encoding when the streams already fit.
    fast = None
    if target == "mkv" and video:
        fast = ["-map", "0:v:0", "-map", "0:a?", "-c", "copy"]
    elif target in ("mp4", "mov") and video in ("h264", "hevc"):
        fast = ["-map", "0:v:0", "-map", "0:a?", "-c:v", "copy"]
        if video == "hevc":
            fast += ["-tag:v", "hvc1"]                    # so QuickTime/iPhone can play it
        fast += ["-c:a", "copy"] if audio in (None, "aac", "mp3", "alac") else ["-c:a", "aac", "-b:a", "192k"]
        fast += ["-movflags", "+faststart"]
    if fast:
        try:
            _run_ffmpeg(base + fast + [str(dst)], duration, progress)
            if Path(dst).stat().st_size > 0:
                return
        except ConversionError:
            pass                                          # fall through to a full re-encode
        if progress:
            progress(0.0)

    if target not in ENCODE_ARGS:
        raise ConversionError(f"Don't know how to make {target.upper()}")
    _run_ffmpeg(base + ["-map", "0:v:0", "-map", "0:a?", "-sn"] + ENCODE_ARGS[target] + [str(dst)],
                duration, progress)


# ----------------------------------------------------------------------------- documents (LibreOffice)

SOFFICE_FILTERS = {
    "txt": "txt:Text (encoded):UTF8",
    "csv": "csv:Text - txt - csv (StarCalc):44,34,76",
}


def convert_document(src, dst, target, progress=None):
    so = find_soffice()
    if not so:
        raise ConversionError("LibreOffice isn't installed (needed for documents). See README.")
    profile = (data_dir() / "lo_profile").resolve()
    with tempfile.TemporaryDirectory() as tmp, file_lock("libreoffice"):
        cmd = [so, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--invisible",
               "--norestore", "--nolockcheck", "--nodefault",
               "--convert-to", SOFFICE_FILTERS.get(target, target), "--outdir", tmp, str(src)]
        run_quiet(cmd, timeout=600)
        produced = [p for p in Path(tmp).iterdir() if p.is_file()]
        if not produced:
            raise ConversionError("LibreOffice couldn't convert this file")
        out = Path(tmp) / f"{Path(src).stem}.{target}"
        out = out if out.exists() else produced[0]
        try:
            os.replace(out, dst)
        except OSError:
            shutil.copyfile(out, dst)


# ----------------------------------------------------------------------------- PDF

def convert_pdf(src, dst, target, progress=None):
    """PDF -> Word (DOCX), plain text, or one image per page."""
    import pypdfium2 as pdfium
    try:
        doc = pdfium.PdfDocument(str(src))
    except Exception as e:
        raise ConversionError(f"Can't open this PDF ({e}). It may be damaged or password-protected.")
    pages = len(doc)
    if pages == 0:
        raise ConversionError("This PDF has no pages")

    if target in ("png", "jpg"):
        dst = Path(dst)
        if pages == 1:
            outputs = [dst]
            folder = None
        else:                                   # one image per page, in a folder next to the PDF
            folder = dst.with_name(f"{dst.stem} ({target.upper()} pages)")
            i = 1
            while folder.exists():
                folder = dst.with_name(f"{dst.stem} ({target.upper()} pages {i})")
                i += 1
            folder.mkdir()
            width = len(str(pages))
            outputs = [folder / f"{dst.stem} page {n + 1:0{width}d}.{target}" for n in range(pages)]
        for n in range(pages):
            image = doc[n].render(scale=2).to_pil()          # 144 dpi: sharp on screen, fine to print
            if target == "jpg":
                image = image.convert("RGB")
                image.save(outputs[n], "JPEG", quality=int(settings()["jpg_quality"]), optimize=True)
            else:
                image.save(outputs[n], "PNG")
            if progress:
                progress((n + 1) / pages)
        doc.close()
        return folder                                        # a folder replaces the single file

    if target == "txt":
        parts = []
        for n in range(pages):
            textpage = doc[n].get_textpage()
            parts.append(textpage.get_text_range().replace("\r\n", "\n").strip())
            textpage.close()
            if progress:
                progress((n + 1) / pages)
        doc.close()
        text = "\n\n".join(parts).strip()
        if not text:
            raise ConversionError("This PDF has no text to copy (it may be scanned pictures)")
        Path(dst).write_text(text + "\n", encoding="utf-8")
        return None

    if target == "docx":
        doc.close()
        try:
            from pdf2docx import Converter                  # keeps layout, tables and pictures
        except ImportError:
            Converter = None
        if Converter is not None:
            try:
                cv = Converter(str(src))
                cv.convert(str(dst))
                cv.close()
                return None
            except Exception as e:
                get_logger().warning("pdf2docx failed (%s), using plain text Word file", e)
        _pdf_to_simple_docx(src, dst, progress)
        return None
    raise ConversionError(f"Can't make {target.upper()} from a PDF")


def _pdf_to_simple_docx(src, dst, progress=None):
    """Fallback: a Word file with the PDF's text, one page per page."""
    import pypdfium2 as pdfium
    from docx import Document
    word = Document()
    doc = pdfium.PdfDocument(str(src))
    for n in range(len(doc)):
        textpage = doc[n].get_textpage()
        text = textpage.get_text_range().replace("\r\n", "\n")
        textpage.close()
        for para in text.split("\n\n"):
            if para.strip():
                word.add_paragraph(para.strip())
        if n < len(doc) - 1:
            word.add_page_break()
        if progress:
            progress((n + 1) / len(doc))
    doc.close()
    word.save(str(dst))


ENGINES = {
    "pillow": convert_image,
    "ffmpeg": convert_media,
    "soffice": convert_document,
    "pdf": convert_pdf,
}
