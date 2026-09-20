# -*- coding: utf-8 -*-
"""BYOK Key 加密（webapp/secret_encrypt.py）单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp.secret_encrypt import decrypt_secret, encrypt_secret


def test_roundtrip():
    """加密-解密往返一致，密文带 enc:v1: 前缀且不含明文。"""
    plain = "sk-abc-1234567890"
    enc = encrypt_secret(plain)
    assert enc.startswith("enc:v1:")
    assert plain not in enc, "密文中泄露明文"
    assert decrypt_secret(enc) == plain


def test_empty_string():
    """空串原样返回，不生成密文。"""
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""


def test_legacy_plaintext_compat():
    """存量明文（无前缀）原样返回，保持兼容直至迁移。"""
    assert decrypt_secret("legacy-plain-key") == "legacy-plain-key"


def test_cipher_deterministic_format():
    """密文长度稳定且可重复解密（同一明文多次加密结果不同但都可解）。"""
    plain = "sk-x" * 20
    enc1 = encrypt_secret(plain)
    enc2 = encrypt_secret(plain)
    assert enc1 != enc2, "Fernet 应带随机 IV，两次密文不同"
    assert decrypt_secret(enc1) == plain
    assert decrypt_secret(enc2) == plain
