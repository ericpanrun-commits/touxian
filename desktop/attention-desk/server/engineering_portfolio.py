"""Read-only folder portfolio. No originals, index or metadata enter a database."""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

DEFAULT_ROOT = r"D:\Engineering-Portfolio"
TYPES = {".jpg", ".jpeg", ".png", ".pdf", ".sldprt", ".sldasm", ".slddrw", ".dwg", ".dxf", ".step", ".stp"}
IMAGES = {".jpg", ".jpeg", ".png"}
MAX_PROJECTS, MAX_FILES, MAX_ENTRIES, MAX_DEPTH = 500, 2000, 20000, 12
REPOSITORY = Path(__file__).resolve().parents[3]


def fail(status: int, code: str, message: str):
    raise HTTPException(status, detail={"code": code, "message": message})


def encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode(value: str) -> str:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True).decode("utf-8")
    except (ValueError, UnicodeError, binascii.Error):
        fail(400, "invalid_path", "作品路径无效，请刷新作品集。")


def linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def safe_root(value: str, repository: Path) -> Path:
    if "\x00" in value:
        fail(400, "invalid_root", "作品目录不能包含空字符。")
    raw = Path(value)
    if not raw.is_absolute() or str(raw).startswith(("\\\\", "//")) or raw == Path(raw.anchor):
        fail(400, "invalid_root", "请选择本机的独立绝对目录，例如 D:\\Engineering-Portfolio。")
    try:
        for part in (raw, *raw.parents):
            if part.exists() and linked(part):
                fail(400, "linked_root", "作品目录不能经过符号链接或目录联接，请选择原始文件夹。")
        root = raw.resolve()
        if root.is_relative_to(repository.resolve()) or any((part / ".git").exists() for part in (root, *root.parents)):
            fail(400, "repository_root", "作品目录必须位于 Git 仓库之外，请选择其他本地目录。")
        return root
    except OSError:
        fail(400, "root_unavailable", "无法访问作品目录，请检查磁盘和读取权限。")


