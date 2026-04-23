# Aria2 Plus 全栈可视化管理中心

一个面向 `Aria2 + WebDAV + Rclone` 的全栈可视化管理中心。

本项目目标不是做一个 AriaNg 风格的纯前端壳，而是提供一套可直接部署的 `Web UI + 本地 API Server` 一体化方案，覆盖以下完整链路：

1. 前端粘贴磁力链接 / 种子链接
2. 后端先扫描 WebDAV 远端文件，按文件名做去重
3. 若远端已存在，直接拦截并提示“网盘已存在，停止添加任务”
4. 若远端不存在，再调用 Aria2 RPC 添加下载任务
5. Aria2 下载完成后，通过 `--on-download-complete` 回调后端
6. 后端调用 `rclone move` 将下载产物搬运到 WebDAV
7. 上传失败时，前端提供红色“手动重试上传”按钮

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
  - 返回前端仪表盘快照
- `GET /api/settings`
  - 读取当前设置
- `PUT /api/settings`
  - 保存设置到 `data/settings.json`
- `WebSocket /ws/events`
  - 向前端推送状态和日志

### 1.3 去重与校验逻辑

- 自动去重：当前是“按文件名匹配”
- 深层校验：由前端按钮触发 `rclone md5sum`
- 对于无法从磁力链接中提取文件名的场景：
  - 必须填写“文件名提示”
  - 或者点击“忽略去重强制下发”

---

## 2. 目录结构

```text
aria2-webui/
├─ data/
│  └─ settings.json          # 持久化配置
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

- `data/settings.json`：持久化配置
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

## 4. 运行环境要求

### 4.1 必需依赖

- Python 3.10+
- `pip`
- `aria2c`
- `rclone`
- Windows 下建议系统自带 `curl`
- Linux 下需要 `bash` + `curl`

### 4.2 Python 依赖

安装命令：

```bash
pip install -r requirements.txt
```

当前 Python 依赖：

- `fastapi`
- `uvicorn[standard]`

### 4.3 不需要的东西

本项目当前不需要：

- Node.js
- npm / pnpm / yarn
- 数据库
- Redis

---

## 5. 配置文件说明

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
  "hook_token": "change-me-hook-token"
}
```

### 5.1 字段解释

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
| `aria2_poll_interval_seconds` | Aria2 轮询间隔 | `3` | 越小越实时，越大越省资源 |
| `api_host` | API Server 监听地址 | `127.0.0.1` | `server/run.py` 会读取它 |
| `api_port` | API Server 监听端口 | `8080` | 修改后需要重启服务 |
| `hook_token` | Aria2 回调保护 Token | `change-me-hook-token` | 回调脚本必须同步 |

---

## 6. Rclone / WebDAV 配置要求

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

## 7. 部署前必须确认的耦合点

这部分非常重要。下次无论是你本人部署，还是丢给 OpenCode / Claude Code 部署，都必须优先检查这几个耦合点。

### 7.1 下载目录耦合

后端依赖 `download_dir` 来：

- 扫描本地文件
- 计算本地 MD5
- 找到下载完成后的上传源文件
- 删除搬运完成后的本地文件

所以：

- `data/settings.json` 的 `download_dir`
- Aria2 的 `--dir`

必须是同一个目录。

错误示例：

- `settings.json` 写的是 `downloads`
- 但 Aria2 实际下载到了 `D:\downloads2`

这样会导致：

- 页面看不到本地文件
- 上传搬运找不到源文件
- MD5 校验找不到对应文件

### 7.2 回调 Token 耦合

这三个地方必须一致：

- `data/settings.json` 里的 `hook_token`
- `ARIA2_PLUS_HOOK_TOKEN` 环境变量
- `scripts/aria2-hook.bat` / `scripts/aria2-hook.sh` 运行时实际传入的 Token

### 7.3 RPC Secret 耦合

这两个地方必须一致：

- Aria2 启动参数里的 `--rpc-secret=...`
- `data/settings.json` 里的 `aria2_rpc_secret`

### 7.4 远端名称耦合

`settings.json` 里的：

- `rclone_binary`
- `rclone_remote`
- `rclone_remote_path`

必须与你机器上真实存在的 Rclone 配置一致。

---

## 8. Windows 部署说明

### 8.1 安装依赖

确保命令可用：

```bat
python --version
aria2c --version
rclone version
curl --version
```

如果 `python` 命令不可用，也可以尝试：

