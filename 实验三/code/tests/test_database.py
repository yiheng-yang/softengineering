"""使用独立临时文件验证真实 SQLite 约束，不连接用户业务数据库。"""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from hotel_app.database import connect, initialize, transaction
from hotel_app.repositories import CatalogRepository


class DatabaseTests(unittest.TestCase):
    """每个测试都从新建数据库开始，失败也自动清理连接和临时目录。"""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database_path = Path(directory.name) / "test.sqlite3"
        self.connection = connect(self.database_path)
        self.addCleanup(self.connection.close)
        initialize(self.connection)
        self.catalog = CatalogRepository(self.connection)
        with transaction(self.connection):
            self.hotel = self.catalog.create_hotel("测试酒店", "测试地址")
            self.room_type = self.catalog.create_room_type(
                self.hotel.id, "大床房", "大床", 2
            )
            self.insert(
                "users", id="customer", username="test_customer",
                password_hash="test-fixture-not-a-real-password-hash",
                name="测试顾客", contact="test", role="CUSTOMER",
            )
            self.insert(
                "orders", id="order", customer_id="customer",
                hotel_id=self.hotel.id, check_in_date="2026-11-10",
                check_out_date="2026-11-12", total_cents=60000,
            )
            for item_id in ("item1", "item2", "item3"):
                self.insert(
                    "order_items", id=item_id, order_id="order",
                    room_type_id=self.room_type.id, guests=2,
                )
            self.insert(
                "rooms", id="room", hotel_id=self.hotel.id,
                room_type_id=self.room_type.id, number="301",
            )
            self.insert_inventory()

    def insert(self, table: str, **values: object) -> None:
        """测试夹具辅助函数；表名、列名仅来自本测试文件的固定值。"""
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        self.connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )

    def insert_inventory(self, **overrides: object) -> None:
        """创建一晚库存；允许单个测试覆盖其关注的字段。"""
        values = {
            "id": "inventory", "room_type_id": self.room_type.id,
            "stay_date": "2026-11-10", "price_cents": 30000,
            "total_rooms": 2, "occupied_rooms": 0,
        }
        values.update(overrides)
        self.insert("daily_inventory", **values)

    def scalar(self, query: str) -> object:
        """读取测试断言需要的单个值，SQL 仅来自本文件的固定字符串。"""
        return self.connection.execute(query).fetchone()[0]

    def test_data_survives_connection_close(self) -> None:
        self.connection.close()
        with closing(connect(self.database_path)) as reopened:
            catalog = CatalogRepository(reopened)
            self.assertEqual(catalog.get_hotel(self.hotel.id), self.hotel)
            self.assertEqual(catalog.list_room_types(self.hotel.id), [self.room_type])
            self.assertEqual(reopened.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_repository_reads_are_scoped_and_parameterized(self) -> None:
        with transaction(self.connection):
            other = self.catalog.create_hotel("另一酒店 ' ; --", "另一地址")
        self.assertEqual(self.catalog.list_room_types(other.id), [])
        self.assertEqual(self.catalog.get_hotel(other.id), other)
        self.assertIsNone(self.catalog.get_hotel("' OR 1=1 --"))

    def test_repository_requires_explicit_transaction(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "开启事务"):
            self.catalog.create_hotel("禁止隐式提交", "地址")

    def test_initialization_is_repeatable_without_losing_data(self) -> None:
        initialize(self.connection)
        self.assertEqual(self.catalog.get_hotel(self.hotel.id), self.hotel)
        self.assertEqual(self.scalar("PRAGMA user_version"), 1)

    def test_unversioned_nonempty_database_is_not_overwritten(self) -> None:
        with closing(connect(":memory:")) as connection:
            connection.execute("CREATE TABLE existing_data (value TEXT)")
            connection.execute("INSERT INTO existing_data VALUES ('keep')")
            with self.assertRaisesRegex(RuntimeError, "拒绝覆盖"):
                initialize(connection)
            row = connection.execute("SELECT * FROM existing_data").fetchone()
            self.assertEqual(row[0], "keep")

    def test_unknown_database_version_is_rejected(self) -> None:
        with closing(connect(":memory:")) as connection:
            connection.execute("PRAGMA user_version = 99")
            with self.assertRaisesRegex(RuntimeError, "显式迁移"):
                initialize(connection)

    def test_duplicate_inventory_is_rejected(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.insert_inventory(id="duplicate")

    def test_invalid_foreign_key_is_rejected(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.catalog.create_room_type("missing-hotel", "房型", "大床", 2)

    def test_referenced_hotel_cannot_be_deleted(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.connection.execute(
                    "DELETE FROM hotels WHERE id = ?", (self.hotel.id,)
                )

    def test_inventory_values_are_bounded_integers(self) -> None:
        cases = [
            {"total_rooms": -1}, {"occupied_rooms": -1},
            {"occupied_rooms": 3}, {"price_cents": -1},
            {"price_cents": 1.5}, {"total_rooms": "invalid"},
            {"occupied_rooms": 0.5},
        ]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(sqlite3.IntegrityError):
                    with transaction(self.connection):
                        self.insert_inventory(
                            id="invalid", stay_date="2026-11-11", **invalid
                        )

    def test_inventory_cannot_be_reduced_below_occupancy(self) -> None:
        with transaction(self.connection):
            self.connection.execute("UPDATE daily_inventory SET occupied_rooms = 2")
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.connection.execute("UPDATE daily_inventory SET total_rooms = 1")

    def test_invalid_calendar_dates_are_rejected(self) -> None:
        for invalid in ("2026-02-30", "2026-13-01", "nonsense", "2026-1-1"):
            with self.subTest(date=invalid):
                with self.assertRaises(sqlite3.IntegrityError):
                    with transaction(self.connection):
                        self.insert_inventory(id="invalid", stay_date=invalid)

    def test_order_date_range_is_one_to_fourteen_nights(self) -> None:
        for invalid in ("2026-11-09", "2026-11-10", "2026-11-25"):
            with self.subTest(check_out=invalid):
                with self.assertRaises(sqlite3.IntegrityError):
                    with transaction(self.connection):
                        self.connection.execute(
                            "UPDATE orders SET check_out_date = ?", (invalid,)
                        )
        for valid in ("2026-11-11", "2026-11-24"):
            with transaction(self.connection):
                self.connection.execute(
                    "UPDATE orders SET check_out_date = ?", (valid,)
                )

    def test_room_belongs_to_its_room_types_hotel(self) -> None:
        with transaction(self.connection):
            other = self.catalog.create_hotel("另一个酒店", "地址")
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.insert(
                    "rooms", id="invalid", hotel_id=other.id,
                    room_type_id=self.room_type.id, number="301",
                )

    def test_staff_requires_hotel_but_customers_do_not_have_one(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.connection.execute("UPDATE users SET role = 'STAFF'")
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.connection.execute(
                    "UPDATE users SET hotel_id = ?", (self.hotel.id,)
                )

    def test_nightly_detail_cannot_duplicate_or_mismatch_date(self) -> None:
        values = {
            "id": "night", "item_id": "item1", "inventory_id": "inventory",
            "stay_date": "2026-11-10", "price_cents": 30000,
        }
        with transaction(self.connection):
            self.insert("night_details", **values)
        cases = (
            {"id": "duplicate"},
            {"id": "wrong", "stay_date": "2026-11-11"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(sqlite3.IntegrityError):
                    with transaction(self.connection):
                        self.insert("night_details", **{**values, **changes})

    def test_assignment_uniqueness_and_historical_reuse(self) -> None:
        with transaction(self.connection):
            self.insert(
                "room_assignments", id="first", item_id="item1", room_id="room",
                checked_in_at="2026-11-10T06:00:00Z",
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.insert(
                    "room_assignments", id="conflict", item_id="item2", room_id="room",
                    checked_in_at="2026-11-10T07:00:00Z",
                )
        with transaction(self.connection):
            self.connection.execute(
                "UPDATE room_assignments SET checked_out_at = '2026-11-12T03:00:00Z'"
            )
            self.insert(
                "room_assignments", id="second", item_id="item2", room_id="room",
                checked_in_at="2026-11-12T06:00:00Z",
            )
        self.assertEqual(self.scalar("SELECT count(*) FROM room_assignments"), 2)
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.insert(
                    "room_assignments", id="repeat-item",
                    item_id="item1", room_id="room",
                    checked_in_at="2026-11-10T06:00:00Z",
                    checked_out_at="2026-11-10T07:00:00Z",
                )

    def test_failed_transaction_rolls_back_all_prior_writes(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.connection.execute("UPDATE daily_inventory SET occupied_rooms = 1")
                self.connection.execute("UPDATE orders SET status = 'CONFIRMED'")
                self.insert(
                    "audit_logs", id="log", action="TEST", entity_type="order",
                    entity_id="order", detail="rollback test",
                )
                self.insert_inventory(id="duplicate")
        self.assertEqual(self.scalar("SELECT occupied_rooms FROM daily_inventory"), 0)
        self.assertEqual(self.scalar("SELECT status FROM orders"), "PENDING")
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_logs"), 0)
        self.assertFalse(self.connection.in_transaction)

    def test_python_exception_also_rolls_back(self) -> None:
        with self.assertRaisesRegex(ValueError, "模拟业务失败"):
            with transaction(self.connection):
                self.connection.execute("UPDATE daily_inventory SET occupied_rooms = 1")
                raise ValueError("模拟业务失败")
        self.assertEqual(self.scalar("SELECT occupied_rooms FROM daily_inventory"), 0)

    def test_nested_transaction_is_rejected_and_outer_write_rolled_back(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "嵌套事务"):
            with transaction(self.connection):
                self.connection.execute("UPDATE daily_inventory SET occupied_rooms = 1")
                with transaction(self.connection):
                    self.fail("不应进入嵌套事务")
        self.assertEqual(self.scalar("SELECT occupied_rooms FROM daily_inventory"), 0)

    def test_initialization_cannot_interrupt_business_transaction(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "业务事务"):
            with transaction(self.connection):
                initialize(self.connection)

    def test_request_key_is_unique_for_customer(self) -> None:
        with transaction(self.connection):
            self.insert(
                "booking_requests", customer_id="customer", request_key="request-1",
                payload_digest="original-digest", order_id="order",
            )
            self.insert(
                "orders", id="other-order", customer_id="customer",
                hotel_id=self.hotel.id, check_in_date="2026-11-10",
                check_out_date="2026-11-11", total_cents=30000,
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.connection):
                self.insert(
                    "booking_requests", customer_id="customer", request_key="request-1",
                    payload_digest="different-digest", order_id="other-order",
                )


if __name__ == "__main__":
    unittest.main()
