"""Flask server - web UI and REST API for plugin management."""

import json
import os
import logging
import threading
from pathlib import Path
from datetime import datetime

from flask import (
    Flask, render_template, request, jsonify, send_from_directory,
    abort, Response, stream_with_context,
)

from .config import Settings, PluginTable
from .plugin_manager import (
    sort_folder, scan_folder, get_output_summary, get_date_folders,
    suggest_folder_name, match_plugin, parse_deb,
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

    # ========== Folder Management API ==========

    @app.route('/api/folders', methods=['GET'])
    def list_folders():
        folders = get_date_folders(settings.webdav_root)
        return jsonify(folders)

    @app.route('/api/folders', methods=['POST'])
    def create_folder():
        data = request.get_json(silent=True) or {}
        name = data.get('name', suggest_folder_name())
        folder_path = settings.webdav_root / name

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
        folder_path = settings.webdav_root / folder_name
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
        folder_path = settings.webdav_root / folder_name
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
        folder_path = settings.webdav_root / folder_name
        file_path = folder_path / filename
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
            return jsonify({'success': True})
        return jsonify({'error': 'File not found'}), 404

    @app.route('/api/folders/<folder_name>', methods=['DELETE'])
    def delete_folder(folder_name):
        folder_path = settings.webdav_root / folder_name
        if folder_path.exists():
            import shutil
            shutil.rmtree(str(folder_path))
            return jsonify({'success': True})
        return jsonify({'error': 'Folder not found'}), 404

    # ========== Upload API ==========

    @app.route('/api/upload/<folder_name>', methods=['POST'])
    def upload_file(folder_name):
        """Upload a deb file to a specific folder via web UI."""
        folder_path = settings.webdav_root / folder_name
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
        summary = get_output_summary(settings.output_dir, plugin_table)
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
        return jsonify({
            'webdav_port': settings.get('webdav_port', 5001),
            'web_port': settings.get('web_port', 5000),
            'storage_path': str(settings.storage_path),
            'webdav_root': str(settings.webdav_root),
            'output_dir': str(settings.output_dir),
            'hostname': os.environ.get('COMPUTERNAME', 'localhost'),
        })

    # ========== Error Handlers ==========

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(500)
    def server_error(e):
        logger.exception('Internal server error')
        return jsonify({'error': 'Internal server error'}), 500

    return app
