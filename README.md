# IOS_Plugin - iOS 越狱插件管理工具

通过 WebDAV 快捷传输和管理 iOS 越狱插件（`.deb`），支持自动识别、版本比对、分类归档，提供可视化操作面板。

## 功能特点

### 📦 插件表管理
- 管理插件识别库：**插件名称**、**关键词**、**备注**
- 支持增删改查，关键词用于文件名模糊匹配
- 内置 10 个常用越狱插件（Apple File Conduit 2、Filza、iCleaner Pro 等）
- 可重置为默认列表

### 🔍 智能匹配排序
- 多策略文件名匹配（包名 → 名称 → 关键词 → 模糊匹配）
- 自定义模糊匹配阈值（0-1 可调）
- 上传后自动或手动触发排序

### 📋 Debian 版本对比
- 完整实现 Debian 版本号比较算法
- 支持 epoch、upstream、revision 三段式版本
- 支持 `~`（tilde）预发布版本号
- 更新时自动保留最新版本

### 🌐 WebDAV 协议支持
- 内置 WebDAV 服务器（端口 5001），支持任意 WebDAV 客户端
- 手机上可直接通过 WebDAV 访问和上传 deb 文件
- 页面上也支持拖拽上传

### 🖥️ Web 管理面板
- 深色圆角主题，Nunito 字体
- 5 个功能标签页：仪表盘、插件表、文件夹、输出目录、设置
- 实时仪表盘统计
- 文件名匹配测试工具

### 📁 文件夹管理
- 自动按日期（MMDD）命名文件夹
- 文件夹内文件浏览与删除
- 侧栏 WebDAV 地址显示，方便移动端访问

### ⚙️ 可自定义存储
- 可自定义 WebDAV 和输出目录的存储路径
- 端口可配置（Web 面板 + WebDAV）

---

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动

```bash
python main.py
```

可选参数：

| 参数 | 说明 |
|------|------|
| `--port PORT` | Web 管理面板端口（默认 5000） |
| `--webdav-port PORT` | WebDAV 端口（默认 5001） |
| `--no-browser` | 不自动打开浏览器 |
| `--no-webdav` | 不启动 WebDAV 服务 |

启动后终端会显示访问地址：

```
Web UI:     http://localhost:5000
WebDAV:     http://localhost:5001
局域网 Web: http://<本机IP>:5000
局域网 DAV: http://<本机IP>:5001
```

### 构建可执行文件

```bash
python build_exe.py
```

生成 `dist/DebManager.exe`，无需 Python 环境即可运行。

---

## 使用流程

### 基本工作流

```
手机上传 deb → 日期文件夹 → 排序匹配 → 输出目录（按插件归档）
```

1. **新建文件夹** — 在「文件夹」页新建日期文件夹（自动以 MMDD 命名）
2. **上传 deb** — 通过 WebDAV（手机端）或页面拖拽上传 deb 文件
3. **排序匹配** — 点击「排序匹配」，系统自动识别插件并归档到 output 目录
4. **查看结果** — 在「输出目录」页查看已分类的插件

### 插件表配置

在「插件表」页面管理所有可识别的插件：

- **添加** — 输入插件名称，关键词自动生成（名称去空格小写），可加备注
- **编辑** — 修改名称、关键词或备注
- **删除** — 移除不需要的插件
- **测试匹配** — 输入 deb 文件名测试能否正确匹配

### 匹配规则

系统按以下策略依次尝试匹配：

1. **Control 包名** — 解析 deb 的 `control` 文件，匹配 Package 字段
2. **Control 名称** — 匹配 Name 字段
3. **关键词** — 从文件名提取名称后匹配插件表关键词
4. **模糊匹配** — Python `difflib.get_close_matches()`，阈值可调（默认 0.6）

---

## 项目结构

```
IOS_Plugin/
├── main.py                 # 入口，启动 Flask + WebDAV
├── build_exe.py            # PyInstaller 打包脚本
├── requirements.txt        # 依赖
├── app/
│   ├── __init__.py
│   ├── config.py           # 设置 & 插件表 JSON 持久化
│   ├── server.py           # Flask Web 服务 & REST API
│   ├── plugin_manager.py   # 核心：匹配、排序、版本比较
│   └── deb_parser.py       # .deb ar 解析器
├── templates/
│   └── index.html          # SPA 前端（Bootstrap 5 深色主题）
├── data/                   # 默认数据目录
│   ├── settings.json       # 应用设置
│   ├── plugin_table.json   # 插件表数据
│   ├── webdav/             # WebDAV 文件存储
│   └── output/             # 排序后的插件输出
└── static/                 # 静态文件（可选）
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings` | 获取设置 |
| PUT | `/api/settings` | 更新设置 |
| GET | `/api/plugins` | 获取插件列表 |
| POST | `/api/plugins` | 添加插件 |
| PUT | `/api/plugins/:id` | 更新插件 |
| DELETE | `/api/plugins/:id` | 删除插件 |
| POST | `/api/plugins/reset` | 重置为默认插件表 |
| GET | `/api/folders` | 列出日期文件夹 |
| POST | `/api/folders` | 新建文件夹 |
| GET | `/api/folders/:name` | 查看文件夹内容 |
| POST | `/api/folders/:name/sort` | 排序匹配文件夹内插件 |
| DELETE | `/api/folders/:name` | 删除文件夹 |
| DELETE | `/api/folders/:name/files/:file` | 删除文件 |
| POST | `/api/upload/:name` | 上传 deb 文件 |
| GET | `/api/output` | 获取输出目录概况 |
| POST | `/api/output/clear` | 清空输出目录 |
| POST | `/api/output/rescan` | 重新扫描所有文件夹 |
| POST | `/api/match-test` | 测试文件名匹配 |
| GET | `/api/info` | 服务器信息 |

---

## 技术栈

- **后端**: Python 3, Flask, Waitress
- **WebDAV**: wsgidav, Cheroot
- **前端**: Bootstrap 5.3 (Dark), Bootstrap Icons, Nunito 字体
- **打包**: PyInstaller
- **版本算法**: 自实现 Debian 版本比较
- **解析器**: 自实现 ar 归档解析（.deb 格式）
