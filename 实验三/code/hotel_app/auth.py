"""本地账号认证和会话身份；不保存或记录明文密码。"""

import hashlib
import hmac
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Optional

from hotel_app.booking import BookingError
from hotel_app.database import operation
from hotel_app.repositories import find_login_record

ITERATIONS = 600000
LOGGER = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """每次使用独立随机盐；编码中记录算法及成本。"""
    if not isinstance(password, str) or not password:
        raise ValueError("密码不能为空")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """损坏或不支持的哈希格式视为认证失败，不接受自定义高迭代成本。"""
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$")
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if (algorithm != "pbkdf2_sha256" or rounds != str(ITERATIONS)
                or len(salt) != 16 or len(expected) != 32):
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, ITERATIONS
        )
        return hmac.compare_digest(actual, expected)
    except ValueError:
        return False


@dataclass(frozen=True)
class Account:
    """界面可持有的身份信息，不包含密码哈希。"""

    id: str
    username: str
    name: str
    role: str
    hotel_id: Optional[str]


class AuthService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.current: Optional[Account] = None

    def login(self, username: str, password: str) -> Account:
        self.logout()  # 切换账号失败时，不能保留上一个账号的权限。
        if isinstance(username, str) and isinstance(password, str):
            with operation(self.connection, LOGGER, write=False):
                row = find_login_record(self.connection, username.strip())
            if row is not None and verify_password(password, row["password_hash"]):
                values = dict(row)
                del values["password_hash"]
                self.current = Account(**values)
                return self.current
        raise BookingError("AUTH_FAILED", "账号或密码不正确")

    def logout(self) -> None:
        self.current = None
