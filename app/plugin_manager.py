"""Core plugin management - matching, sorting, versioning logic."""

import re
import difflib
import shutil
import logging
import os
from pathlib import Path
from datetime import datetime

from .deb_parser import parse_deb

logger = logging.getLogger(__name__)


def compare_versions(v1, v2):
    """Compare two Debian version strings. Returns -1 if v1 < v2, 0 if equal, 1 if v1 > v2."""
    if v1 == v2:
        return 0

    # Strip epoch
    epoch1 = '0'
    epoch2 = '0'
    if ':' in v1:
        epoch1, v1 = v1.split(':', 1)
    if ':' in v2:
        epoch2, v2 = v2.split(':', 1)

    # Compare epoch numerically
    if int(epoch1) != int(epoch2):
        return -1 if int(epoch1) < int(epoch2) else 1

    # Split into upstream and debian revision
    # Debian version: [epoch:]upstream_version[-debian_revision]
    # Split on last '-' for revision (or use all as upstream)
    if '-' in v1:
        upstream1, rev1 = v1.rsplit('-', 1)
    else:
        upstream1, rev1 = v1, ''

    if '-' in v2:
        upstream2, rev2 = v2.rsplit('-', 1)
    else:
        upstream2, rev2 = v2, ''

    # Compare upstream version
    result = _compare_upstream(upstream1, upstream2)
    if result != 0:
        return result

    # Compare debian revisions
    return _compare_revision(rev1, rev2)


def _split_version_parts(version):
    """Split version string into alternating (is_numeric, text) parts."""
    parts = []
    if not version:
        return parts

    i = 0
    while i < len(version):
        c = version[i]
        # Handle tilde specially - it sorts before anything
        if c == '~':
            parts.append(('tilde', '~'))
            i += 1
            continue

        # Gather consecutive digits or non-digits
        j = i
        is_digit = c.isdigit()
        while j < len(version) and version[j].isdigit() == is_digit and version[j] != '~':
            j += 1

        if is_digit:
            parts.append(('num', version[i:j]))
        else:
            parts.append(('str', version[i:j]))
        i = j

    return parts


def _compare_upstream(v1, v2):
    """Compare two upstream version strings using Debian rules."""
    if v1 == v2:
        return 0

    p1 = _split_version_parts(v1)
    p2 = _split_version_parts(v2)

    max_len = max(len(p1), len(p2))
    for i in range(max_len):
        if i >= len(p1):
            # v1 exhausted — check v2's next part
            # tilde sorts before end-of-string (pre-release), anything else after
            return 1 if p2[i][0] == 'tilde' else -1
        if i >= len(p2):
            # v2 exhausted — check v1's next part
            return -1 if p1[i][0] == 'tilde' else 1

        t1, s1 = p1[i]
        t2, s2 = p2[i]

        # Tilde sorts before everything
        if t1 == 'tilde' and t2 != 'tilde':
            return -1
        if t1 != 'tilde' and t2 == 'tilde':
            return 1
        if t1 == 'tilde' and t2 == 'tilde':
            continue

        # Numeric comparison
        if t1 == 'num' and t2 == 'num':
            n1 = int(s1.lstrip('0') or '0')
            n2 = int(s2.lstrip('0') or '0')
            if n1 < n2:
                return -1
            elif n1 > n2:
                return 1
        else:
            # Lexicographic comparison
            if s1 < s2:
                return -1
            elif s1 > s2:
                return 1

    return 0


def _compare_revision(r1, r2):
    """Compare debian revision strings."""
    if not r1 and not r2:
        return 0
    if not r1:
        return -1  # No revision means older than any revision
    if not r2:
        return 1

    # Revisions follow same rules as upstream
    return _compare_upstream(r1, r2)


def normalize_name(name):
    """Normalize a name for comparison (lowercase, strip non-alphanumeric)."""
    name = name.lower()
    name = re.sub(r'[^a-z0-9一-鿿]', '', name)
    return name.strip()


def extract_name_from_filename(filename):
    """Extract a probable plugin name from a deb filename.

    Handles patterns like:
      package-name_1.2.3.deb             -> package-name
      PackageName_1.2.3-1.deb            -> PackageName
      com.example.package_1.0.deb        -> com.example.package
      package-name-1.2.3.deb
      测试_0.0-1_无根.deb              -> 测试_无根
      App_Store_1.2.3-beta.deb           -> App_Store
    """
    name = filename
    if name.lower().endswith('.deb'):
        name = name[:-4]

    # Split by underscore and filter out version-like parts.
    # A version part starts with a digit and contains only version characters.
    version_segment = re.compile(r'^\d[\d\.\~\-\+]*$')
    parts = name.split('_')
    non_version = [p for p in parts if not version_segment.match(p)]

    # Only use new logic when underscores exist AND at least one version part was found
    if len(parts) > 1 and len(non_version) != len(parts):
        return '_'.join(non_version)

    # Fallback: strip version suffix at end (hyphen-separated names like package-name-1.2.3)
    version_pattern = r'[_\-]\d+[\d\.\~\-\+]*(?:-\d+)?$'
    stripped = re.sub(version_pattern, '', name)

    if len(stripped) < len(name):
        return stripped.strip('_- ')

    version_pattern2 = r'[-]\d+[\d\.]*$'
    stripped = re.sub(version_pattern2, '', name)
    return stripped.strip('_- ')


