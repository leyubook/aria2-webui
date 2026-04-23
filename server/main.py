from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import shutil
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = ROOT_DIR / "public"
DATA_DIR = ROOT_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULT_SETTINGS: dict[str, Any] = {
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
}
ARIA2_KEYS = [
    "gid",
    "status",
    "totalLength",
    "completedLength",
    "downloadSpeed",
    "dir",
    "files",
    "bittorrent",
    "infoHash",
    "errorMessage",
]
TASK_LIMIT = 200


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def resolve_local_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def normalize_remote_base(remote: str) -> str:
    remote = (remote or "webdav:").strip()
    if ":" not in remote:
        remote = f"{remote}:"
    return remote


def build_remote_root(settings: dict[str, Any]) -> str:
    remote = normalize_remote_base(str(settings.get("rclone_remote", "webdav:")))
    suffix = str(settings.get("rclone_remote_path", "")).replace("\\", "/").strip("/")
    if not suffix:
        return remote
    if remote.endswith(":"):
        return f"{remote}{suffix}"
    return f"{remote.rstrip('/')}/{suffix}"


def join_remote_path(base: str, child: str) -> str:
    child = child.replace("\\", "/").strip("/")
    if not child:
        return base
    if base.endswith(":"):
        return f"{base}{child}"
    return f"{base.rstrip('/')}/{child}"


