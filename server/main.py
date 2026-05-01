from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import smtplib
import ssl
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Header, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = ROOT_DIR / "public"
DATA_DIR = ROOT_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
AUTH_FILE = DATA_DIR / "auth.json"
RSS_FILE = DATA_DIR / "rss.json"
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
    "api_token": "",
    "rss_poll_interval_minutes": 15,
    # Mikan
    "mikan_domain": "mikanani.me",
    # TMDB
    "tmdb_api_key": "",
    "tmdb_api_url": "https://api.themoviedb.org/3",
    # Bangumi
    "bangumi_access_token": "",
    # 通知
    "notify_telegram_bot_token": "",
    "notify_telegram_chat_id": "",
    "notify_email_smtp_host": "",
    "notify_email_smtp_port": 465,
    "notify_email_smtp_user": "",
    "notify_email_smtp_password": "",
    "notify_email_from": "",
    "notify_email_to": "",
    "notify_serverchan_key": "",
    # 重命名
    "max_filename_length": 200,
    # 全局排除
    "global_exclude_patterns": [],
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
SENSITIVE_KEYS = {
    "aria2_rpc_secret", "hook_token", "api_token",
    "tmdb_api_key", "bangumi_access_token",
    "notify_telegram_bot_token", "notify_email_smtp_password",
}
SNAPSHOT_THROTTLE_SECONDS = 1.0


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def mask_settings(settings: dict[str, Any]) -> dict[str, Any]:
    masked = dict(settings)
    for key in SENSITIVE_KEYS:
        if key in masked and masked[key]:
            masked[key] = "***"
    return masked


async def verify_api_token(x_api_token: str = Header(default="")) -> None:
    expected = str(runtime.settings.get("api_token", "")).strip()
    if not expected:
        return
    if x_api_token != expected:
        raise HTTPException(status_code=401, detail="API token 无效")


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
    current = load_settings()
    merged = {**DEFAULT_SETTINGS, **settings}
    # Don't let masked values overwrite real secrets
    for key in SENSITIVE_KEYS:
        if merged.get(key) == "***":
            merged[key] = current.get(key, DEFAULT_SETTINGS.get(key, ""))
    ensure_runtime_paths(merged)
    SETTINGS_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return merged


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """哈希密码，返回 (hash, salt)"""
    if salt is None:
        salt = secrets.token_hex(16)
    password_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return password_hash, salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """验证密码"""
    computed_hash, _ = hash_password(password, salt)
    return computed_hash == password_hash


def load_auth() -> dict[str, Any] | None:
    """加载认证信息"""
    if not AUTH_FILE.exists():
        return None
    try:
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        return None


