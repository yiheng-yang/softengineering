"""认证、会话清理及演示数据原子初始化测试。"""

import sqlite3
import unittest
from contextlib import closing
from datetime import date

from hotel_app.auth import AuthService, hash_password, verify_password
from hotel_app.booking import BookingError
from hotel_app.database import connect, initialize
from hotel_app.demo import create_demo


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect(":memory:")
        self.addCleanup(self.connection.close)
        initialize(self.connection)
        self.credentials = create_demo(self.connection, date(2026, 11, 3))
        self.auth = AuthService(self.connection)

    def test_roles_password_secrecy_and_logout(self) -> None:
        hotel_id = self.connection.execute("SELECT id FROM hotels").fetchone()[0]
        for username, password in self.credentials.items():
            account = self.auth.login(username, password)
            self.assertEqual(account.role, username.upper())
            self.assertEqual(
                account.hotel_id, hotel_id if username == "staff" else None
            )
            self.assertNotIn("password_hash", vars(account))
            self.assertNotIn(password, "\n".join(self.connection.iterdump()))
            self.auth.logout()
            self.assertIsNone(self.auth.current)

    def test_failed_login_clears_old_identity_and_has_uniform_error(self) -> None:
        for username, password in (("customer", "wrong"), ("missing", "wrong"),
                                   ("' OR 1=1 --", "wrong"), (None, None)):
            self.auth.login("admin", self.credentials["admin"])
            with self.assertRaises(BookingError) as caught:
                self.auth.login(username, password)
            self.assertEqual((caught.exception.code, str(caught.exception)),
                             ("AUTH_FAILED", "账号或密码不正确"))
            self.assertIsNone(self.auth.current)

    def test_random_salt_wrong_password_and_malformed_hash(self) -> None:
        password = " 带空格的测试密码 "
        first, second = hash_password(password), hash_password(password)
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password(password, first))
        self.assertTrue(verify_password(password, second))
        self.assertFalse(verify_password(password.strip(), first))
        for invalid in ("", "plain-text", first.replace("600000", "99999999999"),
                        first.replace("pbkdf2_sha256", "unknown"), first + "!", None):
            self.assertFalse(verify_password(password, invalid))
        self.connection.execute("UPDATE users SET password_hash = 'broken'")
        with self.assertRaises(BookingError):
            self.auth.login("customer", self.credentials["customer"])

    def test_demo_consistency_and_repeat_does_not_overwrite(self) -> None:
        for table, expected in (("users", 3), ("hotels", 1), ("room_types", 2),
                                ("rooms", 4), ("daily_inventory", 60)):
            row = self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            self.assertEqual(row[0], expected)
        self.assertEqual(self.connection.execute(
            "SELECT min(stay_date), max(stay_date), min(total_rooms), max(total_rooms) "
            "FROM daily_inventory"
        ).fetchone()[:], ("2026-11-03", "2026-12-02", 2, 2))
        violations = self.connection.execute("PRAGMA foreign_key_check").fetchall()
        self.assertEqual(violations, [])
        before = list(self.connection.iterdump())
        with self.assertRaises(BookingError) as caught:
            create_demo(self.connection)
        self.assertEqual(caught.exception.code, "DEMO_NOT_EMPTY")
        self.assertEqual(list(self.connection.iterdump()), before)

    def test_any_existing_business_record_blocks_demo(self) -> None:
        with closing(connect(":memory:")) as connection:
            initialize(connection)
            connection.execute(
                "INSERT INTO audit_logs (id, action, entity_type, entity_id, detail) "
                "VALUES ('keep', 'TEST', 'test', 'test', '保留')"
            )
            before = list(connection.iterdump())
            with self.assertRaises(BookingError):
                create_demo(connection)
            self.assertEqual(list(connection.iterdump()), before)

    def test_seed_failure_rolls_back_every_table(self) -> None:
        with closing(connect(":memory:")) as connection:
            initialize(connection)
            before = list(connection.iterdump())
            connection.execute(
                "CREATE TEMP TRIGGER fail_user BEFORE INSERT ON users "
                "WHEN NEW.role = 'ADMIN' BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                create_demo(connection)
            self.assertEqual(list(connection.iterdump()), before)
