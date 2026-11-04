"""真实 SQLite 上的报价、预订、故障回滚和并发竞争测试。"""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from support import BookingFixture

from hotel_app.booking import (
    BookingError,
    Quote,
    RoomRequest,
)
from hotel_app.booking_service import BookingService
from hotel_app.database import connect, transaction


class BookingTests(BookingFixture):
    """每个测试使用独立文件；并发线程各自打开连接。"""

    def test_quote_does_not_reserve_inventory(self) -> None:
        quote = self.service.get_quote("a", self.request)
        self.assertEqual(quote.total_cents, 120000)
        self.assertEqual(len(quote.prices), 4)
        self.assert_empty_booking()

    def test_multi_type_multi_night_booking(self) -> None:
        receipt = self.submit()
        self.assertEqual(receipt.status, "PENDING")
        self.assertEqual(receipt.total_cents, 120000)
        self.assertEqual(receipt.created_at, self.now)
        self.assertEqual(
            receipt.confirmation_deadline, self.now + timedelta(minutes=15)
        )
        self.assertEqual(self.scalar("SELECT count(*) FROM orders"), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM order_items"), 2)
        self.assertEqual(self.scalar("SELECT count(*) FROM night_details"), 4)
        self.assertEqual(
            self.scalar("SELECT sum(price_cents) FROM night_details"), 120000
        )
        self.assert_occupied(4)
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_logs"), 1)
        with closing(connect(self.database_path)) as reopened:
            self.assertEqual(
                reopened.execute("SELECT id FROM orders").fetchone()[0],
                receipt.order_id,
            )

    def test_two_rooms_of_same_type_count_twice(self) -> None:
        self.request = replace(
            self.request,
            rooms=(RoomRequest(self.double, 2), RoomRequest(self.double, 1)),
        )
        receipt = self.submit()
        self.assertEqual(receipt.total_cents, 124000)
        counts = self.connection.execute(
            "SELECT occupied_rooms FROM daily_inventory WHERE room_type_id = ?",
            (self.double,),
        ).fetchall()
        self.assertEqual([row[0] for row in counts], [2, 2])

    def test_repeated_type_cannot_overbook(self) -> None:
        self.request = replace(self.request, rooms=(RoomRequest(self.double, 1),) * 2)
        quote = self.service.get_quote("a", self.request)
        self.connection.execute("UPDATE daily_inventory SET total_rooms = 1")
        with self.assertRaises(BookingError) as caught:
            self.service.submit("a", self.request, quote, "request")
        self.assertEqual(caught.exception.code, "UNAVAILABLE")
        self.assert_empty_booking()

    def test_invalid_input_is_rejected_by_quote_and_submit(self) -> None:
        quote = self.service.get_quote("a", self.request)
        cases = [
            replace(self.request, rooms=()),
            replace(self.request, rooms=(RoomRequest(self.double, 1),) * 6),
            replace(self.request, check_out=date(2026, 11, 25)),
            replace(self.request, check_out=self.request.check_in),
            replace(self.request, check_out=date(2026, 11, 9)),
            replace(self.request, rooms=(RoomRequest(self.double, 3),)),
            replace(self.request, rooms=(RoomRequest(self.double, True),)),
            replace(self.request, rooms=(RoomRequest(self.double, 0),)),
            replace(self.request, rooms=(RoomRequest(self.foreign, 1),)),
            replace(self.request, rooms=(RoomRequest("missing", 1),)),
            replace(self.request, check_in="2026-11-10"),
            replace(self.request, hotel_id="missing"),
        ]
        for request in cases:
            with self.subTest(request=request):
                with self.assertRaises(BookingError) as caught:
                    self.service.get_quote("a", request)
                self.assertEqual(caught.exception.code, "INVALID_INPUT")
                with self.assertRaises(BookingError) as caught:
                    self.service.submit("a", request, quote, "invalid")
                self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assert_empty_booking()

    def test_capacity_is_rechecked_at_submission(self) -> None:
        quote = self.service.get_quote("a", self.request)
        self.connection.execute("UPDATE room_types SET capacity = 1")
        with self.assertRaises(BookingError) as caught:
            self.service.submit("a", self.request, quote, "request")
        self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assert_empty_booking()

    def test_only_customer_role_can_quote_or_submit(self) -> None:
        quote = self.service.get_quote("a", self.request)
        for actor_id in ("missing", "admin", "staff", None):
            with self.subTest(actor=actor_id):
                with self.assertRaises(BookingError) as caught:
                    self.service.get_quote(actor_id, self.request)
                self.assertEqual(caught.exception.code, "FORBIDDEN")
                with self.assertRaises(BookingError) as caught:
                    self.service.submit(actor_id, self.request, quote, "request")
                self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.assert_empty_booking()

    def test_one_missing_closed_or_sold_out_night_rejects_everything(self) -> None:
        statements = (
            "DELETE FROM daily_inventory WHERE id = ?",
            "UPDATE daily_inventory SET sale_status = 'STOPPED' WHERE id = ?",
            "UPDATE daily_inventory SET total_rooms = 0 WHERE id = ?",
        )
        for sql in statements:
            with self.subTest(sql=sql):
                quote = self.service.get_quote("a", self.request)
                row = tuple(self.connection.execute(
                    "SELECT * FROM daily_inventory WHERE id = ?", (f"{self.twin}-11",)
                ).fetchone())
                self.connection.execute(sql, (row[0],))
                with self.assertRaises(BookingError) as caught:
                    self.service.submit("a", self.request, quote, "request")
                self.assertEqual(caught.exception.code, "UNAVAILABLE")
                self.assert_empty_booking()
                self.connection.execute(
                    "DELETE FROM daily_inventory WHERE id = ?", (row[0],)
                )
                self.connection.execute(
                    "INSERT INTO daily_inventory VALUES (?, ?, ?, ?, ?, ?, ?)", row
                )

    def test_closed_hotel_cannot_accept_booking(self) -> None:
        quote = self.service.get_quote("a", self.request)
        self.connection.execute("UPDATE hotels SET status = 'CLOSED'")
        with self.assertRaises(BookingError) as caught:
            self.service.submit("a", self.request, quote, "request")
        self.assertEqual(caught.exception.code, "UNAVAILABLE")
        self.assert_empty_booking()

    def test_same_total_but_changed_nightly_prices_require_acceptance(self) -> None:
        quote = self.service.get_quote("a", self.request)
        self.connection.execute(
            "UPDATE daily_inventory SET price_cents = 31000 WHERE room_type_id = ?",
            (self.double,),
        )
        fresh = self.service.get_quote("a", self.request)
        self.assertEqual(fresh.total_cents, quote.total_cents)
        with self.assertRaises(BookingError) as caught:
            self.service.submit("a", self.request, quote, "old-price")
        self.assertEqual(caught.exception.code, "PRICE_CHANGED")
        self.assert_empty_booking()
        result = self.service.submit("a", self.request, fresh, "new-price")
        self.assertEqual(result.total_cents, quote.total_cents)

    def test_missing_duplicate_and_negative_quote_entries_are_rejected(self) -> None:
        quote = self.service.get_quote("a", self.request)
        cases = (
            quote.prices[:-1], quote.prices + (quote.prices[0],),
            (replace(quote.prices[0], price_cents=-1),) + quote.prices[1:],
        )
        for prices in cases:
            with self.subTest(prices=prices):
                with self.assertRaises(BookingError) as caught:
                    self.service.submit(
                        "a", self.request, Quote(quote.request, prices), "bad"
                    )
                self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assert_empty_booking()

    def test_replay_ignores_current_availability_and_date(self) -> None:
        quote = self.service.get_quote("a", self.request)
        original = self.service.submit("a", self.request, quote, "same-key")
        self.connection.execute(
            "UPDATE daily_inventory SET sale_status = 'STOPPED', price_cents = 99999"
        )
        self.connection.execute("UPDATE hotels SET status = 'CLOSED'")
        self.now += timedelta(days=30)
        replay = self.service.submit("a", self.request, quote, "same-key")
        self.assertEqual(original, replay)
        self.assertEqual(self.scalar("SELECT count(*) FROM orders"), 1)
        self.assert_occupied(4)

    def test_room_and_quote_order_do_not_change_request_identity(self) -> None:
        quote = self.service.get_quote("a", self.request)
        original = self.service.submit("a", self.request, quote, "same-key")
        reordered = replace(self.request, rooms=tuple(reversed(self.request.rooms)))
        shuffled_quote = Quote(reordered, tuple(reversed(quote.prices)))
        replay = self.service.submit("a", reordered, shuffled_quote, "same-key")
        self.assertEqual(original, replay)

    def test_same_key_with_changed_content_or_price_conflicts(self) -> None:
        quote = self.service.get_quote("a", self.request)
        self.service.submit("a", self.request, quote, "same-key")
        changed = replace(self.request, rooms=(RoomRequest(self.double, 1),))
        changed_quote = self.service.get_quote("a", changed)
        changed_price = replace(
            quote, prices=(replace(quote.prices[0], price_cents=1),) + quote.prices[1:]
        )
        cases = ((changed, changed_quote), (self.request, changed_price))
        for request, accepted in cases:
            with self.subTest(request=request):
                with self.assertRaises(BookingError) as caught:
                    self.service.submit("a", request, accepted, "same-key")
                self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.scalar("SELECT count(*) FROM orders"), 1)

    def test_duplicate_warning_then_explicit_addition(self) -> None:
        first = self.submit("first")
        with self.assertRaises(BookingError) as caught:
            self.submit("second")
        self.assertEqual(caught.exception.code, "DUPLICATE_BOOKING")
        self.assertEqual(caught.exception.order_ids, (first.order_id,))
        self.assertEqual(self.scalar("SELECT count(*) FROM booking_requests"), 1)
        second = self.submit("second", allow_duplicate=True)
        self.assertNotEqual(first.order_id, second.order_id)
        self.assertEqual(self.scalar("SELECT count(*) FROM orders"), 2)
        self.assert_occupied(8)

    def test_request_keys_are_scoped_to_customer(self) -> None:
        quote = self.service.get_quote("a", self.request)
        first = self.service.submit("a", self.request, quote, "same-key")
        second = self.service.submit("b", self.request, quote, "same-key")
        self.assertNotEqual(first.order_id, second.order_id)

    def test_saved_prices_do_not_change_with_catalog(self) -> None:
        self.submit()
        self.connection.execute("UPDATE daily_inventory SET price_cents = 1")
        self.assertEqual(self.scalar("SELECT total_cents FROM orders"), 120000)
        self.assertEqual(
            self.scalar("SELECT sum(price_cents) FROM night_details"), 120000
        )

    def test_storage_failure_rolls_back_stock_orders_and_requests(self) -> None:
        for table in ("night_details", "audit_logs"):
            with self.subTest(table=table):
                self.connection.execute(
                    f"CREATE TEMP TRIGGER simulate_failure BEFORE INSERT ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END"
                )
                with self.assertLogs("hotel_app.booking_service", level="ERROR"):
                    with self.assertRaises(BookingError) as caught:
                        self.submit()
                self.assertEqual(caught.exception.code, "STORAGE_ERROR")
                self.assertNotIn("simulated", str(caught.exception))
                self.assert_empty_booking()
                self.connection.execute("DROP TRIGGER simulate_failure")
        self.submit()  # 回滚未留下请求标识，可以重试同一个请求。

    def test_conditional_stock_update_failure_rolls_back_prior_updates(self) -> None:
        quote = self.service.get_quote("a", self.request)
        # 跳过第二晚更新，使条件更新的 rowcount 为0，验证防御性分支。
        self.connection.execute(
            "CREATE TEMP TRIGGER skip_stock BEFORE UPDATE ON daily_inventory "
            "WHEN OLD.stay_date = '2026-11-11' BEGIN SELECT RAISE(IGNORE); END"
        )
        with self.assertRaises(BookingError) as caught:
            self.service.submit("a", self.request, quote, "request")
        self.assertEqual(caught.exception.code, "UNAVAILABLE")
        self.assert_empty_booking()

    def test_busy_database_can_retry_same_key(self) -> None:
        quote = self.service.get_quote("a", self.request)
        with closing(connect(self.database_path)) as competing:
            competing.execute("PRAGMA busy_timeout = 1")
            service = BookingService(competing, clock=lambda: self.now)
            with transaction(self.connection):
                with self.assertRaises(BookingError) as caught:
                    service.submit("a", self.request, quote, "request")
                self.assertEqual(caught.exception.code, "BUSY")
            result = service.submit("a", self.request, quote, "request")
        self.assertEqual(result.status, "PENDING")

    def test_new_booking_uses_beijing_date(self) -> None:
        self.now = datetime(2026, 11, 10, 16, tzinfo=timezone.utc)
        with self.assertRaises(BookingError) as caught:
            self.service.get_quote("a", self.request)
        self.assertEqual(caught.exception.code, "INVALID_INPUT")

    def compete(self, actors, keys):
        """线程同时发起提交，无 sleep；每个线程独立连接同一数据库。"""
        quote = self.service.get_quote("a", self.request)
        barrier = threading.Barrier(2, timeout=5)

        def worker(actor, key):
            with closing(connect(self.database_path)) as connection:
                service = BookingService(connection, clock=lambda: self.now)
                barrier.wait()
                try:
                    return service.submit(actor, self.request, quote, key)
                except BookingError as error:
                    return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(worker, actor, key) for actor, key in zip(actors, keys)
            ]
            return [future.result(timeout=10) for future in futures]

    def test_two_connections_compete_for_last_room(self) -> None:
        self.request = replace(self.request, rooms=(RoomRequest(self.double, 1),))
        self.connection.execute("UPDATE daily_inventory SET total_rooms = 1")
        results = self.compete(("a", "b"), ("first", "second"))
        errors = [result for result in results if isinstance(result, BookingError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "UNAVAILABLE")
        self.assertEqual(self.scalar("SELECT count(*) FROM orders"), 1)
        self.assert_occupied(2)

    def test_concurrent_same_request_returns_one_order(self) -> None:
        results = self.compete(("a", "a"), ("same-key", "same-key"))
        self.assertTrue(all(not isinstance(result, BookingError) for result in results))
        self.assertEqual(results[0].order_id, results[1].order_id)
        self.assertEqual(self.scalar("SELECT count(*) FROM orders"), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM booking_requests"), 1)
        self.assert_occupied(4)

    def test_concurrent_same_content_with_new_keys_warns_once(self) -> None:
        results = self.compete(("a", "a"), ("first", "second"))
        errors = [result for result in results if isinstance(result, BookingError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "DUPLICATE_BOOKING")
        self.assertEqual(self.scalar("SELECT count(*) FROM orders"), 1)

    def test_five_rooms_fourteen_nights_are_allowed(self) -> None:
        self.request = replace(
            self.request, check_out=date(2026, 11, 24),
            rooms=(RoomRequest(self.double, 2),) * 5,
        )
        with transaction(self.connection):
            self.connection.execute("DELETE FROM daily_inventory")
            for night in self.request.nights:
                self.connection.execute(
                    "INSERT INTO daily_inventory "
                    "(id, room_type_id, stay_date, price_cents, total_rooms) "
                    "VALUES (?, ?, ?, 10000, 5)",
                    (night.isoformat(), self.double, night.isoformat()),
                )
        receipt = self.submit()
        self.assertEqual(receipt.total_cents, 700000)
        self.assertEqual(self.scalar("SELECT count(*) FROM order_items"), 5)
        self.assertEqual(self.scalar("SELECT count(*) FROM night_details"), 70)
        self.assert_occupied(70)

    def test_invalid_submission_options_create_nothing(self) -> None:
        quote = self.service.get_quote("a", self.request)
        cases = (("", False), (None, False), ("valid", "yes"))
        for key, allow_duplicate in cases:
            with self.subTest(key=key, allow_duplicate=allow_duplicate):
                with self.assertRaises(BookingError) as caught:
                    self.service.submit(
                        "a", self.request, quote, key, allow_duplicate=allow_duplicate
                    )
                self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assert_empty_booking()


if __name__ == "__main__":
    unittest.main()
