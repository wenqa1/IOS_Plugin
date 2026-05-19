"""IOS_Plugin - iOS WebDAV插件管理工具

功能：
  - 插件表管理
  - 自动匹配排序
  - 版本自动更新
  - WebDAV协议支持
  - 可视化操作面板
  - 可自定义存储位置

启动方式：
  python main.py [--port PORT] [--webdav-port PORT]
"""

import sys
import os
import argparse
import threading
import logging
import webbrowser
from pathlib import Path

# Ensure the app package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('debmanager')


def get_data_dir():
    """Determine the data directory for storing settings and plugin table."""
    # Use app directory by default (portable)
    app_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    data_dir = app_dir / 'data'
    return data_dir


def start_webdav(settings):
    """Start the WebDAV server in a background thread."""
    from wsgidav.wsgidav_app import WsgiDAVApp
    from wsgidav.fs_dav_provider import FilesystemDavProvider

    webdav_root = str(settings.webdav_root)
    port = settings.get('webdav_port', 5001)

    config = {
        'host': '0.0.0.0',
        'port': port,
        'provider_mapping': {
            '/': FilesystemDavProvider(webdav_root),
        },
        'verbose': 0,
        'logging': {
            'enable': False,
        },
        'simple_dc': {
            'user_mapping': {
                '*': True,  # Allow anonymous access
            }
        },
        'middleware_stack': None,  # Use default
    }

    # Suppress wsgidav logging
    logging.getLogger('wsgidav').setLevel(logging.WARNING)

    try:
        app = WsgiDAVApp(config)
        logger.info('WebDAV server started on http://0.0.0.0:%s', port)
        logger.info('WebDAV root: %s', webdav_root)

        # Run WebDAV in a way that supports keyboard interrupt
        from wsgidav.server.server_cli import _run
        _run(app, config)
    except Exception as e:
        logger.error('WebDAV server error: %s', e)


def main():
    parser = argparse.ArgumentParser(description='IOS_Plugin - iOS WebDAV插件管理工具')
    parser.add_argument('--port', type=int, default=None, help='Web UI port')
    parser.add_argument('--webdav-port', type=int, default=None, help='WebDAV port')
    parser.add_argument('--no-browser', action='store_true', help='Don\'t open browser')
    parser.add_argument('--no-webdav', action='store_true', help='Don\'t start WebDAV')
    args = parser.parse_args()

    # Initialize data directory and settings
    data_dir = get_data_dir()
    logger.info('Data directory: %s', data_dir)

    from app.config import Settings, PluginTable
    from app.server import create_app

    settings = Settings(data_dir)
    plugin_table = PluginTable(data_dir)

    # Override ports from command line
    if args.port:
        settings.set('web_port', args.port)
    if args.webdav_port:
        settings.set('webdav_port', args.webdav_port)

    # Ensure storage directories exist
    dirs = settings.ensure_dirs()
    logger.info('WebDAV root: %s', dirs['webdav'])
    logger.info('Output dir: %s', dirs['output'])

    web_port = settings.get('web_port', 5000)

    # Start WebDAV in background thread
    if not args.no_webdav:
        webdav_thread = threading.Thread(
            target=start_webdav,
            args=(settings,),
            daemon=True,
        )
        webdav_thread.start()
        logger.info('WebDAV thread started')
    else:
        logger.info('WebDAV disabled')

    # Create Flask app
    app = create_app(settings, plugin_table)

    # Print startup info
    hostname = os.environ.get('COMPUTERNAME', 'localhost')
    print('\n' + '=' * 55)
    print('  DebManager - iOS越狱插件管理工具')
    print('=' * 55)
    print(f'  Web UI:     http://localhost:{web_port}')
    print(f'  WebDAV:     http://localhost:{settings.get("webdav_port", 5001)}')
    print(f'  局域网 Web: http://{hostname}:{web_port}')
    print(f'  局域网 DAV: http://{hostname}:{settings.get("webdav_port", 5001)}')
    print(f'  存储路径:   {settings.storage_path}')
    print(f'  数据目录:   {data_dir}')
    print('=' * 55)
    print('  按 Ctrl+C 停止服务')
    print('=' * 55 + '\n')

    # Open browser
    if not args.no_browser:
        def open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open(f'http://localhost:{web_port}')
        threading.Thread(target=open_browser, daemon=True).start()

    # Run Flask with waitress (production WSGI server)
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=web_port)
    except KeyboardInterrupt:
        print('\n服务已停止')
    except Exception as e:
        logger.error('Server error: %s', e)
        # Fallback to Flask dev server
        print('使用开发服务器启动...')
        app.run(host='0.0.0.0', port=web_port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