def ensure_runtime_paths(settings: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    resolve_local_path(str(settings.get("download_dir", "downloads"))).mkdir(
        parents=True,
        exist_ok=True,
    )


def load_settings() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(
            json.dumps(DEFAULT_SETTINGS, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        loaded = {}
    settings = {**DEFAULT_SETTINGS, **loaded}
    ensure_runtime_paths(settings)
    return settings


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_SETTINGS, **settings}
    ensure_runtime_paths(merged)
    SETTINGS_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return merged


def build_filename_hint(uri: str, explicit_hint: str | None = None) -> str | None:
    if explicit_hint:
        return Path(explicit_hint.strip()).name or explicit_hint.strip()
    value = uri.strip()
    if not value:
        return None
    if value.startswith("magnet:?"):
        parsed = urllib.parse.urlparse(value)
        display_name = urllib.parse.parse_qs(parsed.query).get("dn", [None])[0]
        if display_name:
            return Path(display_name).name
        return None
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme in {"http", "https", "ftp"}:
        return Path(urllib.parse.unquote(parsed.path)).name or None
    local_path = Path(value.strip('"'))
    if local_path.exists():
        return local_path.name
    return Path(value).name or None


def is_torrent_source(uri: str) -> bool:
    value = uri.strip().lower()
    if value.startswith("magnet:?"):
        return False
    if value.endswith(".torrent"):
        return True
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme in {"http", "https", "ftp"} and parsed.path.lower().endswith(".torrent"):
        return True
    return Path(uri.strip('"')).suffix.lower() == ".torrent"


def parse_rclone_ls(raw_output: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        size_part, path_part = parts
        try:
            size_value = int(size_part)
        except ValueError:
            continue
        files.append(
            {
                "path": path_part.strip(),
                "name": Path(path_part.strip()).name,
                "size": size_value,
            }
        )
    files.sort(key=lambda item: item["path"].lower())
    return files


def scan_local_files(settings: dict[str, Any]) -> list[dict[str, Any]]:
    download_root = resolve_local_path(str(settings.get("download_dir", "downloads")))
    if not download_root.exists():
        return []
    entries: list[dict[str, Any]] = []
    for path in download_root.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        entries.append(
            {
                "path": path.relative_to(download_root).as_posix(),
                "name": path.name,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                .astimezone()
                .isoformat(timespec="seconds"),
            }
        )
    entries.sort(key=lambda item: item["path"].lower())
    return entries


def compute_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_torrent_bytes(uri: str) -> bytes:
    local_candidate = Path(uri.strip('"'))
    if local_candidate.exists():
        return local_candidate.read_bytes()
    request = urllib.request.Request(
        uri,
        headers={"User-Agent": "Aria2Plus/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def choose_upload_source(task: "TaskRecord", settings: dict[str, Any]) -> Path | None:
    download_root = resolve_local_path(str(settings.get("download_dir", "downloads")))
    candidates: list[Path] = []
    if task.local_path:
        candidates.append(Path(task.local_path))
    for raw in task.files:
        candidates.append(Path(raw))
    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        marker = str(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        unique_candidates.append(candidate)
    existing = [candidate for candidate in unique_candidates if candidate.exists()]
    if not existing and task.name:
        guessed_dir = download_root / task.name
        if guessed_dir.exists():
            existing = [guessed_dir]
    if not existing:
        return None
    if len(existing) == 1:
        return existing[0]
    try:
        common_root = Path(os.path.commonpath([str(path) for path in existing]))
    except ValueError:
        common_root = existing[0]
    if common_root.exists() and common_root != download_root:
        return common_root
    if task.name:
        preferred_dir = download_root / task.name
        if preferred_dir.exists():
            return preferred_dir
    return existing[0]


def parse_md5sum_output(raw_output: str) -> str | None:
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if parts:
            return parts[0]
    return None


def build_task_from_aria2(item: dict[str, Any], queue_position: int | None = None) -> "TaskRecord":
    bittorrent_name = (
        item.get("bittorrent", {}).get("info", {}).get("name")
        if isinstance(item.get("bittorrent"), dict)
        else None
    )
    file_paths = [
        file_entry.get("path")
        for file_entry in item.get("files", [])
        if isinstance(file_entry, dict) and file_entry.get("path")
    ]
    display_name = bittorrent_name or (Path(file_paths[0]).name if file_paths else item.get("gid", "unknown"))
    primary_path = file_paths[0] if file_paths else None
    return TaskRecord(
        gid=item.get("gid", "unknown"),
        name=display_name,
        aria2_status=item.get("status", "waiting"),
        queue_position=queue_position,
        total_length=int(item.get("totalLength", 0) or 0),
        completed_length=int(item.get("completedLength", 0) or 0),
        download_speed=int(item.get("downloadSpeed", 0) or 0),
        local_path=primary_path,
        download_dir=item.get("dir"),
        files=file_paths,
        info_hash=item.get("infoHash"),
        last_message=item.get("errorMessage", "") or "",
    )


def trim_tasks(tasks: dict[str, "TaskRecord"]) -> None:
    if len(tasks) <= TASK_LIMIT:
        return
    sorted_items = sorted(
        tasks.items(),
        key=lambda item: item[1].updated_at,
        reverse=True,
    )
    keep = {gid for gid, _task in sorted_items[:TASK_LIMIT]}
    for gid in list(tasks):
        if gid not in keep:
            del tasks[gid]


class AddTaskRequest(BaseModel):
    uri: str
    filename_hint: str | None = None
    force: bool = False


class VerifyMd5Request(BaseModel):
    remote_path: str
    local_file_name: str | None = None


class HookRequest(BaseModel):
    gid: str
    token: str | None = None


class SettingsRequest(BaseModel):
    aria2_rpc_url: str
    aria2_rpc_secret: str = ""
    rclone_binary: str = "rclone"
    rclone_remote: str = "webdav:"
    rclone_remote_path: str = "downloads"
    download_dir: str = "downloads"
    webdav_scan_depth: int = Field(default=5, ge=1, le=20)
    webdav_scan_ttl_seconds: int = Field(default=90, ge=10, le=3600)
    aria2_poll_interval_seconds: int = Field(default=3, ge=1, le=30)
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8080, ge=1, le=65535)
    hook_token: str = "change-me-hook-token"


@dataclass
class TaskRecord:
    gid: str
    name: str
    source_uri: str = ""
    aria2_status: str = "waiting"
    upload_state: str = "idle"
    queue_position: int | None = None
    total_length: int = 0
    completed_length: int = 0
    download_speed: int = 0
    local_path: str | None = None
    download_dir: str | None = None
    files: list[str] = field(default_factory=list)
    remote_target: str | None = None
    upload_error: str | None = None
    last_message: str = ""
    info_hash: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @property
    def progress(self) -> float:
        if self.total_length <= 0:
            return 0.0
        return round((self.completed_length / self.total_length) * 100, 2)

    @property
    def ui_status(self) -> str:
        if self.upload_state == "uploading":
            return "uploading"
        if self.upload_state == "failed":
            return "upload_failed"
        if self.upload_state == "uploaded":
            return "uploaded"
        mapping = {
            "active": "downloading",
            "waiting": "queued",
            "paused": "paused",
            "complete": "downloaded",
            "error": "error",
            "removed": "removed",
        }
        return mapping.get(self.aria2_status, "queued")

    def to_dict(self) -> dict[str, Any]:
        return {
            "gid": self.gid,
            "name": self.name,
            "source_uri": self.source_uri,
            "aria2_status": self.aria2_status,
            "ui_status": self.ui_status,
            "upload_state": self.upload_state,
            "queue_position": self.queue_position,
            "total_length": self.total_length,
            "completed_length": self.completed_length,
            "download_speed": self.download_speed,
            "progress": self.progress,
            "local_path": self.local_path,
            "download_dir": self.download_dir,
            "files": self.files,
            "remote_target": self.remote_target,
            "upload_error": self.upload_error,
            "last_message": self.last_message,
            "info_hash": self.info_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AppRuntime:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.tasks: dict[str, TaskRecord] = {}
        self.logs: deque[str] = deque(maxlen=300)
        self.remote_files: list[dict[str, Any]] = []
        self.remote_raw_output = ""
        self.remote_index: dict[str, list[str]] = {}
        self.local_files: list[dict[str, Any]] = []
        self.last_scan_at: str | None = None
        self.last_scan_ts: float | None = None
        self.aria2_online = False
        self.rclone_online = False
        self.last_aria2_error = ""
        self.last_rclone_error = ""
        self.websockets: set[WebSocket] = set()
        self.upload_jobs: dict[str, asyncio.Task[Any]] = {}
        self.lock = asyncio.Lock()

    async def log(self, level: str, source: str, message: str) -> None:
        line = f"[{utc_now()}][{level.upper()}][{source}] {message}"
        async with self.lock:
            self.logs.append(line)
        await self.broadcast({"type": "log", "entry": line})

    async def snapshot(self) -> dict[str, Any]:
        async with self.lock:
            tasks = [task.to_dict() for task in self.tasks.values()]
            tasks.sort(
                key=lambda item: (
                    {"downloading": 0, "queued": 1, "uploading": 2, "upload_failed": 3}.get(
                        item["ui_status"],
                        4,
                    ),
                    item["queue_position"] if item["queue_position"] is not None else 9999,
                    item["updated_at"],
                ),
            )
            stats = {
                "total": len(tasks),
                "queued": sum(task["ui_status"] == "queued" for task in tasks),
                "downloading": sum(task["ui_status"] == "downloading" for task in tasks),
                "uploading": sum(task["ui_status"] == "uploading" for task in tasks),
                "attention": sum(task["ui_status"] in {"upload_failed", "error"} for task in tasks),
                "completed": sum(task["ui_status"] == "uploaded" for task in tasks),
            }
            return {
                "settings": dict(self.settings),
                "tasks": tasks,
                "logs": list(self.logs),
                "remote_files": list(self.remote_files),
                "local_files": list(self.local_files),
                "last_scan_at": self.last_scan_at,
                "stats": stats,
                "health": {
                    "aria2_online": self.aria2_online,
                    "rclone_online": self.rclone_online,
                    "last_aria2_error": self.last_aria2_error,
                    "last_rclone_error": self.last_rclone_error,
                    "websocket_clients": len(self.websockets),
                },
            }

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for socket in list(self.websockets):
            try:
                await socket.send_json(payload)
            except Exception:
                stale.append(socket)
        if stale:
            async with self.lock:
                for socket in stale:
                    self.websockets.discard(socket)

    async def broadcast_snapshot(self) -> None:
        await self.broadcast({"type": "snapshot", "data": await self.snapshot()})

    async def register_socket(self, socket: WebSocket) -> None:
        await socket.accept()
        async with self.lock:
            self.websockets.add(socket)
        await socket.send_json({"type": "snapshot", "data": await self.snapshot()})

    async def unregister_socket(self, socket: WebSocket) -> None:
        async with self.lock:
            self.websockets.discard(socket)

    async def set_remote_cache(self, files: list[dict[str, Any]], raw_output: str) -> None:
        async with self.lock:
            self.remote_files = files
            self.remote_raw_output = raw_output
            self.last_scan_at = utc_now()
            self.last_scan_ts = time.time()
            self.remote_index = {}
            for file_item in files:
                key = file_item["name"].lower()
                self.remote_index.setdefault(key, []).append(file_item["path"])
        await self.broadcast_snapshot()

    async def set_local_cache(self, files: list[dict[str, Any]]) -> None:
        async with self.lock:
            self.local_files = files
        await self.broadcast_snapshot()

    async def find_duplicates(self, filename: str) -> list[str]:
        async with self.lock:
            return list(self.remote_index.get(filename.lower(), []))

    async def save_task(self, task: TaskRecord) -> None:
        async with self.lock:
            existing = self.tasks.get(task.gid)
            if existing:
                task.created_at = existing.created_at
            task.updated_at = utc_now()
            self.tasks[task.gid] = task
            trim_tasks(self.tasks)
        await self.broadcast_snapshot()

    async def update_task(self, gid: str, **changes: Any) -> TaskRecord:
        async with self.lock:
            task = self.tasks.get(gid)
            if not task:
                task = TaskRecord(gid=gid, name=gid)
                self.tasks[gid] = task
            for key, value in changes.items():
                setattr(task, key, value)
            task.updated_at = utc_now()
            trim_tasks(self.tasks)
            clone = copy.deepcopy(task)
        await self.broadcast_snapshot()
        return clone

    async def get_task(self, gid: str) -> TaskRecord | None:
        async with self.lock:
            task = self.tasks.get(gid)
            return copy.deepcopy(task) if task else None

    async def apply_aria2_snapshot(self, records: list[TaskRecord]) -> None:
        async with self.lock:
            for record in records:
                existing = self.tasks.get(record.gid)
                if existing:
                    record.source_uri = existing.source_uri or record.source_uri
                    record.upload_state = existing.upload_state
                    record.upload_error = existing.upload_error
                    record.remote_target = existing.remote_target
                    if existing.last_message and not record.last_message:
                        record.last_message = existing.last_message
                    record.created_at = existing.created_at
                record.updated_at = utc_now()
                self.tasks[record.gid] = record
            trim_tasks(self.tasks)
        await self.broadcast_snapshot()


runtime = AppRuntime()
app = FastAPI(title="Aria2 Plus", version="1.0.0")
app.mount("/assets", StaticFiles(directory=str(PUBLIC_DIR)), name="assets")


async def run_command(args: list[str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def stream_command(
    args: list[str],
    source_name: str,
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_buffer: list[str] = []
    stderr_buffer: list[str] = []

    async def pump(
        stream: asyncio.StreamReader | None,
        target: list[str],
        level: str,
    ) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            target.append(text)
            await runtime.log(level, source_name, text)

    await asyncio.gather(
        pump(process.stdout, stdout_buffer, "info"),
        pump(process.stderr, stderr_buffer, "info"),
    )
    code = await process.wait()
    return code, "\n".join(stdout_buffer), "\n".join(stderr_buffer)


async def aria2_call(method: str, params: list[Any] | None = None) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": f"aria2-plus-{int(time.time() * 1000)}",
        "method": f"aria2.{method}",
        "params": params or [],
    }
    secret = str(runtime.settings.get("aria2_rpc_secret", "")).strip()
    if secret:
        payload["params"] = [f"token:{secret}", *payload["params"]]

    def send() -> Any:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            str(runtime.settings["aria2_rpc_url"]),
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        if "error" in result:
            message = result["error"].get("message", "unknown aria2 rpc error")
            raise RuntimeError(message)
        return result.get("result")

    return await asyncio.to_thread(send)


async def refresh_local_cache() -> list[dict[str, Any]]:
    files = await asyncio.to_thread(scan_local_files, runtime.settings)
    await runtime.set_local_cache(files)
    return files


async def ensure_webdav_scan(force: bool = False) -> tuple[list[dict[str, Any]], str]:
    ttl = int(runtime.settings.get("webdav_scan_ttl_seconds", 90))
    if (
        not force
        and runtime.remote_files
        and runtime.last_scan_ts
        and (time.time() - runtime.last_scan_ts) < ttl
    ):
        return runtime.remote_files, runtime.remote_raw_output

    remote_root = build_remote_root(runtime.settings)
    args = [
        str(runtime.settings.get("rclone_binary", "rclone")),
        "ls",
        remote_root,
        "--max-depth",
        str(runtime.settings.get("webdav_scan_depth", 5)),
    ]
    code, stdout, stderr = await run_command(args)
    if code != 0:
        runtime.rclone_online = False
        runtime.last_rclone_error = stderr.strip() or stdout.strip() or "rclone ls failed"
        await runtime.log("error", "rclone", runtime.last_rclone_error)
        raise HTTPException(status_code=500, detail=runtime.last_rclone_error)
    files = parse_rclone_ls(stdout)
    runtime.rclone_online = True
    runtime.last_rclone_error = ""
    await runtime.set_remote_cache(files, stdout)
    await refresh_local_cache()
    await runtime.log("info", "rclone", f"WebDAV 扫描完成，共发现 {len(files)} 个文件。")
    return files, stdout


async def verify_remote_md5(remote_path: str, local_file_name: str | None = None) -> dict[str, Any]:
    await refresh_local_cache()
    local_target: str | None = None
    local_name = Path(local_file_name).name if local_file_name else Path(remote_path).name
    for file_item in runtime.local_files:
        if file_item["name"].lower() == local_name.lower():
            local_target = file_item["path"]
            break
    if not local_target:
        raise HTTPException(status_code=404, detail=f"本地未找到同名文件：{local_name}")

    local_path = resolve_local_path(runtime.settings["download_dir"]) / Path(local_target)
    local_md5 = await asyncio.to_thread(compute_md5, local_path)

    remote_full_path = join_remote_path(build_remote_root(runtime.settings), remote_path)
    args = [str(runtime.settings.get("rclone_binary", "rclone")), "md5sum", remote_full_path]
    code, stdout, stderr = await run_command(args)
    if code != 0:
        runtime.rclone_online = False
        runtime.last_rclone_error = stderr.strip() or stdout.strip() or "rclone md5sum failed"
        await runtime.log("error", "rclone", runtime.last_rclone_error)
        raise HTTPException(status_code=500, detail=runtime.last_rclone_error)
    runtime.rclone_online = True
    runtime.last_rclone_error = ""
    remote_md5 = parse_md5sum_output(stdout)
    if not remote_md5:
        raise HTTPException(status_code=500, detail="未能解析 rclone md5sum 输出")
    matched = local_md5.lower() == remote_md5.lower()
    await runtime.log(
        "info",
        "md5",
        f"{local_name} MD5 校验完成，结果：{'一致' if matched else '不一致'}。",
    )
    return {
        "remote_path": remote_path,
        "local_path": local_target,
        "remote_md5": remote_md5,
        "local_md5": local_md5,
        "matched": matched,
    }


async def sync_aria2_once() -> None:
    try:
        active = await aria2_call("tellActive", [ARIA2_KEYS])
        waiting = await aria2_call("tellWaiting", [0, 100, ARIA2_KEYS])
        stopped = await aria2_call("tellStopped", [0, 50, ARIA2_KEYS])
    except Exception as exc:
        message = str(exc)
        runtime.aria2_online = False
        if message != runtime.last_aria2_error:
            runtime.last_aria2_error = message
            await runtime.log("error", "aria2", f"Aria2 连接失败：{message}")
            await runtime.broadcast_snapshot()
        return

    runtime.aria2_online = True
    runtime.last_aria2_error = ""
    records: list[TaskRecord] = []
    records.extend(build_task_from_aria2(item) for item in active)
    records.extend(
        build_task_from_aria2(item, queue_position=index + 1)
        for index, item in enumerate(waiting)
    )
    records.extend(build_task_from_aria2(item) for item in stopped)
    await runtime.apply_aria2_snapshot(records)


async def poll_aria2_forever() -> None:
    while True:
        await sync_aria2_once()
        await asyncio.sleep(int(runtime.settings.get("aria2_poll_interval_seconds", 3)))


async def delete_local_source(source: Path) -> None:
    download_root = resolve_local_path(runtime.settings["download_dir"])
    resolved = source.resolve()
    if not is_path_within(resolved, download_root):
        return
    if resolved.is_dir():
        await asyncio.to_thread(shutil.rmtree, resolved, True)
    elif resolved.exists():
        await asyncio.to_thread(resolved.unlink, missing_ok=True)


async def run_upload_pipeline(gid: str) -> None:
    try:
        task = await runtime.get_task(gid)
        if not task:
            await runtime.log("error", "upload", f"未知任务，无法搬运：{gid}")
            return
        source = choose_upload_source(task, runtime.settings)
        if not source:
            await runtime.update_task(
                gid,
                upload_state="failed",
                upload_error="未找到可搬运的本地文件或目录。",
                last_message="自动搬运失败，请手动重试上传。",
            )
            await runtime.log("error", "upload", f"{task.name} 未找到本地源文件。")
            return

        remote_root = build_remote_root(runtime.settings)
        remote_target = join_remote_path(remote_root, source.name) if source.is_dir() else remote_root
        await runtime.update_task(
            gid,
            upload_state="uploading",
            upload_error=None,
            remote_target=remote_target,
            last_message="正在搬运至 WebDAV。",
            local_path=str(source),
        )
        await runtime.log("info", "upload", f"开始搬运 {source.name} -> {remote_target}")

        args = [
            str(runtime.settings.get("rclone_binary", "rclone")),
            "move",
            str(source),
            remote_target,
            "--stats",
            "1s",
            "--stats-one-line",
            "--transfers",
            "1",
            "--checkers",
            "1",
            "--create-empty-src-dirs",
        ]
        code, stdout, stderr = await stream_command(args, "rclone")
        if code != 0:
            runtime.rclone_online = False
            runtime.last_rclone_error = stderr.strip() or stdout.strip() or "rclone move failed"
            await runtime.update_task(
                gid,
                upload_state="failed",
                upload_error=runtime.last_rclone_error,
                last_message="自动搬运失败，请手动重试上传。",
            )
            await runtime.log("error", "upload", f"{task.name} 搬运失败：{runtime.last_rclone_error}")
            return

        runtime.rclone_online = True
        runtime.last_rclone_error = ""
        await delete_local_source(source)
        await runtime.update_task(
            gid,
            upload_state="uploaded",
            upload_error=None,
            last_message="搬运完成，远端已同步。",
            remote_target=remote_target,
        )
        await runtime.log("info", "upload", f"{task.name} 已搬运完成。")
        await refresh_local_cache()
        try:
            await ensure_webdav_scan(force=True)
        except HTTPException:
            pass
    finally:
        runtime.upload_jobs.pop(gid, None)


@app.on_event("startup")
async def on_startup() -> None:
    await refresh_local_cache()
    app.state.poller = asyncio.create_task(poll_aria2_forever())
    await runtime.log("info", "system", "Aria2 Plus API Server 已启动。")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    poller: asyncio.Task[Any] | None = getattr(app.state, "poller", None)
    if poller:
        poller.cancel()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/api/dashboard")
async def get_dashboard() -> dict[str, Any]:
    return await runtime.snapshot()


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return dict(runtime.settings)


@app.put("/api/settings")
async def put_settings(payload: SettingsRequest) -> dict[str, Any]:
    runtime.settings = save_settings(payload.model_dump())
    await refresh_local_cache()
    await runtime.log("info", "settings", "系统设置已保存。")
    await runtime.broadcast_snapshot()
    return {"ok": True, "settings": dict(runtime.settings)}


@app.post("/api/webdav/scan")
async def post_webdav_scan() -> dict[str, Any]:
    files, raw_output = await ensure_webdav_scan(force=True)
    return {
        "ok": True,
        "files": files,
        "raw_output": raw_output,
        "local_files": runtime.local_files,
        "last_scan_at": runtime.last_scan_at,
    }


@app.post("/api/webdav/verify-md5")
async def post_webdav_verify_md5(payload: VerifyMd5Request) -> dict[str, Any]:
    result = await verify_remote_md5(payload.remote_path, payload.local_file_name)
    return {"ok": True, **result}


@app.post("/api/local/refresh")
async def post_local_refresh() -> dict[str, Any]:
    files = await refresh_local_cache()
    await runtime.log("info", "local", f"本地下载目录已刷新，共 {len(files)} 个文件。")
    return {"ok": True, "files": files}


@app.post("/api/tasks/add")
async def post_add_task(payload: AddTaskRequest) -> dict[str, Any]:
    uri = payload.uri.strip()
    if not uri:
        raise HTTPException(status_code=400, detail="任务链接不能为空")

    filename_hint = build_filename_hint(uri, payload.filename_hint)
    if not filename_hint and not payload.force:
        raise HTTPException(
            status_code=400,
            detail="当前任务无法提取文件名，无法做去重校验。请填写“文件名提示”或使用强制下发。",
        )

    if filename_hint and not payload.force:
        await ensure_webdav_scan(force=False)
        matches = await runtime.find_duplicates(filename_hint)
        if matches:
            await runtime.log("warning", "dedupe", f"{filename_hint} 已存在于 WebDAV，任务已拦截。")
            return {
                "ok": False,
                "duplicate": True,
                "message": "网盘已存在，停止添加任务。",
                "filename_hint": filename_hint,
                "matches": matches,
            }

    try:
        if is_torrent_source(uri):
            torrent_bytes = await asyncio.to_thread(load_torrent_bytes, uri)
            encoded = base64.b64encode(torrent_bytes).decode("utf-8")
            gid = await aria2_call("addTorrent", [encoded, [], {}])
        else:
            gid = await aria2_call("addUri", [[uri], {}])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Aria2 下发失败：{exc}") from exc

    task = TaskRecord(
        gid=str(gid),
        name=filename_hint or str(gid),
        source_uri=uri,
        aria2_status="waiting",
        last_message="任务已进入 Aria2 队列。",
    )
    await runtime.save_task(task)
    await runtime.log("info", "aria2", f"任务已下发：{task.name} ({task.gid})")
    asyncio.create_task(sync_aria2_once())
    return {
        "ok": True,
        "duplicate": False,
        "gid": str(gid),
        "filename_hint": filename_hint,
        "message": "任务已成功下发到 Aria2。",
    }


@app.post("/api/tasks/{gid}/retry-upload")
async def post_retry_upload(gid: str) -> dict[str, Any]:
    task = await runtime.get_task(gid)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    current_job = runtime.upload_jobs.get(gid)
    if current_job and not current_job.done():
        return {"ok": True, "message": "上传任务已在执行中。"}
    runtime.upload_jobs[gid] = asyncio.create_task(run_upload_pipeline(gid))
    await runtime.log("warning", "upload", f"手动重试上传：{task.name}")
    return {"ok": True, "message": "已重新触发上传搬运。"}


@app.post("/api/aria2/hook")
async def post_aria2_hook(payload: HookRequest) -> dict[str, Any]:
    expected = str(runtime.settings.get("hook_token", "")).strip()
    if expected and payload.token != expected:
        raise HTTPException(status_code=403, detail="hook token 无效")

    try:
        status = await aria2_call("tellStatus", [payload.gid, ARIA2_KEYS])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取任务状态失败：{exc}") from exc

    await runtime.apply_aria2_snapshot([build_task_from_aria2(status)])
    current_job = runtime.upload_jobs.get(payload.gid)
    if current_job and not current_job.done():
        return {"ok": True, "message": "上传任务已在执行中。"}
    runtime.upload_jobs[payload.gid] = asyncio.create_task(run_upload_pipeline(payload.gid))
    await runtime.log("info", "hook", f"收到 Aria2 完成回调：{payload.gid}")
    return {"ok": True, "gid": payload.gid, "message": "上传搬运流程已启动。"}


@app.websocket("/ws/events")
async def websocket_events(socket: WebSocket) -> None:
    await runtime.register_socket(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        await runtime.unregister_socket(socket)
