"""Configuration management - persisted as JSON files."""

import json
import os
import shutil
from pathlib import Path

DEFAULT_SETTINGS = {
    "storage_path": "",
    "web_host": "0.0.0.0",
    "web_port": 5099,
    "webdav_host": "0.0.0.0",
    "webdav_port": 5001,
    "auto_sort": True,
    "poll_interval": 10,
    "fuzzy_match_threshold": 0.6,
}

DEFAULT_PLUGIN_TABLE = [
    {"id": 1, "name": "Apple File Conduit 2", "keyword": "applefileconduit", "remark": "文件传输"},
    {"id": 2, "name": "Filza File Manager", "keyword": "filza", "remark": "文件管理器"},
    {"id": 3, "name": "iCleaner Pro", "keyword": "icleaner", "remark": "清理工具"},
    {"id": 4, "name": "NewTerm 2", "keyword": "newterm", "remark": "终端模拟器"},
    {"id": 5, "name": "Cydia Installer", "keyword": "cydia", "remark": "包管理器"},
    {"id": 6, "name": "Sileo", "keyword": "sileo", "remark": "包管理器"},
    {"id": 7, "name": "Zebra", "keyword": "zebra", "remark": "包管理器"},
    {"id": 8, "name": "RocketBootstrap", "keyword": "rocketbootstrap", "remark": "依赖库"},
    {"id": 9, "name": "PreferenceLoader", "keyword": "preferenceloader", "remark": "依赖库"},
    {"id": 10, "name": "AppSync Unified", "keyword": "appsync", "remark": "签名补丁"},
]


class Settings:
    """Manages application settings stored in a JSON file."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_file = self.data_dir / 'settings.json'
        self._settings = dict(DEFAULT_SETTINGS)
        self._load()

    def _load(self):
        if self.settings_file.exists():
            try:
                data = json.loads(self.settings_file.read_text(encoding='utf-8'))
                self._settings.update(data)
            except (json.JSONDecodeError, OSError):
                pass
        self._save()

    def _save(self):
        self.settings_file.write_text(
            json.dumps(self._settings, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def get(self, key, default=None):
        return self._settings.get(key, default)

    def set(self, key, value):
        self._settings[key] = value
        self._save()

    def get_all(self):
        return dict(self._settings)

    def update(self, data):
        self._settings.update(data)
        self._save()

    @property
    def storage_path(self):
        path = self._settings.get('storage_path', '')
        if path:
            return Path(path)
        return self.data_dir

    @property
    def webdav_root(self):
        return self.storage_path / 'webdav'

    @property
    def output_dir(self):
        return self.storage_path / 'output'

    def ensure_dirs(self):
        """Create all required directories."""
        self.webdav_root.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return {
            'webdav': str(self.webdav_root),
            'output': str(self.output_dir),
            'data': str(self.data_dir),
        }


class PluginTable:
    """Manages the plugin table stored in a JSON file."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.table_file = self.data_dir / 'plugin_table.json'
        self._entries = []
        self._next_id = 1
        self._load()

    def _load(self):
        if self.table_file.exists():
            try:
                data = json.loads(self.table_file.read_text(encoding='utf-8'))
                self._entries = data.get('entries', [])
                self._next_id = data.get('next_id', 1)
            except (json.JSONDecodeError, OSError):
                self._entries = list(DEFAULT_PLUGIN_TABLE)
                self._next_id = max(e['id'] for e in self._entries) + 1
        else:
            self._entries = list(DEFAULT_PLUGIN_TABLE)
            self._next_id = max(e['id'] for e in self._entries) + 1
            self._save()

    def _save(self):
        self.table_file.write_text(
            json.dumps({'entries': self._entries, 'next_id': self._next_id}, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def get_all(self):
        return list(self._entries)

    def get(self, entry_id):
        for e in self._entries:
            if e['id'] == entry_id:
                return dict(e)
        return None

    def add(self, name, keyword='', remark=''):
        entry = {
            'id': self._next_id,
            'name': name,
            'keyword': keyword or name.lower().replace(' ', ''),
            'remark': remark,
        }
        self._next_id += 1
        self._entries.append(entry)
        self._save()
        return entry

    def update(self, entry_id, **kwargs):
        for i, e in enumerate(self._entries):
            if e['id'] == entry_id:
                for key, value in kwargs.items():
                    if value is not None and key in e:
                        e[key] = value
                self._entries[i] = e
                self._save()
                return dict(e)
        return None

    def delete(self, entry_id):
        self._entries = [e for e in self._entries if e['id'] != entry_id]
        self._save()
        return True
