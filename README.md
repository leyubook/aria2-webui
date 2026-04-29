# Aria2 Plus 全栈可视化管理中心

一个面向 `Aria2 + WebDAV + Rclone` 的全栈可视化管理中心。

本项目目标不是做一个 AriaNg 风格的纯前端壳，而是提供一套可直接部署的 `Web UI + 本地 API Server` 一体化方案，覆盖以下完整链路：

1. 前端粘贴磁力链接 / 种子链接
2. 后端先扫描 WebDAV 远端文件，按文件名做去重
3. 若远端已存在，直接拦截并提示"网盘已存在，停止添加任务"
4. 若远端不存在，再调用 Aria2 RPC 添加下载任务
5. Aria2 下载完成后，通过 `--on-download-complete` 回调后端
6. 后端调用 `rclone move` 将下载产物搬运到 WebDAV
7. 上传失败时，前端提供红色"手动重试上传"按钮

这个仓库的当前实现由 Python/FastAPI 提供 API 和静态页面服务，前端为原生 HTML/CSS/JS，无 Node.js 构建依赖。

---

## 1. 当前项目实现了什么

### 1.1 Web 界面

- 顶部导航：
  - 智能监控
  - 任务面板
  - 网盘文件浏览
  - 系统设置
- 左侧：
  - 添加任务入口
  - 远程 WebDAV 文件树
  - 本地下载目录文件树
- 中部：
  - 作业队列与进度看板
  - Aria2 实时任务卡片
  - 下载中 / 排队中 / 搬运中 / 搬运失败 / 已完成状态
- 右侧：
  - 异常任务列表
  - Rclone / Aria2 日志控制台

### 1.2 后端能力

- `POST /api/webdav/scan`
  - 调用 `rclone ls`
  - 返回远端文件列表 JSON
- `POST /api/webdav/verify-md5`
  - 调用 `rclone md5sum`
  - 手动执行远端文件与本地同名文件的 MD5 深层校验
- `POST /api/tasks/add`
  - 智能去重后下发 Aria2
- `POST /api/tasks/{gid}/retry-upload`
  - 手动重试上传搬运
- `POST /api/aria2/hook`
  - 接收 Aria2 完成回调并触发 `rclone move`
- `GET /api/dashboard`
  - 返回前端仪表盘快照（敏感字段已遮蔽）
- `GET /api/settings`
  - 读取当前设置（敏感字段已遮蔽）
- `PUT /api/settings`
  - 保存设置到 `data/settings.json`（自动跳过遮蔽值）
- `WebSocket /ws/events`
  - 向前端推送状态和日志

### 1.3 去重与校验逻辑

- 自动去重：当前是"按文件名匹配"
- 深层校验：由前端按钮触发 `rclone md5sum`
- 对于无法从磁力链接中提取文件名的场景：
  - 必须填写"文件名提示"
  - 或者点击"忽略去重强制下发"

---

## 2. 目录结构

```text
aria2-plus/
├─ data/
│  └─ settings.json          # 持久化配置（已加入 .gitignore）
├─ public/
│  ├─ index.html             # 前端页面
│  ├─ app.css                # 页面样式
│  └─ app.js                 # 页面逻辑
├─ scripts/
│  ├─ start-server.bat       # Windows 启动 API Server
│  ├─ start-server.sh        # Linux 启动 API Server
│  ├─ aria2-hook.bat         # Windows Aria2 完成回调脚本
│  └─ aria2-hook.sh          # Linux Aria2 完成回调脚本
├─ server/
│  ├─ __init__.py
│  ├─ main.py                # FastAPI 主逻辑
│  └─ run.py                 # 按 settings.json 启动 Uvicorn
├─ requirements.txt
├─ .gitignore
├─ install.md                # 详细部署教程
└─ README.md
```

---

## 3. 技术架构

### 3.1 组件说明

- FastAPI：
  - 提供 REST API
  - 提供 WebSocket
  - 直接托管 `public/` 下的静态页面
- Aria2：
  - 负责下载任务执行
  - 通过 JSON-RPC 与后端交互
  - 通过 `--on-download-complete` 通知后端搬运
- Rclone：
  - 负责扫描 WebDAV
  - 负责 MD5 校验
  - 负责下载完成后的搬运上传
- 前端页面：
  - 不依赖 React / Vue / Node 构建
  - 直接由后端静态托管

### 3.2 状态存储说明

- `data/settings.json`：持久化配置（已加入 `.gitignore`，不会被 git 追踪）
- 任务面板中的运行态任务：当前保存在后端内存中
- 注意：
  - 重启 API Server 后，仪表盘的内存态任务历史会丢失
  - 但本地文件、远端文件、Aria2 当前真实任务状态仍可以重新拉取

### 3.3 当前实现的关键约束

- `download_dir` 必须与 Aria2 实际下载目录保持一致
- `hook_token` 必须与 Aria2 回调脚本中的 Token 一致
- 如果启用 `aria2_rpc_secret`，网页设置和 Aria2 启动参数必须一致
- `rclone_remote` 必须是你本机已经配置好的 remote 名称

---

## 4. 安全特性

### 4.1 API Token 认证

