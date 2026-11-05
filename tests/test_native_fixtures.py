"""Interchange samples saved by the pinned PDF.js through real web UI gestures."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from paperassist.library import Library

FIXTURES = Path(__file__).parent / 'fixtures'


class NativeFixtureTests(unittest.TestCase):
    def test_pdfjs_annotations_keep_geometry_colors_and_renderable_appearances(self):
        reader = PdfReader(FIXTURES / 'pdfjs-annotations.pdf')
        self.assertEqual(len(reader.pages), 3)
        self.assertIn('Native migration synthetic sample', reader.pages[0].extract_text())
        annotations = [ref.get_object() for ref in reader.pages[0]['/Annots']]
        highlights = [a for a in annotations if a['/Subtype'] == '/Highlight']
        self.assertEqual(len(highlights), 5)
        for annotation, color in zip(highlights, ('f5d76e', '8fd3aa', '79c5df', 'e9a9bc', '243f3a')):
            self.assertEqual([round(value * 255) for value in annotation['/C']], list(bytes.fromhex(color)))
            self.assertEqual(len(annotation['/QuadPoints']), 8)
            appearance = annotation['/AP']['/N'].get_object()
            self.assertEqual(appearance['/Resources']['/ExtGState']['/R0']['/BM'], '/Multiply')
            self.assertIn(b'f*', appearance.get_data())
        ink = [a for a in annotations if a['/Subtype'] == '/Ink' and '/IT' not in a]
        self.assertEqual(len(ink), 1)
        self.assertEqual(ink[0]['/BS']['/W'], 4)
        self.assertGreater(len(ink[0]['/InkList'][0]), 4)
        self.assertIn(b'4 w 1 J 1 j', ink[0]['/AP']['/N'].get_object().get_data())
        freehand = [a for a in annotations if a.get('/IT') == '/InkHighlight']
        self.assertEqual(len(freehand), 1)
        self.assertEqual(freehand[0]['/Subtype'], '/Ink')
        self.assertGreater(len(freehand[0]['/InkList'][0]), 4)
        for annotation in annotations:
            self.assertEqual((annotation['/Type'], annotation['/F'], annotation['/CA']), ('/Annot', 4, 1))
            self.assertNotIn('/NM', annotation)
            appearance = annotation['/AP']['/N'].get_object()
            self.assertEqual((appearance['/Type'], appearance['/Subtype']), ('/XObject', '/Form'))
            self.assertEqual(len(appearance['/BBox']), 4)
            self.assertTrue(appearance.get_data())

    def test_matching_sidecar_notes_round_trip_without_changing_the_pdf(self):
        payload = (FIXTURES / 'pdfjs-annotations.pdf').read_bytes()
        notes = json.loads((FIXTURES / 'pdfjs-notes.json').read_text())
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(set(note), {'page', 'text', 'quote', 'anchor'})
        self.assertEqual(note['anchor']['version'], 1)
        self.assertIn('Second line', note['quote'])
        pages = PdfReader(io.BytesIO(payload)).pages
        self.assertEqual(len(note['anchor']['pages'][0]['rects']), 2)
        for part in note['anchor']['pages']:
            page = pages[part['page'] - 1]
            for x0, y0, x1, y1 in part['rects']:
                self.assertTrue(0 <= x0 < x1 <= float(page.mediabox.width))
                self.assertTrue(0 <= y0 < y1 <= float(page.mediabox.height))
        with tempfile.TemporaryDirectory() as directory:
            library = Library(directory)
            doc = library.import_stream(io.BytesIO(payload), 'synthetic.pdf')
            library.save_notes(doc['id'], notes)
            self.assertEqual(Library(directory).get_notes(doc['id']), notes)
            self.assertEqual(library.get_path(doc['id']).read_bytes(), payload)
            self.assertEqual(library.get_document(doc['id'])['revision'], 0)
