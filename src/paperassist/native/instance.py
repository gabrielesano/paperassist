"""Per-library, same-user file delivery over local sockets (never TCP or HTTP)."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from paperassist.paths import config_file

MAX_FILES = 256
MAX_BYTES = 256 * 1024
TIMEOUT_MS = 5000


def absolute_paths(values):
    if not isinstance(values, list) or len(values) > MAX_FILES:
        raise ValueError(f'Open at most {MAX_FILES} PDF files at a time.')
    paths = []
    for value in values:
        if not isinstance(value, str) or not value or '\0' in value:
            raise ValueError('Choose local PDF files to open.')
        if value.startswith('file:'):
            url = QUrl(value)
            if not url.isLocalFile() or url.host() not in ('', 'localhost'):
                raise ValueError('Only local PDF files can be opened.')
            url.setHost('')
            value = url.toLocalFile()
        paths.append(str(Path(value).expanduser().resolve()))
    return list(dict.fromkeys(paths))


def server_name(directory):
    directory = Path(directory).resolve()
    # An existing directory's identity also handles aliases and case-insensitive filesystems.
    try:
        info = directory.stat()
        identity = [info.st_dev, info.st_ino] if info.st_ino else os.path.normcase(str(directory))
    except OSError:
        identity = os.path.normcase(str(directory))
    scope = [str(config_file().parent.resolve()), identity]
    key = hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:40]
    if sys.platform == 'win32':
        return 'paperassist-' + key
    return str(Path('/tmp') / f'paperassist-{os.getuid()}' / key)


def _line(socket, deadline):
    while not socket.canReadLine():
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0 or not socket.waitForReadyRead(remaining):
            raise RuntimeError('The running PaperAssist window did not confirm the request. Try again.')
        if socket.bytesAvailable() > MAX_BYTES:
            raise RuntimeError('Invalid response from the running PaperAssist window.')
    return json.loads(bytes(socket.readLine(MAX_BYTES)).decode('utf-8'))


def forward(directory, paths, connect_ms=150):
    socket = QLocalSocket()
    socket.connectToServer(server_name(directory))
    if not socket.waitForConnected(connect_ms):
        socket.abort()
        return False
    try:
        deadline = time.monotonic() + TIMEOUT_MS / 1000
        greeting = _line(socket, deadline)
        if not isinstance(greeting, dict) or type(greeting.get('pid')) is not int:
            raise RuntimeError('Invalid response from the running PaperAssist window.')
        if sys.platform == 'win32' and type(greeting.get('pid')) is int:
            import ctypes
            ctypes.WinDLL('user32').AllowSetForegroundWindow(greeting['pid'])
        payload = json.dumps({'version': 1, 'paths': absolute_paths(paths)}, ensure_ascii=False).encode() + b'\n'
        if len(payload) > MAX_BYTES:
            raise ValueError('The list of PDF paths is too long.')
        socket.write(payload)
        if socket.bytesToWrite() and not socket.waitForBytesWritten(TIMEOUT_MS):
            raise RuntimeError('Could not deliver the PDFs to the running PaperAssist window.')
        answer = _line(socket, deadline)
        if not isinstance(answer, dict):
            raise RuntimeError('Invalid response from the running PaperAssist window.')
        if answer.get('ok') is not True:
            raise RuntimeError(answer.get('error') or 'The running PaperAssist window rejected the request.')
        return True
    except (ValueError, UnicodeError) as error:
        raise RuntimeError(f'Could not deliver the PDFs: {error}') from None
    finally:
        socket.abort()


class InstanceServer(QObject):
    """Start only while holding the library lock; only that owner may remove a stale endpoint."""
    def __init__(self, directory, accept, parent=None):
        super().__init__(parent)
        self.accept = accept
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.clients = {}
        name = server_name(directory)
        if sys.platform != 'win32':
            # macOS ignores Unix socket mode bits; a private parent directory is required.
            folder = Path(name).parent
            folder.mkdir(mode=0o700, exist_ok=True)
            info = folder.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError('The private PaperAssist socket directory is not owned by this user.')
            folder.chmod(0o700)
        if not self.server.listen(name):
            QLocalServer.removeServer(name)
            if not self.server.listen(name):
                raise RuntimeError(f'Could not enable PDF file delivery: {self.server.errorString()}')
        self.server.newConnection.connect(self._connections)

    def _connections(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.clients[socket] = bytearray()
            socket.setReadBufferSize(MAX_BYTES + 1)
            socket.readyRead.connect(lambda current=socket: self._read(current))
            socket.disconnected.connect(lambda current=socket: self._drop(current))
            socket.write(json.dumps({'pid': os.getpid()}).encode() + b'\n')
            timer = QTimer(socket, singleShot=True, interval=TIMEOUT_MS)
            timer.timeout.connect(lambda current=socket: current.abort())
            timer.start()
            self._read(socket)

    def _read(self, socket):
        if socket not in self.clients:
            return
        buffer = self.clients[socket]
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > MAX_BYTES:
            socket.abort()
            return
        if b'\n' not in buffer:
            return
        self.clients.pop(socket)
        try:
            line, extra = bytes(buffer).split(b'\n', 1)
            request = json.loads(line.decode('utf-8'))
            if (extra.strip() or not isinstance(request, dict)
                    or type(request.get('version')) is not int or request['version'] != 1):
                raise ValueError('Invalid PDF opening request.')
            self.accept(absolute_paths(request.get('paths')))
            response = {'ok': True}
        except (ValueError, OSError, RuntimeError) as error:
            response = {'error': str(error)}
        socket.write(json.dumps(response).encode() + b'\n')
        socket.disconnectFromServer()

    def _drop(self, socket):
        self.clients.pop(socket, None)
        socket.deleteLater()

    def close(self):
        self.accept = None
        self.server.close()
        for socket in list(self.clients):
            socket.abort()
        self.clients.clear()