新增 `api_token` 配置项。当 `api_token` 不为空时，所有写操作端点需要携带 `X-Api-Token` 请求头：

- `PUT /api/settings`
- `POST /api/webdav/scan`
- `POST /api/webdav/verify-md5`
- `POST /api/local/refresh`
- `POST /api/tasks/add`
- `POST /api/tasks/{gid}/retry-upload`

只读端点（`GET /api/dashboard`、`GET /api/settings`、`GET /`、`WebSocket /ws/events`）和 Aria2 回调端点（`POST /api/aria2/hook`，已有独立 `hook_token` 校验）不需要 `api_token`。

留空则不启用认证。

### 4.2 敏感字段遮蔽

所有 API 响应中，以下字段自动替换为 `***`：

- `aria2_rpc_secret`
- `hook_token`
- `api_token`

前端设置页中，这些字段以 `password` 输入框呈现，显示为 `***` 时 placeholder 提示"已设置，留空保持不变"。提交时如果值仍为 `***`，后端自动保留原有值，不会误覆盖。

### 4.3 Hook 脚本安全

- Windows：使用 PowerShell `ConvertTo-Json` 安全构建 JSON，避免命令注入
- Linux：优先使用 `jq` 构建 JSON；回退时对 GID 做转义处理

### 4.4 配置文件保护

`data/settings.json` 已加入 `.gitignore`，避免密钥随仓库泄露。

---

## 5. 运行环境要求

### 5.1 必需依赖

- Python 3.10+
- `pip`
- `aria2c`
- `rclone`
- Windows 下建议系统自带 `curl` 或 PowerShell 5+
- Linux 下需要 `bash` + `curl`（推荐安装 `jq`）

### 5.2 Python 依赖

安装命令：

```bash
pip install -r requirements.txt
```

当前 Python 依赖：

- `fastapi`
- `uvicorn[standard]`

### 5.3 不需要的东西

本项目当前不需要：

- Node.js
- npm / pnpm / yarn
- 数据库
- Redis

---

## 6. 配置文件说明

配置文件路径：

```text
data/settings.json
```

默认内容：

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
  "hook_token": "change-me-hook-token",
  "api_token": ""
}
```

### 6.1 字段解释

| 字段 | 作用 | 示例 | 备注 |
| --- | --- | --- | --- |
| `aria2_rpc_url` | Aria2 JSON-RPC 地址 | `http://127.0.0.1:6800/jsonrpc` | 必须和 Aria2 启动参数一致 |
| `aria2_rpc_secret` | Aria2 RPC 密码 | `my-secret` | 如果 Aria2 开启了 `--rpc-secret`，这里必须同步填写 |
| `rclone_binary` | Rclone 可执行文件名或绝对路径 | `rclone` | 也可以是 `/usr/bin/rclone` |
| `rclone_remote` | 已配置好的 Rclone remote 名称 | `webdav:` | 必须带冒号 |
| `rclone_remote_path` | WebDAV 下的目标子目录 | `downloads` | 最终路径形如 `webdav:downloads` |
| `download_dir` | 本地下载目录 | `downloads` | 必须与 Aria2 下载目录一致 |
| `webdav_scan_depth` | `rclone ls` 扫描深度 | `5` | 用于远端文件探测 |
| `webdav_scan_ttl_seconds` | 远端扫描缓存秒数 | `90` | 避免频繁重复扫描 |
| `aria2_poll_interval_seconds` | Aria2 轮询间隔（秒） | `3` | 越小越实时，越大越省资源 |
| `api_host` | API Server 监听地址 | `127.0.0.1` | `server/run.py` 会读取它 |
| `api_port` | API Server 监听端口 | `8080` | 修改后需要重启服务 |
| `hook_token` | Aria2 回调保护 Token | `change-me-hook-token` | 回调脚本必须同步 |
| `api_token` | API 写操作认证 Token | `my-api-token` | 留空则不启用认证 |

---

## 7. Rclone / WebDAV 配置要求

本项目不会帮你自动创建 Rclone remote。

你必须先在服务器或本机上完成：

```bash
rclone config
```

至少要确保：

1. 已创建一个 remote，比如 `webdav`
2. 该 remote 可以正常访问你的 WebDAV 网盘
3. 执行下面命令能成功列出文件：

```bash
rclone ls webdav:
```

如果你的远端目标目录想放在 `webdav` 根下的 `downloads/`，则设置：

```json
{
  "rclone_remote": "webdav:",
  "rclone_remote_path": "downloads"
}
```

最终后端会操作的远端路径形如：

```text
webdav:downloads
```

---

## 8. 部署前必须确认的耦合点

这部分非常重要。无论是你本人部署，还是丢给代码代理部署，都必须优先检查这几个耦合点。

### 8.1 下载目录耦合

后端依赖 `download_dir` 来：

- 扫描本地文件
- 计算本地 MD5
- 找到下载完成后的上传源文件
- 删除搬运完成后的本地文件

所以：

- `data/settings.json` 的 `download_dir`
- Aria2 的 `--dir`

必须是同一个目录。

### 8.2 回调 Token 耦合

这三个地方必须一致：

