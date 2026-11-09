"""Optional local OCR, isolated from both the UI and the PDF reader's PDFium process."""

import csv
import hashlib
import importlib.metadata
import json
import math
import multiprocessing
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

VERSION = 1
MAX_PIXELS = 12_000_000
MAX_PAGES = 2
ATTEMPT_SECONDS = 20
TOTAL_SECONDS = 60
ATTEMPTS = ((200, 3), (300, 11))
MAX_WORDS = 4000
MAX_TSV_BYTES = 8 * 1024 * 1024
_LOCK = threading.Lock()


def _result(status, message, pages=None, **extra):
    return {"status": status, "message": message, "pages": pages or [], "cache_hit": False, **extra}


def _environment():
    env = dict(os.environ)
    env.update(OMP_THREAD_LIMIT="1", OMP_NUM_THREADS="1")
    if getattr(sys, "frozen", False):
        for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH"):
            original = env.get(name + "_ORIG")
            if original is None:
                env.pop(name, None)
            else:
                env[name] = original
    return env


@contextmanager
def _external_dlls():
    # This context only runs in the isolated worker, never in the reader process.
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    import ctypes
    kernel = ctypes.windll.kernel32
    size = kernel.GetDllDirectoryW(0, None)
    previous = ctypes.create_unicode_buffer(size + 1)
    kernel.GetDllDirectoryW(size + 1, previous)
    kernel.SetDllDirectoryW(None)
    try:
        yield
    finally:
        kernel.SetDllDirectoryW(previous.value or None)


def _engine():
    found = shutil.which("tesseract")
    if found:
        return str(Path(found).resolve())
    candidates = [Path(prefix) / "tesseract" for prefix in ("/usr/bin", "/usr/local/bin", "/opt/homebrew/bin")]
    if os.name == "nt":
        candidates = [Path(os.environ.get(name, "")) / "Tesseract-OCR" / "tesseract.exe"
                      for name in ("ProgramFiles", "ProgramFiles(x86)")]
    return next((str(path) for path in candidates if path.is_file()), None)


def _probe(language):
    engine = _engine()
    if not engine:
        return {"available": False, "message": "Offline OCR is unavailable: Tesseract 5 is not installed.", "languages": []}
    try:
        with _external_dlls():
            version = subprocess.run([engine, "--version"], capture_output=True, timeout=3,
                                     env=_environment(), check=True).stdout.decode("utf-8", errors="replace")
            listed = subprocess.run([engine, "--list-langs"], capture_output=True, timeout=3,
                                    env=_environment(), check=True).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as error:
        return {"available": False, "message": f"Offline OCR could not start Tesseract: {error}", "languages": []}
    match = re.search(r"tesseract\s+(\d+\.\d+(?:\.\d+)?)", version, re.I)
    if not match or int(match[1].split(".")[0]) != 5:
        return {"available": False, "message": "Offline OCR requires Tesseract 5.", "languages": []}
    languages = [line.strip() for line in listed.splitlines() if re.fullmatch(r"[A-Za-z][\w/-]*", line.strip())]
    missing = [name for name in language.split("+") if name not in languages]
    if missing:
        return {"available": False, "message": "Offline OCR language is not installed: " + ", ".join(missing),
                "engine_version": match[1], "languages": languages}
    directory = re.search(r'in "([^"]+)"', listed)
    models = {}
    if directory:
        for name in language.split("+"):
            path = Path(directory[1]) / (name + ".traineddata")
            if not path.is_file():
                break
            with path.open("rb") as source:
                models[name] = hashlib.file_digest(source, "sha256").hexdigest()
    return {"available": True, "message": "Offline OCR is ready.", "engine": engine,
            "engine_version": match[1], "languages": languages, "models": models,
            "cacheable": len(models) == len(language.split("+"))}


def _terminate(process):
    if process.pid is None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.is_alive():
        process.kill()
    process.join(timeout=3)


def _launch(task, arguments, cancel, deadline, progress=None):
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(task, arguments, send))
    try:
        process.start()
        send.close()
        while True:
            if cancel.is_set():
                return _result("cancelled", "Offline OCR cancelled.")
            if time.monotonic() >= deadline:
                return _result("timeout", "Offline OCR reached its time limit.")
            if receive.poll(.05):
                try:
                    message = receive.recv()
                except EOFError:
                    return _result("failed", "The offline OCR worker stopped unexpectedly.")
                if message["kind"] == "result":
                    process.join(timeout=.2)
                    return message["value"]
                if progress:
                    progress(message["value"])
            elif not process.is_alive():
                return _result("failed", "The offline OCR worker stopped unexpectedly.")
    finally:
        send.close()
        receive.close()
        if process.pid is not None:
            _terminate(process)
            process.close()


