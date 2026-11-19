"""HTTP transport, SSL/CA discovery, per-host throttling, and SSRF-safe PDF downloads."""

import http.client
import ipaddress
import os
import socket
import ssl
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from paperassist import __version__

MAX_PDF = 100 * 1024 * 1024
# Minimum seconds between requests per host; arXiv asks for three.
_INTERVALS = {
    'export.arxiv.org': 3,
    'api.crossref.org': 1,
    'api.adsabs.harvard.edu': 1,
    'api.semanticscholar.org': 1.1,
    'api.core.ac.uk': 1,
}
_LOCK = threading.Lock()
_HOST_LOCKS: dict[str, threading.Lock] = {}
_CACHE: dict[str, bytes] = {}
_LAST_REQUEST: dict[str, float] = {}
_SSL_CONTEXTS: dict[tuple[str | None, str | None], ssl.SSLContext] = {}

_LINUX_CA_FILES = (
    '/etc/ssl/certs/ca-certificates.crt',
    '/etc/pki/tls/certs/ca-bundle.crt',
    '/etc/ssl/ca-bundle.pem',
    '/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem',
    '/etc/ssl/cert.pem',
)
_LINUX_CA_DIRS = (
    '/etc/ssl/certs',
    '/etc/pki/tls/certs',
)


def clear_cache():
    """Clear HTTP response, throttle, and SSL context caches (mainly for tests)."""
    with _LOCK:
        _CACHE.clear()
        _LAST_REQUEST.clear()
        _SSL_CONTEXTS.clear()
    _resolve_ca_paths.cache_clear()


@lru_cache(maxsize=8)
def _resolve_ca_paths(ca_files, ca_dirs):
    cafile = next((path for path in ca_files if Path(path).is_file()), None)
    capath = None if cafile else next((path for path in ca_dirs if Path(path).is_dir()), None)
    return cafile, capath


def _fallback_ca_paths():
    """Locate host CA certificates on Linux when bundled OpenSSL's compiled-in path is absent."""
    if not sys.platform.startswith('linux'):
        return None, None
    paths = ssl.get_default_verify_paths()
    if paths.cafile or paths.capath:
        return None, None
    if os.environ.get(paths.openssl_cafile_env) or os.environ.get(paths.openssl_capath_env):
        return None, None
    return _resolve_ca_paths(_LINUX_CA_FILES, _LINUX_CA_DIRS)


def configure_ssl_env():
    """Populate SSL_CERT_FILE/SSL_CERT_DIR for standalone Linux builds whose OpenSSL dir differs from host."""
    cafile, capath = _fallback_ca_paths()
    paths = ssl.get_default_verify_paths()
    if cafile:
        os.environ.setdefault(paths.openssl_cafile_env, cafile)
    elif capath:
        os.environ.setdefault(paths.openssl_capath_env, capath)


def _ssl_context():
    key = _fallback_ca_paths()
    with _LOCK:
        context = _SSL_CONTEXTS.get(key)
    if context is None:
        cafile, capath = key
        context = ssl.create_default_context(cafile=cafile, capath=capath) if (cafile or capath) else ssl.create_default_context()
        with _LOCK:
            _SSL_CONTEXTS.setdefault(key, context)
            context = _SSL_CONTEXTS[key]
    return context


class Blocked(Exception):
    """A PDF link exists but the site will not hand it to an automated client; retrying cannot help."""

    def __init__(self, message, url=None):
        super().__init__(message)
        self.url = url


def download_pdf(url):
    """Fetch a PDF over public HTTPS only, pinning the verified IP to prevent DNS rebinding."""
    # Connect to the address already checked, so DNS rebinding cannot reach a local host.
    for _ in range(6):
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError('Only public HTTPS downloads are allowed')
        addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('Download host is not public')
        address = addresses[0][4][0]
        connection = http.client.HTTPSConnection(parts.hostname, timeout=30)
        raw_socket = socket.create_connection((address, 443), timeout=30)
        try:
            connection.sock = _ssl_context().wrap_socket(raw_socket, server_hostname=parts.hostname)
            path = parts.path or '/'
            if parts.query:
                path += '?' + parts.query
            connection.request('GET', path, headers={'User-Agent': f'PaperAssist/{__version__}'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise ValueError('Redirect has no destination')
                url = urljoin(url, location)
                if not url.startswith('https://'):
                    # Publisher DOI links often pass through plain HTTP; that is a page for the browser, not an error.
                    raise Blocked('The link redirects to an address that cannot be fetched automatically.')
                continue
            if response.status in (401, 403):
                raise Blocked(f'The site refused the automatic download (HTTP {response.status}).')
            if response.status != 200:
                raise ValueError(f'Download returned HTTP {response.status}')
            return response.read(MAX_PDF + 1)
        finally:
            connection.close()
            raw_socket.close()
    raise ValueError('Too many redirects')


def get(url, cfg, headers=None, fresh=False):
    """Fetch with per-host throttling. Credentials travel in `headers`, never in the cache key or messages."""
    host = urlsplit(url).hostname
    with _LOCK:
        if not fresh and url in _CACHE:
            return _CACHE[url]
        host_lock = _HOST_LOCKS.setdefault(host, threading.Lock())
    with host_lock:
        if not fresh:
            with _LOCK:
                if url in _CACHE:
                    return _CACHE[url]
        delay = _INTERVALS.get(host, 0) - (time.monotonic() - _LAST_REQUEST.get(host, float('-inf')))
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST[host] = time.monotonic()
    # Release locks before network I/O so one slow or throttled host never blocks another.
    agent = f'PaperAssist/{__version__}' + (f' (mailto:{cfg.email})' if cfg.email else '')
    request = Request(url, headers={'User-Agent': agent, **(headers or {})})
    with urlopen(request, timeout=15, context=_ssl_context()) as response:
        data = response.read(2 * 1024 * 1024)
    if not fresh:
        with _LOCK:
            if len(_CACHE) >= 200:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[url] = data
    return data
