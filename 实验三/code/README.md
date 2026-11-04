# 酒店预订系统

实验三课程项目，Python + Tkinter + SQLite。

## 启动

```sh
./run.command
```

也可双击 `run.command`。默认使用 `demo.sqlite3`。
之前提供的新登录测试库，使用：

```sh
./run.command gui --database demo_login.sqlite3
```

没有演示库时显式创建，终端会显示三个账号的随机密码：

```sh
./run.command demo --database 新的演示库.sqlite3
./run.command gui --database 新的演示库.sqlite3
```

账号为 `customer`、`staff`、`admin`。初始化拒绝非空业务库，
不会重置原密码或覆盖订单。密码不放进源码和Git。

## 功能

- 顾客：酒店及房型选择、多房型预订、逐晚报价、订单查询和取消。
- 工作人员：本酒店确认和拒绝、分房入住、退房、房态及清洁。
- 管理员：平台订单查询、酒店原因取消。
- 程序启动及每60秒处理确认超时和未到店订单；程序关闭后停止。

每单最多5间、14晚。入住日北京时间00:00起可入住，次日12:00起未到店关闭。
退房日期当天起可退房。取消费用由服务按成交金额和状态计算。
本版不含注册、订单调整、续住换房、短信、统计或线上支付。

## 结构

- `desktop.py` / `desktop_model.py`：窗口、会话状态与按身份过滤的查询。
- `booking*.py`：报价和预订；`order*.py`：订单状态、费用和库存释放。
- `stay*.py`：入住、退房和清洁；`auth.py`：密码认证。
- `database.py` / `schema.sql`：SQLite事务与表结构。
- `tests/`：自动化测试；`../开发记录/`：真实开发过程记录。

## 检查

```sh
make check
make lint
make test
make test-gui
```

`make lint` 通过uv运行Ruff，需要首次下载工具。
`make test` 运行78项测试、跳过3项窗口测试；有桌面会话时，
`make test-gui`执行全部81项。测试使用临时数据库。

## 环境

项目运行仅依赖Python标准库；Tk需8.6或更新版本。
这台Mac的系统Tk 8.5出现白屏，已安装项目专用Python 3.12/Tk 9。
启动脚本和Makefile优先使用 `.runtime`，不替换系统Python。
其他机器可安装带Tk的Python，或通过已有uv配置：

```sh
uv python install 3.12 --install-dir .runtime
```

`.runtime`、数据库、缓存不加入Git或最终源码包。
实验报告由学生自行撰写，本文件为运行说明。
