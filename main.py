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
logger = logging.getLogger('IOS_Plugin')


def load_ip_config(settings):
    """Load network config from IP.txt if it exists."""
    ip_file = Path(os.path.dirname(os.path.abspath(__file__))) / 'IP.txt'
    if not ip_file.exists():
        return
    overrides = {}
    for line in ip_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            key, val = line.split('=', 1)
            key = key.strip().upper()
            val = val.strip()
            if key == 'WEB_HOST':
                overrides['web_host'] = val
            elif key == 'WEB_PORT':
                overrides['web_port'] = int(val)
            elif key == 'WEBDAV_HOST':
                overrides['webdav_host'] = val
            elif key == 'WEBDAV_PORT':
                overrides['webdav_port'] = int(val)
    if overrides:
        settings.update(overrides)
        logger.info('Loaded config from IP.txt: %s', overrides)


def get_data_dir():
    """Determine the data directory for storing settings and plugin table."""
    # Use app directory by default (portable)
    app_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    data_dir = app_dir / 'data'
    return data_dir


def start_webdav(settings):
    """Start the WebDAV server in a background thread."""
    from wsgidav.wsgidav_app import WsgiDAVApp
    from wsgidav.fs_dav_provider import FilesystemProvider
    from cheroot import wsgi

    webdav_root = str(settings.webdav_root)
    host = settings.get('webdav_host', '0.0.0.0')
    port = settings.get('webdav_port', 5001)

    config = {
        'host': host,
        'port': port,
        'provider_mapping': {
            '/': FilesystemProvider(webdav_root),
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
    }

    # Suppress wsgidav logging
    logging.getLogger('wsgidav').setLevel(logging.WARNING)

    try:
        app = WsgiDAVApp(config)
        logger.info('WebDAV server started on http://%s:%s', host, port)
        logger.info('WebDAV root: %s', webdav_root)

        server = wsgi.Server(
            bind_addr=(config['host'], config['port']),
            wsgi_app=app,
            server_name=f'WsgiDAV/{port}',
            numthreads=10,
        )
        server.start()
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

    # Load IP.txt config (overrides defaults, overridden by CLI)
    load_ip_config(settings)

    # Override from command line
    if args.port:
        settings.set('web_port', args.port)
    if args.webdav_port:
        settings.set('webdav_port', args.webdav_port)

    # Ensure storage directories exist
    dirs = settings.ensure_dirs()
    logger.info('WebDAV root: %s', dirs['webdav'])
    logger.info('Output dir: %s', dirs['output'])

    web_port = settings.get('web_port', 5099)
    web_host = settings.get('web_host', '0.0.0.0')

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
    webdav_host = settings.get('webdav_host', '0.0.0.0')
    webdav_port = settings.get('webdav_port', 5001)
    print('\n' + '=' * 55)
    print('  IOS_Plugin - iOS插件管理工具')
    print('=' * 55)
    print(f'  Web UI:     http://{web_host}:{web_port}')
    print(f'  WebDAV:     http://{webdav_host}:{webdav_port}')
    print(f'  局域网 Web: http://{hostname}:{web_port}')
    print(f'  局域网 DAV: http://{hostname}:{webdav_port}')
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
        serve(app, host=web_host, port=web_port)
    except KeyboardInterrupt:
        print('\n服务已停止')
    except Exception as e:
        logger.error('Server error: %s', e)
        # Fallback to Flask dev server
        print('使用开发服务器启动...')
        app.run(host=web_host, port=web_port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
