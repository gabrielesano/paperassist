"""The native Sources dialog on a disposable library; every network call is mocked."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_native_support import needs_qt, pdf_available

if pdf_available:
    from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QWidget

    from paperassist.native.sources_dialog import SOURCES, SourcesDialog, show_sources
    from paperassist.workspace import Workspace

FAKE_KEY = "fake-key-for-tests-1234"


@needs_qt
class SourcesDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name).resolve()
        config = patch("paperassist.paths.config_file", return_value=self.dir / "config.json")
        config.start()
        self.addCleanup(config.stop)
        env = patch.dict("os.environ", {"PAPERASSIST_EMAIL": "", "OPENALEX_API_KEY": ""})
        env.start()
        self.addCleanup(env.stop)
        self.workspace = Workspace(self.dir / "library")
        self.addCleanup(self.workspace.close)
        self.dialog = self.make_dialog()

    def make_dialog(self):
        dialog = SourcesDialog(self.workspace)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def row(self, source_id, dialog=None):
        return next(r for r in (dialog or self.dialog).rows if r.source[0] == source_id)

    def stored(self):
        return json.loads((self.workspace.data_dir / "settings.json").read_text())

    def pump(self, until, seconds=10):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end and not until():
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(until(), "timed out")

    def test_every_source_has_a_row_with_a_switch_and_accessible_names(self):
        self.assertEqual([r.source[0] for r in self.dialog.rows], [s[0] for s in SOURCES])
        self.assertEqual(self.row("ads").key_field.accessibleName(), "NASA ADS token")
        self.assertEqual(self.row("core").key_field.accessibleName(), "CORE key")
        self.assertIsNone(self.row("arxiv").key_field)
        self.assertEqual(self.row("ads").status.text(), "Needs a token")
        self.assertEqual(self.row("unpaywall").status.text(), "Needs an email")
        self.assertEqual(self.row("openalex").status.text(), "Ready")

    def test_nothing_changed_saves_nothing(self):
        self.dialog.save()
        self.assertEqual(self.dialog.message.text(), "Nothing to save.")
        self.assertFalse((self.workspace.data_dir / "settings.json").exists())

    def test_saving_stores_only_the_changes_and_masks_the_key(self):
        self.dialog.email.setText("reader@example.org")
        self.row("ads").key_field.setText(FAKE_KEY)
        self.row("arxiv").toggle.setChecked(False)
        self.dialog.save()
        self.assertEqual(self.dialog.message.text(), "Saved.")
        self.assertEqual(self.stored(), {"email": "reader@example.org", "enabled": {"arxiv": False}, "keys": {"ads": FAKE_KEY}})
        ads = self.row("ads")
        self.assertEqual(ads.key_field.text(), "")  # a key is never shown again
        self.assertEqual(ads.status.text(), "Ready · token set …1234")
        self.assertNotIn(FAKE_KEY, " ".join(w.text() for w in self.dialog.findChildren(type(ads.status))))
        self.assertEqual(self.row("arxiv").status.text(), "Turned off")
        self.assertEqual(self.row("unpaywall").status.text(), "Ready")

    def test_the_key_field_hides_what_is_typed(self):
        self.assertEqual(self.row("core").key_field.echoMode(), QLineEdit.Password)

    def test_remove_is_staged_until_save_and_keep_undoes_it(self):
        self.workspace.update_settings({"keys": {"core": FAKE_KEY, "ads": FAKE_KEY + "x"}, "proxy": "https://proxy.example.org/login?url="})
        dialog = self.make_dialog()
        core = self.row("core", dialog)
        core.remove.click()
        self.assertEqual((core.remove.text(), core.key_field.isEnabled()), ("Keep", False))
        self.assertIn("removed when you save", core.status.text())
        self.assertIn("core", self.stored()["keys"])
        core.remove.click()
        self.assertEqual(core.remove.text(), "Remove")
        self.assertEqual(dialog.changes(), {})
        core.remove.click()
        dialog.save()
        self.assertEqual(list(self.stored()["keys"]), ["ads"])
        self.assertEqual(self.stored()["proxy"], "https://proxy.example.org/login?url=")  # omitted values stay

    def test_an_emptied_field_clears_the_setting(self):
        self.workspace.update_settings({"email": "reader@example.org", "proxy": "https://proxy.example.org/?url={url}"})
        dialog = self.make_dialog()
        self.assertEqual(dialog.email.text(), "reader@example.org")
        dialog.email.setText("  ")
        dialog.save()
        self.assertNotIn("email", self.stored())
        self.assertIn("proxy", self.stored())

    def test_a_rejected_value_is_explained_and_kept(self):
        self.dialog.email.setText("not an address")
        self.dialog.proxy.setText("http://insecure.example.org")
        self.dialog.save()
        self.assertIn("Email", self.dialog.message.text())
        self.assertEqual(self.dialog.email.text(), "not an address")
        self.row("ads").key_field.setText("short")
        self.dialog.email.setText("reader@example.org")
        self.dialog.save()
        self.assertIn("https", self.dialog.message.text())
        self.assertFalse((self.workspace.data_dir / "settings.json").exists())

    def test_check_needs_saved_settings_then_reports_per_provider_without_blocking(self):
        self.workspace.update_settings({"keys": {"ads": FAKE_KEY}})
        dialog = self.make_dialog()
        row = self.row("ads", dialog)
        row.key_field.setText(FAKE_KEY + "2")
        with patch.object(self.workspace, "check_settings") as check:
            dialog.check()
            check.assert_not_called()
        self.assertIn("Save your changes first", dialog.message.text())
        row.key_field.clear()
        release = threading.Event()
        threads = []

        def slow_check():
            threads.append(threading.current_thread())
            release.wait(5)
            return {"ads": {"ok": False, "message": "Key rejected."}, "core": {"ok": True, "message": "Accepted."}}

        with patch.object(self.workspace, "check_settings", slow_check):
            dialog.check()
            self.assertFalse(dialog.check_button.isEnabled())
            self.app.processEvents()  # the UI thread stays responsive while the request is out
            self.assertEqual(dialog.message.text(), "Checking access…")
            release.set()
            self.pump(lambda: dialog.check_button.isEnabled())
        self.assertIsNot(threads[0], threading.main_thread())
        self.assertEqual(row.result.text(), "Problem: Key rejected.")
        self.assertEqual(self.row("core", dialog).result.text(), "Accepted: Accepted.")
        self.assertFalse(self.row("arxiv", dialog).result.isVisibleTo(dialog))

    def test_a_failing_check_reports_the_error(self):
        with patch.object(self.workspace, "check_settings", side_effect=OSError("offline")):
            self.dialog.check()
            self.pump(lambda: self.dialog.check_button.isEnabled())
        self.assertEqual(self.dialog.message.text(), "offline")

    def host(self, workspace):
        window = QWidget()
        window.workspace = workspace  # the only thing the dialog asks of a main window
        self.addCleanup(window.deleteLater)
        return window

    def test_loose_files_explain_that_sources_belong_to_a_library(self):
        box = show_sources(self.host(None))
        self.addCleanup(box.close)
        self.assertIsInstance(box, QMessageBox)
        self.assertTrue(box.isVisible())
        self.assertIn("library", box.text())

    def test_show_sources_opens_the_dialog_for_the_library(self):
        dialog = show_sources(self.host(self.workspace))
        self.addCleanup(dialog.close)
        self.assertIsInstance(dialog, SourcesDialog)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.email.text(), "")


if __name__ == "__main__":
    unittest.main()
