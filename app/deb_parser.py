"""Deb file parser - extracts control metadata from .deb packages."""

import struct
import tarfile
import gzip
import io
import re
import logging

logger = logging.getLogger(__name__)


class ArArchiveError(Exception):
    pass


def _read_ar_members(filepath):
    """Parse an ar archive and return list of (name, data) tuples."""
    members = []
    with open(filepath, 'rb') as f:
        magic = f.read(8)
        if magic != b'!<arch>\n':
            raise ArArchiveError('Not a valid ar archive')

        while True:
            header = f.read(60)
            if len(header) < 60:
                break

            name = header[:16].decode('ascii', errors='replace').strip()
            # Parse size (bytes 48-58)
            try:
                size_str = header[48:58].decode('ascii').strip()
                size = int(size_str)
            except (ValueError, UnicodeDecodeError):
                break

            data = f.read(size)
            if len(data) < size:
                break  # truncated

            # Strip trailing '/' from name (ar convention)
            clean_name = name.rstrip('/')
            members.append((clean_name, data))

            # Pad to even boundary
            if size % 2 == 1:
                f.read(1)

    return members


def _parse_control_text(text):
    """Parse Debian control file text into a dict."""
    fields = {}
    current_key = None
    current_value = ''

    for line in text.splitlines():
        # Continuation line (starts with space or tab)
        if (line.startswith(' ') or line.startswith('\t')) and current_key:
            current_value += '\n' + line
            continue

        # Save previous field
        if current_key:
            fields[current_key] = current_value.strip()

        # New field
        match = re.match(r'^([\w-]+):\s*(.*)', line)
        if match:
            current_key = match.group(1)
            current_value = match.group(2)
        else:
            current_key = None

    if current_key:
        fields[current_key] = current_value.strip()

    return fields


def parse_deb(filepath):
    """Parse a .deb file and extract control metadata.

    Returns dict with keys like: Package, Version, Name, Description, etc.
    Returns None if parsing fails.
    """
    try:
        members = _read_ar_members(filepath)
    except (ArArchiveError, OSError) as e:
        logger.debug('Failed to read ar archive %s: %s', filepath, e)
        return None

    # Find control.tar member (may be .gz, .xz, .zst, or uncompressed)
    control_data = None
    for name, data in members:
        if 'control.tar' in name:
            control_data = data
            break

    if control_data is None:
        logger.debug('No control.tar found in %s', filepath)
        return None

    # Try to extract the 'control' file from the tar
    try:
        raw = control_data

        # Try gzip decompression
        if raw[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                raw = gz.read()

        # Try to find 'control' in the tar
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:*') as tar:
            for member_name in ('control', './control'):
                try:
                    extracted = tar.extractfile(member_name)
                    if extracted:
                        text = extracted.read().decode('utf-8', errors='replace')
                        return _parse_control_text(text)
                except KeyError:
                    continue

            # Fallback: iterate members
            for m in tar.getmembers():
                if m.name == 'control' or m.name.endswith('/control'):
                    extracted = tar.extractfile(m)
                    if extracted:
                        text = extracted.read().decode('utf-8', errors='replace')
                        return _parse_control_text(text)

    except Exception as e:
        logger.debug('Failed to extract control from %s: %s', filepath, e)

    return None