```bat
py -3 --version
```

### 8.2 安装 Python 依赖

在项目根目录执行：

```bat
pip install -r requirements.txt
```

### 8.3 修改配置

编辑：

```text
data\settings.json
```

至少确认以下字段正确：

- `aria2_rpc_url`
- `aria2_rpc_secret`
- `rclone_binary`
- `rclone_remote`
- `rclone_remote_path`
- `download_dir`
- `hook_token`

### 8.4 启动 API Server

```bat
scripts\start-server.bat
```

该脚本会：

- 进入项目根目录
- 自动创建 `downloads\`
- 优先尝试 `python`
- 若失败再尝试 `py -3`
- 通过 `python -m server.run` 按 `settings.json` 启动服务

### 8.5 启动 Aria2

最关键的是保证 `--dir` 与 `download_dir` 一致。

示例：

```bat
set ARIA2_PLUS_HOOK_URL=http://127.0.0.1:8080/api/aria2/hook
set ARIA2_PLUS_HOOK_TOKEN=change-me-hook-token

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

```bat
set ARIA2_PLUS_HOOK_URL=http://127.0.0.1:8080/api/aria2/hook
set ARIA2_PLUS_HOOK_TOKEN=your-hook-token

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

### 8.6 打开页面

浏览器访问：

```text
http://127.0.0.1:8080
```

如果你修改了 `api_host` / `api_port`，按实际值访问。

---

## 9. Linux 部署说明

### 9.1 安装依赖

以 Debian/Ubuntu 为例：

```bash
sudo apt update
sudo apt install -y python3 python3-pip aria2 rclone curl
```

### 9.2 安装 Python 依赖

```bash
pip3 install -r requirements.txt
```

如果你使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 9.3 修改配置

编辑：

```bash
data/settings.json
```

### 9.4 给脚本执行权限

```bash
chmod +x scripts/start-server.sh scripts/aria2-hook.sh
```

### 9.5 启动 API Server

```bash
./scripts/start-server.sh
```

### 9.6 启动 Aria2

同样要保证 `--dir` 与 `download_dir` 一致。

示例：

```bash
export ARIA2_PLUS_HOOK_URL="http://127.0.0.1:8080/api/aria2/hook"
export ARIA2_PLUS_HOOK_TOKEN="change-me-hook-token"

aria2c \
  --enable-rpc=true \
  --rpc-listen-all=true \
  --rpc-allow-origin-all=true \
  --rpc-listen-port=6800 \
  --max-concurrent-downloads=1 \
  --dir="$(pwd)/downloads" \
  --on-download-complete="$(pwd)/scripts/aria2-hook.sh"
```

启用 RPC Secret 的示例：

```bash
export ARIA2_PLUS_HOOK_URL="http://127.0.0.1:8080/api/aria2/hook"
export ARIA2_PLUS_HOOK_TOKEN="your-hook-token"

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

---

## 10. 推荐的启动顺序

无论 Windows 还是 Linux，建议按这个顺序启动：

1. 确认 `rclone ls <remote>` 正常
2. 确认 `data/settings.json` 已正确配置
3. 启动 API Server
4. 启动 Aria2 RPC
5. 浏览器打开首页
6. 在“系统设置”页面再次检查配置是否正确
7. 点击“扫描 WebDAV”测试远端扫描
8. 添加一个测试下载任务

---

## 11. 部署后验收清单

### 11.1 基础连通性检查

打开浏览器：

```text
http://127.0.0.1:8080
```

检查是否能看到三栏布局页面。

### 11.2 API 检查

```bash
curl http://127.0.0.1:8080/api/dashboard
```

预期：

- 返回 JSON
- 不报 404

### 11.3 WebDAV 扫描检查

```bash
curl -X POST http://127.0.0.1:8080/api/webdav/scan
```

预期：

- 返回 JSON
- `files` 是数组
- 如果 Rclone 远端正常，应能列出远端文件

### 11.4 Aria2 RPC 检查

查看页面顶部健康状态：

- Aria2 RPC 应显示在线

或者直接看：

```bash
curl http://127.0.0.1:8080/api/dashboard
```

返回的 `health.aria2_online` 应为 `true`。

### 11.5 下载与搬运链路检查

完整测试至少要过这 5 步：

1. 添加一个不存在于 WebDAV 的测试任务
2. 页面出现“排队中”或“下载中”
3. 下载完成后状态切到“正在搬运至 WebDAV”
4. 日志控制台出现 Rclone 输出
5. 搬运完成后，远端扫描结果中能看到新文件

