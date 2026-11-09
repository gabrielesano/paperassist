"""Offline OCR: bounded, cancellable and never touching the source PDF."""
import hashlib
import io
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject

from paperassist import ocr
from paperassist.metadata import extract_ocr_metadata

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None

needs_ocr = unittest.skipUnless(pdfium and shutil.which("tesseract") and ocr._probe("eng")["available"],
                                "Tesseract with English data and PDFium are required")

TITLE = ("Adiabatic Heating of", "Coronal Loops")
AUTHORS = "Ada Lovelace and Grace Hopper"
BODY = ("The loops were observed in 1998 and again in 2004 with the same instrument.",
        "Their temperature follows an exponential decay over many minutes of data.",
        "We compare these measurements with a simple model of radiative cooling.",
        "The model reproduces the observed cooling times within the stated errors.",
        "A second set of loops shows a slower decline caused by continued heating.",
        "These results suggest that heating persists long after the first brightening.",
        "doi:10.1234/loops.5")


def scan_pdf(path, rotate=0, title=TITLE, authors=AUTHORS):
    """A page holding only a bitmap of a title page, like a scanned paper without a text layer."""
    source = PdfWriter()
    fonts = DictionaryObject({NameObject(f"/F{number}"): source._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject(name)})) for number, name in ((1, "/Helvetica"), (2, "/Helvetica-Bold"))})
    page = source.add_blank_page(612, 792)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
    content = "".join(f"BT /F2 24 Tf 60 {700 - 30 * number} Td ({line}) Tj ET\n" for number, line in enumerate(title))
    content += f"BT /F1 13 Tf 60 {650 - 30 * (len(title) - 1)} Td ({authors}) Tj ET\n"
    content += "BT /F1 11 Tf 60 560 Td (Department of Physics) Tj ET\n"
    content += "BT /F2 11 Tf 60 520 Td (Abstract) Tj ET\n"
    content += "".join(f"BT /F1 10 Tf 60 {500 - 15 * number} Td ({line}) Tj ET\n" for number, line in enumerate(BODY))
    stream = DecodedStreamObject()
    stream.set_data(content.encode("ascii"))
    page[NameObject("/Contents")] = source._add_object(stream)
    buffer = io.BytesIO()
    source.write(buffer)
    rendered = pdfium.PdfDocument(buffer.getvalue())
    bitmap = rendered[0].render(scale=200 / 72, grayscale=True, rotation=rotate)
    pixels = memoryview(bitmap.buffer).cast("B")
    raw = b"".join(pixels[row * bitmap.stride:row * bitmap.stride + bitmap.width] for row in range(bitmap.height))
    width, height = bitmap.width * .36, bitmap.height * .36
    image = DecodedStreamObject()
    image.set_data(raw)
    image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(bitmap.width), NameObject("/Height"): NumberObject(bitmap.height),
                  NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8)})
    target = PdfWriter()
    scanned = target.add_blank_page(width, height)
    scanned[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({
        NameObject("/Im0"): target._add_object(image.flate_encode())})})
    drawing = DecodedStreamObject()
    drawing.set_data(f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode("ascii"))
    scanned[NameObject("/Contents")] = target._add_object(drawing)
    if rotate:
        scanned.rotate((360 - rotate) % 360)
    with open(path, "wb") as output:
        target.write(output)
    return Path(path)


class Sink:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)

    def close(self):
        pass


def descendants_using(folder):
    """Processes whose command line mentions this test's private folder."""
    found = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            if str(folder) in (entry / "cmdline").read_bytes().decode("utf-8", errors="replace"):
                found.append(int(entry.name))
        except OSError:
            pass
    return found


def sleeper(marker):
    """Stands in for the OCR worker: a session leader with a long-running external child."""
    os.setsid()
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", marker])
    time.sleep(30)


@unittest.skipUnless(os.name == "posix" and Path("/proc").is_dir(), "needs /proc process listing")
class ProcessGroupTests(unittest.TestCase):
    def test_terminating_the_worker_also_stops_its_external_child(self):
        with tempfile.TemporaryDirectory() as marker:
            process = multiprocessing.get_context("spawn").Process(target=sleeper, args=(marker,))
            process.start()
            end = time.monotonic() + 10
            while not descendants_using(marker) and time.monotonic() < end:
                time.sleep(.05)
            self.assertTrue(descendants_using(marker))
            ocr._terminate(process)
            end = time.monotonic() + 2
            while descendants_using(marker) and time.monotonic() < end:
                time.sleep(.05)
            self.assertEqual(descendants_using(marker), [])


