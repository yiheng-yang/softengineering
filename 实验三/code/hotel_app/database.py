"""SQLite 连接、建表与事务边界，不包含酒店业务流程。"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, Union

from hotel_app.booking import BookingError

SCHEMA_VERSION = 1


def utc_now(clock: Optional[Callable[[], datetime]] = None) -> datetime:
    """共用可注入时钟；拒绝没有时区的时间。"""
    now = clock() if clock is not None else datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("时钟必须返回带时区的时间")
    return now.astimezone(timezone.utc)


def connect(database_path: Union[str, Path]) -> sqlite3.Connection:
    """打开连接；调用者负责关闭，写入须使用显式事务。"""
    connection = sqlite3.connect(str(database_path), isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    """仅初始化空数据库；已有当前版本可重复打开，不自动迁移旧版本。"""
    if connection.in_transaction:
        raise RuntimeError("不能在业务事务中初始化数据库")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version != 0:
        raise RuntimeError(f"不支持数据库版本 {version}，需要显式迁移")
    existing = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    if existing is not None:
        raise RuntimeError("数据库不是空库，拒绝覆盖未标记版本的已有数据")
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + schema
            + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
        )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


@contextmanager
def transaction(
    connection: sqlite3.Connection, *, write: bool = True
) -> Iterator[None]:
    """统一提交或回滚；查询报价可使用读快照，暂不允许嵌套事务。"""
    if connection.in_transaction:
        raise RuntimeError("不允许嵌套事务；请由应用服务统一管理事务")
    connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


@contextmanager
def operation(
    connection: sqlite3.Connection, logger: logging.Logger, *, write: bool = True
) -> Iterator[None]:
    """服务共用的事务错误处理，向界面隐藏内部 SQL 错误。"""
    try:
        with transaction(connection, write=write):
            yield
    except sqlite3.Error as error:
        if isinstance(error, sqlite3.OperationalError) and any(
            message in str(error).lower()
            for message in ("database is locked", "database table is locked")
        ):
            raise BookingError("BUSY", "数据库暂时繁忙，请稍后重试") from error
        logger.exception("数据库操作失败，事务已回滚")
        raise BookingError("STORAGE_ERROR", "保存或读取失败，请稍后重试") from error
