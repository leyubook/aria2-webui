# Aria2 Plus 全栈可视化管理中心

这一版模板包含：

- FastAPI 后端：`server/main.py`
- 原生 HTML/CSS/JS 前端：`public/index.html`、`public/app.css`、`public/app.js`
- 启动脚本：`scripts/start-server.bat`、`scripts/start-server.sh`
- Aria2 完成回调脚本：`scripts/aria2-hook.bat`、`scripts/aria2-hook.sh`

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

要求：

- Python 3.10+
- 已安装并配置好 `aria2c`
- 已安装并配置好 `rclone`

## 2. 启动 Web UI + API Server

Windows:

```bat
scripts\start-server.bat
```

Linux:

```bash
chmod +x scripts/start-server.sh scripts/aria2-hook.sh
./scripts/start-server.sh
```

默认访问地址：

```text
http://127.0.0.1:8080
```

实际绑定地址以 `data/settings.json` 的 `api_host` / `api_port` 为准。

## 3. Aria2 RPC 启动示例

Windows:

```bat
aria2c --enable-rpc=true --rpc-listen-all=true --rpc-allow-origin-all=true --rpc-listen-port=6800 --max-concurrent-downloads=1 --on-download-complete=scripts\aria2-hook.bat
```

Linux:

```bash
aria2c --enable-rpc=true --rpc-listen-all=true --rpc-allow-origin-all=true --rpc-listen-port=6800 --max-concurrent-downloads=1 --on-download-complete=./scripts/aria2-hook.sh
```

如果启用了 RPC Secret，请在网页“系统设置”里同步填写 `aria2_rpc_secret`。

## 4. Rclone / WebDAV 说明

- 在 `data/settings.json` 或网页“系统设置”中配置：
  - `rclone_binary`
  - `rclone_remote`
  - `rclone_remote_path`
  - `download_dir`
- `rclone_remote` 示例：`webdav:`
- `rclone_remote_path` 示例：`downloads`

后端提供的核心接口：

- `POST /api/webdav/scan`
- `POST /api/webdav/verify-md5`
- `POST /api/tasks/add`
- `POST /api/tasks/{gid}/retry-upload`
- `POST /api/aria2/hook`

## 5. Hook Token

`scripts/aria2-hook.bat` 和 `scripts/aria2-hook.sh` 默认使用：

```text
change-me-hook-token
```

请确保它与 `data/settings.json` 中的 `hook_token` 一致。

## 6. 当前模板已覆盖的业务流程

- WebDAV 扫描并展示远端文件列表
- 基于文件名的自动去重拦截
- 手动触发远端 / 本地刷新
- 任务排队、下载、搬运、失败重试的可视化状态
- `rclone md5sum` 的深层校验按钮
- `aria2 --on-download-complete` 回调后自动执行 `rclone move`