def save_auth(username: str, password: str) -> dict[str, Any]:
    """保存认证信息"""
    password_hash, salt = hash_password(password)
    auth_data = {
        "username": username,
        "password_hash": password_hash,
        "salt": salt,
    }
    AUTH_FILE.write_text(
        json.dumps(auth_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return auth_data


def generate_token() -> str:
    """生成随机 token"""
    return secrets.token_hex(32)


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
    api_token: str = ""
    rss_poll_interval_minutes: int = Field(default=15, ge=1, le=1440)
    # Mikan
    mikan_domain: str = "mikanani.me"
    # TMDB
    tmdb_api_key: str = ""
    tmdb_api_url: str = "https://api.themoviedb.org/3"
    # Bangumi
    bangumi_access_token: str = ""
    # 通知
    notify_telegram_bot_token: str = ""
    notify_telegram_chat_id: str = ""
    notify_email_smtp_host: str = ""
    notify_email_smtp_port: int = Field(default=465, ge=1, le=65535)
    notify_email_smtp_user: str = ""
    notify_email_smtp_password: str = ""
    notify_email_from: str = ""
    notify_email_to: str = ""
    notify_serverchan_key: str = ""
    # 重命名
    max_filename_length: int = Field(default=200, ge=50, le=500)
    # 全局排除
    global_exclude_patterns: list[str] = Field(default_factory=list)


class AuthSetupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# ── RSS models ──────────────────────────────────────────────

DEFAULT_EPISODE_PATTERNS = [
    r"(?<=-\ )(\d{1,3}|\d{1,3}\.\d{1,2})(?:v\d{1,2})?(?:END)?(?=\ )",
    r"(?<=\[)(\d{1,3}|\d{1,3}\.\d{1,2})(?:v\d{1,2})?(?:END)?(?=])",
    r"(?<=\【)(\d{1,3}|\d{1,3}\.\d{1,2})(?:v\d{1,2})?(?:END)?(?=】)",
]


# ── 追番订阅数据模型 ────────────────────────────────────

@dataclass
class EpisodeInfo:
    title: str = ""
    downloaded_at: str = ""
    file_path: str = ""
    quality: str = ""
    subgroup: str = ""
    source: str = "primary"  # "primary" or "standby"
    aria2_gid: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "downloaded_at": self.downloaded_at,
            "file_path": self.file_path,
            "quality": self.quality,
            "subgroup": self.subgroup,
            "source": self.source,
            "aria2_gid": self.aria2_gid,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EpisodeInfo":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Subscription:
    id: str
    name: str
    enabled: bool = True

    # RSS 源
    rss_url: str = ""
    standby_rss_url: str = ""
    mikan_url: str = ""
    feed_id: str = ""

    # 元数据
    tmdb_id: str = ""
    bangumi_url: str = ""
    bangumi_id: str = ""
    poster_url: str = ""
    description: str = ""
    air_date: str = ""
    season: int = 1
    total_episodes: int = 0

    # 过滤
    match_pattern: str = ""
    exclude_pattern: str = ""
    episode_regex: str = ""
    episode_group_index: int = 1
    episode_offset: int = 0

    # 下载追踪
    last_episode: int = 0
    downloaded_episodes: dict[str, EpisodeInfo] = field(default_factory=dict)

    # 行为标志
    auto_disable_when_complete: bool = True
    download_dir_template: str = ""
    theater_mode: bool = False
    theater_save_path: str = ""
    skip_half_episodes: bool = False
    download_only_latest: bool = False
    omission_detection: bool = False
    slacking_days: int = 0

    # 通知
    notify_on_download: bool = True
    notify_on_complete: bool = True
    notify_on_missing: bool = False

    # 时间戳
    created_at: str = ""
    updated_at: str = ""
    last_checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "rss_url": self.rss_url,
            "standby_rss_url": self.standby_rss_url,
            "mikan_url": self.mikan_url,
            "feed_id": self.feed_id,
            "tmdb_id": self.tmdb_id,
            "bangumi_url": self.bangumi_url,
            "bangumi_id": self.bangumi_id,
            "poster_url": self.poster_url,
            "description": self.description,
            "air_date": self.air_date,
            "season": self.season,
            "total_episodes": self.total_episodes,
            "match_pattern": self.match_pattern,
            "exclude_pattern": self.exclude_pattern,
            "episode_regex": self.episode_regex,
            "episode_group_index": self.episode_group_index,
            "episode_offset": self.episode_offset,
            "last_episode": self.last_episode,
            "downloaded_episodes": {k: v.to_dict() for k, v in self.downloaded_episodes.items()},
            "auto_disable_when_complete": self.auto_disable_when_complete,
            "download_dir_template": self.download_dir_template,
            "theater_mode": self.theater_mode,
            "theater_save_path": self.theater_save_path,
            "skip_half_episodes": self.skip_half_episodes,
            "download_only_latest": self.download_only_latest,
            "omission_detection": self.omission_detection,
            "slacking_days": self.slacking_days,
            "notify_on_download": self.notify_on_download,
            "notify_on_complete": self.notify_on_complete,
            "notify_on_missing": self.notify_on_missing,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_checked_at": self.last_checked_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Subscription":
        ep_raw = d.get("downloaded_episodes", {})
        ep_dict: dict[str, EpisodeInfo] = {}
        if isinstance(ep_raw, dict):
            for k, v in ep_raw.items():
                ep_dict[k] = EpisodeInfo.from_dict(v) if isinstance(v, dict) else EpisodeInfo(title=str(v))
        elif isinstance(ep_raw, list):
            for item in ep_raw:
                ep_dict[str(item)] = EpisodeInfo(title=str(item))
        known = {f.name for f in cls.__dataclass_fields__.values() if hasattr(f, 'name')}
        kwargs = {k: v for k, v in d.items() if k in known and k != "downloaded_episodes"}
        kwargs["downloaded_episodes"] = ep_dict
        return cls(**kwargs)


# 通知模板
DEFAULT_NOTIFICATION_TEMPLATES: dict[str, str] = {
    "download_started": "开始下载：${title} - EP${episode}\n质量：${quality} | 字幕组：${subgroup}",
    "download_completed": "下载完成：${title} - EP${episode}\n保存路径：${file_path}",
    "subscription_completed": "追番完结：${title}\n共 ${total_episodes} 集",
    "episode_missing": "缺集警告：${title}\n期望 EP${expected}，未在 RSS 中发现",
    "slacking_detected": "更新停滞：${title}\n距上次更新已 ${days} 天",
}

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".wmv", ".flv", ".mov", ".ts", ".m2ts"}
SUBTITLE_EXTENSIONS = {".ass", ".srt", ".sub", ".ssa", ".idx", ".sup"}


def load_subscriptions() -> list[Subscription]:
    """加载追番订阅数据。"""
    if not RSS_FILE.exists():
        return []
    try:
        raw = json.loads(RSS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        return []

    # 兼容旧格式：将 rules 迁移为 subscriptions
    if raw.get("version", 1) < 2:
        subs = _migrate_rss_v1(raw)
        return subs

    return [Subscription.from_dict(s) for s in raw.get("subscriptions", [])]


def _migrate_rss_v1(raw: dict) -> list[Subscription]:
    """将 v1 的 rules 迁移为 subscriptions。"""
    feeds = raw.get("feeds", [])
    rules = raw.get("rules", [])
    feed_map = {f["id"]: f for f in feeds}
    subs: list[Subscription] = []
    for rule in rules:
        rss_url = ""
        feed_id = ""
        if rule.get("feed_ids"):
            first_feed = feed_map.get(rule["feed_ids"][0])
            if first_feed:
                rss_url = first_feed.get("url", "")
                feed_id = first_feed.get("id", "")
        ep_dict: dict[str, EpisodeInfo] = {}
        for ep_str in rule.get("downloaded_episodes", []):
            ep_dict[str(ep_str)] = EpisodeInfo(title=str(ep_str))
        sub = Subscription(
            id=rule.get("id", uuid.uuid4().hex[:12]),
            name=rule.get("name", ""),
            enabled=rule.get("enabled", True),
            rss_url=rss_url,
            feed_id=feed_id,
            match_pattern=rule.get("filter_pattern", ""),
            episode_regex=rule.get("episode_regex", ""),
            last_episode=rule.get("last_episode", 0),
            downloaded_episodes=ep_dict,
            download_dir_template=rule.get("download_dir", ""),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        subs.append(sub)
    # 备份旧文件
    backup = DATA_DIR / "rss.json.v1.bak"
    if not backup.exists():
        shutil.copy2(str(RSS_FILE), str(backup))
    # 保存新格式
    save_subscriptions(subs)
    return subs


def save_subscriptions(subs: list[Subscription]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "version": 2,
        "subscriptions": [s.to_dict() for s in subs],
    }
    content = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(RSS_FILE))
    except BaseException:
        os.unlink(tmp_path)
        raise


# ── 追番订阅请求模型 ────────────────────────────────────

class CreateSubscriptionRequest(BaseModel):
    name: str
    rss_url: str = ""
    mikan_url: str = ""
    standby_rss_url: str = ""
    tmdb_id: str = ""
    bangumi_url: str = ""
    match_pattern: str = ""
    exclude_pattern: str = ""
    episode_regex: str = ""
    episode_group_index: int = 1
    episode_offset: int = 0
    auto_disable_when_complete: bool = True
    download_dir_template: str = ""
    theater_mode: bool = False
    theater_save_path: str = ""
    skip_half_episodes: bool = False
    download_only_latest: bool = False
    omission_detection: bool = False
    slacking_days: int = 0
    notify_on_download: bool = True
    notify_on_complete: bool = True
    notify_on_missing: bool = False
    season: int = 1
    total_episodes: int = 0


class UpdateSubscriptionRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    rss_url: str | None = None
    standby_rss_url: str | None = None
    mikan_url: str | None = None
    tmdb_id: str | None = None
    bangumi_url: str | None = None
    match_pattern: str | None = None
    exclude_pattern: str | None = None
    episode_regex: str | None = None
    episode_group_index: int | None = None
    episode_offset: int | None = None
    auto_disable_when_complete: bool | None = None
    download_dir_template: str | None = None
    theater_mode: bool | None = None
    theater_save_path: str | None = None
    skip_half_episodes: bool | None = None
    download_only_latest: bool | None = None
    omission_detection: bool | None = None
    slacking_days: int | None = None
    notify_on_download: bool | None = None
    notify_on_complete: bool | None = None
    notify_on_missing: bool | None = None
    season: int | None = None
    total_episodes: int | None = None


class MarkEpisodeRequest(BaseModel):
    episode: str
    title: str = ""


class MikanParseRequest(BaseModel):
    url: str


class TmdbFetchRequest(BaseModel):
    tmdb_id: str


class BangumiFetchRequest(BaseModel):
    url: str


class NotifyTestRequest(BaseModel):
    message: str = "这是一条测试通知"


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
        self._last_snapshot_ts: float = 0.0
        self._snapshot_pending: bool = False
        self._poll_failures: int = 0
        # 认证相关
        self.auth_data: dict[str, Any] | None = load_auth()
        self.auth_tokens: dict[str, str] = {}  # token -> username
        # 追番订阅
        self.subscriptions: list[Subscription] = load_subscriptions()
        self._rss_seen_guids: OrderedDict[str, None] = OrderedDict()  # 已处理过的 RSS 条目 GUID，保序
        self._notification_templates: dict[str, str] = dict(DEFAULT_NOTIFICATION_TEMPLATES)

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
                "settings": mask_settings(self.settings),
                "tasks": tasks,
                "logs": list(self.logs),
                "remote_files": list(self.remote_files),
                "local_files": list(self.local_files),
                "last_scan_at": self.last_scan_at,
                "stats": stats,
                "subscriptions": [s.to_dict() for s in self.subscriptions],
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

    async def broadcast_snapshot(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_snapshot_ts < SNAPSHOT_THROTTLE_SECONDS:
            self._snapshot_pending = True
            return
        self._last_snapshot_ts = now
        self._snapshot_pending = False
        await self.broadcast({"type": "snapshot", "data": await self.snapshot()})

    async def flush_pending_snapshot(self) -> None:
        if self._snapshot_pending:
            self._last_snapshot_ts = time.time()
            self._snapshot_pending = False
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
        # 有活跃下载时跳过节流，立即推送进度
        has_active = any(r.aria2_status == "active" for r in records)
        await self.broadcast_snapshot(force=has_active)


runtime = AppRuntime()


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
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(request, timeout=10, context=ctx) as response:
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


async def sync_active_only() -> None:
    """仅轮询活跃任务，用于下载中的快速进度更新。"""
    try:
        active = await aria2_call("tellActive", [ARIA2_KEYS])
    except Exception:
        return
    records = [build_task_from_aria2(item) for item in active]
    if records:
        await runtime.apply_aria2_snapshot(records)


async def poll_aria2_forever() -> None:
    FAST_INTERVAL = 1  # 有活跃下载时每秒刷新
    while True:
        base_interval = int(runtime.settings.get("aria2_poll_interval_seconds", 3))
        await sync_aria2_once()
        await runtime.flush_pending_snapshot()
        if runtime.aria2_online:
            runtime._poll_failures = 0
            # 有活跃下载时加速轮询
            has_active = any(t.aria2_status == "active" for t in runtime.tasks.values())
            delay = FAST_INTERVAL if has_active else base_interval
        else:
            runtime._poll_failures += 1
            delay = min(base_interval * (2 ** runtime._poll_failures), 60)
        await asyncio.sleep(delay)


def parse_rss_feed(xml_text: str) -> list[dict[str, str]]:
    """解析 RSS 2.0 / Atom XML，返回 [{title, link, guid}] 列表。"""
    items: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    ATOM_NS = "{http://www.w3.org/2005/Atom}"

    # RSS 2.0
    for item_el in root.iter("item"):
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        guid = (item_el.findtext("guid") or link or title).strip()
        enclosure = item_el.find("enclosure")
        if enclosure is not None and enclosure.get("url"):
            link = enclosure.get("url", link)
        if title and link:
            items.append({"title": title, "link": link, "guid": guid})

    # Atom — 优先取 rel="enclosure" 的 link
    for entry_el in root.iter(f"{ATOM_NS}entry"):
        title = (entry_el.findtext(f"{ATOM_NS}title") or "").strip()
        link_els = entry_el.findall(f"{ATOM_NS}link")
        link = ""
        for link_el in link_els:
            rel = link_el.get("rel", "alternate")
            href = link_el.get("href", "").strip()
            if rel == "enclosure" and href:
                link = href
                break
            if rel == "alternate" and href and not link:
                link = href
        if not link and link_els:
            link = link_els[0].get("href", "").strip()
        guid = (entry_el.findtext(f"{ATOM_NS}id") or link or title).strip()
        if title and link:
            items.append({"title": title, "link": link, "guid": guid})

    return items


def extract_episode(title: str, regex: str) -> int | None:
    """从标题中用正则提取集数，返回整数或 None。"""
    try:
        m = re.search(regex, title)
    except re.error:
        return None
    if not m:
        return None
    # 取第一个捕获组
    raw = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def _fetch_url(url: str, headers: dict[str, str] | None = None) -> str:
    """同步拉取 URL 内容（供 asyncio.to_thread 调用）。"""
    hdrs = {"User-Agent": "Aria2Plus/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _fetch_url_json(url: str, headers: dict[str, str] | None = None) -> Any:
    """同步拉取 JSON（供 asyncio.to_thread 调用）。"""
    hdrs = {"User-Agent": "Aria2Plus/1.0", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Mikan 集成 ────────────────────────────────────────────

def parse_mikan_url(url: str) -> dict[str, str]:
    """从 Mikan 页面 URL 提取 bangumiId 和 subgroupId。
    支持格式：
      https://mikanani.me/Home/Bangumi/1234
      https://mikanani.me/Home/Bangumi/1234?subgroupid=567
      https://mikanani.me/Home/Expand?bangumiId=1234&subgroupid=567
    """
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    bangumi_id = ""
    subgroup_id = ""

    # 从路径提取 /Home/Bangumi/1234
    path_parts = parsed.path.rstrip("/").split("/")
    if "Bangumi" in path_parts:
        idx = path_parts.index("Bangumi")
        if idx + 1 < len(path_parts):
            bangumi_id = path_parts[idx + 1]

    # 从查询参数提取
    if not bangumi_id:
        bangumi_id = qs.get("bangumiId", [""])[0]
    subgroup_id = qs.get("subgroupid", [""])[0]

    return {"bangumi_id": bangumi_id, "subgroup_id": subgroup_id}


def build_mikan_rss_url(bangumi_id: str, subgroup_id: str = "", domain: str = "mikanani.me") -> str:
    """从 bangumiId 和 subgroupId 生成 Mikan RSS URL。"""
    if subgroup_id:
        return f"https://{domain}/RSS/Bangumi?bangumiId={bangumi_id}&subgroupid={subgroup_id}"
    return f"https://{domain}/RSS/Bangumi?bangumiId={bangumi_id}"


# ── TMDB 集成 ─────────────────────────────────────────────

def _fetch_tmdb_info(tmdb_id: str, api_key: str, api_url: str) -> dict[str, Any]:
    """从 TMDB 获取动漫元数据。"""
    url = f"{api_url}/tv/{tmdb_id}?api_key={api_key}&language=zh-CN"
    data = _fetch_url_json(url)
    poster_path = data.get("poster_path", "")
    return {
        "poster_url": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else "",
        "description": data.get("overview", ""),
        "air_date": data.get("first_air_date", ""),
        "name": data.get("name", ""),
        "original_name": data.get("original_name", ""),
        "number_of_seasons": data.get("number_of_seasons", 0),
        "number_of_episodes": data.get("number_of_episodes", 0),
        "genres": [g["name"] for g in data.get("genres", [])],
        "vote_average": data.get("vote_average", 0),
    }


def generate_nfo(sub: Subscription, tmdb_info: dict[str, Any]) -> str:
    """生成 Emby/Jellyfin 兼容的 NFO XML。"""
    tvshow = ET.Element("tvshow")
    ET.SubElement(tvshow, "title").text = sub.name
    ET.SubElement(tvshow, "originaltitle").text = tmdb_info.get("original_name", "")
    ET.SubElement(tvshow, "plot").text = tmdb_info.get("description", "")
    ET.SubElement(tvshow, "premiered").text = tmdb_info.get("air_date", "")
    ET.SubElement(tvshow, "season").text = str(sub.season)
    ET.SubElement(tvshow, "episode").text = str(sub.total_episodes)
    if tmdb_info.get("vote_average"):
        ET.SubElement(tvshow, "rating").text = str(tmdb_info["vote_average"])
    for genre in tmdb_info.get("genres", []):
        ET.SubElement(tvshow, "genre").text = genre
    if sub.tmdb_id:
        uniqueid = ET.SubElement(tvshow, "uniqueid", type="tmdb")
        uniqueid.text = sub.tmdb_id
    if sub.bangumi_id:
        uniqueid = ET.SubElement(tvshow, "uniqueid", type="bangumi")
        uniqueid.text = sub.bangumi_id
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(tvshow, encoding="unicode")


# ── Bangumi 集成 ──────────────────────────────────────────

def parse_bangumi_url(url: str) -> str | None:
    """从 bgm.tv URL 提取 subject ID。"""
    parsed = urllib.parse.urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "subject":
        try:
            return str(int(parts[1]))
        except ValueError:
            pass
    return None


def _fetch_bangumi_info(subject_id: str, access_token: str = "") -> dict[str, Any]:
    """从 Bangumi API 获取动漫信息。"""
    url = f"https://api.bgm.tv/v0/subjects/{subject_id}"
    headers: dict[str, str] = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    data = _fetch_url_json(url, headers)
    # 获取剧集数
    eps_data = _fetch_url_json(f"https://api.bgm.tv/v0/subjects/{subject_id}/episodes?limit=100&type=0", headers)
    total_eps = eps_data.get("total", 0)
    return {
        "name": data.get("name", ""),
        "name_cn": data.get("name_cn", ""),
        "summary": data.get("summary", ""),
        "air_date": data.get("date", ""),
        "total_episodes": total_eps,
        "rating": data.get("rating", {}).get("score", 0),
        "image": data.get("images", {}).get("large", ""),
    }


# ── 通知系统 ──────────────────────────────────────────────

def render_template(template: str, variables: dict[str, str]) -> str:
    """渲染 ${var} 模板变量。"""
    result = template
    for key, value in variables.items():
        result = result.replace(f"${{{key}}}", value)
    return result


async def send_telegram(token: str, chat_id: str, message: str) -> None:
    """通过 Telegram Bot API 发送消息。"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    await asyncio.to_thread(urllib.request.urlopen, req, timeout=15, context=ctx)


async def send_email(
    smtp_host: str, smtp_port: int, user: str, password: str,
    from_addr: str, to_addrs: str, subject: str, body: str,
) -> None:
    """通过 SMTP 发送邮件。"""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addrs

    def _send() -> None:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            server.starttls()
        try:
            server.login(user, password)
            server.sendmail(from_addr, [to_addrs], msg.as_string())
        finally:
            server.quit()

    await asyncio.to_thread(_send)


async def send_serverchan(key: str, title: str, message: str) -> None:
    """通过 Server酱 发送通知。"""
    url = f"https://sctapi.ftqq.com/{key}.send"
    payload = json.dumps({"title": title, "desp": message}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    await asyncio.to_thread(urllib.request.urlopen, req, timeout=15, context=ctx)


async def dispatch_notification(
    event_type: str,
    sub: Subscription,
    variables: dict[str, str],
    settings: dict[str, Any] | None = None,
) -> None:
    """渲染模板并分发通知到所有已配置的渠道。"""
    if settings is None:
        settings = runtime.settings
    template = runtime._notification_templates.get(event_type, "")
    if not template:
        return
    message = render_template(template, variables)
    title = f"[Aria2 Plus] {sub.name}"

    # Telegram
    tg_token = str(settings.get("notify_telegram_bot_token", "")).strip()
    tg_chat = str(settings.get("notify_telegram_chat_id", "")).strip()
    if tg_token and tg_chat:
        try:
            await send_telegram(tg_token, tg_chat, f"<b>{title}</b>\n{message}")
        except Exception as exc:
            await runtime.log("warning", "notify", f"Telegram 通知失败: {exc}")

    # Email
    smtp_host = str(settings.get("notify_email_smtp_host", "")).strip()
    smtp_user = str(settings.get("notify_email_smtp_user", "")).strip()
    smtp_pass = str(settings.get("notify_email_smtp_password", "")).strip()
    email_to = str(settings.get("notify_email_to", "")).strip()
    if smtp_host and smtp_user and smtp_pass and email_to:
        try:
            await send_email(
                smtp_host,
                int(settings.get("notify_email_smtp_port", 465)),
                smtp_user,
                smtp_pass,
                str(settings.get("notify_email_from", smtp_user)),
                email_to,
                title,
                message,
            )
        except Exception as exc:
            await runtime.log("warning", "notify", f"邮件通知失败: {exc}")

    # Server酱
    sckey = str(settings.get("notify_serverchan_key", "")).strip()
    if sckey:
        try:
            await send_serverchan(sckey, title, message)
        except Exception as exc:
            await runtime.log("warning", "notify", f"Server酱通知失败: {exc}")


# ── 检测与智能 ─────────────────────────────────────────────

def detect_episode_gaps(downloaded: dict[str, EpisodeInfo], last_episode: int) -> list[int]:
    """检测缺集：返回 1..last_episode 中缺失的集数列表。"""
    if last_episode <= 0:
        return []
    downloaded_eps: set[int] = set()
    for ep_str in downloaded:
        try:
            downloaded_eps.add(int(float(ep_str)))
        except (ValueError, TypeError):
            pass
    return sorted(set(range(1, last_episode + 1)) - downloaded_eps)


def check_completion(sub: Subscription) -> bool:
    """检查订阅是否已完结（已下载集数 >= 总集数）。"""
    if not sub.auto_disable_when_complete or sub.total_episodes <= 0:
        return False
    unique_eps: set[int] = set()
    for ep_str in sub.downloaded_episodes:
        try:
            unique_eps.add(int(float(ep_str)))
        except (ValueError, TypeError):
            pass
    return len(unique_eps) >= sub.total_episodes


def render_save_path(template: str, sub: Subscription, episode: str = "") -> str:
    """渲染下载目录模板变量。"""
    if not template:
        return ""
    variables = {
        "title": sub.name,
        "season": str(sub.season).zfill(2),
        "seasonFormat": f"S{str(sub.season).zfill(2)}",
        "episode": episode,
    }
    return render_template(template, variables)


def extract_episode_v2(title: str, regex: str, group_index: int = 1) -> int | None:
    """从标题提取集数，支持指定捕获组索引。"""
    try:
        m = re.search(regex, title)
    except re.error:
        return None
    if not m:
        return None
    if m.lastindex and m.lastindex >= group_index:
        raw = m.group(group_index)
    elif m.lastindex and m.lastindex >= 1:
        raw = m.group(1)
    else:
        raw = m.group(0)
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def extract_quality(title: str) -> str:
    """从标题中提取分辨率信息。"""
    patterns = [r"2160[pP]", r"1080[pP]", r"720[pP]", r"480[pP]", r"4[Kk]"]
    for p in patterns:
        m = re.search(p, title)
        if m:
            return m.group(0)
    return ""


def extract_subgroup(title: str) -> str:
    """从标题中提取字幕组名称（方括号内容）。"""
    m = re.match(r"^\[([^\]]+)\]", title)
    if m:
        return m.group(1)
    return ""


def classify_download_files(file_paths: list[str]) -> dict[str, list[str]]:
    """将下载文件分类为视频、字幕和其他。"""
    result: dict[str, list[str]] = {"video": [], "subtitle": [], "other": []}
    for fp in file_paths:
        ext = Path(fp).suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            result["video"].append(fp)
        elif ext in SUBTITLE_EXTENSIONS:
            result["subtitle"].append(fp)
        else:
            result["other"].append(fp)
    return result


def render_rename_template(template: str, variables: dict[str, str]) -> str:
    """渲染重命名模板变量。"""
    return render_template(template, variables)


async def check_rss_feeds() -> None:
    """检查所有启用的追番订阅，自动下载新集。"""
    async with runtime.lock:
        subs_snapshot = list(runtime.subscriptions)

    enabled_subs = [s for s in subs_snapshot if s.enabled and s.rss_url]
    if not enabled_subs:
        return

    settings = runtime.settings
    global_excludes = settings.get("global_exclude_patterns", [])
    mikan_domain = str(settings.get("mikan_domain", "mikanani.me"))

    for sub in enabled_subs:
        await _check_single_subscription(sub, settings, global_excludes, mikan_domain)

    async with runtime.lock:
        save_subscriptions(runtime.subscriptions)
    await runtime.broadcast_snapshot()


async def _check_single_subscription(
    sub: Subscription,
    settings: dict[str, Any],
    global_excludes: list[str],
    mikan_domain: str,
) -> None:
    """检查单个订阅的 RSS 源。"""
    # 获取主 RSS
    try:
        xml_text = await asyncio.to_thread(_fetch_url, sub.rss_url)
    except Exception as exc:
        await runtime.log("error", "rss", f"拉取 RSS 失败 [{sub.name}]: {exc}")
        return

    items = parse_rss_feed(xml_text)
    sub.last_checked_at = utc_now()

    # 获取备用 RSS（洗版）
    standby_items: list[dict[str, str]] = []
    if sub.standby_rss_url:
        try:
            standby_xml = await asyncio.to_thread(_fetch_url, sub.standby_rss_url)
            standby_items = parse_rss_feed(standby_xml)
        except Exception as exc:
            await runtime.log("warning", "rss", f"备用 RSS 拉取失败 [{sub.name}]: {exc}")

    downloaded_any = False

    # 处理主 RSS 条目
    for item in items:
        await _process_rss_item(sub, item, "primary", settings, global_excludes)
        downloaded_any = True

    # 处理备用 RSS 条目（洗版）
    for item in standby_items:
        await _process_standby_item(sub, item, settings, global_excludes)

    # 后续检测
    if sub.omission_detection and sub.last_episode > 0:
        gaps = detect_episode_gaps(sub.downloaded_episodes, sub.last_episode)
        if gaps and sub.notify_on_missing:
            gap_str = ", ".join(str(g) for g in gaps[:10])
            await dispatch_notification("episode_missing", sub, {
                "title": sub.name,
                "expected": gap_str,
            })
            await runtime.log("warning", "rss", f"[{sub.name}] 缺集检测：缺少 EP {gap_str}")

    if check_completion(sub):
        sub.enabled = False
        await runtime.log("info", "rss", f"[{sub.name}] 追番完结，自动暂停。")
        await dispatch_notification("subscription_completed", sub, {
            "title": sub.name,
            "total_episodes": str(sub.total_episodes),
        })

    sub.updated_at = utc_now()


async def _process_rss_item(
    sub: Subscription,
    item: dict[str, str],
    source: str,
    settings: dict[str, Any],
    global_excludes: list[str],
) -> bool:
    """处理单个 RSS 条目，返回是否触发了下载。"""
    title = item["title"]
    link = item["link"]
    guid = item.get("guid", link)

    if guid in runtime._rss_seen_guids:
        return False

    # 全局排除
    for pattern in global_excludes:
        try:
            if re.search(pattern, title, re.IGNORECASE):
                runtime._rss_seen_guids[guid] = None
                return False
        except re.error:
            pass

    # 订阅排除
    if sub.exclude_pattern:
        try:
            if re.search(sub.exclude_pattern, title, re.IGNORECASE):
                return False
        except re.error:
            pass

    # 标题匹配
    if sub.match_pattern:
        try:
            if not re.search(sub.match_pattern, title, re.IGNORECASE):
                return False
        except re.error as exc:
            await runtime.log("warning", "rss", f"[{sub.name}] 匹配正则无效: {exc}")
            return False

    # 提取集数
    ep = extract_episode_v2(title, sub.episode_regex, sub.episode_group_index) if sub.episode_regex else None
    if ep is not None and sub.episode_offset:
        ep = ep + sub.episode_offset

    # 跳过半集
    if sub.skip_half_episodes and ep is not None:
        raw_check = re.search(r"(\d+)\.5", title)
        if raw_check:
            return False

    ep_str = str(ep) if ep is not None else title

    # 已下载检查
    if ep_str in sub.downloaded_episodes:
        runtime._rss_seen_guids[guid] = None
        return False

    # 仅下载最新模式
    if sub.download_only_latest and ep is not None:
        if ep < sub.last_episode and sub.last_episode > 0:
            return False

    # 解析下载目录
    opts: dict[str, Any] = {}
    if sub.download_dir_template:
        resolved_dir = render_save_path(sub.download_dir_template, sub, ep_str)
        if resolved_dir:
            opts["dir"] = resolved_dir
    elif sub.theater_mode and sub.theater_save_path:
        opts["dir"] = sub.theater_save_path

    # 下载
    try:
        gid = await aria2_call("addUri", [[link], opts])
    except Exception as exc:
        await runtime.log("error", "rss", f"RSS 自动下载失败 [{title}]: {exc}")
        return False

    # 更新追踪
    quality = extract_quality(title)
    subgroup = extract_subgroup(title)
    ep_info = EpisodeInfo(
        title=title,
        downloaded_at=utc_now(),
        quality=quality,
        subgroup=subgroup,
        source=source,
        aria2_gid=str(gid),
    )
    sub.downloaded_episodes[ep_str] = ep_info
    if len(sub.downloaded_episodes) > 500:
        oldest = list(sub.downloaded_episodes.keys())[:len(sub.downloaded_episodes) - 500]
        for k in oldest:
            del sub.downloaded_episodes[k]

    if ep is not None and ep > sub.last_episode:
        sub.last_episode = ep

    # 创建任务记录
    task = TaskRecord(
        gid=str(gid),
        name=title,
        source_uri=link,
        aria2_status="waiting",
        last_message=f"追番自动下载：{sub.name} EP{ep_str}",
    )
    await runtime.save_task(task)
    await runtime.log("info", "rss", f"追番自动下载：{title} (订阅: {sub.name}, EP{ep_str}, GID: {gid})")

    # 通知
    if sub.notify_on_download:
        await dispatch_notification("download_started", sub, {
            "title": sub.name,
            "episode": ep_str,
            "quality": quality,
            "subgroup": subgroup,
        })

    runtime._rss_seen_guids[guid] = None
    # 限制 seen 大小
    while len(runtime._rss_seen_guids) > 10000:
        runtime._rss_seen_guids.popitem(last=False)
    return True


async def _process_standby_item(
    sub: Subscription,
    item: dict[str, str],
    settings: dict[str, Any],
    global_excludes: list[str],
) -> None:
    """处理备用 RSS 条目（洗版逻辑）。"""
    title = item["title"]
    link = item["link"]

    # 全局排除
    for pattern in global_excludes:
        try:
            if re.search(pattern, title, re.IGNORECASE):
                return
        except re.error:
            pass

    if sub.exclude_pattern:
        try:
            if re.search(sub.exclude_pattern, title, re.IGNORECASE):
                return
        except re.error:
            pass

    if sub.match_pattern:
        try:
            if not re.search(sub.match_pattern, title, re.IGNORECASE):
                return
        except re.error:
            return

    ep = extract_episode_v2(title, sub.episode_regex, sub.episode_group_index) if sub.episode_regex else None
    if ep is not None and sub.episode_offset:
        ep = ep + sub.episode_offset
    ep_str = str(ep) if ep is not None else title

    # 如果主源已下载该集，跳过
    if ep_str in sub.downloaded_episodes:
        existing = sub.downloaded_episodes[ep_str]
        if existing.source == "primary":
            return  # 主源版本已存在，不覆盖
        # 已是备用版本，也不重复下载
        return

    # 下载备用版本
    opts: dict[str, Any] = {}
    if sub.download_dir_template:
        resolved_dir = render_save_path(sub.download_dir_template, sub, ep_str)
        if resolved_dir:
            opts["dir"] = resolved_dir

    try:
        gid = await aria2_call("addUri", [[link], opts])
    except Exception as exc:
        await runtime.log("error", "rss", f"备用 RSS 下载失败 [{title}]: {exc}")
        return

    quality = extract_quality(title)
    subgroup = extract_subgroup(title)
    ep_info = EpisodeInfo(
        title=title,
        downloaded_at=utc_now(),
        quality=quality,
        subgroup=subgroup,
        source="standby",
        aria2_gid=str(gid),
    )
    sub.downloaded_episodes[ep_str] = ep_info
    if ep is not None and ep > sub.last_episode:
        sub.last_episode = ep

    task = TaskRecord(
        gid=str(gid),
        name=title,
        source_uri=link,
        aria2_status="waiting",
        last_message=f"备用源下载（洗版）：{sub.name} EP{ep_str}",
    )
    await runtime.save_task(task)
    await runtime.log("info", "rss", f"备用源下载（洗版）：{title} (订阅: {sub.name}, EP{ep_str})")

    if sub.notify_on_download:
        await dispatch_notification("download_started", sub, {
            "title": sub.name,
            "episode": ep_str,
            "quality": quality,
            "subgroup": subgroup,
        })


async def poll_rss_forever() -> None:
    """后台循环，定时检查 RSS 源。"""
    # 启动时先等一轮
    await asyncio.sleep(30)
    while True:
        try:
            await check_rss_feeds()
        except Exception as exc:
            await runtime.log("error", "rss", f"RSS 轮询异常：{exc}")
        interval = int(runtime.settings.get("rss_poll_interval_minutes", 15))
        await asyncio.sleep(max(interval, 1) * 60)


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


@asynccontextmanager
async def lifespan(application: FastAPI):
    await refresh_local_cache()
    poller = asyncio.create_task(poll_aria2_forever())
    rss_poller = asyncio.create_task(poll_rss_forever())
    await runtime.log("info", "system", "Aria2 Plus API Server 已启动。")
    yield
    poller.cancel()
    rss_poller.cancel()
    for job in list(runtime.upload_jobs.values()):
        job.cancel()
    runtime.upload_jobs.clear()


# App instantiation after lifespan is defined
app = FastAPI(title="Aria2 Plus", version="1.0.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=str(PUBLIC_DIR)), name="assets")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/api/auth/status")
async def auth_status() -> dict[str, Any]:
    """检查认证状态"""
    return {
        "has_auth": runtime.auth_data is not None,
        "username": runtime.auth_data["username"] if runtime.auth_data else None,
    }


@app.post("/api/auth/setup")
async def auth_setup(payload: AuthSetupRequest) -> dict[str, Any]:
    """首次设置账户"""
    if runtime.auth_data is not None:
        raise HTTPException(status_code=400, detail="账户已存在，请使用登录接口")
    if not payload.username or not payload.password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    runtime.auth_data = save_auth(payload.username, payload.password)
    await runtime.log("info", "auth", f"账户已设置：{payload.username}")
    return {"ok": True, "message": "账户设置成功"}


@app.post("/api/auth/login")
async def auth_login(payload: LoginRequest) -> dict[str, Any]:
    """登录"""
    if runtime.auth_data is None:
        raise HTTPException(status_code=400, detail="请先设置账户")
    if payload.username != runtime.auth_data["username"]:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not verify_password(payload.password, runtime.auth_data["password_hash"], runtime.auth_data["salt"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = generate_token()
    runtime.auth_tokens[token] = payload.username
    await runtime.log("info", "auth", f"用户登录成功：{payload.username}")
    return {"ok": True, "token": token, "username": payload.username}


@app.post("/api/auth/logout")
async def auth_logout(x_api_token: str = Header(default="")) -> dict[str, Any]:
    """登出"""
    if x_api_token in runtime.auth_tokens:
        del runtime.auth_tokens[x_api_token]
    return {"ok": True, "message": "已登出"}


@app.post("/api/auth/change-password")
async def change_password(payload: ChangePasswordRequest) -> dict[str, Any]:
    """修改密码"""
    if runtime.auth_data is None:
        raise HTTPException(400, "未设置账户")
    if not payload.old_password or not payload.new_password:
        raise HTTPException(400, "旧密码和新密码不能为空")
    if len(payload.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    if not verify_password(payload.old_password, runtime.auth_data["password_hash"], runtime.auth_data["salt"]):
        raise HTTPException(401, "旧密码错误")
    runtime.auth_data = save_auth(runtime.auth_data["username"], payload.new_password)
    runtime.auth_tokens.clear()
    await runtime.log("info", "auth", "密码已修改，需重新登录")
    return {"ok": True, "message": "密码修改成功，请重新登录"}


@app.get("/api/export")
async def export_data() -> dict[str, Any]:
    """导出配置数据"""
    import zipfile
    import io
    from fastapi.responses import Response

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # settings.json
        zf.writestr('settings.json', json.dumps(runtime.settings, indent=2, ensure_ascii=False))

        # auth.json
        if runtime.auth_data:
            zf.writestr('auth.json', json.dumps(runtime.auth_data, indent=2, ensure_ascii=False))

        # manifest
        manifest = {
            "exported_at": utc_now(),
            "version": "1.0.0",
            "files": ["settings.json", "auth.json"],
        }
        zf.writestr('export_manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False))

    buffer.seek(0)
    await runtime.log("info", "export", "配置数据已导出")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=aria2-plus-export.zip"},
    )


@app.post("/api/import")
async def import_data(file: bytes = File(...), _: None = Depends(verify_api_token)) -> dict[str, Any]:
    """导入配置数据（ZIP 文件）"""
    import zipfile
    import io

    try:
        buffer = io.BytesIO(file)
        with zipfile.ZipFile(buffer, 'r') as zf:
            names = zf.namelist()

            # 导入 settings.json
            if 'settings.json' in names:
                imported_settings = json.loads(zf.read('settings.json'))
                if isinstance(imported_settings, dict):
                    runtime.settings.update(imported_settings)
                    save_settings(runtime.settings)

            # 导入 auth.json
            if 'auth.json' in names:
                imported_auth = json.loads(zf.read('auth.json'))
                if isinstance(imported_auth, dict) and "username" in imported_auth:
                    AUTH_FILE.write_text(
                        json.dumps(imported_auth, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    runtime.auth_data = imported_auth
                    runtime.auth_tokens.clear()

        await runtime.log("info", "import", "配置数据已导入，需重新登录")
        return {"ok": True, "detail": "导入成功，请重新登录"}
    except zipfile.BadZipFile:
        raise HTTPException(400, "无效的 ZIP 文件")
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}")


@app.get("/api/dashboard")
async def get_dashboard() -> dict[str, Any]:
    return await runtime.snapshot()


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return mask_settings(runtime.settings)


@app.put("/api/settings")
async def put_settings(payload: SettingsRequest, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    runtime.settings = save_settings(payload.model_dump())
    await refresh_local_cache()
    await runtime.log("info", "settings", "系统设置已保存。")
    await runtime.broadcast_snapshot()
    return {"ok": True, "settings": mask_settings(runtime.settings)}


@app.post("/api/webdav/scan")
async def post_webdav_scan(_: None = Depends(verify_api_token)) -> dict[str, Any]:
    files, raw_output = await ensure_webdav_scan(force=True)
    return {
        "ok": True,
        "files": files,
        "raw_output": raw_output,
        "local_files": runtime.local_files,
        "last_scan_at": runtime.last_scan_at,
    }


@app.post("/api/webdav/verify-md5")
async def post_webdav_verify_md5(payload: VerifyMd5Request, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    result = await verify_remote_md5(payload.remote_path, payload.local_file_name)
    return {"ok": True, **result}


@app.post("/api/local/refresh")
async def post_local_refresh(_: None = Depends(verify_api_token)) -> dict[str, Any]:
    files = await refresh_local_cache()
    await runtime.log("info", "local", f"本地下载目录已刷新，共 {len(files)} 个文件。")
    return {"ok": True, "files": files}


@app.post("/api/tasks/add")
async def post_add_task(payload: AddTaskRequest, _: None = Depends(verify_api_token)) -> dict[str, Any]:
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
async def post_retry_upload(gid: str, _: None = Depends(verify_api_token)) -> dict[str, Any]:
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


# ── RSS 工具 endpoints ──────────────────────────────────────

@app.get("/api/rss/presets")
async def get_rss_presets() -> dict[str, Any]:
    return {"episode_patterns": DEFAULT_EPISODE_PATTERNS}


@app.post("/api/rss/check")
async def post_rss_check(_: None = Depends(verify_api_token)) -> dict[str, Any]:
    """手动触发一次 RSS 检查（含自动下载）。"""
    await check_rss_feeds()
    return {"ok": True, "message": "RSS 检查已完成，请查看日志。"}


# ── 追番订阅 endpoints ───────────────────────────────────

@app.get("/api/subscriptions")
async def get_subscriptions() -> list[dict[str, Any]]:
    return [s.to_dict() for s in runtime.subscriptions]


@app.post("/api/subscriptions")
async def post_subscription(payload: CreateSubscriptionRequest, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    if not payload.name.strip():
        raise HTTPException(400, "订阅名称不能为空")

    rss_url = payload.rss_url.strip()
    mikan_url = payload.mikan_url.strip()

    # 如果提供了 Mikan URL，自动解析 RSS URL
    if mikan_url and not rss_url:
        try:
            parsed = parse_mikan_url(mikan_url)
            if parsed["bangumi_id"]:
                domain = str(runtime.settings.get("mikan_domain", "mikanani.me"))
                rss_url = build_mikan_rss_url(parsed["bangumi_id"], parsed["subgroup_id"], domain)
        except Exception as exc:
            raise HTTPException(400, f"Mikan URL 解析失败: {exc}")

    if not rss_url:
        raise HTTPException(400, "RSS URL 或 Mikan URL 不能为空")

    sub = Subscription(
        id=uuid.uuid4().hex[:12],
        name=payload.name.strip(),
        rss_url=rss_url,
        mikan_url=mikan_url,
        standby_rss_url=payload.standby_rss_url.strip(),
        tmdb_id=payload.tmdb_id.strip(),
        bangumi_url=payload.bangumi_url.strip(),
        match_pattern=payload.match_pattern,
        exclude_pattern=payload.exclude_pattern,
        episode_regex=payload.episode_regex,
        episode_group_index=payload.episode_group_index,
        episode_offset=payload.episode_offset,
        auto_disable_when_complete=payload.auto_disable_when_complete,
        download_dir_template=payload.download_dir_template,
        theater_mode=payload.theater_mode,
        theater_save_path=payload.theater_save_path,
        skip_half_episodes=payload.skip_half_episodes,
        download_only_latest=payload.download_only_latest,
        omission_detection=payload.omission_detection,
        slacking_days=payload.slacking_days,
        notify_on_download=payload.notify_on_download,
        notify_on_complete=payload.notify_on_complete,
        notify_on_missing=payload.notify_on_missing,
        season=payload.season,
        total_episodes=payload.total_episodes,
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    # 尝试获取 TMDB/Bangumi 元数据
    if sub.tmdb_id and str(runtime.settings.get("tmdb_api_key", "")).strip():
        try:
            tmdb_info = await asyncio.to_thread(
                _fetch_tmdb_info, sub.tmdb_id,
                str(runtime.settings["tmdb_api_key"]),
                str(runtime.settings.get("tmdb_api_url", "https://api.themoviedb.org/3")),
            )
            sub.poster_url = tmdb_info.get("poster_url", "")
            sub.description = tmdb_info.get("description", "")
            if tmdb_info.get("air_date"):
                sub.air_date = tmdb_info["air_date"]
            if not sub.total_episodes and tmdb_info.get("number_of_episodes"):
                sub.total_episodes = tmdb_info["number_of_episodes"]
        except Exception as exc:
            await runtime.log("warning", "tmdb", f"TMDB 获取失败: {exc}")

    if sub.bangumi_url:
        bangumi_id = parse_bangumi_url(sub.bangumi_url)
        if bangumi_id:
            sub.bangumi_id = bangumi_id
            token = str(runtime.settings.get("bangumi_access_token", "")).strip()
            try:
                bgm_info = await asyncio.to_thread(_fetch_bangumi_info, bangumi_id, token)
                if not sub.description and bgm_info.get("summary"):
                    sub.description = bgm_info["summary"]
                if not sub.total_episodes and bgm_info.get("total_episodes"):
                    sub.total_episodes = bgm_info["total_episodes"]
                if not sub.air_date and bgm_info.get("air_date"):
                    sub.air_date = bgm_info["air_date"]
                if bgm_info.get("image") and not sub.poster_url:
                    sub.poster_url = bgm_info["image"]
            except Exception as exc:
                await runtime.log("warning", "bangumi", f"Bangumi 获取失败: {exc}")

    async with runtime.lock:
        runtime.subscriptions.append(sub)
        save_subscriptions(runtime.subscriptions)
    await runtime.log("info", "rss", f"已添加追番订阅：{sub.name}")
    await runtime.broadcast_snapshot()
    return {"ok": True, "subscription": sub.to_dict()}


@app.put("/api/subscriptions/{sub_id}")
async def put_subscription(sub_id: str, payload: UpdateSubscriptionRequest, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    async with runtime.lock:
        sub = next((s for s in runtime.subscriptions if s.id == sub_id), None)
        if not sub:
            raise HTTPException(404, "订阅不存在")
        for field_name in payload.model_fields:
            val = getattr(payload, field_name, None)
            if val is not None:
                setattr(sub, field_name, val)
        sub.updated_at = utc_now()
        save_subscriptions(runtime.subscriptions)

    # 如果更新了 tmdb_id 或 bangumi_url，重新获取元数据
    need_tmdb = payload.tmdb_id is not None and payload.tmdb_id.strip()
    need_bangumi = payload.bangumi_url is not None and payload.bangumi_url.strip()

    if need_tmdb and str(runtime.settings.get("tmdb_api_key", "")).strip():
        try:
            tmdb_info = await asyncio.to_thread(
                _fetch_tmdb_info, sub.tmdb_id,
                str(runtime.settings["tmdb_api_key"]),
                str(runtime.settings.get("tmdb_api_url", "https://api.themoviedb.org/3")),
            )
            sub.poster_url = tmdb_info.get("poster_url", "")
            sub.description = tmdb_info.get("description", "")
            if tmdb_info.get("air_date"):
                sub.air_date = tmdb_info["air_date"]
            if not sub.total_episodes and tmdb_info.get("number_of_episodes"):
                sub.total_episodes = tmdb_info["number_of_episodes"]
            async with runtime.lock:
                save_subscriptions(runtime.subscriptions)
        except Exception as exc:
            await runtime.log("warning", "tmdb", f"TMDB 获取失败: {exc}")

    if need_bangumi:
        bangumi_id = parse_bangumi_url(sub.bangumi_url)
        if bangumi_id:
            sub.bangumi_id = bangumi_id
            token = str(runtime.settings.get("bangumi_access_token", "")).strip()
            try:
                bgm_info = await asyncio.to_thread(_fetch_bangumi_info, bangumi_id, token)
                if not sub.description and bgm_info.get("summary"):
                    sub.description = bgm_info["summary"]
                if not sub.total_episodes and bgm_info.get("total_episodes"):
                    sub.total_episodes = bgm_info["total_episodes"]
                if not sub.air_date and bgm_info.get("air_date"):
                    sub.air_date = bgm_info["air_date"]
                if bgm_info.get("image") and not sub.poster_url:
                    sub.poster_url = bgm_info["image"]
                async with runtime.lock:
                    save_subscriptions(runtime.subscriptions)
            except Exception as exc:
                await runtime.log("warning", "bangumi", f"Bangumi 获取失败: {exc}")

    await runtime.broadcast_snapshot()
    return {"ok": True, "subscription": sub.to_dict()}


@app.delete("/api/subscriptions/{sub_id}")
async def delete_subscription(sub_id: str, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    async with runtime.lock:
        before = len(runtime.subscriptions)
        runtime.subscriptions = [s for s in runtime.subscriptions if s.id != sub_id]
        if len(runtime.subscriptions) == before:
            raise HTTPException(404, "订阅不存在")
        save_subscriptions(runtime.subscriptions)
    await runtime.log("info", "rss", f"已删除追番订阅：{sub_id}")
    await runtime.broadcast_snapshot()
    return {"ok": True}


@app.post("/api/subscriptions/{sub_id}/toggle")
async def toggle_subscription(sub_id: str, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    async with runtime.lock:
        sub = next((s for s in runtime.subscriptions if s.id == sub_id), None)
        if not sub:
            raise HTTPException(404, "订阅不存在")
        sub.enabled = not sub.enabled
        sub.updated_at = utc_now()
        save_subscriptions(runtime.subscriptions)
    state = "启用" if sub.enabled else "禁用"
    await runtime.log("info", "rss", f"已{state}追番订阅：{sub.name}")
    await runtime.broadcast_snapshot()
    return {"ok": True, "subscription": sub.to_dict()}


@app.post("/api/subscriptions/{sub_id}/check")
async def check_single_subscription(sub_id: str, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    """手动触发单个订阅的检查。"""
    sub = next((s for s in runtime.subscriptions if s.id == sub_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    settings = runtime.settings
    global_excludes = settings.get("global_exclude_patterns", [])
    mikan_domain = str(settings.get("mikan_domain", "mikanani.me"))
    await _check_single_subscription(sub, settings, global_excludes, mikan_domain)
    async with runtime.lock:
        save_subscriptions(runtime.subscriptions)
    await runtime.broadcast_snapshot()
    return {"ok": True, "subscription": sub.to_dict()}


@app.post("/api/subscriptions/{sub_id}/episodes")
async def mark_episode(sub_id: str, payload: MarkEpisodeRequest, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    """手动标记某集为已下载。"""
    async with runtime.lock:
        sub = next((s for s in runtime.subscriptions if s.id == sub_id), None)
        if not sub:
            raise HTTPException(404, "订阅不存在")
        ep_info = EpisodeInfo(title=payload.title or payload.episode, downloaded_at=utc_now())
        sub.downloaded_episodes[payload.episode] = ep_info
        try:
            ep_num = int(float(payload.episode))
            if ep_num > sub.last_episode:
                sub.last_episode = ep_num
        except (ValueError, TypeError):
            pass
        sub.updated_at = utc_now()
        save_subscriptions(runtime.subscriptions)
    await runtime.broadcast_snapshot()
    return {"ok": True, "subscription": sub.to_dict()}


@app.delete("/api/subscriptions/{sub_id}/episodes/{ep}")
async def unmark_episode(sub_id: str, ep: str, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    """移除已标记的集数。"""
    async with runtime.lock:
        sub = next((s for s in runtime.subscriptions if s.id == sub_id), None)
        if not sub:
            raise HTTPException(404, "订阅不存在")
        if ep in sub.downloaded_episodes:
            del sub.downloaded_episodes[ep]
        sub.updated_at = utc_now()
        save_subscriptions(runtime.subscriptions)
    await runtime.broadcast_snapshot()
    return {"ok": True, "subscription": sub.to_dict()}


@app.get("/api/subscriptions/{sub_id}/gaps")
async def get_episode_gaps(sub_id: str) -> dict[str, Any]:
    """获取缺集列表。"""
    sub = next((s for s in runtime.subscriptions if s.id == sub_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    gaps = detect_episode_gaps(sub.downloaded_episodes, sub.last_episode)
    return {"ok": True, "gaps": gaps, "last_episode": sub.last_episode}


# ── 集成服务 endpoints ────────────────────────────────────

@app.post("/api/mikan/parse")
async def parse_mikan(payload: MikanParseRequest) -> dict[str, Any]:
    """解析 Mikan URL，返回 bangumiId、subgroupId 和自动生成的 RSS URL。"""
    try:
        parsed = parse_mikan_url(payload.url)
    except Exception as exc:
        raise HTTPException(400, f"URL 解析失败: {exc}")
    if not parsed["bangumi_id"]:
        raise HTTPException(400, "无法从 URL 中提取 bangumiId")
    domain = str(runtime.settings.get("mikan_domain", "mikanani.me"))
    rss_url = build_mikan_rss_url(parsed["bangumi_id"], parsed["subgroup_id"], domain)
    return {
        "ok": True,
        "bangumi_id": parsed["bangumi_id"],
        "subgroup_id": parsed["subgroup_id"],
        "rss_url": rss_url,
    }


@app.post("/api/tmdb/fetch")
async def fetch_tmdb(payload: TmdbFetchRequest) -> dict[str, Any]:
    """获取 TMDB 元数据。"""
    api_key = str(runtime.settings.get("tmdb_api_key", "")).strip()
    if not api_key:
        raise HTTPException(400, "未配置 TMDB API Key")
    api_url = str(runtime.settings.get("tmdb_api_url", "https://api.themoviedb.org/3"))
    try:
        info = await asyncio.to_thread(_fetch_tmdb_info, payload.tmdb_id, api_key, api_url)
        return {"ok": True, **info}
    except Exception as exc:
        raise HTTPException(500, f"TMDB 请求失败: {exc}")


@app.post("/api/bangumi/fetch")
async def fetch_bangumi(payload: BangumiFetchRequest) -> dict[str, Any]:
    """获取 Bangumi 元数据。"""
    subject_id = parse_bangumi_url(payload.url)
    if not subject_id:
        raise HTTPException(400, "无法从 URL 提取 subject ID")
    token = str(runtime.settings.get("bangumi_access_token", "")).strip()
    try:
        info = await asyncio.to_thread(_fetch_bangumi_info, subject_id, token)
        return {"ok": True, "subject_id": subject_id, **info}
    except Exception as exc:
        raise HTTPException(500, f"Bangumi 请求失败: {exc}")


@app.post("/api/notify/test")
async def test_notification(payload: NotifyTestRequest, _: None = Depends(verify_api_token)) -> dict[str, Any]:
    """发送测试通知。"""
    dummy_sub = Subscription(id="test", name="测试通知")
    await dispatch_notification("download_started", dummy_sub, {
        "title": "测试通知",
        "episode": "01",
        "quality": "1080p",
        "subgroup": "Aria2Plus",
        "file_path": "/tmp/test",
        "total_episodes": "12",
        "expected": "5, 6",
        "days": "3",
    })
    return {"ok": True, "message": "测试通知已发送，请检查各渠道。"}


@app.websocket("/ws/events")
async def websocket_events(socket: WebSocket) -> None:
    await runtime.register_socket(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        await runtime.unregister_socket(socket)