def match_plugin(filename, plugin_table, control_data=None, threshold=0.6):
    """Match a deb file against the plugin table.

    Args:
        filename: The deb filename
        plugin_table: List of plugin entries with 'name', 'keyword', 'package' keys
        control_data: Dict from deb control (Package, Name fields)
        threshold: Fuzzy match cutoff (0-1)

    Returns:
        Matched plugin entry dict, or None
    """
    # Strategy 1: Match by package ID (from deb control)
    if control_data:
        pkg_id = control_data.get('Package', '').lower()
        for plugin in plugin_table:
            if plugin.get('package', '').lower() == pkg_id:
                return plugin
            # Partial match on package
            if pkg_id and plugin.get('package', '').lower() and \
               (pkg_id in plugin['package'].lower() or plugin['package'].lower() in pkg_id):
                return plugin

    # Strategy 2: Match by Name from control
    if control_data:
        ctrl_name = control_data.get('Name', '')
        if ctrl_name:
            norm_ctrl = normalize_name(ctrl_name)
            for plugin in plugin_table:
                norm_plugin = normalize_name(plugin['name'])
                if norm_ctrl == norm_plugin:
                    return plugin

    # Strategy 3: Match by keyword
    extracted = extract_name_from_filename(filename)
    norm_filename = normalize_name(extracted)
    if not norm_filename:
        return None

    # Direct match on keyword
    for plugin in plugin_table:
        kw = normalize_name(plugin.get('keyword', ''))
        if kw and (norm_filename == kw or kw in norm_filename or norm_filename in kw):
            return plugin

    # Strategy 4: Fuzzy match on name
    names_map = {}
    for plugin in plugin_table:
        n = normalize_name(plugin['name'])
        names_map[n] = plugin
        # Also add keyword
        kw = normalize_name(plugin.get('keyword', ''))
        if kw:
            names_map[kw] = plugin

    candidates = list(names_map.keys())
    matches = difflib.get_close_matches(norm_filename, candidates, n=1, cutoff=threshold)
    if matches:
        return names_map[matches[0]]

    return None


def get_deb_version(filepath):
    """Extract version from a deb file by parsing control data."""
    control = parse_deb(str(filepath))
    if control and 'Version' in control:
        return control['Version']
    return None


def _extract_version_from_filename(filename):
    """Try to extract version from a deb filename.

    Handles patterns like:
      name_1.2.3.deb          -> 1.2.3
      name_1.2.3-1.deb        -> 1.2.3-1
      name_0.0-1_desc.deb     -> 0.0-1  (version in middle)
      name-1.2.3.deb
    """
    name = filename
    if name.lower().endswith('.deb'):
        name = name[:-4]

    # Look for a version segment: starts with digit, followed by digits/dots/tildes/hyphens/pluses
    version_re = re.compile(r'\b\d[\d\.\~\-\+]*\b')
    # Split by underscores and find the first part that looks like a version
    for part in name.split('_'):
        part = part.strip()
        if version_re.fullmatch(part):
            return part

    # Try matching at end for hyphen-separated
    match = re.search(r'[-](\d[\d\.\~]*)$', name)
    if match:
        return match.group(1)

    return ''


def scan_folder(folder_path):
    """Scan a folder for .deb files and return info about each."""
    folder = Path(folder_path)
    if not folder.exists():
        return []

    results = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() == '.deb' and f.is_file():
            control = parse_deb(str(f))
            info = {
                'filename': f.name,
                'path': str(f),
                'size': f.stat().st_size,
                'modified': datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            }
            if control:
                info['package'] = control.get('Package', '')
                info['version'] = control.get('Version', '')
                info['display_name'] = control.get('Name', '')
            else:
                info['package'] = ''
                info['version'] = _extract_version_from_filename(f.name)
                info['display_name'] = ''
            results.append(info)

    return results