### 11.6 去重检查

至少验证一次：

1. 先让远端存在一个已知文件名
2. 再在前端添加同名任务
3. 页面应直接弹窗提示“网盘已存在，停止添加任务”
4. Aria2 不应新增该任务

---

## 12. 给 OpenCode / Claude Code 的部署指令

如果你下次把这个仓库直接丢给 OpenCode、Claude Code 或其他代码代理，请要求它严格按下面顺序执行。

### 12.1 代理必须先阅读的文件

1. `README.md`
2. `data/settings.json`
3. `scripts/start-server.bat`
4. `scripts/start-server.sh`
5. `scripts/aria2-hook.bat`
6. `scripts/aria2-hook.sh`
7. `server/main.py`

### 12.2 代理的标准部署任务

让代理按下面流程执行：

1. 检查系统是否安装 Python 3.10+、`aria2c`、`rclone`、`curl`
2. 安装 `requirements.txt`
3. 验证 `rclone ls <remote>` 是否可用
4. 编辑 `data/settings.json`，写入正确的：
   - `aria2_rpc_url`
   - `aria2_rpc_secret`
   - `rclone_remote`
   - `rclone_remote_path`
   - `download_dir`
   - `hook_token`
5. 启动 `python -m server.run`
6. 用与 `download_dir` 一致的 `--dir` 启动 Aria2
7. 为 Aria2 配置 `--on-download-complete` 指向对应平台的 hook 脚本
8. 访问首页
9. 调用 `POST /api/webdav/scan` 做冒烟测试
10. 添加测试任务验证整条链路

### 12.3 代理部署时绝不能漏的检查

- `download_dir` 是否和 Aria2 的 `--dir` 完全一致
- `hook_token` 是否一致
- `aria2_rpc_secret` 是否一致
- `rclone_remote` 是否真的存在
- API Server 的实际端口是否与你访问页面时使用的端口一致

### 12.4 可以直接给代理的任务提示词

你可以把下面这段话直接丢给 OpenCode / Claude Code：

```text
请先完整阅读 README.md，并按 README 的部署顺序执行本项目。
重点检查：
1. data/settings.json 的 download_dir 必须与 aria2c --dir 一致
2. hook_token 必须与 Aria2 回调脚本环境变量一致
3. aria2_rpc_secret 必须与 Aria2 RPC 配置一致
4. rclone_remote 必须是本机真实存在且可访问的 remote
5. 启动 API Server 后，先调用 POST /api/webdav/scan 验证 Rclone
6. 再启动 aria2c，并验证下载完成后会触发 /api/aria2/hook
完成后请执行一次完整链路验收，并把实际启动命令、修改过的配置、验收结果返回。
```

---

## 13. 当前前端功能说明

### 13.1 智能监控

- 看板视图展示：
  - 排队中
  - 下载中
  - 上传搬运中
  - 异常待处理
  - 完成归档

### 13.2 任务面板

- 卡片展示每个任务：
  - 文件名
  - 队列位置
  - 下载进度
  - 下载速度
  - 搬运状态
  - 错误信息

### 13.3 网盘文件浏览

- 显示远端文件列表
- 每个远端文件可以点击“校验本地 MD5”

### 13.4 系统设置

- 允许在页面直接修改 `settings.json` 中的大部分关键配置

### 13.5 操作日志控制台

- WebSocket 实时接收后端日志
- 可看到 Rclone 搬运进度输出

---

## 14. 当前后端业务流程

### 14.1 添加任务流程

1. 前端调用 `POST /api/tasks/add`
2. 后端尝试提取文件名
3. 如果不能提取文件名且不是强制下发，则直接报错
4. 如果能提取文件名，则先执行或复用远端扫描缓存
5. 若远端存在同名文件，则返回重复任务提示
6. 若远端不存在，则调用：
   - `aria2.addUri`
   - 或 `aria2.addTorrent`
7. 任务进入面板

### 14.2 下载完成后的搬运流程

1. Aria2 下载完成
2. `--on-download-complete` 调用 `scripts/aria2-hook.bat` 或 `scripts/aria2-hook.sh`
3. Hook 脚本向 `POST /api/aria2/hook` 发送 `gid + token`
4. 后端通过 RPC 读取任务详情
5. 后端定位本地下载文件
6. 调用 `rclone move` 搬运到 WebDAV
7. 搬运成功后删除本地源文件
8. 刷新本地文件列表和远端扫描缓存

