"""显式初始化演示业务数据；只允许空业务库，密码只返回给调用者。"""

import secrets
import sqlite3
from datetime import date, timedelta
from typing import Dict, Optional
from uuid import uuid4

from hotel_app.auth import hash_password
from hotel_app.booking import BookingError
from hotel_app.database import transaction, utc_now
from hotel_app.order_rules import BEIJING
from hotel_app.repositories import CatalogRepository


def create_demo(
    connection: sqlite3.Connection, start_date: Optional[date] = None
) -> Dict[str, str]:
    """返回三个演示账号的新密码；任何已有业务数据都会阻止导入。"""
    if start_date is None:
        start_date = utc_now().astimezone(BEIJING).date()
    credentials = {}
    tables = ("hotels users room_types daily_inventory rooms orders order_items "
              "night_details room_assignments booking_requests "
              "audit_logs settlements").split()
    with transaction(connection):
        if any(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
               for table in tables):
            raise BookingError("DEMO_NOT_EMPTY", "演示数据仅能写入空业务库，请使用新数据库文件")
        catalog = CatalogRepository(connection)
        hotel = catalog.create_hotel("演示酒店", "演示地址（课程测试数据）")
        for name, bed, price, numbers in (
            ("大床房", "大床", 30000, ("301", "302")),
            ("双床房", "双床", 28000, ("401", "402")),
        ):
            room_type = catalog.create_room_type(hotel.id, name, bed, 2)
            connection.executemany(
                "INSERT INTO rooms (id, hotel_id, room_type_id, number) "
                "VALUES (?, ?, ?, ?)",
                [(uuid4().hex, hotel.id, room_type.id, number) for number in numbers],
            )
            connection.executemany(
                "INSERT INTO daily_inventory "
                "(id, room_type_id, stay_date, price_cents, total_rooms) "
                "VALUES (?, ?, ?, ?, 2)",
                [(uuid4().hex, room_type.id,
                  (start_date + timedelta(days=day)).isoformat(), price)
                 for day in range(30)],
            )
        for role in ("CUSTOMER", "STAFF", "ADMIN"):
            username = role.lower()
            password = secrets.token_urlsafe(12)
            connection.execute(
                "INSERT INTO users "
                "(id, username, password_hash, name, contact, role, hotel_id) "
                "VALUES (?, ?, ?, ?, '演示联系方式', ?, ?)",
                (uuid4().hex, username, hash_password(password), "演示" + username,
                 role, hotel.id if role == "STAFF" else None),
            )
            credentials[username] = password
    return credentials
