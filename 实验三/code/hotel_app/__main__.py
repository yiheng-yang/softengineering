"""初始化数据库、创建演示数据或启动桌面界面。"""

import argparse
from contextlib import closing
from pathlib import Path

from hotel_app.booking import BookingError
from hotel_app.database import connect, initialize
from hotel_app.demo import create_demo


def main() -> None:
    """演示密码只在成功创建后显示，不保存到文件或业务日志。"""
    parser = argparse.ArgumentParser(description="酒店预订系统：数据库与演示数据初始化")
    parser.add_argument("command", choices=["init", "demo", "gui"])
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    default_name = "hotel.sqlite3" if args.command == "init" else "demo.sqlite3"
    database = args.database or Path(default_name)
    if args.command == "gui" and not database.is_file():
        parser.exit(1, "数据库不存在，请先使用 init 或 demo 命令创建。\n")
    try:
        with closing(connect(database)) as connection:
            initialize(connection)
            if args.command == "gui":
                from hotel_app.desktop import launch
                launch(connection)
                return
            credentials = create_demo(connection) if args.command == "demo" else {}
    except (BookingError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    print(f"数据库已就绪：{database.resolve()}")
    for username, password in credentials.items():
        print(f"演示账号 {username}，密码 {password}")


if __name__ == "__main__":
    main()