@needs_ocr
class OcrRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.scan = scan_pdf(Path(cls.shared.name) / "scan.pdf")
        cls.sha = hashlib.sha256(cls.scan.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name) / "cache"
        self.cancel = threading.Event()

    def recover(self, path=None, **options):
        return ocr.recover(path or self.scan, self.cache, self.sha, self.cancel, **options)

    def test_scan_is_read_without_touching_the_source_and_leaves_no_temporary_files(self):
        before = (self.scan.read_bytes(), self.scan.stat().st_mtime_ns)
        report = self.recover()
        self.assertEqual(report["status"], "completed", report["message"])
        self.assertEqual((self.scan.read_bytes(), self.scan.stat().st_mtime_ns), before)
        self.assertEqual([entry.suffix for entry in self.cache.iterdir()], [".json"])
        page = report["pages"][0]
        self.assertIn("Lovelace", [word["text"] for word in page["words"]])
        self.assertTrue(all(0 <= x0 < x1 <= page["size"][0] and 0 <= y0 < y1 <= page["size"][1]
                            for x0, y0, x1, y1 in (word["bbox"] for word in page["words"])))

    def test_generated_scan_gives_provisional_title_and_authors_with_candidates_only(self):
        result = extract_ocr_metadata(self.recover())
        self.assertEqual(result["title"], "Adiabatic Heating of Coronal Loops")
        self.assertEqual(result["authors"], "Ada Lovelace; Grace Hopper")
        self.assertIsNone(result["year"])
        self.assertEqual({1998, 2004} & set(result["candidates"]["year"]), {1998, 2004})
        self.assertEqual(result["sources"], {"title": "ocr_title", "authors": "ocr_authors"})

    def test_rotated_page_is_rendered_upright_before_recognition(self):
        rotated = scan_pdf(Path(self.shared.name) / "rotated.pdf", rotate=90)
        report = self.recover(rotated)
        self.assertEqual(extract_ocr_metadata(report)["title"], "Adiabatic Heating of Coronal Loops")

    def test_paths_with_spaces_and_unicode_are_supported(self):
        folder = Path(self.shared.name) / "Cartella è ü 文书"
        folder.mkdir(exist_ok=True)
        path = scan_pdf(folder / "scan file è.pdf")
        self.cache = folder / "cache dir"
        report = ocr.recover(path, self.cache, hashlib.sha256(path.read_bytes()).hexdigest(), self.cancel)
        self.assertEqual(report["status"], "completed", report["message"])

    def test_cache_is_reused_forced_and_invalidated_by_configuration(self):
        first = self.recover()
        with patch.object(ocr, "_launch", wraps=ocr._launch) as launch:
            second = self.recover()
            self.assertEqual([call.args[0] for call in launch.call_args_list], ["probe"])
            self.assertTrue(second["cache_hit"] and not first["cache_hit"])
            self.assertEqual(second["pages"], first["pages"])
            self.assertFalse(self.recover(force=True)["cache_hit"])
        with patch.object(ocr, "MAX_PIXELS", 5_000_000):
            self.assertFalse(self.recover()["cache_hit"])

    def test_missing_executable_and_language_are_unavailable_then_recoverable(self):
        with patch.object(ocr, "_engine", return_value=None):
            self.assertFalse(ocr._probe("eng")["available"])
            self.assertIn("not installed", ocr._probe("eng")["message"])
        gone = {"available": False, "message": "Offline OCR is unavailable: Tesseract is not installed."}
        with patch.object(ocr, "_launch", return_value=gone):
            unavailable = self.recover()
        self.assertEqual((unavailable["status"], unavailable["message"]), ("unavailable", gone["message"]))
        self.assertFalse(self.cache.exists() and list(self.cache.glob("*.json")))
        language = self.recover(language="zzz")
        self.assertEqual(language["status"], "unavailable")
        self.assertIn("zzz", language["message"])
        self.assertEqual(self.recover()["status"], "completed")

    def test_time_limit_is_reported_and_not_cached(self):
        with patch.object(ocr, "TOTAL_SECONDS", .05):
            report = self.recover()
        self.assertEqual(report["status"], "timeout")
        self.assertEqual(list(self.cache.glob("*")) if self.cache.exists() else [], [])
        self.assertEqual(self.recover()["status"], "completed")

    def test_attempt_timeout_kills_tesseract_and_removes_images(self):
        info = ocr._probe("eng")
        with tempfile.TemporaryDirectory() as staging:
            sink = Sink()
            arguments = {"path": str(self.scan), "temporary": staging, "engine": info["engine"],
                         "language": "eng", "deadline": time.monotonic() + 60}
            with patch.object(ocr, "ATTEMPT_SECONDS", .01):
                ocr._run("ocr", arguments, sink)
            self.assertEqual(sink.messages[-1]["value"]["status"], "timeout")
            self.assertEqual(descendants_using(staging), [])

    def test_cancel_before_start_does_nothing(self):
        self.cancel.set()
        with patch.object(ocr, "_launch") as launch:
            self.assertEqual(self.recover()["status"], "cancelled")
        launch.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_cancel_during_recognition_stops_tesseract_and_removes_temporary_files(self):
        stages = []

        def progress(step):
            stages.append(step["stage"])
            if step["stage"] == "recognizing":
                self.cancel.set()
        started = time.monotonic()
        report = self.recover(progress=progress)
        self.assertEqual(report["status"], "cancelled")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("recognizing", stages)
        end = time.monotonic() + .3  # a killed process needs a moment to disappear; a live Tesseract needs about a second
        while descendants_using(self.cache) and time.monotonic() < end:
            time.sleep(.05)
        self.assertEqual(descendants_using(self.cache), [])
        self.assertEqual(list(self.cache.rglob("*.pgm")) + list(self.cache.rglob("*.tsv")) + list(self.cache.glob("*.json")), [])
        self.assertEqual(list(self.cache.glob("ocr-*")), [])

    def test_failure_is_not_cached_and_a_later_run_succeeds(self):
        broken = Path(self.shared.name) / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4 not really a pdf")
        report = ocr.recover(broken, self.cache, hashlib.sha256(broken.read_bytes()).hexdigest(), self.cancel)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(list(self.cache.glob("*.json")), [])
        self.assertEqual(self.recover()["status"], "completed")

    def test_blank_page_is_reported_as_empty(self):
        blank = Path(self.shared.name) / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(300, 400)
        with blank.open("wb") as output:
            writer.write(output)
        report = ocr.recover(blank, self.cache, hashlib.sha256(blank.read_bytes()).hexdigest(), self.cancel)
        self.assertEqual(report["status"], "empty")
        self.assertEqual(extract_ocr_metadata(report), {})

    def test_malformed_sha256_and_concurrent_use_are_refused(self):
        self.assertEqual(ocr.recover(self.scan, self.cache, "abc", self.cancel)["status"], "failed")
        with ocr._LOCK:
            self.assertEqual(self.recover()["status"], "busy")


