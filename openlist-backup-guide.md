# OpenList 网盘数据导出与导入指南

> 官方文档：https://doc.oplist.org/guide/advanced/backup

## 备份方法

### 方法一：内置备份（推荐）

OpenList 后台提供内置的 **备份/恢复** 功能，路径：`管理 → 备份与恢复`

**备份操作：**
1. 登录 OpenList 后台 (`http://<服务器IP>:5244/@manage`)
2. 进入 `管理 → 备份与恢复`
3. 点击 **备份**，下载生成的 JSON 配置文件
4. 可选填写 **加密密码**，导出时会加密保护

**恢复操作：**
1. 进入同一页面，点击 **恢复**
2. 选择之前下载的备份文件
3. 可选勾选 **覆盖**（会覆盖当前用户信息）
4. 如有加密，先输入加密密码再恢复

> **注意**：内置备份不包含索引数据，仅备份配置（存储驱动、用户、设置等）

---

### 方法二：SQLite 数据库备份（完整备份）

直接备份 OpenList 的 SQLite 数据库文件，**包含索引数据**。

**备份文件位置：**
- Docker 部署（宿主机路径）：`/etc/openlist/`
- 文件列表：
  - `data.db` — 主数据库
  - `data.db-shm` — WAL 共享内存文件
  - `data.db-wal` — WAL 日志文件
  - `config.json` — 站点配置

**操作步骤：**

```bash
# 1. 停止 OpenList（确保 WAL 文件合并到 data.db）
docker stop openlist

# 2. 备份整个数据目录
cp -av /etc/openlist/ /root/back/openlist-backup/

# 3. 重启 OpenList
docker start openlist
```

> **重要**：必须先停止 OpenList 再备份，否则 `data.db-shm` 和 `data.db-wal` 文件不会合并到 `data.db`，导致数据不完整。

---

### 方法三：其他数据库

如果使用 MySQL、PostgreSQL 等外部数据库，请使用对应的数据库备份工具（如 `mysqldump`、`pg_dump`）进行备份。

---

## 恢复方法

### 从内置备份恢复

1. 登录新的 OpenList 后台
2. 进入 `管理 → 备份与恢复`
3. 点击 **恢复**，选择 JSON 备份文件
4. 重启 OpenList 使配置生效

### 从 SQLite 备份恢复

```bash
# 1. 停止 OpenList
docker stop openlist

# 2. 替换数据目录
rm -rf /etc/openlist/*
cp -av /root/back/openlist-backup/* /etc/openlist/

# 3. 重启 OpenList
docker start openlist
```

### 从 AList V3 迁移

1. 使用 AList 的备份功能导出配置
2. 备份 AList 的 `data` 文件夹
3. 卸载 AList，安装 OpenList
4. 使用 OpenList 的恢复功能导入配置
5. 进入设置页面，逐页点击 **加载默认设置 → 保存**

---

## 本次备份记录

| 项目 | 详情 |
|------|------|
| 备份时间 | 2026-05-02 |
| 服务器 | 172.245.66.61 |
| 备份路径 | `/root/back/openlist-backup/` |
| 备份方法 | 方法二（SQLite 直接备份） |
| 容器状态 | 已停止并重启 |

**备份内容：**
```
/root/back/openlist-backup/
├── config.json    (2.8KB) — 站点配置
├── data.db        (76KB)  — 主数据库（用户、存储驱动、设置等）
├── log/
│   └── log.log
└── temp/
```

**OpenList 容器信息：**
- 镜像：`openlistteam/openlist:latest`
- 端口：`5244`
- 数据挂载：`/etc/openlist → /opt/openlist/data`
- Admin 初始密码：`XIrrYz6g`

---

## 注意事项

1. **磁盘空间**：备份前确保目标目录有足够空间
2. **停止容器**：SQLite 备份前必须停止 OpenList，避免数据损坏
3. **定期备份**：建议定期执行备份，特别是在修改存储配置后
4. **加密备份**：敏感配置建议使用加密密码保护
5. **验证备份**：备份完成后建议检查文件完整性