def sort_folder(folder_path, settings, plugin_table):
    """Sort deb files from a date folder into the output directory.

    Output files are saved flat (no subdirectories) with the naming
    format: {PluginName}_{Version}.deb

    Returns:
        Dict with 'sorted', 'unmatched', 'updated' lists
    """
    output_dir = Path(settings.output_dir)
    input_folder = Path(folder_path)

    if not input_folder.exists():
        return {'sorted': [], 'unmatched': [], 'updated': []}

    threshold = settings.get('fuzzy_match_threshold', 0.6)
    table = plugin_table.get_all()

    result = {'sorted': [], 'unmatched': [], 'updated': []}

    for f in sorted(input_folder.iterdir()):
        if f.suffix.lower() != '.deb' or not f.is_file():
            continue

        control = parse_deb(str(f))
        match = match_plugin(f.name, table, control, threshold)

        if match:
            version = control.get('Version', '') if control else _extract_version_from_filename(f.name)
            if not version:
                version = 'unknown'

            # Flat output: PluginName_Version.deb
            safe_name = match['name'].replace('/', '_').replace('\\', '_')
            output_filename = f"{safe_name}_{version}.deb"
            dest_path = output_dir / output_filename

            # Check if we need to update (newer version)
            was_updated = False
            existing = _find_existing_deb(output_dir, control, match['name'])

            if existing and version != 'unknown':
                cmp = compare_versions(version, existing['version'])
                if cmp > 0:
                    existing['path'].unlink()
                    shutil.copy2(str(f), str(dest_path))
                    was_updated = True
                    logger.info('Updated %s: %s -> %s', match['name'], existing['version'], version)
                elif cmp == 0:
                    logger.debug('Skipping %s: version %s unchanged', match['name'], version)
                    continue
                else:
                    logger.debug('Skipping %s: existing %s > new %s', match['name'], existing['version'], version)
                    continue
            else:
                shutil.copy2(str(f), str(dest_path))
                was_updated = False

            entry = {
                'filename': output_filename,
                'plugin': match['name'],
                'version': version,
                'dest': str(dest_path),
                'updated': was_updated,
            }
            result['sorted'].append(entry)
            if was_updated:
                result['updated'].append(entry)
        else:
            result['unmatched'].append({
                'filename': f.name,
                'path': str(f),
            })

    return result


def _find_existing_deb(output_dir, control, plugin_name):
    """Find a previously sorted deb for the same plugin in flat output.

    Matches by output filename prefix (PluginName_Version.deb).
    Falls back to control-based matching for real debs when filename is ambiguous.
    """
    if not output_dir.exists():
        return None
    norm_plugin = normalize_name(plugin_name)
    for f in output_dir.iterdir():
        if f.suffix.lower() != '.deb' or not f.is_file():
            continue

        # Strategy 1: Match by filename prefix — works for all files
        stem = f.stem
        if '_' in stem:
            name_part, ver_part = stem.rsplit('_', 1)
        else:
            name_part = stem
            ver_part = ''

        if normalize_name(name_part) == norm_plugin:
            return {'version': ver_part, 'path': f}

        # Strategy 2: Match by control data (for real debs where filename differs)
        if control:
            ctrl = parse_deb(str(f))
            if ctrl:
                ctrl_name = ctrl.get('Name', '') or ctrl.get('Package', '')
                if normalize_name(ctrl_name) == norm_plugin:
                    return {'version': ctrl.get('Version', ''), 'path': f}
    return None


def get_output_summary(output_dir):
    """Get a summary of all sorted plugins in the output directory.

    Reads flat .deb files directly from output_dir (no subdirectories).
    """
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return []

    results = []
    for f in sorted(output_dir.iterdir()):
        if f.suffix.lower() != '.deb' or not f.is_file():
            continue
        control = parse_deb(str(f))
        if control:
            version = control.get('Version', '')
        else:
            version = _extract_version_from_filename(f.name)

        # Extract plugin name from filename: PluginName_Version.deb
        stem = f.stem
        # Strip version suffix (everything after last underscore)
        plugin_name = stem.rsplit('_', 1)[0] if '_' in stem else stem

        results.append({
            'plugin_name': plugin_name,
            'filename': f.name,
            'version': version,
            'size': f.stat().st_size,
            'modified': datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            'path': str(f),
        })

    return results


def scan_webdav_files(webdav_root):
    """Scan all files in the WebDAV root directory."""
    root = Path(webdav_root)
    if not root.exists():
        return []

    items = []
    for f in sorted(root.iterdir()):
        if not f.is_dir():
            continue
        files = []
        for entry in sorted(f.iterdir()):
            if entry.is_file():
                control = parse_deb(str(entry))
                if control:
                    version = control.get('Version', '')
                else:
                    version = _extract_version_from_filename(entry.name)
                files.append({
                    'filename': entry.name,
                    'version': version,
                    'size': entry.stat().st_size,
                    'modified': datetime.fromtimestamp(entry.stat().st_mtime).isoformat(),
                })
        items.append({
            'folder': f.name,
            'file_count': len(files),
            'total_size': sum(ff['size'] for ff in files),
            'modified': datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            'files': files,
        })
    return items


def get_date_folders(webdav_root):
    """Get list of date-named folders in the webdav root."""
    root = Path(webdav_root)
    if not root.exists():
        return []

    folders = []
    for f in root.iterdir():
        if f.is_dir():
            # Count deb files
            deb_count = len(list(f.glob('*.deb')))
            folders.append({
                'name': f.name,
                'path': str(f),
                'deb_count': deb_count,
                'modified': datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })

    return sorted(folders, key=lambda x: x['name'], reverse=True)


def suggest_folder_name(root_path=None):
    """Generate a default folder name based on current date (MMDD).

    If root_path is given, ensures uniqueness by appending (1), (2), etc.
    when the base name already exists.
    """
    base = datetime.now().strftime('%m%d')
    if root_path is None:
        return base

    if not (Path(root_path) / base).exists():
        return base

    counter = 1
    while True:
        name = f"{base}({counter})"
        if not (Path(root_path) / name).exists():
            return name
        counter += 1