class TsvTests(unittest.TestCase):
    HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"

    def parse(self, rows):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "page.tsv"
            path.write_text(self.HEAD + rows, encoding="utf-8")
            return ocr._parse_tsv(path, 0, [100, 100], [200, 200])

    def test_malformed_rows_are_skipped_and_boxes_are_converted_to_points(self):
        page = self.parse("5\t1\t1\t1\t1\t1\t20\t40\t100\t20\t96.5\tHello\n"
                          "5\t1\t1\t1\t1\t2\tx\t40\t100\t20\t96\tBad\n"
                          "5\t1\t1\t1\t1\t3\t20\t40\t-5\t20\t96\tNegative\n"
                          "5\t1\t1\t1\t1\t4\t20\t40\t10\t20\tnan\tNotANumber\n"
                          "5\t1\t1\t1\t1\t5\t20\t40\t10\t20\t-1\tNoConfidence\n"
                          "4\t1\t1\t1\t1\t0\t0\t0\t10\t10\t-1\t\n")
        self.assertEqual([word["text"] for word in page["words"]], ["Hello"])
        self.assertEqual(page["words"][0]["bbox"], [10, 20, 60, 30])
        self.assertEqual(page["lines"][0]["text"], "Hello")

    def test_oversized_output_is_rejected(self):
        with patch.object(ocr, "MAX_TSV_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "size limit"):
                self.parse("5\t1\t1\t1\t1\t1\t20\t40\t100\t20\t96.5\tHello\n")


class ProbeTests(unittest.TestCase):
    def fake_runner(self, version_out, langs_out):
        def fake_run(args, **kwargs):
            if "--version" in args:
                return subprocess.CompletedProcess(args, 0, stdout=version_out)
            if "--list-langs" in args:
                return subprocess.CompletedProcess(args, 0, stdout=langs_out)
            raise AssertionError(f"Unexpected arguments: {args}")
        return fake_run

    def test_version_4_1_is_accepted_and_cacheable_via_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / "eng.traineddata"
            model.write_bytes(b"dummy traineddata 4.1")
            runner = self.fake_runner(b"tesseract 4.1.1\n leptonica-1.82.0\n",
                                      b"List of available languages (1):\neng\n")
            with patch.object(ocr, "_engine", return_value="/usr/bin/tesseract"), \
                 patch.object(ocr.subprocess, "run", side_effect=runner), \
                 patch.dict(os.environ, {"TESSDATA_PREFIX": folder}):
                info = ocr._probe("eng")
            self.assertTrue(info["available"])
            self.assertEqual(info["engine_version"], "4.1.1")
            self.assertEqual(info["languages"], ["eng"])
            self.assertEqual(info["models"], {"eng": hashlib.sha256(b"dummy traineddata 4.1").hexdigest()})
            self.assertTrue(info["cacheable"])
            self.assertEqual(info["message"], "Offline OCR is ready.")

    def test_version_5_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / "eng.traineddata"
            model.write_bytes(b"dummy traineddata 5")
            runner = self.fake_runner(b"tesseract 5.3.0\n leptonica-1.82.0\n",
                                      f'List of available languages in "{folder}" (1):\neng\n'.encode())
            with patch.object(ocr, "_engine", return_value="/usr/bin/tesseract"), \
                 patch.object(ocr.subprocess, "run", side_effect=runner):
                info = ocr._probe("eng")
            self.assertTrue(info["available"])
            self.assertEqual(info["engine_version"], "5.3.0")
            self.assertEqual(info["models"], {"eng": hashlib.sha256(b"dummy traineddata 5").hexdigest()})
            self.assertTrue(info["cacheable"])
            self.assertEqual(info["message"], "Offline OCR is ready.")

    def test_version_6_is_accepted(self):
        runner = self.fake_runner(b"tesseract 6.0.0\n leptonica-1.85.0\n",
                                  b"List of available languages (1):\neng\n")
        with patch.object(ocr, "_engine", return_value="/usr/bin/tesseract"), \
             patch.object(ocr.subprocess, "run", side_effect=runner):
            info = ocr._probe("eng")
        self.assertTrue(info["available"])
        self.assertEqual(info["engine_version"], "6.0.0")
        self.assertEqual(info["message"], "Offline OCR is ready.")

    def test_older_versions_are_rejected(self):
        for raw_version in (b"tesseract 4.0.0\n", b"tesseract 4.0\n", b"tesseract 3.05.02\n", b"tesseract 3.05\n"):
            with self.subTest(version=raw_version):
                runner = self.fake_runner(raw_version, b"List of available languages (1):\neng\n")
                with patch.object(ocr, "_engine", return_value="/usr/bin/tesseract"), \
                     patch.object(ocr.subprocess, "run", side_effect=runner):
                    info = ocr._probe("eng")
                self.assertFalse(info["available"])
                self.assertEqual(info["message"], "Offline OCR requires Tesseract 4.1 or newer.")

    def test_engine_dir_tessdata_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            tessdata = Path(folder) / "tessdata"
            tessdata.mkdir()
            (tessdata / "eng.traineddata").write_bytes(b"engine relative")
            runner = self.fake_runner(b"tesseract 4.1.1\n", b"List of available languages (1):\neng\n")
            with patch.object(ocr, "_engine", return_value=str(Path(folder) / "tesseract")), \
                 patch.object(ocr.subprocess, "run", side_effect=runner), \
                 patch.dict(os.environ, {}, clear=True):
                info = ocr._probe("eng")
            self.assertTrue(info["available"])
            self.assertEqual(info["models"], {"eng": hashlib.sha256(b"engine relative").hexdigest()})
            self.assertTrue(info["cacheable"])

    def test_windows_localappdata_engine_discovery(self):
        fake_local = "/fake/users/me/AppData/Local"
        target = Path(fake_local) / "Programs" / "Tesseract-OCR" / "tesseract.exe"

        class FakeOs:
            name = "nt"
            environ = {"LOCALAPPDATA": fake_local, "ProgramFiles": "", "ProgramFiles(x86)": ""}

        with patch.object(ocr.shutil, "which", return_value=None), \
             patch.object(ocr, "os", FakeOs), \
             patch.object(Path, "is_file", autospec=True, side_effect=lambda p: p == target):
            self.assertEqual(ocr._engine(), str(target))


if __name__ == "__main__":
    unittest.main()