def capabilities(language="eng"):
    """Check installed local tools without loading PDFium or changing the app's DLL search path."""
    if not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3})*", language):
        return {"available": False, "message": "Choose installed OCR language codes such as eng or eng+ita.", "languages": []}
    result = _launch("probe", {"language": language}, threading.Event(), time.monotonic() + 8)
    if "available" not in result:
        return {"available": False, "message": result["message"], "languages": []}
    return result


def _config(language):
    try:
        renderer = importlib.metadata.version("pypdfium2")
    except importlib.metadata.PackageNotFoundError:
        renderer = "unavailable"
    return {"version": VERSION, "language": language, "renderer": renderer, "attempts": ATTEMPTS,
            "max_pixels": MAX_PIXELS, "max_pages": MAX_PAGES, "attempt_seconds": ATTEMPT_SECONDS,
            "total_seconds": TOTAL_SECONDS}


def _read_cache(path, key):
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("cache_key") == key and report.get("status") in ("completed", "empty") and isinstance(report.get("pages"), list):
            return {**report, "cache_hit": True}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def _write_cache(path, report):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            json.dump(report, output, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def recover(path, cache_dir, sha256, cancel, *, language="eng", force=False, progress=None):
    """Read at most two identifying pages; source PDF bytes are never rewritten.

    The caller supplies the immutable original and its library fingerprint. Cache
    identity also includes the OCR engine, installed model hashes and render config.
    """
    if cancel.is_set():
        return _result("cancelled", "Offline OCR cancelled.")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        return _result("failed", "Offline OCR needs the PDF's SHA-256 fingerprint.")
    if not _LOCK.acquire(blocking=False):
        return _result("busy", "Another offline OCR task is already running.")
    deadline = time.monotonic() + TOTAL_SECONDS
    try:
        if not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3})*", language):
            return _result("unavailable", "Choose installed OCR language codes such as eng or eng+ita.")
        info = _launch("probe", {"language": language}, cancel, min(deadline, time.monotonic() + 8))
        if not info.get("available"):
            return info if info.get("status") in ("cancelled", "timeout") else _result("unavailable", info["message"])
        config = _config(language)
        if config["renderer"] == "unavailable":
            return _result("unavailable", "Offline OCR is unavailable: PDFium is not installed.")
        identity = {"sha256": sha256.lower(), "engine_version": info["engine_version"], "models": info["models"], "config": config}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cache = Path(cache_dir)
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = cache / (key + ".json")
        if not force and info["cacheable"] and (cached := _read_cache(destination, key)):
            return cached
        with tempfile.TemporaryDirectory(prefix="ocr-", dir=cache) as staging:
            arguments = {"path": str(Path(path).resolve()), "temporary": staging, "engine": info["engine"],
                         "language": language, "deadline": deadline}
            report = _launch("ocr", arguments, cancel, deadline, progress)
        report.update(cache_key=key, engine_version=info["engine_version"], config=config, sha256=sha256.lower())
        if info["cacheable"] and report["status"] in ("completed", "empty") and not cancel.is_set():
            _write_cache(destination, report)
        return report
    except (OSError, ValueError, RuntimeError) as error:
        return _result("failed", f"Offline OCR failed: {error}")
    finally:
        _LOCK.release()


def _scale(width, height, dpi):
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise ValueError("Invalid PDF page size")
    scale = min(dpi / 72, math.sqrt(MAX_PIXELS / (width * height)))
    while math.ceil(width * scale) * math.ceil(height * scale) > MAX_PIXELS:
        scale *= .999
    return scale


def _render(page, path, dpi):
    width, height = page.get_size()
    scale = _scale(width, height, dpi)
    bitmap = page.render(scale=scale, grayscale=True, draw_annots=False, limit_image_cache=True)
    try:
        with path.open("wb") as output:
            output.write(f"P5\n{bitmap.width} {bitmap.height}\n255\n".encode("ascii"))
            pixels = memoryview(bitmap.buffer).cast("B")
            for row in range(bitmap.height):
                output.write(pixels[row * bitmap.stride:row * bitmap.stride + bitmap.width])
        return [width, height], [bitmap.width, bitmap.height], scale * 72
    finally:
        bitmap.close()


