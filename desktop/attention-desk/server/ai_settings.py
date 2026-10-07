"""Windows user-bound DPAPI secrets; public configuration never contains a key."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from .personal_store import PersonalError


class AIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    base_url: str = "https://api.deepseek.com"
    model: str = Field(default="deepseek-flash", min_length=1, max_length=100)
    timeout: int = Field(default=45, ge=5, le=180)
    max_output_tokens: int = Field(default=4096, ge=256, le=32768)
    thinking: bool = False

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value):
        parts = urlsplit(value.strip())
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("请填写不含密钥、用户名或查询参数的 HTTPS base URL")
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))

    @field_validator("model")
    @classmethod
    def model_name(cls, value):
        if not value.strip() or any(char in value for char in "\r\n"):
            raise ValueError("模型名不能为空或包含换行")
        return value.strip()


def protect(value: str, *, decrypt=False) -> str:
    if os.name != "nt":
        raise PersonalError("当前系统不支持 Windows 密钥保护；请使用后端 DEEPSEEK_API_KEY 环境变量。", "secure_storage_unavailable", 503)
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    try:
        raw = base64.b64decode(value, validate=True) if decrypt else value.encode("utf-8")
    except (ValueError, TypeError):
        raise PersonalError("保存的密钥格式损坏，请在本地设置页重新填写。", "credential_error", 503)
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)):
        raise PersonalError("密钥无法由当前 Windows 用户读取或保护，请重新填写；已有文件未覆盖。", "credential_error", 503)
    try:
        result = ctypes.string_at(destination.pbData, destination.cbData)
        try:
            return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
        except UnicodeDecodeError:
            raise PersonalError("保存的密钥无法读取，请在本地设置页重新填写。", "credential_error", 503)
    finally:
        kernel32.LocalFree(ctypes.cast(destination.pbData, wintypes.HLOCAL))


class AISettings:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self):
        if not self.path.exists():
            return {}, AIConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data, AIConfig.model_validate(data.get("config", {}))
        except (OSError, ValueError, TypeError, AttributeError):
            raise PersonalError("提取设置无法读取，请检查配置文件；没有清空或覆盖它。", "configuration_error", 503)

    def public(self):
        data, config = self._read()
        environment = bool(os.getenv("ATTENTION_DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY"))
        return {**config.model_dump(), "provider": "deepseek", "has_api_key": bool(data.get("protected_key") or environment),
                "key_source": "windows" if data.get("protected_key") else "environment" if environment else "missing"}

    def resolved(self):
        data, config = self._read()
        key = protect(data["protected_key"], decrypt=True) if data.get("protected_key") else (os.getenv("ATTENTION_DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "")
        return config, SecretStr(key.strip())

    def save(self, payload: dict):
        allowed = {*AIConfig.model_fields, "api_key", "clear_api_key"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise PersonalError("提取设置包含不支持的字段。")
        data, old = self._read()
        try:
            config = AIConfig.model_validate({**old.model_dump(), **{key: value for key, value in payload.items() if key in AIConfig.model_fields}})
        except ValueError:
            raise PersonalError("请核对 HTTPS 地址、模型名、5–180 秒超时和 256–32768 输出 token 上限。")
        key = payload.get("api_key", "")
        if not isinstance(key, str) or len(key) > 4096 or any(char in key for char in "\r\n"):
            raise PersonalError("API Key 格式不正确，请重新填写单行密钥。")
        protected_key = None if payload.get("clear_api_key") is True else data.get("protected_key")
        if key.strip():
            protected_key = protect(key.strip())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Replace in the same directory only after validation/encryption succeeds.
        from uuid import uuid4
        temporary = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps({"config": config.model_dump(), "protected_key": protected_key}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            raise PersonalError("提取设置未能保存，请保留输入后重试。", "storage_error", 503)
        finally:
            temporary.unlink(missing_ok=True)
        return self.public()