- `data/settings.json` 里的 `hook_token`
- `ARIA2_PLUS_HOOK_TOKEN` 环境变量
- `scripts/aria2-hook.bat` / `scripts/aria2-hook.sh` 运行时实际传入的 Token

### 8.3 RPC Secret 耦合

这两个地方必须一致：

- Aria2 启动参数里的 `--rpc-secret=...`
- `data/settings.json` 里的 `aria2_rpc_secret`

### 8.4 远端名称耦合

`settings.json` 里的：

- `rclone_binary`
- `rclone_remote`
- `rclone_remote_path`

必须与你机器上真实存在的 Rclone 配置一致。

---

## 9. 性能与稳定性优化

### 9.1 Snapshot 广播节流

后端对 WebSocket 推送做了节流：1 秒内最多广播一次完整快照。轮询周期结束时补发一次 pending 的快照。

### 9.2 Aria2 轮询指数退避

当 Aria2 离线时，轮询间隔按 `base × 2^n` 指数退避，最大 60 秒。恢复在线后立即重置为正常间隔。

### 9.3 优雅关闭

服务停止时，会取消 Aria2 轮询任务和所有进行中的上传任务。

### 9.4 前端智能轮询

WebSocket 连接成功后自动停止 8 秒定时轮询；断开后自动恢复轮询，确保数据不丢失。

---

## 10. 开发模式

启动 API Server 时，通过环境变量控制 Uvicorn 热重载：

```bash
# 开发模式（自动热重载）
ARIA2_PLUS_RELOAD=1 python -m server.run

# 生产模式（默认，不热重载）
python -m server.run
```

---

## 11. API 一览

### 11.1 仪表盘

```http
GET /api/dashboard                # 只读，无需 token
```

### 11.2 设置

```http
GET /api/settings                 # 只读，无需 token（敏感字段已遮蔽）
PUT /api/settings                 # 需要 X-Api-Token（如果启用了 api_token）
```

### 11.3 WebDAV

```http
POST /api/webdav/scan             # 需要 X-Api-Token
POST /api/webdav/verify-md5       # 需要 X-Api-Token
```

### 11.4 本地目录

```http
POST /api/local/refresh           # 需要 X-Api-Token
```

### 11.5 任务

```http
POST /api/tasks/add               # 需要 X-Api-Token
POST /api/tasks/{gid}/retry-upload # 需要 X-Api-Token
```

### 11.6 Aria2 回调

```http
POST /api/aria2/hook              # 用 hook_token 独立校验，不需要 X-Api-Token
```

### 11.7 WebSocket

```http
GET /ws/events                     # 只读，无需 token
```

---

## 12. 常见问题排查

### 12.1 页面能打开，但 Aria2 显示离线

检查：

- Aria2 是否真的已启动
- `aria2_rpc_url` 是否正确
- `aria2_rpc_secret` 是否匹配
- 6800 端口是否被防火墙拦截

### 12.2 页面能扫描本地，但扫描 WebDAV 失败

检查：

- `rclone` 命令是否存在
- `rclone_remote` 是否正确
- `rclone_remote_path` 是否正确
- `rclone ls webdav:` 是否能手工跑通

### 12.3 任务下载完成了，但没有自动搬运

优先检查：

- `--on-download-complete` 是否真的配置了 hook 脚本
- `ARIA2_PLUS_HOOK_URL` 是否正确
- `ARIA2_PLUS_HOOK_TOKEN` 是否与 `settings.json` 一致
- API Server 是否正在运行
- 日志控制台里是否出现 `/api/aria2/hook` 相关日志

### 12.4 自动搬运失败，点击重试仍失败

检查：

- `download_dir` 与 `--dir` 是否一致
- 本地文件是否确实存在
- Rclone 对远端目录是否有写权限
- Rclone 是否能执行 `move`

### 12.5 去重没有拦住重复任务

检查：

- 远端扫描是否成功
- 远端文件名是否真的与待下载文件名一致
- 磁力链接是否能提取到 `dn`
- 若不能提取，是否已填写"文件名提示"

### 12.6 API 返回 401

如果你配置了 `api_token`，所有写操作需要携带 HTTP 请求头：

```
X-Api-Token: your-token-here
```

前端设置页保存 `api_token` 后会自动在后续请求中携带。

---

## 13. 已知限制

当前版本是"可部署模板 + 可运行原型"，不是经过大规模生产验证的成熟版本。

当前已知限制：

- 任务历史主要保存在内存中，重启服务后不会长期保留
- 自动去重目前是按文件名，不是哈希级自动比对
- MD5 深层校验目前是手动触发，不是自动前置逻辑
- 没有做 Docker 化部署
- 没有内置 systemd / NSSM / Supervisor 配置文件

---

## 14. 后续建议

如果你准备把这个项目继续做成长期可维护版本，下一步建议优先做：

1. 增加持久化数据库，保留完整任务历史
2. 把 Aria2 配置、Rclone 配置和项目配置彻底打通
3. 增加 Docker Compose 部署方式
4. 增加 Linux systemd 服务文件
5. 增加更强的远端去重策略

详细的部署教程请参阅 **[install.md](install.md)**。