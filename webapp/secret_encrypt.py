# -*- coding: utf-8 -*-
"""
用户敏感配置加密（BYOK API Key 等）。

设计目标：DB 泄露时，用户的 API Key 不以明文落库。
- 算法：Fernet（AES-128-CBC + HMAC 完整性校验，cryptography 库）
- 密文格式：enc:v1:<base64 token>（前缀便于识别与未来迁移）
- 密钥来源（按优先级）：
    1. 环境变量 LLM_KEY_ENCRYPT_SECRET（生产推荐，随 .env 注入；容器重建不丢）
    2. 项目根目录 .llm_encrypt.key（自动生成，已 gitignore；
       服务器发版用 zip 解压不会覆盖已存在文件，密钥天然持久）
- 存量兼容：decrypt_secret 对非 enc:v1: 前缀的原样返回（读老明文）；
  init_db 启动时全量迁移为密文，迁移幂等。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_ROOT = Path(__file__).resolve().parent.parent
_KEY_FILE = _ROOT / ".llm_encrypt.key"
_PREFIX = "enc:v1:"

_fernet: Fernet | None = None
_fernet_lock = threading.Lock()


def _load_key() -> bytes:
    """取密钥：环境变量优先，否则读/生成根目录密钥文件。"""
    env = os.environ.get("LLM_KEY_ENCRYPT_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    try:
        _KEY_FILE.write_bytes(key)
    except OSError:
        pass  # 只读环境：密钥仅本次进程有效，功能仍可用
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        with _fernet_lock:
            if _fernet is None:
                _fernet = Fernet(_load_key())
    return _fernet


def encrypt_secret(plain: str) -> str:
    """明文 -> 密文；空串原样返回空串。"""
    if not plain:
        return ""
    token = _get_fernet().encrypt(plain.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt_secret(stored: str) -> str:
    """密文 -> 明文；非 enc:v1: 前缀视为存量明文原样返回；密钥不匹配返回空串。"""
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):
        return stored
    try:
        return _get_fernet().decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