class Portfolio:
    def __init__(self, settings: Path | None = None, repository: Path = REPOSITORY):
        self.settings = settings or Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config"))) / "Eric OS" / "engineering-portfolio.json"
        self.repository = repository
        self.lock = threading.RLock()

    def root(self) -> Path:
        value = os.environ.get("ERIC_PORTFOLIO_ROOT") or DEFAULT_ROOT
        if not os.environ.get("ERIC_PORTFOLIO_ROOT"):
            try:
                if self.settings.exists():
                    value = json.loads(self.settings.read_text(encoding="utf-8"))["root"]
                    if not isinstance(value, str):
                        raise ValueError()
            except (OSError, ValueError, KeyError, TypeError):
                fail(503, "settings_unavailable", "作品目录设置无法读取，请检查本机设置文件。")
        return safe_root(value, self.repository)

    def configure(self, value: str) -> dict:
        with self.lock:
            if os.environ.get("ERIC_PORTFOLIO_ROOT"):
                fail(409, "root_managed", "当前目录由 ERIC_PORTFOLIO_ROOT 指定，请先修改该本机启动设置。")
            root = safe_root(value, self.repository)
            if not root.is_dir():
                fail(400, "root_missing", "目录不存在，请先在资源管理器中创建文件夹，再保存路径。")
            try:
                self.settings.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.settings.with_suffix(".tmp")
                temporary.write_text(json.dumps({"root": str(root)}, ensure_ascii=False), encoding="utf-8")
                temporary.replace(self.settings)
            except OSError:
                fail(503, "settings_unwritable", "目录设置未保存，请检查本机设置文件的写入权限。")
            return {"root": str(root)}

    def project(self, identity: str) -> Path:
        name = decode(identity)
        if not name or name in {".", ".."} or any(char in name for char in "/\\:\x00"):
            fail(400, "invalid_project", "作品路径无效，请刷新作品集。")
        root = self.root()
        project = root / name
        try:
            if not project.is_dir() or linked(project) or (project / ".git").exists():
                fail(404, "project_missing", "作品文件夹已移动或删除，请刷新作品集。")
        except OSError:
            fail(404, "project_missing", "作品文件夹暂时无法读取，请刷新作品集。")
        return project

    def scan_project(self, project: Path) -> dict:
        files, warnings = [], set()
        visited = 0

        def walk(directory: Path, depth: int):
            nonlocal visited
            if depth > MAX_DEPTH:
                warnings.add("部分子目录层级过深，未继续读取。")
                return
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > MAX_ENTRIES or len(files) >= MAX_FILES:
                            warnings.add("作品文件较多，仅展示前 2,000 个支持文件（最多检查 20,000 个目录项）。")
                            return
                        path = Path(entry.path)
                        if entry.name.startswith(".") or entry.name.startswith("~$"):
                            continue
                        try:
                            if linked(path):
                                warnings.add("已跳过符号链接和目录联接。")
                            elif entry.is_dir(follow_symlinks=False):
                                if not (path / ".git").exists():
                                    walk(path, depth + 1)
                            elif entry.is_file(follow_symlinks=False) and path.suffix.lower() in TYPES:
                                info = entry.stat(follow_symlinks=False)
                                relative = path.relative_to(project).as_posix()
                                files.append({"id": encode(relative), "name": relative, "type": path.suffix[1:].upper(),
                                              "size": info.st_size, "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                                              "preview": "image" if path.suffix.lower() in IMAGES else "pdf" if path.suffix.lower() == ".pdf" else None})
                        except OSError:
                            warnings.add("部分文件暂时无法读取，可稍后刷新。")
            except OSError:
                warnings.add("部分目录暂时无法读取，请检查权限后刷新。")

        walk(project, 0)
        files.sort(key=lambda item: (item["name"].casefold(), item["name"]))
        images = [item for item in files if item["preview"] == "image"]
        images.sort(key=lambda item: (item["name"].casefold() not in {"cover.jpg", "cover.png"}, item["name"].casefold(), item["name"]))
        return {"id": encode(project.name), "title": project.name, "files": files,
                "types": sorted({item["type"] for item in files}), "cover": images[0]["id"] if images else None,
                "warnings": sorted(warnings)}

    def scan(self) -> dict:
        with self.lock:
            root = self.root()
            result = {"root": str(root), "exists": root.is_dir(), "managed": bool(os.environ.get("ERIC_PORTFOLIO_ROOT")), "items": [], "warnings": []}
            if not result["exists"]:
                return result
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                            continue
                        try:
                            if linked(path) or (path / ".git").exists():
                                result["warnings"].append(f"已跳过链接或 Git 仓库：{entry.name}")
                                continue
                            if len(result["items"]) >= MAX_PROJECTS:
                                result["warnings"].append("仅展示前 500 个作品，请缩小作品总目录范围。")
                                break
                            result["items"].append(self.scan_project(path))
                        except OSError:
                            result["warnings"].append(f"暂时无法读取：{entry.name}")
            except OSError:
                fail(503, "root_unavailable", "无法读取作品目录，请检查磁盘和读取权限后刷新。")
            result["items"].sort(key=lambda item: (item["title"].casefold(), item["title"]))
            return result

    def preview(self, project_id: str, file_id: str) -> Path:
        project = self.project(project_id)
        name = decode(file_id)
        relative = Path(name)
        if relative.anchor or not name or any(part in {".", ".."} for part in name.replace("\\", "/").split("/")) or ":" in name or "\x00" in name:
            fail(400, "invalid_file", "文件路径无效，请刷新作品集。")
        path = project / relative
        try:
            if any(linked(part) or (part / ".git").exists() for part in (path, *path.parents) if part != project and part.is_relative_to(project)) or not path.resolve().is_relative_to(project):
                fail(404, "file_missing", "文件已移动或无法读取，请刷新作品集。")
            if not path.is_file():
                fail(404, "file_missing", "文件已移动或删除，请刷新作品集。")
        except OSError:
            fail(404, "file_missing", "文件暂时无法读取，请刷新作品集。")
        if path.suffix.lower() not in IMAGES | {".pdf"}:
            fail(415, "preview_unavailable", "CAD / SolidWorks 文件仅展示信息，请在资源管理器中打开。")
        return path


def local_access(request: Request, response: Response):
    # This router is mounted only in the desktop app, never in cloud/LAN apps.
    from .runtime import RuntimeConfig
    runtime = RuntimeConfig.from_env()
    try:
        peer_local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        peer_local = False
    host = request.url.hostname
    origin = request.headers.get("origin")
    local_host = host in {"127.0.0.1", "localhost", "::1"}
    parsed = urlsplit(origin) if origin else None
    origin_ok = not parsed or (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and not parsed.username and not parsed.password)
    if runtime.role != "desktop" or runtime.environment != "development" or not peer_local or not local_host or not origin_ok or request.headers.get("sec-fetch-site") == "cross-site":
        fail(403, "local_only", "作品集仅可在这台 Windows 电脑的本地工作台使用。")
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/personal/portfolio", tags=["local-portfolio"], dependencies=[Depends(local_access)])


def service(request: Request) -> Portfolio:
    return request.app.state.engineering_portfolio


class RootInput(BaseModel):
    root: str = Field(min_length=1, max_length=1024)


@router.get("")
def list_projects(portfolio: Portfolio = Depends(service)):
    return portfolio.scan()


@router.put("/root")
def update_root(body: RootInput, portfolio: Portfolio = Depends(service)):
    return portfolio.configure(body.root)


@router.get("/projects/{project_id}/preview/{file_id}")
def preview(project_id: str, file_id: str, portfolio: Portfolio = Depends(service)):
    path = portfolio.preview(project_id, file_id)
    media = "application/pdf" if path.suffix.lower() == ".pdf" else "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media, content_disposition_type="inline", filename=path.name,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.post("/projects/{project_id}/open")
def open_project(project_id: str, portfolio: Portfolio = Depends(service)):
    project = portfolio.project(project_id)
    if os.name != "nt":
        fail(501, "windows_required", "请在 Windows 本地工作台打开作品文件夹。")
    try:
        os.startfile(str(project), "explore")
    except OSError:
        fail(503, "explorer_failed", "资源管理器未能打开，请稍后重试。")
    return {"opened": True}
