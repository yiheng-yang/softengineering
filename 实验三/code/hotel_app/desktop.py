"""最小桌面界面：只组织输入输出，业务操作交给现有服务。"""

import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox, simpledialog, ttk

from hotel_app.booking import BookingError, BookingRequest, RoomRequest
from hotel_app.database import utc_now
from hotel_app.desktop_model import DesktopModel
from hotel_app.order_rules import BEIJING

STATES = dict(PENDING="待确认", CONFIRMED="已确认", CHECKED_IN="已入住",
              COMPLETED="已完成", CANCELLED="已取消", REJECTED="已拒绝", EXPIRED="已超时",
              AVAILABLE="可入住", OCCUPIED="已入住", DIRTY="待清洁", MAINTENANCE="维修中")
ROLES = dict(CUSTOMER="顾客", STAFF="工作人员", ADMIN="管理员")


def money(cents):
    return f"¥{cents // 100}.{cents % 100:02d}"


class DesktopApp:
    def __init__(self, root, connection):
        self.root, self.model = root, DesktopModel(connection)
        root.title("酒店预订系统")
        root.geometry("1080x760")
        root.minsize(940, 680)
        self.status = tk.StringVar(value="欢迎使用酒店预订系统")
        status_label = ttk.Label(root, textvariable=self.status, padding=8)
        status_label.pack(side="bottom", fill="x")
        self.body = ttk.Frame(root, padding=18)
        self.body.pack(fill="both", expand=True)
        self.login_page()
        self.timer = root.after(100, self.tick)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def run(self, action):
        try:
            return action()
        except (BookingError, ValueError) as error:
            self.status.set(str(error))
            messagebox.showerror("无法完成操作", str(error), parent=self.root)
            button = getattr(self, "submit_button", None)
            if (self.model.quote is None and button is not None
                    and button.winfo_exists()):
                self.invalidate()

    def button(self, parent, text, action):
        widget = ttk.Button(parent, text=text, command=lambda: self.run(action))
        widget.pack(side="left", padx=4, pady=6)
        return widget

    def table(self, parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=8)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=120, minwidth=70)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        return tree

    def login_page(self):
        self.model.logout()
        self.cart = []
        self.order_records = {}
        for widget in self.body.winfo_children():
            widget.destroy()
        form = ttk.Frame(self.body, padding=24)
        form.pack(pady=100)
        ttk.Label(form, text="酒店预订系统", font=("Helvetica", 24)).pack(pady=20)
        self.username, self.password = tk.StringVar(), tk.StringVar()
        fields = (("账号", self.username, ""), ("密码", self.password, "•"))
        for label, variable, mask in fields:
            ttk.Label(form, text=label).pack(anchor="w")
            ttk.Entry(form, textvariable=variable, show=mask, width=32).pack(pady=6)
        self.button(form, "登录", self.login)
        self.status.set("请输入账号和密码")

    def login(self):
        try:
            account = self.model.login(self.username.get(), self.password.get())
        finally:
            self.password.set("")
        for widget in self.body.winfo_children():
            widget.destroy()
        bar = ttk.Frame(self.body)
        bar.pack(fill="x")
        ttk.Label(bar, text=f"{account.name} · {ROLES[account.role]}").pack(side="left")
        self.button(bar, "退出登录", self.login_page)
        self.button(bar, "刷新", self.refresh)
        notebook = ttk.Notebook(self.body)
        notebook.pack(fill="both", expand=True)
        if account.role == "CUSTOMER":
            self.booking_page(notebook)
        orders = ttk.Frame(notebook, padding=12)
        notebook.add(orders, text="我的订单" if account.role == "CUSTOMER" else "订单处理")
        columns = ("订单编号", "酒店", "入住", "退房", "状态", "总额", "费用")
        self.order_table = self.table(orders, columns)
        actions = {"CUSTOMER": ("取消订单",), "STAFF": ("确认", "拒绝", "入住", "退房"),
                   "ADMIN": ("酒店原因取消",)}[account.role]
        for action in actions:
            self.button(orders, action, lambda action=action: self.order_action(action))
        if account.role == "STAFF":
            rooms = ttk.Frame(notebook, padding=12)
            notebook.add(rooms, text="房间状态")
            self.room_table = self.table(rooms, ("房号", "房型", "房态"))
            self.button(rooms, "完成清洁", self.clean)
        self.refresh()
        self.status.set("已登录；选择记录后可执行相应操作")

    def booking_page(self, notebook):
        page = ttk.Frame(notebook, padding=12)
        notebook.add(page, text="预订房间")
        self.hotels = self.model.hotels()
        self.hotel = ttk.Combobox(
            page, state="readonly", values=[h["name"] for h in self.hotels]
        )
        self.hotel.pack(fill="x", pady=4)
        dates = ttk.Frame(page)
        dates.pack(fill="x")
        today = utc_now().astimezone(BEIJING).date()
        self.arrival = tk.StringVar(value=str(today))
        self.departure = tk.StringVar(value=str(today + timedelta(days=1)))
        for label, variable in (("入住日期", self.arrival), ("退房日期", self.departure)):
            ttk.Label(dates, text=label + "（YYYY-MM-DD）").pack(side="left", padx=4)
            ttk.Entry(dates, textvariable=variable, width=13).pack(side="left", padx=4)
        choices = ttk.Frame(page)
        choices.pack(fill="x", pady=6)
        self.room_type = ttk.Combobox(choices, state="readonly", width=30)
        self.room_type.pack(side="left")
        ttk.Label(choices, text="该间入住人数").pack(side="left", padx=8)
        self.guests = tk.StringVar(value="1")
        ttk.Entry(choices, textvariable=self.guests, width=5).pack(side="left")
        self.button(choices, "添加一间", self.add_room)
        self.button(choices, "移除选中房间", self.remove_room)
        self.cart_list = tk.Listbox(page, height=5)
        self.cart_list.pack(fill="x", pady=6)
        actions = ttk.Frame(page)
        actions.pack(fill="x")
        self.button(actions, "获取报价", self.get_quote)
        self.submit_button = self.button(actions, "确认报价并预订 / 重试", self.submit)
        self.quote_text = tk.Text(page, height=10, wrap="word", state="disabled")
        self.quote_text.pack(fill="both", expand=True, pady=8)
        for variable in (self.arrival, self.departure, self.guests):
            variable.trace_add("write", lambda *_: self.invalidate())
        self.hotel.bind("<<ComboboxSelected>>", lambda _: self.change_hotel())
        self.room_type.bind("<<ComboboxSelected>>", lambda _: self.invalidate())
        if self.hotels:
            self.hotel.current(0)
        self.change_hotel()

    def invalidate(self):
        self.model.invalidate_quote()
        self.submit_button.configure(state="disabled")
        self.show_quote("请添加房间并获取最新报价。每单最多5间、14晚。")

    def show_quote(self, text):
        self.quote_text.configure(state="normal")
        self.quote_text.delete("1.0", "end")
        self.quote_text.insert("1.0", text)
        self.quote_text.configure(state="disabled")

    def change_hotel(self):
        self.cart.clear()
        self.cart_list.delete(0, "end")
        self.types = []
        if self.hotels:
            self.types = self.model.room_types(self.hotels[self.hotel.current()]["id"])
        labels = [f'{r["name"]}（最多{r["capacity"]}人）' for r in self.types]
        self.room_type.configure(values=labels)
        self.room_type.set("")
        if self.types:
            self.room_type.current(0)
        self.invalidate()

    def add_room(self):
        if not self.types or self.room_type.current() < 0:
            raise BookingError("INVALID_INPUT", "请选择房型")
        room = self.types[self.room_type.current()]
        guests = int(self.guests.get())
        self.cart.append(RoomRequest(room["id"], guests))
        self.cart_list.insert("end", f'{room["name"]} · {guests}人')
        self.invalidate()

    def remove_room(self):
        selected = self.cart_list.curselection()
        if selected:
            self.cart.pop(selected[0])
            self.cart_list.delete(selected[0])
            self.invalidate()

    def get_quote(self):
        if not self.hotels:
            raise BookingError("UNAVAILABLE", "暂无营业酒店")
        request = BookingRequest(self.hotels[self.hotel.current()]["id"],
                                 date.fromisoformat(self.arrival.get()),
                                 date.fromisoformat(self.departure.get()),
                                 tuple(self.cart))
        quote = self.model.get_quote(request)
        names = {r["id"]: r["name"] for r in self.types}
        lines = [f"总价：{money(quote.total_cents)}；共{len(self.cart)}间房。到店付款。"]
        lines += [f"{p.stay_date} · {names[p.room_type_id]} · 每间{money(p.price_cents)}"
                  for p in quote.prices]
        self.show_quote("\n".join(lines))
        self.submit_button.configure(state="normal")

    def submit(self):
        try:
            result = self.model.submit_quote()
        except BookingError as error:
            if error.code != "DUPLICATE_BOOKING":
                raise
            prompt = "已有相同内容订单：\n" + "\n".join(error.order_ids) + "\n仍要新增？"
            if not messagebox.askyesno("重复预订提醒", prompt, parent=self.root):
                return
            result = self.model.submit_quote(allow_duplicate=True)
        text = (f"订单：{result.order_id}\n状态：{STATES[result.status]}"
                f"\n总价：{money(result.total_cents)}")
        messagebox.showinfo("订单结果", text, parent=self.root)
        self.refresh()

    def refresh(self):
        if self.model.auth.current is None:
            return
        self.order_records = {r["id"]: r for r in self.model.order_rows()}
        self.order_table.delete(*self.order_table.get_children())
        for key, row in self.order_records.items():
            values = (key[:8], row["hotel_name"], row["check_in_date"],
                      row["check_out_date"], STATES[row["status"]],
                      money(row["total_cents"]), money(row["fee_cents"]))
            self.order_table.insert("", "end", iid=key, values=values)
        if self.model.auth.current.role == "STAFF":
            self.room_table.delete(*self.room_table.get_children())
            for row in self.model.room_rows():
                values = (row["number"], row["type_name"], STATES[row["status"]])
                self.room_table.insert("", "end", iid=row["id"], values=values)

    @staticmethod
    def selected(tree):
        if not tree.selection():
            raise BookingError("INVALID_INPUT", "请先选择一条记录")
        return tree.selection()[0]

    def order_action(self, action):
        order_id, actor = self.selected(self.order_table), self.model.auth.current.id
        if action == "入住":
            return self.allocate_dialog(order_id)
        reason = ""
        if action in ("拒绝", "酒店原因取消"):
            reason = simpledialog.askstring(action, "请填写处理原因", parent=self.root)
            if reason is None:
                return
        prompt = "待确认订单免费取消，已确认订单收取成交总额20%。确认取消？"
        if action == "取消订单" and not messagebox.askyesno(
            "取消订单", prompt, parent=self.root
        ):
            return
        calls = {"确认": lambda: self.model.orders.confirm(actor, order_id),
                 "拒绝": lambda: self.model.orders.reject(actor, order_id, reason),
                 "取消订单": lambda: self.model.orders.cancel(actor, order_id),
                 "酒店原因取消": lambda: self.model.orders.cancel_by_hotel(
                     actor, order_id, reason),
                 "退房": lambda: self.model.stays.check_out(actor, order_id)}
        self.show_result(calls[action]())

    def show_result(self, order):
        text = (f"状态：{STATES[order.status]}\n应收费用：{money(order.fee_cents)}"
                f"\n关闭原因：{order.close_reason or '无'}")
        messagebox.showinfo("订单当前状态", text, parent=self.root)
        self.refresh()

    def allocate_dialog(self, order_id):
        items = self.model.item_rows(order_id)
        self.model.orders.close_no_shows()
        order = self.model.orders.repository.get(order_id)
        if order.status != "CONFIRMED":
            return self.show_result(order)
        rooms = self.model.room_rows()
        window = tk.Toplevel(self.root)
        window.title("整单分配房间")
        window.transient(self.root)
        window.grab_set()
        selections, used = {}, set()
        for item in items:
            label = ttk.Label(window, text=f'{item["type_name"]} · {item["guests"]}人')
            label.pack(padx=24, pady=6)
            choices = {r["number"]: r["id"] for r in rooms
                       if r["room_type_id"] == item["room_type_id"]
                       and r["status"] == "AVAILABLE"}
            combo = ttk.Combobox(window, values=list(choices), state="readonly")
            combo.pack(padx=24, pady=4)
            available = (number for number, key in choices.items() if key not in used)
            combo.set(next(available, ""))
            used.add(choices.get(combo.get()))
            selections[item["id"]] = (combo, choices)

        def apply():
            if any(not combo.get() for combo, _ in selections.values()):
                raise BookingError("ROOM_UNAVAILABLE", "请为每一间房选择房号")
            mapping = {key: choices[combo.get()]
                       for key, (combo, choices) in selections.items()}
            result = self.model.stays.check_in(
                self.model.auth.current.id, order_id, mapping
            )
            window.destroy()
            self.show_result(result.order)

        self.button(window, "整单入住", apply)
        self.button(window, "返回", window.destroy)

    def clean(self):
        self.model.stays.complete_cleaning(
            self.model.auth.current.id, self.selected(self.room_table)
        )
        self.refresh()

    def tick(self):
        try:
            self.model.orders.expire_due()
            self.model.orders.close_no_shows()
            self.refresh()
        except BookingError as error:
            self.status.set("到期清理失败，将稍后重试：" + str(error))
        finally:
            self.timer = self.root.after(60000, self.tick)

    def close(self):
        self.root.after_cancel(self.timer)
        self.model.logout()
        self.root.destroy()


def launch(connection):
    if tk.TkVersion < 8.6:
        raise RuntimeError("当前 Tk 版本过旧，请使用 ./run.command 启动项目专用环境。")
    root = tk.Tk()
    DesktopApp(root, connection)
    root.mainloop()
