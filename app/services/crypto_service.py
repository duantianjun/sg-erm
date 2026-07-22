"""RSA 密钥管理服务。

为自定义扩展发布者管理 RSA 2048 密钥对：
- 生成新密钥对
- 用系统密钥加密存储私钥
- 解密私钥用于签名
- 公钥以 PEM 格式存储

私钥加密方案：PBKDF2 + AES-256-GCM
"""
import hashlib
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import settings

# 常量
KEY_SIZE = 2048
SALT_LEN = 16
NONCE_LEN = 12


def _derive_key(password: str, salt: bytes) -> bytes:
    """从密码和盐派生 AES-256 密钥。"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_private_key(private_key_pem: str, password: str) -> str:
    """用密码加密私钥 PEM 字符串。

    格式: base64(salt + nonce + ciphertext)
    """
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive_key(password, salt)

    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, private_key_pem.encode("utf-8"), None)

    # salt(16) + nonce(12) + ciphertext
    combined = salt + nonce + ciphertext
    return combined.hex()


def decrypt_private_key(encrypted: str, password: str) -> str:
    """解密私钥 PEM 字符串。"""
    combined = bytes.fromhex(encrypted)
    salt = combined[:SALT_LEN]
    nonce = combined[SALT_LEN : SALT_LEN + NONCE_LEN]
    ciphertext = combined[SALT_LEN + NONCE_LEN :]

    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def generate_key_pair() -> tuple[str, str]:
    """生成 RSA 2048 密钥对。

    Returns:
        (private_key_pem, public_key_pem)
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=KEY_SIZE,
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return private_pem, public_pem


def sign_data(private_key_pem: str, data: bytes) -> bytes:
    """用 RSA 私钥对数据签名，返回签名字节。"""
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )
    signature = private_key.sign(
        data,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return signature


def sign_sha256_file(private_key_pem: str, tgz_path: str) -> bytes:
    """计算 .tgz 文件的 SHA256 并用私钥签名。

    StackGres 的签名流程：
    1. 计算 .tgz 文件的 SHA256
    2. 用发布者私钥签名这个哈希值
    3. 签名结果写入 .sha256 文件（Base64）
    """
    import base64

    # 读取 .tgz 文件并计算 SHA256
    hasher = hashlib.sha256()
    with open(tgz_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    digest = hasher.hexdigest()

    # 用私钥签名哈希值（字符串形式）
    signature = sign_data(private_key_pem, digest.encode("utf-8"))
    return base64.b64encode(signature)


def get_system_password() -> str:
    """获取系统加密密码（来自环境变量 SECRET_KEY）。"""
    pwd = settings.secret_key
    if pwd == "change-me-in-production" or len(pwd) < 16:
        # 开发环境警告：密码太短
        pass
    return pwd