def _parse_tsv(path, page_number, size, pixels):
    if path.stat().st_size > MAX_TSV_BYTES:
        raise ValueError("OCR output exceeded its size limit")
    words, groups = [], {}
    with path.open(encoding="utf-8", errors="replace", newline="") as source:
        for row in csv.DictReader(source, delimiter="\t"):
            text = row.get("text", "").strip()
            if row.get("level") != "5" or not text:
                continue
            try:
                confidence = float(row["conf"])
                x, y, width, height = (float(row[key]) for key in ("left", "top", "width", "height"))
            except (KeyError, ValueError, TypeError):
                continue
            if not all(math.isfinite(value) for value in (confidence, x, y, width, height)) or confidence < 0 or width <= 0 or height <= 0:
                continue
            box = [max(0, min(size[0], x / pixels[0] * size[0])), max(0, min(size[1], y / pixels[1] * size[1])),
                   max(0, min(size[0], (x + width) / pixels[0] * size[0])), max(0, min(size[1], (y + height) / pixels[1] * size[1]))]
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            key = tuple(row.get(name, "") for name in ("block_num", "par_num", "line_num"))
            groups.setdefault(key, []).append(len(words))
            words.append({"text": text[:500], "bbox": [round(value, 3) for value in box], "confidence": min(100, confidence)})
            if len(words) == MAX_WORDS:
                break
    lines = []
    for indexes in groups.values():
        members = [words[index] for index in indexes]
        box = [min(word["bbox"][0] for word in members), min(word["bbox"][1] for word in members),
               max(word["bbox"][2] for word in members), max(word["bbox"][3] for word in members)]
        lines.append({"text": " ".join(word["text"] for word in members), "bbox": box,
                      "confidence": statistics.mean(word["confidence"] for word in members), "words": indexes})
    return {"page": page_number, "size": size, "pixels": pixels, "words": words, "lines": lines,
            "text": "\n".join(line["text"] for line in lines), "ocr_rotation": 0}


def _quality(page):
    words = page["words"]
    return (sum(len(word["text"]) for word in words if word["confidence"] >= 50),
            statistics.mean(word["confidence"] for word in words) if words else 0)


def _cover_only(page):
    """A first page without a recognisable title may be a cover or stamp sheet; read the next page too."""
    from .metadata import extract_ocr_metadata
    return not extract_ocr_metadata({"pages": [page]}).get("title")


def _recognize(arguments, page, number, send):
    attempts, timed_out = [], False
    for attempt, (dpi, segmentation) in enumerate(ATTEMPTS, 1):
        remaining = arguments["deadline"] - time.monotonic()
        if remaining <= 0:
            break
        send.send({"kind": "progress", "value": {"stage": "rendering", "page": number + 1, "attempt": attempt, "dpi": dpi}})
        image = Path(arguments["temporary"]) / f"page-{number}-{attempt}.pgm"
        size, pixels, actual_dpi = _render(page, image, dpi)
        output = image.with_suffix("")
        send.send({"kind": "progress", "value": {"stage": "recognizing", "page": number + 1, "attempt": attempt, "dpi": round(actual_dpi)}})
        command = [arguments["engine"], str(image), str(output), "-l", arguments["language"],
                   "--oem", "1", "--psm", str(segmentation), "--dpi", str(max(1, round(actual_dpi))), "tsv"]
        with _external_dlls():
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, env=_environment())
        try:
            _, errors = process.communicate(timeout=min(ATTEMPT_SECONDS, max(.01, arguments["deadline"] - time.monotonic())))
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.communicate()
            continue
        if process.returncode:
            raise ValueError("Tesseract could not read this page: " + errors.decode("utf-8", errors="replace")[-1000:].strip())
        recognized = _parse_tsv(output.with_suffix(".tsv"), number, size, pixels)
        recognized.update(dpi=round(actual_dpi, 2), rotation=page.get_rotation(), attempt=attempt)
        attempts.append(recognized)
        if _quality(recognized)[0] >= 80 and _quality(recognized)[1] >= 65:
            break
    if not attempts:
        raise TimeoutError("Tesseract reached the OCR time limit" if timed_out else "Offline OCR reached its time limit")
    return max(attempts, key=_quality)


def _worker(task, arguments, send):
    # A session of its own lets the parent kill Tesseract together with this process.
    if os.name != "nt":
        os.setsid()
    _run(task, arguments, send)


def _run(task, arguments, send):
    try:
        if task == "probe":
            result = _probe(arguments["language"])
        else:
            import pypdfium2 as pdfium
            pages = []
            with pdfium.PdfDocument(arguments["path"]) as document:
                for number in range(min(MAX_PAGES, len(document))):
                    page = document[number]
                    try:
                        pages.append(_recognize(arguments, page, number, send))
                    finally:
                        page.close()
                    if number == 0 and not _cover_only(pages[0]):
                        break
            found = any(page["words"] for page in pages)
            result = _result("completed" if found else "empty", "Offline OCR finished." if found else "No readable text was found by offline OCR.", pages)
        send.send({"kind": "result", "value": result})
    except TimeoutError as error:
        send.send({"kind": "result", "value": _result("timeout", str(error))})
    except Exception as error:
        send.send({"kind": "result", "value": _result("failed", f"Offline OCR failed: {error}")})
    finally:
        send.close()
