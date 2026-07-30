# -*- coding: utf-8 -*-
"""crypto_service 纯函数单元测试。"""
import hashlib

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.services.crypto_service import (
    decrypt_private_key,
    encrypt_private_key,
    generate_key_pair,
    sign_data,
    sign_sha256_file,
)


@pytest.mark.unit
class TestEncryptDecryptPrivateKey:
    def test_roundtrip(self):
        pem = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"
        password = "test-password-1234"
        encrypted = encrypt_private_key(pem, password)
        assert encrypted != pem
        assert decrypt_private_key(encrypted, password) == pem

    def test_wrong_password_raises(self):
        encrypted = encrypt_private_key("secret-data", "right-password")
        with pytest.raises(Exception):
            decrypt_private_key(encrypted, "wrong-password")


@pytest.mark.unit
class TestGenerateKeyPair:
    def test_returns_valid_pem_pair(self):
        private_pem, public_pem = generate_key_pair()
        assert "BEGIN PRIVATE KEY" in private_pem
        assert "BEGIN PUBLIC KEY" in public_pem
        # 私钥可加载
        private_key = serialization.load_pem_private_key(
            private_pem.encode(), password=None
        )
        # 公钥可加载
        serialization.load_pem_public_key(public_pem.encode())
        assert private_key.key_size == 2048


@pytest.mark.unit
class TestSignData:
    def test_signature_verifies_with_public_key(self):
        private_pem, public_pem = generate_key_pair()
        data = b"hello world"
        signature = sign_data(private_pem, data)
        public_key = serialization.load_pem_public_key(public_pem.encode())
        public_key.verify(
            signature, data, padding.PKCS1v15(), hashes.SHA256()
        )

    def test_signature_fails_for_tampered_data(self):
        private_pem, public_pem = generate_key_pair()
        signature = sign_data(private_pem, b"original")
        public_key = serialization.load_pem_public_key(public_pem.encode())
        from cryptography.exceptions import InvalidSignature
        with pytest.raises(InvalidSignature):
            public_key.verify(
                signature, b"tampered", padding.PKCS1v15(), hashes.SHA256()
            )


@pytest.mark.unit
class TestSignSha256File:
    def test_returns_base64_signature_matching_sha256(self, tmp_path):
        private_pem, public_pem = generate_key_pair()
        tgz = tmp_path / "pkg.tgz"
        content = b"\x1f\x8b\x08fake-tarball"
        tgz.write_bytes(content)

        import base64
        signature_b64 = sign_sha256_file(private_pem, str(tgz))

        # 签名是对 SHA256 hexdigest 字符串签名
        expected_digest = hashlib.sha256(content).hexdigest()
        raw_sig = base64.b64decode(signature_b64)

        public_key = serialization.load_pem_public_key(public_pem.encode())
        public_key.verify(
            raw_sig,
            expected_digest.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
