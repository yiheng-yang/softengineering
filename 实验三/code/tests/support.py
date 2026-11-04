"""预订和订单生命周期共用测试数据；没有 test_ 方法。"""

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from hotel_app.booking import BookingRequest, OrderReceipt, RoomRequest
from hotel_app.booking_service import BookingService
from hotel_app.database import connect, initialize, transaction
from hotel_app.repositories import CatalogRepository


class BookingFixture(unittest.TestCase):
    """每个测试创建独立 SQLite 文件，业务数据由真实预订服务产生。"""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database_path = Path(directory.name) / "booking.sqlite3"
        self.connection = connect(self.database_path)
        self.addCleanup(self.connection.close)
        initialize(self.connection)
        self.now = datetime(2026, 11, 3, 8, tzinfo=timezone.utc)
        self.service = BookingService(self.connection, clock=lambda: self.now)
        catalog = CatalogRepository(self.connection)
        with transaction(self.connection):
            hotel = catalog.create_hotel("预订测试酒店", "测试地址")
            other = catalog.create_hotel("另一酒店", "另一地址")
            self.hotel_id = hotel.id
            self.double = catalog.create_room_type(hotel.id, "大床", "大床", 2).id
            self.twin = catalog.create_room_type(hotel.id, "双床", "双床", 2).id
            self.foreign = catalog.create_room_type(other.id, "外部房型", "床", 2).id
            users = (
                ("a", "CUSTOMER"), ("b", "CUSTOMER"),
                ("admin", "ADMIN"), ("staff", "STAFF"),
            )
            for user_id, role in users:
                self.connection.execute(
                    "INSERT INTO users "
                    "(id, username, password_hash, name, contact, role, hotel_id) "
                    "VALUES (?, ?, 'test-only-not-a-password-hash', "
                    "'测试', 'test', ?, ?)",
                    (user_id, user_id, role, hotel.id if role == "STAFF" else None),
                )
            for room_type, prices in ((self.double, (30000, 32000)),
                                      (self.twin, (28000, 30000))):
                for day, price in zip((10, 11), prices):
                    self.connection.execute(
                        "INSERT INTO daily_inventory "
                        "(id, room_type_id, stay_date, price_cents, total_rooms) "
                        "VALUES (?, ?, ?, ?, 4)",
                        (f"{room_type}-{day}", room_type, f"2026-11-{day}", price),
                    )
        self.request = BookingRequest(
            self.hotel_id, date(2026, 11, 10), date(2026, 11, 12),
            (RoomRequest(self.double, 2), RoomRequest(self.twin, 2)),
        )

    def scalar(self, query: str) -> object:
        """仅用于本文件固定 SQL 的测试断言。"""
        return self.connection.execute(query).fetchone()[0]

    def submit(self, key: str = "request-1", **options: object) -> OrderReceipt:
        """正常请求的简写；异常报价场景直接调用服务。"""
        quote = self.service.get_quote("a", self.request)
        return self.service.submit("a", self.request, quote, key, **options)

    def assert_empty_booking(self) -> None:
        """失败后订单及关联记录不应残留，所有库存仍未占用。"""
        tables = (
            "orders", "order_items", "night_details", "booking_requests", "audit_logs"
        )
        for table in tables:
            self.assertEqual(self.scalar(f"SELECT count(*) FROM {table}"), 0)
        self.assert_occupied(0)

    def assert_occupied(self, expected: int) -> None:
        """核对全库已占房晚数；其他测试分别核对各房型数量。"""
        self.assertEqual(
            self.scalar("SELECT sum(occupied_rooms) FROM daily_inventory"), expected
        )

