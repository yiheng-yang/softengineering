"""不依赖图形环境的桌面查询隔离和报价交互测试。"""

from dataclasses import replace
from unittest.mock import patch

from support import BookingFixture

from hotel_app.auth import Account
from hotel_app.booking import BookingError
from hotel_app.desktop_model import DesktopModel


class DesktopModelTests(BookingFixture):
    def setUp(self) -> None:
        super().setUp()
        self.model = DesktopModel(self.connection)
        self.model.booking.clock = lambda: self.now
        self.use("a", "CUSTOMER")

    def use(self, user_id, role, hotel_id=None):
        self.model.auth.current = Account(user_id, user_id, user_id, role, hotel_id)

    def test_order_and_room_queries_are_scoped(self) -> None:
        first = self.submit().order_id
        quote = self.service.get_quote("b", self.request)
        second = self.service.submit("b", self.request, quote, "other").order_id
        other_hotel = self.connection.execute(
            "SELECT hotel_id FROM room_types WHERE id = ?", (self.foreign,)
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO orders (id, customer_id, hotel_id, check_in_date, "
            "check_out_date, total_cents) VALUES ('outside', 'b', ?, '2026-11-10', "
            "'2026-11-12', 0)", (other_hotel,),
        )
        for room_id, hotel_id, room_type in (("local", self.hotel_id, self.double),
                                           ("foreign", other_hotel, self.foreign)):
            self.connection.execute(
                "INSERT INTO rooms (id, hotel_id, room_type_id, number) "
                "VALUES (?, ?, ?, ?)",
                (room_id, hotel_id, room_type, room_id),
            )
        self.assertEqual([row["id"] for row in self.model.order_rows()], [first])
        with self.assertRaises(BookingError):
            self.model.room_rows()
        self.use("staff", "STAFF", self.hotel_id)
        ids = {row["id"] for row in self.model.order_rows()}
        self.assertEqual(ids, {first, second})
        self.assertEqual([row["id"] for row in self.model.room_rows()], ["local"])
        self.assertEqual(len(self.model.item_rows(first)), 2)
        with self.assertRaises(BookingError):
            self.model.item_rows("outside")
        self.use("admin", "ADMIN")
        self.assertEqual(len(self.model.order_rows()), 3)

    def test_quote_edits_login_failure_and_logout_clear_state(self) -> None:
        self.model.get_quote(self.request)
        self.assertIsNotNone(self.model.request_key)
        self.model.invalidate_quote()
        with self.assertRaises(BookingError):
            self.model.submit_quote()
        self.model.get_quote(self.request)
        with self.assertRaises(BookingError):
            self.model.login("missing", "wrong")
        self.assertIsNone(self.model.auth.current)
        self.assertIsNone(self.model.quote)
        self.use("a", "CUSTOMER")
        self.model.get_quote(self.request)
        self.model.logout()
        self.assertIsNone(self.model.request_key)
        for query in (self.model.hotels, self.model.order_rows, self.model.room_rows):
            with self.assertRaises(BookingError):
                query()

    def test_retry_keeps_key_and_duplicate_click_returns_same_order(self) -> None:
        self.model.get_quote(self.request)
        key = self.model.request_key
        for code in ("BUSY", "STORAGE_ERROR"):
            with patch.object(self.model.booking, "submit",
                              side_effect=BookingError(code, "重试")):
                with self.assertRaises(BookingError):
                    self.model.submit_quote()
            self.assertEqual(self.model.request_key, key)
        first = self.model.submit_quote()
        self.assertEqual(self.model.submit_quote(), first)
        self.assertEqual(self.scalar("SELECT count(*) FROM orders"), 1)

    def test_changed_price_and_failed_new_quote_discard_old_quote(self) -> None:
        self.model.get_quote(self.request)
        self.connection.execute(
            "UPDATE daily_inventory SET price_cents = price_cents + 1"
        )
        with self.assertRaises(BookingError) as caught:
            self.model.submit_quote()
        self.assertEqual(caught.exception.code, "PRICE_CHANGED")
        self.assertIsNone(self.model.quote)
        self.model.get_quote(self.request)
        with self.assertRaises(BookingError):
            self.model.get_quote(replace(self.request, rooms=()))
        self.assertIsNone(self.model.quote)
        self.assertIsNone(self.model.request_key)
