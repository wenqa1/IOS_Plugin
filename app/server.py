"""Flask server - web UI and REST API for plugin management."""

import os
import logging
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote

from flask import (
    Flask, render_template, request, jsonify, send_from_directory,
)

from .plugin_manager import (
    sort_folder, scan_folder, get_output_summary, get_date_folders,
    suggest_folder_name, match_plugin, parse_deb, scan_webdav_files,
)

logger = logging.getLogger(__name__)


def create_app(settings, plugin_table):
    """Create and configure the Flask application."""
    app = Flask(__name__,
                template_folder=str(Path(__file__).parent.parent / 'templates'),
                static_folder=str(Path(__file__).parent.parent / 'static'))

    # Ensure directories exist
    dirs = settings.ensure_dirs()
    logger.info('Data directories: %s', dirs)

    # ========== Page Routes ==========

    @app.route('/')
    def index():
        return render_template('index.html',
                               webdav_port=settings.get('webdav_port', 5001))

    # ========== Settings API ==========

    @app.route('/api/settings', methods=['GET'])
    def get_settings():
        return jsonify(settings.get_all())

    @app.route('/api/settings', methods=['PUT'])
    def update_settings():
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        settings.update(data)
        settings.ensure_dirs()
        return jsonify({'success': True, 'settings': settings.get_all()})

    @app.route('/api/settings/dirs', methods=['GET'])
    def get_dirs():
        dirs = settings.ensure_dirs()
        return jsonify(dirs)

    # ========== Plugin Table API ==========

    @app.route('/api/plugins', methods=['GET'])
    def get_plugins():
        return jsonify(plugin_table.get_all())

    @app.route('/api/plugins', methods=['POST'])
    def add_plugin():
        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({'error': 'Plugin name is required'}), 400
        entry = plugin_table.add(
            name=data['name'],
            keyword=data.get('keyword', ''),
            remark=data.get('remark', ''),
        )
        return jsonify({'success': True, 'plugin': entry}), 201

    @app.route('/api/plugins/<int:plugin_id>', methods=['PUT'])
    def update_plugin(plugin_id):
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data'}), 400
        entry = plugin_table.update(
            plugin_id,
            name=data.get('name'),
            keyword=data.get('keyword'),
            remark=data.get('remark'),
        )
        if entry:
            return jsonify({'success': True, 'plugin': entry})
        return jsonify({'error': 'Plugin not found'}), 404

    @app.route('/api/plugins/<int:plugin_id>', methods=['DELETE'])
    def delete_plugin(plugin_id):
        plugin_table.delete(plugin_id)
        return jsonify({'success': True})

    @app.route('/api/plugins/reset', methods=['POST'])
    def reset_plugins():
        """Reset plugin table to defaults."""
        from .config import DEFAULT_PLUGIN_TABLE
        plugin_table._entries = list(DEFAULT_PLUGIN_TABLE)
        plugin_table._next_id = max(e['id'] for e in plugin_table._entries) + 1
        plugin_table._save()
        return jsonify({'success': True, 'plugins': plugin_table.get_all()})

    # ========== Path Safety Helper ==========

    def _safe_folder_path(folder_name):
        """Resolve a folder name within webdav_root, preventing path traversal."""
        resolved = (settings.webdav_root / folder_name).resolve()
        root = settings.webdav_root.resolve()
        if not str(resolved).startswith(str(root)):
            return None
        return resolved

    # ========== Folder Management API ==========

    @app.route('/api/folders', methods=['GET'])
    def list_folders():
        folders = get_date_folders(settings.webdav_root)
        return jsonify(folders)

    @app.route('/api/folders', methods=['POST'])
    def create_folder():
        data = request.get_json(silent=True) or {}
        name = data.get('name', suggest_folder_name(settings.webdav_root))
        if not name or '..' in name or '/' in name or '\\' in name:
            return jsonify({'error': 'Invalid folder name'}), 400
        folder_path = _safe_folder_path(name)
        if folder_path is None:
            return jsonify({'error': 'Invalid folder name'}), 400
        if folder_path.exists():
            return jsonify({'error': f'文件夹 "{name}" 已存在'}), 409

        folder_path.mkdir(parents=True, exist_ok=True)
        return jsonify({
            'success': True,
            'folder': {
                'name': name,
                'path': str(folder_path),
                'deb_count': 0,
                'modified': datetime.now().isoformat(),
            }
        }), 201

    @app.route('/api/folders/<folder_name>', methods=['GET'])
    def get_folder(folder_name):
        folder_path = _safe_folder_path(folder_name)
        if folder_path is None:
            return jsonify({'error': 'Invalid folder name'}), 400
        if not folder_path.exists():
            return jsonify({'error': 'Folder not found'}), 404
        files = scan_folder(folder_path)
        return jsonify({
            'name': folder_name,
            'path': str(folder_path),
            'files': files,
        })

    @app.route('/api/folders/<folder_name>/sort', methods=['POST'])
    def sort_folder_route(folder_name):
        """Sort/match plugins in a date folder and copy to output."""
        folder_path = _safe_folder_path(folder_name)
        if folder_path is None:
            return jsonify({'error': 'Invalid folder name'}), 400
        if not folder_path.exists():
            return jsonify({'error': 'Folder not found'}), 404

        result = sort_folder(folder_path, settings, plugin_table)

        return jsonify({
            'success': True,
            'folder': folder_name,
            'sorted': result['sorted'],
            'unmatched': result['unmatched'],
            'updated': result['updated'],
            'sorted_count': len(result['sorted']),
            'unmatched_count': len(result['unmatched']),
            'updated_count': len(result['updated']),
        })

    @app.route('/api/folders/<folder_name>/files/<filename>', methods=['DELETE'])
    def delete_file(folder_name, filename):
        folder_path = _safe_folder_path(folder_name)
        if folder_path is None:
            return jsonify({'error': 'Invalid folder name'}), 400
        if '/' in filename or '\\' in filename or '..' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        file_path = folder_path / filename
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
            return jsonify({'success': True})
        return jsonify({'error': 'File not found'}), 404

    @app.route('/api/folders/<folder_name>', methods=['DELETE'])
    def delete_folder(folder_name):
        folder_path = _safe_folder_path(folder_name)
        if folder_path is None:
            return jsonify({'error': 'Invalid folder name'}), 400
        if folder_path.exists():
            import shutil
            shutil.rmtree(str(folder_path))
            return jsonify({'success': True})
        return jsonify({'error': 'Folder not found'}), 404

    # ========== Upload API ==========

    @app.route('/api/upload/<folder_name>', methods=['POST'])
    def upload_file(folder_name):
        """Upload a deb file to a specific folder via web UI."""
        folder_path = _safe_folder_path(folder_name)
        if folder_path is None:
            return jsonify({'error': 'Invalid folder name'}), 400
        if not folder_path.exists():
            return jsonify({'error': 'Folder not found'}), 404

        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        save_path = folder_path / file.filename
        file.save(str(save_path))

        # Parse the uploaded deb for info
        control = parse_deb(str(save_path))
        info = {
            'filename': file.filename,
            'size': save_path.stat().st_size,
            'package': control.get('Package', '') if control else '',
            'version': control.get('Version', '') if control else '',
        }

        return jsonify({'success': True, 'file': info}), 201

    # ========== Output API ==========

    @app.route('/api/output', methods=['GET'])
    def get_output():
        summary = get_output_summary(settings.output_dir)
        return jsonify(summary)

    @app.route('/api/output/clear', methods=['POST'])
    def clear_output():
        output_dir = Path(settings.output_dir)
        if output_dir.exists():
            import shutil
            for item in output_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(str(item))
                else:
                    item.unlink()
        return jsonify({'success': True})

    @app.route('/api/output/zip', methods=['GET'])
    def download_output_zip():
        """Download all output files as a ZIP archive."""
        import io
        import zipfile
        from flask import send_file

        output_dir = Path(settings.output_dir)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            if output_dir.exists():
                for f in sorted(output_dir.iterdir()):
                    if f.is_file():
                        zf.write(str(f), arcname=f.name)
        buf.seek(0)
        return send_file(
            buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'output_plugins_{datetime.now().strftime("%m%d")}.zip',
        )

    @app.route('/api/output/rescan', methods=['POST'])
    def rescan_output():
        """Rescan all date folders and re-sort everything."""
        folders = get_date_folders(settings.webdav_root)
        results = []
        for folder in folders:
            folder_path = settings.webdav_root / folder['name']
            result = sort_folder(folder_path, settings, plugin_table)
            results.append({
                'folder': folder['name'],
                'sorted': len(result['sorted']),
                'updated': len(result['updated']),
                'unmatched': len(result['unmatched']),
            })
        return jsonify({'success': True, 'results': results})

    # ========== Match Test API ==========

    @app.route('/api/match-test', methods=['POST'])
    def match_test():
        """Test how a filename would be matched against the plugin table."""
        data = request.get_json()
        if not data or not data.get('filename'):
            return jsonify({'error': 'Filename required'}), 400

        filename = data['filename']
        table = plugin_table.get_all()
        match = match_plugin(filename, table, threshold=settings.get('fuzzy_match_threshold', 0.6))

        return jsonify({
            'filename': filename,
            'matched': match is not None,
            'plugin': match,
        })

    # ========== Server Info ==========

    @app.route('/api/info', methods=['GET'])
    def server_info():
        webdav_host = settings.get('webdav_host', '0.0.0.0')
        web_host = settings.get('web_host', '0.0.0.0')
        return jsonify({
            'webdav_port': settings.get('webdav_port', 5001),
            'web_port': settings.get('web_port', 5099),
            'web_host': web_host,
            'webdav_host': webdav_host,
            'storage_path': str(settings.storage_path),
            'webdav_root': str(settings.webdav_root),
            'output_dir': str(settings.output_dir),
            'hostname': os.environ.get('COMPUTERNAME', 'localhost'),
        })

    # ========== WebDAV Connection Test ==========

    @app.route('/api/webdav-test', methods=['POST'])
    def webdav_test():
        """Test connectivity to a WebDAV URL from the server side."""
        data = request.get_json(silent=True) or {}
        url = data.get('url', '').rstrip('/')
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'}), 400

        import urllib.request
        import urllib.error
        import socket

        # Bypass system proxy to test the actual endpoint directly
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)

        try:
            req = urllib.request.Request(url, method='OPTIONS')
            resp = opener.open(req, timeout=5)
            return jsonify({
                'success': True,
                'status': resp.status,
                'url': url,
            })
        except urllib.error.HTTPError as e:
            # WebDAV may return 401/403/405 on OPTIONS — still reachable
            return jsonify({
                'success': True,
                'status': e.code,
                'url': url,
                'note': 'Server reachable (HTTP ' + str(e.code) + ')',
            })
        except (urllib.error.URLError, socket.timeout, ConnectionRefusedError,
                ConnectionResetError, ConnectionAbortedError, ConnectionError,
                OSError) as e:
            reason = '连接超时' if isinstance(e, socket.timeout) else str(e)
            return jsonify({
                'success': False,
                'error': reason,
                'url': url,
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e),
                'url': url,
            })

    # ========== WebDAV File Browser ==========

    @app.route('/api/webdav-files', methods=['GET'])
    def get_webdav_files():
        """List all files in the WebDAV root, grouped by folder."""
        items = scan_webdav_files(settings.webdav_root)
        return jsonify(items)

    @app.route('/api/webdav-open', methods=['POST'])
    def webdav_open():
        """Open the WebDAV root folder in Windows Explorer."""
        import subprocess
        folder = str(settings.webdav_root)
        try:
            subprocess.Popen(['explorer', folder])
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    # ========== File Manager: Download / Rename / Delete ==========

    @app.route('/api/webdav/download/<folder_name>/<path:filename>', methods=['GET'])
    def webdav_download(folder_name, filename):
        """Download a file from a WebDAV folder."""
        folder_name = unquote(folder_name)
        filename = unquote(filename)
        folder_path = (settings.webdav_root / folder_name).resolve()
        root = settings.webdav_root.resolve()
        if not str(folder_path).startswith(str(root)):
            return jsonify({'error': 'Invalid path'}), 400
        if not folder_path.exists() or not folder_path.is_dir():
            return jsonify({'error': 'Folder not found'}), 404
        return send_from_directory(str(folder_path), filename, as_attachment=True)

    @app.route('/api/webdav/rename/<folder_name>/<path:filename>', methods=['POST'])
    def webdav_rename(folder_name, filename):
        """Rename a file in a WebDAV folder."""
        folder_name = unquote(folder_name)
        filename = unquote(filename)
        data = request.get_json()
        if not data or not data.get('new_filename'):
            return jsonify({'error': 'new_filename required'}), 400

        new_fn = data['new_filename']
        if '/' in new_fn or '\\' in new_fn:
            return jsonify({'error': 'Invalid filename'}), 400

        folder_path = (settings.webdav_root / folder_name).resolve()
        root = settings.webdav_root.resolve()
        if not str(folder_path).startswith(str(root)):
            return jsonify({'error': 'Invalid path'}), 400

        src = folder_path / filename
        dst = folder_path / new_fn
        if not src.exists():
            return jsonify({'error': 'File not found'}), 404
        if dst.exists():
            return jsonify({'error': '目标文件已存在'}), 409

        src.rename(dst)
        return jsonify({'success': True, 'new_filename': new_fn})

    @app.route('/api/output/files/<path:filename>', methods=['DELETE'])
    def delete_output_file(filename):
        """Delete a single file from the output directory."""
        filename = unquote(filename)
        output_dir = Path(settings.output_dir).resolve()
        file_path = (output_dir / filename).resolve()
        if not str(file_path).startswith(str(output_dir)):
            return jsonify({'error': 'Invalid path'}), 400
        if not file_path.exists() or not file_path.is_file():
            return jsonify({'error': 'File not found'}), 404
        file_path.unlink()
        return jsonify({'success': True})

    # ========== Error Handlers ==========

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(500)
    def server_error(e):
        logger.exception('Internal server error')
        return jsonify({'error': 'Internal server error'}), 500

    return app
