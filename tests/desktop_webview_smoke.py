"""Opt-in real WebView checks with generated PDFs and temporary libraries.

Linux: xvfb-run -a env QT_QPA_PLATFORM=xcb .venv/bin/python tests/desktop_webview_smoke.py
Annotation edits use PDF.js storage; pointer gestures need interactive checks.
"""
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import webview
from paperassist.cli import main
from paperassist.library import Library
from paperassist.server import library_lock


def wait_for(test, label, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = test()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError(f'Timed out: {label}')


def free_port():
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0))
        return available.getsockname()[1]


if len(sys.argv) == 1:
    with tempfile.TemporaryDirectory(prefix='paperassist-desktop-qa-') as directory:
        port = free_port()
        child = [sys.executable, __file__]
        for phase in ('edit', 'reopen', 'comment', 'blank', 'missing', 'sigterm'):
            subprocess.run(child + [phase, directory, str(port)], check=True, timeout=40)
        owner = subprocess.Popen([sys.executable, '-m', 'paperassist', 'serve', '--data-dir',
            str(Path(directory) / 'library'), '--port', str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            marker = Path(directory) / 'library' / '.server.json'
            wait_for(marker.exists, 'existing server ready')
            url = json.loads(marker.read_text())['url']
            subprocess.run(child + ['attach', directory, str(free_port()), url], check=True, timeout=40)
            assert owner.poll() is None, 'Desktop stopped the existing server'
            with urllib.request.urlopen(url + '/api/config', timeout=5) as response:
                assert response.status == 200
            assert marker.exists(), 'Desktop removed the existing server marker'
        finally:
            owner.terminate()
            owner.wait(timeout=10)
    print('PASS: native Save/Discard/Cancel, reopen, last-tab comment save, blank/missing app close, SIGTERM and attachment')
    sys.exit(0)

phase, directory, selected_port, *server_url = sys.argv[1:]
root, port = Path(directory), int(selected_port)
for variable, name in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache')]:
    os.environ[variable] = str(root / name)
os.environ['PAPERASSIST_EMAIL'] = os.environ['OPENALEX_API_KEY'] = ''
library = Library(root / 'library')
if phase == 'edit':
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
    for number in (1, 2):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 18 Tf 50 720 Td (PaperAssist desktop fixture page {number}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.add_metadata({'/Title': 'Desktop integration fixture'})
    source = io.BytesIO()
    writer.write(source)
    document = library.import_stream(io.BytesIO(source.getvalue()), 'desktop-fixture.pdf')
else:
    document = library.list_documents()[0]
original = library.get_path(document['id'], False).read_bytes()
results = []
original_start = webview.start


def probe():
    window = webview.windows[0]
    js = window.evaluate_js
    try:
        assert window.events.loaded.wait(15), 'Window did not load'
        wait_for(lambda: js('!!window.paperassistDesktop'), 'application bridge')
        if phase == 'blank':
            js("location.href='about:blank'")
            wait_for(lambda: window.get_current_url() == 'about:blank', 'blank navigation')
        elif phase == 'missing':
            js('delete window.paperassistDesktop')
        elif phase == 'sigterm':
            results.append('SIGTERM delivered')
            os.kill(os.getpid(), signal.SIGTERM)
            return
        elif phase == 'attach':
            assert window.get_current_url().rstrip('/') == server_url[0], 'Wrong attached server'
        if phase in ('blank', 'missing', 'attach'):
            window.destroy()
            assert window.events.closed.wait(15), f'{phase} window did not close'
            results.append(phase)
            return
        js("""window.__qaReaders=[];window.__qaDecisions=[];
          const requestClose=window.paperassistDesktop.requestClose;
          window.paperassistDesktop.requestClose=()=>requestClose().then(value=>{
            window.__qaDecisions.push(value);return value;
          });
          import(new URL('/reader.js',location.href).href).then(({Reader})=>{
            const open=Reader.prototype.open;
            Reader.prototype.open=async function(){window.__qaReaders.push(this);return open.call(this);};
            window.__qaHooked=true;
          });""")
        wait_for(lambda: js('window.__qaHooked'), 'reader instrumentation')
        js("document.querySelector('#library-button').click()")
        wait_for(lambda: js("!!document.querySelector('#library-list .library-row button')"), 'library loaded')
        js("document.querySelector('#library-list .library-row button').click()")
        wait_for(lambda: js("window.__qaReaders[0]?.pdf?.numPages===2 && document.querySelector('.page canvas')?.width>0"), 'PDF.js canvas')
        js("document.querySelector('#acquire-dialog[open] [data-close=acquire-dialog]')?.click()")
        wait_for(lambda: js("document.querySelector('.textLayer')?.textContent.includes('desktop fixture')"), 'PDF text')
        if phase == 'comment':
            js("document.querySelector('#comment-tool').click()")
            wait_for(lambda: js("!!document.querySelector('.comment-composer textarea')"), 'comment composer')
            js("""const text=document.querySelector('.comment-composer textarea');
                text.value='Last-tab comment';text.dispatchEvent(new Event('input'));
                document.querySelector('#tabs .tab-close').click();""")
            wait_for(lambda: js("document.querySelector('#unsaved-dialog').open"), 'draft close dialog')
            # Run the queued selection callback after Save has cleared the last tab's draft.
            js("""const timeout=window.setTimeout;
                window.setTimeout=(fn,delay,...args)=>{
                  if(delay===150){window.__qaSelectionCallback=fn;return 0;}
                  return timeout(fn,delay,...args);
                };
                document.dispatchEvent(new Event('selectionchange'));
                window.setTimeout=timeout;
                document.querySelector('#unsaved-save').click();""")
            wait_for(lambda: js("document.querySelectorAll('#tabs .tab').length===0"), 'last tab closed')
            js('window.__qaSelectionCallback()')
            assert library.get_notes(document['id'])[-1]['text'] == 'Last-tab comment'
            window.destroy()
            assert window.events.closed.wait(15), 'Window did not close after comment save'
            results.append(phase)
            return
        if phase == 'reopen':
            assert js("localStorage.getItem('paperassist-desktop-qa')==='persisted'"), 'Profile state lost'
            assert js('window.__qaReaders[0].viewer.currentPageNumber===2'), 'Reading position lost'
            js("""window.__qaReaders[0].pdf.getPage(1).then(p=>p.getAnnotations())
              .then(items=>window.__qaSaved=items.some(a=>a.subtype==='Highlight'));""")
            wait_for(lambda: js('window.__qaSaved'), 'saved highlight in PDF.js')
            assert not js('window.__qaReaders[0].dirty'), 'Reopening made the PDF dirty'
        js("window.__qaReaders[0].viewer.currentPageNumber=2;localStorage.setItem('paperassist-desktop-qa','persisted')")
        js("""const r=window.__qaReaders[0];r.pdf.annotationStorage.setValue('pdfjs_internal_editor_qa',{
          annotationType:9,pageIndex:0,color:[245,215,110],opacity:0.5,thickness:12,
          rect:[50,700,240,720],rotation:0,quadPoints:[50,720,240,720,50,700,240,700],
          outlines:[[50,700,240,700,240,720,50,720]],user:'Desktop QA'});r.refreshStatus();""")
        assert js('window.__qaReaders[0].dirty')
        if phase == 'edit':
            # Native close while the tab's modal is open must not replace its handlers.
            js("document.querySelector('#tabs .tab-close').click()")
            wait_for(lambda: js("document.querySelector('#unsaved-dialog').open"), 'tab close dialog')
            window.destroy()
            wait_for(lambda: js('window.__qaDecisions.length===1'), 'overlapping close refused')
            assert js('window.__qaDecisions[0]===false')
            js("document.querySelector('#unsaved-cancel').click()")
        window.destroy()
        wait_for(lambda: js("document.querySelector('#unsaved-dialog').open"), 'native unsaved dialog')
        if phase == 'edit':
            js("document.querySelector('#unsaved-cancel').click()")
            wait_for(lambda: js('window.__qaDecisions.length===2'), 'Cancel completed')
            assert js('window.__qaReaders[0].dirty'), 'Cancel lost edits'
            assert library.get_document(document['id'])['revision'] == 0, 'Close autosaved'
            window.destroy()
            wait_for(lambda: js("document.querySelector('#unsaved-dialog').open"), 'second close dialog')
        js(f"document.querySelector('#unsaved-{'save' if phase == 'edit' else 'discard'}').click()")
        assert window.events.closed.wait(15), 'Window did not close after decision'
        assert library.get_document(document['id'])['revision'] == 1, 'Wrong PDF revision'
        results.append(phase)
    except Exception:
        traceback.print_exc()
        results.append('FAILED')
        js('window.paperassistDesktop={requestClose:()=>Promise.resolve(true)}')
        window.destroy()


def start(**kwargs):
    return original_start(func=probe, **kwargs)


with patch.object(webview, 'start', start):
    status = main(['desktop', '--data-dir', str(root / 'library'), '--port', str(port)])
assert status == 0 and results and 'FAILED' not in results, (status, results)
assert library.get_path(document['id'], False).read_bytes() == original
assert any(a.get_object().get('/Subtype') == '/Highlight'
           for a in PdfReader(library.get_path(document['id'])).pages[0].get('/Annots', []))
if phase != 'attach':
    assert not (root / 'library' / '.server.json').exists(), 'Server marker survived close'
    with socket.socket() as released:
        assert released.connect_ex(('127.0.0.1', port)) != 0, 'Server still accepts connections'
    with library_lock(root / 'library'):
        pass
print(f'PASS: {phase}')
