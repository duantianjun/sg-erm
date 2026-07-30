# -*- coding: utf-8 -*-
"""auth_service 纯函数单元测试。"""
from datetime import timedelta

import pytest
from jose import jwt

from app.config import settings
from app.services.auth_service import (
    API_TOKEN_PREFIX,
    TOKEN_PREFIX_LEN,
    create_access_token,
    create_refresh_token,
    generate_api_token,
    get_password_hash,
    get_token_prefix,
    hash_api_token,
    verify_api_token,
    verify_password,
)


@pytest.mark.unit
class TestPasswordHash:
    def test_hash_and_verify_roundtrip(self):
        plain = "Admin@1234"
        hashed = get_password_hash(plain)
        assert hashed != plain
        assert verify_password(plain, hashed) is True

    def test_verify_wrong_password_fails(self):
        hashed = get_password_hash("Admin@1234")
        assert verify_password("wrong", hashed) is False


@pytest.mark.unit
class TestCreateAccessToken:
    def test_contains_required_claims(self):
        token = create_access_token(
            data={"sub": "user-1", "token_version": 3}
        )
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        assert payload["sub"] == "user-1"
        assert payload["token_version"] == 3
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload

    def test_type_is_access_not_refresh(self):
        access = create_access_token(data={"sub": "u1", "token_version": 1})
        refresh = create_refresh_token(data={"sub": "u1", "token_version": 1})
        pa = jwt.decode(access, settings.secret_key, algorithms=[settings.jwt_algorithm])
        pr = jwt.decode(refresh, settings.secret_key, algorithms=[settings.jwt_algorithm])
        assert pa["type"] == "access"
        assert pr["type"] == "refresh"


@pytest.mark.unit
class TestRefreshTokenExpiry:
    def test_refresh_lives_longer_than_access(self):
        access = create_access_token(data={"sub": "u1", "token_version": 1})
        refresh = create_refresh_token(data={"sub": "u1", "token_version": 1})
        pa = jwt.decode(access, settings.secret_key, algorithms=[settings.jwt_algorithm])
        pr = jwt.decode(refresh, settings.secret_key, algorithms=[settings.jwt_algorithm])
        assert pr["exp"] > pa["exp"]


@pytest.mark.unit
class TestGenerateApiToken:
    def test_has_sgerm_prefix(self):
        token = generate_api_token()
        assert token.startswith(API_TOKEN_PREFIX)

    def test_unique(self):
        a = generate_api_token()
        b = generate_api_token()
        assert a != b

    def test_prefix_extraction(self):
        token = generate_api_token()
        prefix = get_token_prefix(token)
        assert len(prefix) == TOKEN_PREFIX_LEN
        # 前缀取自明文 token 的 sgerm_ 之后 8 字符
        assert token[len(API_TOKEN_PREFIX):len(API_TOKEN_PREFIX) + TOKEN_PREFIX_LEN] == prefix


@pytest.mark.unit
class TestHashApiToken:
    def test_hash_and_verify_roundtrip(self):
        token = generate_api_token()
        hashed = hash_api_token(token)
        assert hashed != token
        assert verify_api_token(token, hashed) is True

    def test_verify_wrong_token_fails(self):
        hashed = hash_api_token(generate_api_token())
        assert verify_api_token("sgerm_wrong", hashed) is False
