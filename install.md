# Aria2 Plus 部署教程

本文档提供从零开始部署 Aria2 Plus 的详细步骤，分别覆盖 Debian 13 和 Windows 10/11。

---

## 目录

- [Debian 13 部署](#debian-13-部署)
  - [1. 系统更新](#1-系统更新)
  - [2. 安装依赖](#2-安装依赖)
  - [3. 获取项目代码](#3-获取项目代码)
  - [4. 安装 Python 依赖](#4-安装-python-依赖)
  - [5. 配置 Rclone](#5-配置-rclone)
  - [6. 修改项目配置](#6-修改项目配置)
  - [7. 启动 API Server](#7-启动-api-server)
  - [8. 启动 Aria2](#8-启动-aria2)
  - [9. 验收测试](#9-验收测试)
  - [10. 创建 systemd 服务（可选）](#10-创建-systemd-服务可选)
- [Windows 10/11 部署](#windows-1011-部署)
  - [1. 安装依赖](#1-安装依赖-1)
  - [2. 获取项目代码](#2-获取项目代码)
  - [3. 安装 Python 依赖](#3-安装-python-依赖)
  - [4. 配置 Rclone](#4-配置-rclone)
  - [5. 修改项目配置](#5-修改项目配置)
  - [6. 启动 API Server](#6-启动-api-server)
  - [7. 启动 Aria2](#7-启动-aria2)
  - [8. 验收测试](#8-验收测试)
- [安全配置](#安全配置)
- [故障排查速查表](#故障排查速查表)

---

## Debian 13 部署

### 1. 系统更新

```bash
sudo apt update && sudo apt upgrade -y
```

### 2. 安装依赖

```bash
# Python 3 及 pip
sudo apt install -y python3 python3-pip python3-venv

# Aria2
sudo apt install -y aria2

# Rclone
sudo apt install -y rclone

# curl 和 jq（hook 脚本需要）
sudo apt install -y curl jq
```

验证所有工具都已安装：

```bash
python3 --version    # 应 >= 3.10
aria2c --version
rclone version
curl --version
jq --version
```

### 3. 获取项目代码

```bash
# 方式一：git clone（推荐）
sudo apt install -y git
cd /opt
git clone <你的仓库地址> aria2-plus
cd aria2-plus

# 方式二：手动上传
# 将项目文件上传到 /opt/aria2-plus/
```

### 4. 安装 Python 依赖

推荐使用虚拟环境：

```bash
cd /opt/aria2-plus
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如果不使用虚拟环境：

```bash
pip3 install -r requirements.txt
```

### 5. 配置 Rclone

这一步非常关键，项目依赖 Rclone 来访问你的 WebDAV 网盘。

```bash
rclone config
```

按提示操作：

1. 选择 `n`（New remote）
2. 输入名称，比如 `webdav`
3. 选择存储类型（Storage），输入对应编号（通常是 `webdav` 或 `http`）
4. 输入 WebDAV 的 URL、用户名、密码
5. 确认保存

验证：

```bash
rclone ls webdav:
```

如果能看到远端文件列表，说明配置正确。

### 6. 修改项目配置

编辑配置文件：

```bash
nano data/settings.json
```

**必须修改的字段**：

| 字段 | 说明 | 示例 |
|------|------|------|
| `rclone_remote` | 你在 `rclone config` 中创建的 remote 名称，必须带冒号 | `webdav:` |
| `rclone_remote_path` | WebDAV 下的目标子目录 | `downloads` |
| `download_dir` | 本地下载目录，必须与 Aria2 的 `--dir` 一致 | `downloads` |
| `hook_token` | 回调保护 Token，建议改为一个随机字符串 | `my-super-secret-hook-token` |

**可选修改的字段**：

| 字段 | 说明 | 默认值 | 建议 |
|------|------|--------|------|
| `aria2_rpc_secret` | Aria2 RPC 密码 | 空 | 如果 Aria2 启用了 `--rpc-secret`，填写对应值 |
| `api_token` | API 写操作认证 Token | 空 | 建议设置，防止未授权访问 |
| `api_host` | API Server 监听地址 | `127.0.0.1` | 如果需要局域网访问，改为 `0.0.0.0` |
| `api_port` | API Server 监听端口 | `8080` | 可改为其他端口 |

示例配置（假设 WebDAV remote 名为 `webdav`）：

```json
{
  "aria2_rpc_url": "http://127.0.0.1:6800/jsonrpc",
  "aria2_rpc_secret": "",
  "rclone_binary": "rclone",
  "rclone_remote": "webdav:",
  "rclone_remote_path": "downloads",
  "download_dir": "downloads",
  "webdav_scan_depth": 5,
  "webdav_scan_ttl_seconds": 90,
  "aria2_poll_interval_seconds": 3,
  "api_host": "127.0.0.1",
  "api_port": 8080,
  "hook_token": "my-super-secret-hook-token",
  "api_token": ""
}
```

### 7. 启动 API Server

```bash
cd /opt/aria2-plus

# 如果使用虚拟环境
source .venv/bin/activate

# 赋予脚本执行权限
chmod +x scripts/start-server.sh scripts/aria2-hook.sh

# 启动
./scripts/start-server.sh
```

或者手动启动：

```bash
python3 -m server.run
```

开发模式（自动热重载）：

```bash
ARIA2_PLUS_RELOAD=1 python3 -m server.run
```

验证 API Server 是否正常：

```bash
curl http://127.0.0.1:8080/api/dashboard
```

应返回 JSON 数据，不应报 404。

### 8. 启动 Aria2

打开**另一个终端**，执行：

```bash
export ARIA2_PLUS_HOOK_URL="http://127.0.0.1:8080/api/aria2/hook"
export ARIA2_PLUS_HOOK_TOKEN="my-super-secret-hook-token"

aria2c \
  --enable-rpc=true \
  --rpc-listen-all=true \
  --rpc-allow-origin-all=true \
  --rpc-listen-port=6800 \
  --max-concurrent-downloads=1 \
  --dir="$(pwd)/downloads" \
  --on-download-complete="$(pwd)/scripts/aria2-hook.sh"
```

如果启用了 RPC Secret：

```bash
export ARIA2_PLUS_HOOK_URL="http://127.0.0.1:8080/api/aria2/hook"
export ARIA2_PLUS_HOOK_TOKEN="my-super-secret-hook-token"

aria2c \
  --enable-rpc=true \
  --rpc-listen-all=true \
  --rpc-allow-origin-all=true \
  --rpc-listen-port=6800 \
  --rpc-secret="your-rpc-secret" \
  --max-concurrent-downloads=1 \
  --dir="$(pwd)/downloads" \
  --on-download-complete="$(pwd)/scripts/aria2-hook.sh"
```

### 9. 验收测试

1. 浏览器打开 `http://127.0.0.1:8080`
2. 确认页面能看到三栏布局
3. 点击"扫描 WebDAV"，确认能列出远端文件
4. 点击"刷新本地"，确认能看到本地下载目录
5. 添加一个测试磁力链接，确认任务正常下发
6. 等待下载完成，确认自动搬运到 WebDAV
7. 检查"系统设置"页面，确认 Aria2 RPC 显示在线

API 验证：

```bash
# 扫描 WebDAV
curl -X POST http://127.0.0.1:8080/api/webdav/scan

# 查看仪表盘
curl http://127.0.0.1:8080/api/dashboard | python3 -m json.tool
```

### 10. 创建 systemd 服务（可选）

创建 API Server 服务文件：

```bash
sudo nano /etc/systemd/system/aria2-plus.service
```

写入（根据实际路径和用户修改）：

```ini
[Unit]
Description=Aria2 Plus API Server
After=network.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/opt/aria2-plus
ExecStart=/opt/aria2-plus/.venv/bin/python -m server.run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

创建 Aria2 服务文件：

```bash
sudo nano /etc/systemd/system/aria2.service
```

```ini
[Unit]
Description=Aria2 RPC Server
After=network.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/opt/aria2-plus
Environment=ARIA2_PLUS_HOOK_URL=http://127.0.0.1:8080/api/aria2/hook
Environment=ARIA2_PLUS_HOOK_TOKEN=my-super-secret-hook-token
ExecStart=/usr/bin/aria2c \
  --enable-rpc=true \
  --rpc-listen-all=true \
  --rpc-allow-origin-all=true \
  --rpc-listen-port=6800 \
  --max-concurrent-downloads=1 \
  --dir=/opt/aria2-plus/downloads \
  --on-download-complete=/opt/aria2-plus/scripts/aria2-hook.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable aria2-plus aria2
sudo systemctl start aria2-plus aria2

# 查看状态
sudo systemctl status aria2-plus
sudo systemctl status aria2

# 查看日志
sudo journalctl -u aria2-plus -f
sudo journalctl -u aria2 -f
```

---

## Windows 10/11 部署

### 1. 安装依赖

#### Python 3.10+

下载地址：https://www.python.org/downloads/

安装时勾选 **"Add Python to PATH"**。

验证：

```cmd
python --version
:: 应 >= 3.10
```

如果 `python` 命令不可用，可以尝试：

```cmd
py -3 --version
```

#### aria2

下载地址：https://github.com/aria2/aria2/releases

1. 下载 `aria2-<version>-win-64bit-build1.zip`
2. 解压到比如 `C:\aria2`
3. 将 `C:\aria2` 添加到系统环境变量 `PATH`

验证：

```cmd
aria2c --version
```

#### rclone

下载地址：https://rclone.org/downloads/

1. 下载 Windows 版 zip
2. 解压到比如 `C:\rclone`
3. 将 `C:\rclone` 添加到系统环境变量 `PATH`

验证：

```cmd
rclone version
```

#### curl

Windows 10/11 通常自带 curl。验证：

```cmd
curl --version
```

若不可用，可从 https://curl.se/windows/ 下载。

### 2. 获取项目代码

```cmd
:: 方式一：git clone（需安装 git）
git clone <你的仓库地址> D:\aria2
cd D:\aria2

:: 方式二：直接下载 ZIP 并解压到 D:\aria2
```

### 3. 安装 Python 依赖

```cmd
cd D:\aria2
pip install -r requirements.txt
```

如果使用了虚拟环境：

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 4. 配置 Rclone

```cmd
rclone config
```

按提示操作：

1. 选择 `n`（New remote）
2. 输入名称，比如 `webdav`
3. 选择存储类型，输入对应编号
4. 输入 WebDAV 的 URL、用户名、密码
5. 对于密码输入，选择 `y` 然后输入密码

验证：

```cmd
rclone ls webdav:
```

看到远端文件列表即配置成功。

### 5. 修改项目配置

用记事本或 VS Code 编辑：

```text
D:\aria2\data\settings.json
```

**必须修改的字段**：

| 字段 | 说明 | 示例 |
|------|------|------|
| `rclone_remote` | 你在 `rclone config` 中创建的 remote 名称，必须带冒号 | `webdav:` |
| `rclone_remote_path` | WebDAV 下的目标子目录 | `downloads` |
| `download_dir` | 本地下载目录，必须与 Aria2 的 `--dir` 一致 | `downloads` |
| `hook_token` | 回调保护 Token，建议改为随机字符串 | `my-super-secret-hook-token` |

**可选修改的字段**：

| 字段 | 说明 | 默认值 | 建议 |
|------|------|--------|------|
| `aria2_rpc_secret` | Aria2 RPC 密码 | 空 | 如果 Aria2 启用了 `--rpc-secret`，填写对应值 |
| `api_token` | API 写操作认证 Token | 空 | 建议设置 |
| `api_host` | API Server 监听地址 | `127.0.0.1` | 局域网访问改为 `0.0.0.0` |
| `api_port` | API Server 监听端口 | `8080` | 可改为其他端口 |

### 6. 启动 API Server

打开**第一个终端**（cmd 或 PowerShell）：

```cmd
cd D:\aria2
scripts\start-server.bat
```

或者手动启动：

```cmd
cd D:\aria2
python -m server.run
```

开发模式：

```cmd
set ARIA2_PLUS_RELOAD=1
python -m server.run
```

验证 API Server 是否正常：

```cmd
curl http://127.0.0.1:8080/api/dashboard
```

应返回 JSON 数据。

### 7. 启动 Aria2

打开**第二个终端**：

```cmd
cd D:\aria2

set ARIA2_PLUS_HOOK_URL=http://127.0.0.1:8080/api/aria2/hook
set ARIA2_PLUS_HOOK_TOKEN=my-super-secret-hook-token

aria2c ^
  --enable-rpc=true ^
  --rpc-listen-all=true ^
  --rpc-allow-origin-all=true ^
  --rpc-listen-port=6800 ^
  --max-concurrent-downloads=1 ^
  --dir=%cd%\downloads ^
  --on-download-complete=%cd%\scripts\aria2-hook.bat
```

如果启用了 RPC Secret：

```cmd
cd D:\aria2

set ARIA2_PLUS_HOOK_URL=http://127.0.0.1:8080/api/aria2/hook
set ARIA2_PLUS_HOOK_TOKEN=my-super-secret-hook-token

aria2c ^
  --enable-rpc=true ^
  --rpc-listen-all=true ^
  --rpc-allow-origin-all=true ^
  --rpc-listen-port=6800 ^
  --rpc-secret=your-rpc-secret ^
  --max-concurrent-downloads=1 ^
  --dir=%cd%\downloads ^
  --on-download-complete=%cd%\scripts\aria2-hook.bat
```

### 8. 验收测试

1. 浏览器打开 `http://127.0.0.1:8080`
2. 确认页面能看到三栏布局
3. 点击"扫描 WebDAV"，确认能列出远端文件
4. 点击"刷新本地"，确认能看到本地下载目录
5. 添加一个测试磁力链接，确认任务正常下发
6. 等待下载完成，确认自动搬运到 WebDAV
7. 检查"系统设置"页面，确认 Aria2 RPC 显示在线

API 验证：

```cmd
:: 扫描 WebDAV
curl -X POST http://127.0.0.1:8080/api/webdav/scan

:: 查看仪表盘
curl http://127.0.0.1:8080/api/dashboard
```

---

## 安全配置

### 启用 API Token 认证

如果你想让 API 的写操作端点需要认证，在 `data/settings.json` 中设置 `api_token`：

```json
{
  "api_token": "your-secure-random-string"
}
```

设置后，所有写操作需要携带 `X-Api-Token` 请求头：

```bash
curl -X POST http://127.0.0.1:8080/api/webdav/scan \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: your-secure-random-string"
```

前端设置页保存 `api_token` 后会自动在后续请求中携带此 Token。

不设置或留空则不启用认证。只读端点和 WebSocket 不需要 Token。

### 修改 Hook Token

默认 `hook_token` 为 `change-me-hook-token`，强烈建议修改为随机字符串：

```json
{
  "hook_token": "a-long-random-string-here"
}
```

同时修改启动 Aria2 时的环境变量：

```bash
# Linux
export ARIA2_PLUS_HOOK_TOKEN="a-long-random-string-here"

# Windows
set ARIA2_PLUS_HOOK_TOKEN=a-long-random-string-here
```

### 局域网访问

如果需要从其他设备访问管理面板，修改 `api_host`：

```json
{
  "api_host": "0.0.0.0"
}
```

然后通过 `http://<服务器IP>:8080` 访问。

---

## 故障排查速查表

| 症状 | 检查项 |
|------|--------|
| 页面打不开 | `api_host` / `api_port` 是否正确；防火墙是否放行 |
| Aria2 显示离线 | Aria2 是否启动；`aria2_rpc_url` 是否正确；`aria2_rpc_secret` 是否匹配 |
| WebDAV 扫描失败 | `rclone config` 是否完成；`rclone_remote` 是否带冒号；`rclone ls webdav:` 是否正常 |
| 下载完了不搬运 | `--on-download-complete` 是否配置；`ARIA2_PLUS_HOOK_TOKEN` 是否与 `hook_token` 一致；脚本是否有执行权限 |
| 搬运失败 | `download_dir` 与 `--dir` 是否一致；本地文件是否存在；rclone 是否有写权限 |
| 去重不生效 | 远端扫描是否成功；文件名是否匹配；磁力链接是否含 `dn` 参数 |
| API 返回 401 | 是否设置了 `api_token`；请求是否携带 `X-Api-Token` 头 |
| 前端设置页密码字段显示空白 | 正常行为——敏感字段已遮蔽，留空表示保持不变 |
| 设置保存后密码丢失 | 正常行为——后端不会返回真实密码，`***` 值不会被写入配置文件 |