"""真实 Tk 控件测试；设 HOTEL_GUI_TESTS=1 显式启用，需要桌面会话。"""

import os
import tkinter as tk
from unittest import skipUnless
from unittest.mock import patch

from support import BookingFixture

from hotel_app.auth import hash_password
from hotel_app.booking import BookingError
from hotel_app.desktop import DesktopApp


@skipUnless(os.environ.get("HOTEL_GUI_TESTS") == "1", "需显式启用桌面测试")
class DesktopUITests(BookingFixture):
    def setUp(self):
        super().setUp()
        self.connection.execute(
            "UPDATE users SET password_hash = ?", (hash_password("test"),)
        )
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = DesktopApp(self.root, self.connection)
        self.addCleanup(self.app.close)
        services = (self.app.model.booking, self.app.model.orders, self.app.model.stays)
        for service in services:
            service.clock = lambda: self.now

    def login(self, name):
        self.app.username.set(name)
        self.app.password.set("test")
        self.app.login()

    def test_quote_edit_submit_repeat_and_logout(self):
        self.login("a")
        app = self.app
        app.hotel.current(next(i for i, h in enumerate(app.hotels)
                               if h["id"] == self.hotel_id))
        app.change_hotel()
        app.arrival.set("2026-11-10")
        app.departure.set("2026-11-12")
        app.add_room()
        app.get_quote()
        app.departure.set("2026-11-11")
        self.assertIsNone(app.model.quote)
        self.assertEqual(str(app.submit_button["state"]), "disabled")
        app.get_quote()
        with patch("hotel_app.desktop.messagebox.showinfo"):
            app.submit()
            app.submit()
        self.assertEqual(self.scalar("SELECT count(*) FROM orders"), 1)
        self.assertEqual(len(app.order_table.get_children()), 1)
        app.login_page()
        self.assertIsNone(app.model.auth.current)
        self.assertEqual((app.cart, app.order_records), ([], {}))
        self.assertEqual(app.password.get(), "")

    def test_staff_and_admin_actions_show_actual_state(self):
        order_id = self.submit().order_id
        self.login("staff")
        self.app.order_table.selection_set(order_id)
        with patch("hotel_app.desktop.messagebox.showinfo") as info:
            self.app.order_action("确认")
            self.assertIn("已确认", info.call_args.args[1])
        self.app.login_page()
        self.login("admin")
        self.app.order_table.selection_set(order_id)
        with patch("hotel_app.desktop.simpledialog.askstring", return_value="酒店停业"):
            with patch("hotel_app.desktop.messagebox.showinfo") as info:
                self.app.order_action("酒店原因取消")
                self.assertIn("已取消", info.call_args.args[1])

    def test_timer_failure_still_schedules_next_run(self):
        self.root.after_cancel(self.app.timer)
        with patch.object(self.app.model.orders, "expire_due",
                          side_effect=BookingError("BUSY", "数据库繁忙")):
            self.app.tick()
        self.assertIn("稍后重试", self.app.status.get())
        self.assertIn(self.app.timer, self.root.tk.call("after", "info"))
