"""基础资料读写；只执行 SQL，不自行提交事务或处理角色权限。"""

import sqlite3
from typing import List, Optional
from uuid import uuid4

from hotel_app.models import Hotel, RoomType


def find_login_record(
    connection: sqlite3.Connection, username: str
) -> Optional[sqlite3.Row]:
    """仅供认证服务使用；调用方不得把含密码哈希的记录交给界面。"""
    return connection.execute(
        "SELECT id, username, name, role, hotel_id, password_hash FROM users "
        "WHERE username = ?", (username,),
    ).fetchone()


class CatalogRepository:
    """酒店和房型的最小存储接口，供后续应用服务调用。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _require_transaction(self) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError("写入前必须由调用方开启事务")

    def create_hotel(self, name: str, address: str) -> Hotel:
        """新增酒店；输入约束失败时由数据库抛出 IntegrityError。"""
        self._require_transaction()
        hotel = Hotel(uuid4().hex, name.strip(), address.strip(), "OPEN")
        self.connection.execute(
            "INSERT INTO hotels (id, name, address, status) VALUES (?, ?, ?, ?)",
            (hotel.id, hotel.name, hotel.address, hotel.status),
        )
        return hotel

    def get_hotel(self, hotel_id: str) -> Optional[Hotel]:
        """读取酒店；不存在时返回 None。"""
        row = self.connection.execute(
            "SELECT id, name, address, status FROM hotels WHERE id = ?",
            (hotel_id,),
        ).fetchone()
        return Hotel(**dict(row)) if row is not None else None

    def create_room_type(
        self, hotel_id: str, name: str, bed_type: str, capacity: int
    ) -> RoomType:
        """新增房型，使用与调用方共享的数据库事务。"""
        self._require_transaction()
        room_type = RoomType(
            uuid4().hex, hotel_id, name.strip(), bed_type.strip(), capacity
        )
        self.connection.execute(
            "INSERT INTO room_types (id, hotel_id, name, bed_type, capacity) "
            "VALUES (?, ?, ?, ?, ?)",
            (room_type.id, hotel_id, room_type.name, room_type.bed_type, capacity),
        )
        return room_type

    def list_room_types(self, hotel_id: str) -> List[RoomType]:
        """查询指定酒店的房型，按名称及编号稳定排序。"""
        rows = self.connection.execute(
            "SELECT id, hotel_id, name, bed_type, capacity FROM room_types "
            "WHERE hotel_id = ? ORDER BY name, id",
            (hotel_id,),
        ).fetchall()
        return [RoomType(**dict(row)) for row in rows]