### 14.3 手动重试流程

1. 某个任务上传失败
2. 前端显示红色“手动重试上传”
3. 点击后调用 `POST /api/tasks/{gid}/retry-upload`
4. 后端重新执行搬运流程

---

## 15. API 一览

### 15.1 仪表盘

```http
GET /api/dashboard
```

### 15.2 设置

```http
GET /api/settings
PUT /api/settings
```

### 15.3 WebDAV

```http
POST /api/webdav/scan
POST /api/webdav/verify-md5
```

### 15.4 本地目录

```http
POST /api/local/refresh
```

### 15.5 任务

```http
POST /api/tasks/add
POST /api/tasks/{gid}/retry-upload
```

### 15.6 Aria2 回调

```http
POST /api/aria2/hook
```

### 15.7 WebSocket

```http
GET /ws/events
```

---

## 16. 常见问题排查

### 16.1 页面能打开，但 Aria2 显示离线

检查：

- Aria2 是否真的已启动
- `aria2_rpc_url` 是否正确
- `aria2_rpc_secret` 是否匹配
- 6800 端口是否被防火墙拦截

### 16.2 页面能扫描本地，但扫描 WebDAV 失败

检查：

- `rclone` 命令是否存在
- `rclone_remote` 是否正确
- `rclone_remote_path` 是否正确
- `rclone ls webdav:` 是否能手工跑通

### 16.3 任务下载完成了，但没有自动搬运

优先检查：

- `--on-download-complete` 是否真的配置了 hook 脚本
- `ARIA2_PLUS_HOOK_URL` 是否正确
- `ARIA2_PLUS_HOOK_TOKEN` 是否与 `settings.json` 一致
- API Server 是否正在运行
- 日志控制台里是否出现 `/api/aria2/hook` 相关日志

### 16.4 自动搬运失败，点击重试仍失败

检查：

- `download_dir` 与 `--dir` 是否一致
- 本地文件是否确实存在
- Rclone 对远端目录是否有写权限
- Rclone 是否能执行 `move`

你可以手动验证：

```bash
rclone move <本地文件或目录> <remote:path>
```

### 16.5 去重没有拦住重复任务

检查：

- 远端扫描是否成功
- 远端文件名是否真的与待下载文件名一致
- 磁力链接是否能提取到 `dn`
- 若不能提取，是否已填写“文件名提示”

---

## 17. 已知限制

当前版本是“可部署模板 + 可运行原型”，不是经过大规模生产验证的成熟版本。

当前已知限制：

- 任务历史主要保存在内存中，重启服务后不会长期保留
- 自动去重目前是按文件名，不是哈希级自动比对
- MD5 深层校验目前是手动触发，不是自动前置逻辑
- 没有做用户认证和权限系统
- 没有做 Docker 化部署
- 没有内置 systemd / NSSM / Supervisor 配置文件

---

## 18. 后续建议

如果你准备把这个项目继续做成长期可维护版本，下一步建议优先做：

1. 增加持久化数据库，保留完整任务历史
2. 把 Aria2 配置、Rclone 配置和项目配置彻底打通
3. 增加 Docker Compose 部署方式
4. 增加 Linux systemd 服务文件
5. 增加登录鉴权
6. 增加更强的远端去重策略

---

## 19. 最短部署路径

如果你只想最快跑起来，按下面步骤即可：

```bash
pip install -r requirements.txt
```

编辑：

```text
data/settings.json
```

确保：

- `rclone_remote` 正确
- `download_dir` 与 Aria2 的 `--dir` 一致
- `hook_token` 正确

启动 API：

```bash
python -m server.run
```

启动 Aria2：

```bash
export ARIA2_PLUS_HOOK_URL="http://127.0.0.1:8080/api/aria2/hook"
export ARIA2_PLUS_HOOK_TOKEN="change-me-hook-token"

aria2c \
  --enable-rpc=true \
  --rpc-listen-all=true \
  --rpc-allow-origin-all=true \
  --rpc-listen-port=6800 \
  --max-concurrent-downloads=1 \
  --dir="$(pwd)/downloads" \
  --on-download-complete="$(pwd)/scripts/aria2-hook.sh"
```

打开：

```text
http://127.0.0.1:8080
```

先点“扫描 WebDAV”，再添加测试任务。

