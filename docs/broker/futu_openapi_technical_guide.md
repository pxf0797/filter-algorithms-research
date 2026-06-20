# 富途 OpenAPI 完整接口方案 -- 多周期策略系统集成技术文档

> 适用版本：moomoo-api / futu-api SDK 10.x | OpenD 9.x | 更新日期：2026-06

---

## 1. OpenD 网关架构

### 1.1 组件定位

富途 API 采用两层架构：

```
+--------------------+        TCP/Protobuf        +------------------+
|  量化策略 / Streamlit  | <--------------------------> |  FutuOpenD 网关   |
|  (Python SDK)       |   127.0.0.1:11111         |  (本地/云端进程)    |
+--------------------+                             +--------+---------+
                                                           |
                                              +------------+-----------+
                                              |   富途后端服务器          |
                                              +------------------------+
```

**OpenD (FutuOpenD)** 是本地网关进程，运行于用户机器或云服务器，负责中转协议请求到富途后台。SDK 不与富途服务器直连，全部通过 OpenD 转发。这种设计的好处是：用户无需管理证书、密钥和长连接，由 OpenD 统一处理。

### 1.2 启动方式

**方案 A：可视化 OpenD（推荐开发调试）**
- Windows/Mac 图形界面，一键启动
- 后台实际也是启动命令行 OpenD

**方案 B：命令行 / Docker（推荐生产部署）**
```bash
# Linux 命令行启动
./FutuOpenD -cfg_file /path/to/OpenD.xml

# Docker 部署（社区镜像 ostai/futuopend）
docker run -d \
  --name FutuOpenD \
  --restart=always \
  -e "FUTU_LOGIN_ACCOUNT=your_account" \
  -e "FUTU_LOGIN_PWD_MD5=$(echo -n 'password' | md5)" \
  -e "FUTU_LANG=chs" \
  -e "FUTU_IP=0.0.0.0" \
  -e "FUTU_PORT=11111" \
  -e "SERVER_PORT=8000" \
  -p 11111:11111 \
  -p 8000:8000 \
  ostai/futuopend:latest
```

### 1.3 端口配置

| 组件 | 默认端口 | 说明 |
|------|----------|------|
| API 协议端口 (TCP) | 11111 | Python SDK 连接端口 |
| WebSocket 服务端口 | 8000 | 容器内状态检测、短信验证码输入 |
| Telnet 管理端口 | (自定义) | 远程运维命令端口 |

OpenD 的配置文件为 **OpenD.xml**（XML 格式），在同目录下自动读取。关键配置项：

- **监听地址**: `127.0.0.1` (仅本地) / `0.0.0.0` (所有网卡，需配置加密私钥)
- **监听端口**: 默认 11111
- **日志级别**: `no` / `debug` / `info` / `warning` / `error` / `fatal`
- **API 推送频率**: 行情订阅数据推送间隔(毫秒)，不影响 K 线
- **加密私钥路径**: RSA 私钥文件绝对路径(PKCS#1 格式)，实盘交易强烈建议开启

> 安全提示：监听地址若非 `127.0.0.1`，交易接口**必须**配置私钥加密；WebSocket 须配置 SSL。

### 1.4 连接管理

**协议栈**: SDK 与 OpenD 之间走自定义 TCP 协议 + Protobuf 序列化。

封包结构（固定 44 字节头）：

```
+----+----------+-----+-----+----------+---------+----------+----------+
| FT | ProtoID  | Fmt | Ver | SerialNo | BodyLen | SHA1(20) | Rsv(8)   |
| 2B |   4B     | 1B  | 1B  |   4B     |   4B    |   20B    |   8B     |
+----+----------+-----+-----+----------+---------+----------+----------+
|                       Body (Protobuf)                                 |
+-----------------------------------------------------------------------+
```

- **字节序**: Little-endian（x86 原生序）
- **格式**: 推荐 Protobuf（`nProtoFmtType=0`），减少 JSON 转换开销
- **校验**: SHA1 校验体，保证数据完整性

**连接流程**: 建立 TCP 连接 -> InitConnect(1001) -> 请求数据或接收推送 -> 定时 KeepAlive(1004)

**心跳保活**:
- OpenD 返回 `KeepAliveInterval` 值（秒），客户端需在该间隔内发送心跳
- SDK 通常自动处理；如果手动实现协议层，需独立协程/线程定时发送协议 1004
- 典型间隔 30-60 秒，连接断开后 SDK 会抛出异常，需自行实现重连

**加密机制**:
- InitConnect 请求使用 **RSA-1024** 公钥加密（仅首次）
- 后续全部使用 **AES-ECB** 对称加密（密钥由 InitConnect 响应返回）
- 实盘用户强烈推荐开启

---

## 2. 行情订阅

### 2.1 OpenQuoteContext 核心用法

```python
from futu import *

# 连接到本地 OpenD
quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
```

### 2.2 订阅数据（先订阅后获取）

订阅是 Futu API 的核心模式：必须先 `subscribe()` 声明需要哪些股票的哪些数据类型，之后才能 `get_*` 拉取。

```python
# 订阅：支持多股票 + 多数据类型
codes = ['HK.00700', 'HK.09988', 'US.AAPL', 'US.TSLA']
ret, err = quote_ctx.subscribe(
    codes,
    [SubType.QUOTE, SubType.TICKER, SubType.ORDER_BOOK,
     SubType.K_DAY, SubType.K_1M, SubType.K_5M, SubType.RT_DATA],
    subscribe_push=False   # False = 手动拉取模式
)
```

**SubType 全列表**：

| 类型 | 说明 | 典型用途 |
|------|------|----------|
| `QUOTE` | 基础快照(最新价/开盘/高低/成交量) | 价格监控 |
| `TICKER` | 逐笔成交 | 高频/盘口分析 |
| `ORDER_BOOK` | 买卖十档 | 深度分析 |
| `RT_DATA` | 实时分时数据 | 日内走势 |
| `BROKER` | 经纪队列 | 订单流分析 |
| `K_DAY` / `K_WEEK` / `K_MONTH` | 日/周/月 K 线 | 中长周期策略 |
| `K_1M` / `K_5M` / `K_15M` / `K_30M` / `K_60M` | 分钟 K 线 | 短周期策略 |
| `K_3M` / `K_QUARTER` / `K_YEAR` | 3分/季/年 K 线 | 特定周期 |

### 2.3 获取各类数据

```python
# 1. 基础快照
ret, data = quote_ctx.get_stock_quote(codes)
# data 字段: code, last_price, open_price, high_price, low_price,
#            volume, turnover, change_val, change_rate, ...

# 2. 盘口深度
ret, ob = quote_ctx.get_order_book('HK.00700')
# ob['Bid'][0] = {'price': 380.0, 'volume': 5000}
# ob['Ask'][0] = {'price': 380.2, 'volume': 3000}

# 3. K 线（历史）
ret, kdata, page_req_key = quote_ctx.get_history_kline(
    'HK.00700', start='2026-01-01', end='2026-06-20',
    ktype=KLType.K_1M, max_count=1000
)

# 4. 逐笔
ret, ticks = quote_ctx.get_rt_ticker('HK.00700', max_count=1000)

# 5. 分时
ret, rtdata = quote_ctx.get_rt_data('HK.00700')

# 6. 市场快照（批量查询）
ret, snapshots = quote_ctx.get_market_snapshot(codes)
```

### 2.4 WebSocket 推送模式 vs 轮询模式

**推送模式（推荐用于实时策略）**:

```python
class MyQuoteHandler(TickerHandlerBase):
    def on_ticker(self, code, ticker_list):
        for t in ticker_list:
            print(f"[{code}] 成交价:{t.price} 量:{t.volume}")

quote_ctx.set_handler(MyQuoteHandler())
quote_ctx.start()   # 开始异步接收
quote_ctx.subscribe(['HK.00700'], [SubType.TICKER, SubType.QUOTE])

# 程序保持运行...
time.sleep(3600)

quote_ctx.stop()
quote_ctx.close()
```

**轮询模式（推荐多周期策略的历史数据拉取）**:
```python
# subscribe_push=False + while True + time.sleep
# 适合批量扫描场景，无需维护长连接心跳
```

**选择建议**：
- 短周期策略（1-5分钟 K 线轮询）：**轮询模式**，减少连接复杂度
- 中周期策略（日线及以上）：**轮询模式**，按需拉取
- 盘口/逐笔/高频信号：**推送模式**，使用 Handler 回调
- 混合策略：独立连接分别使用两种模式

### 2.5 多品种订阅限制

| 用户等级 | 订阅额度 | 条件 |
|----------|----------|------|
| Level 1 | 1000 个 | 实盘用户（联系富途开通） |
| Level 2 | 300 个 | 净资产 > 10,000 HKD |
| Level 3 | 100 个 | 净资产 < 10,000 HKD |

- 每只股票订阅一种数据类型消耗 1 个额度。如订阅 10 只股票的 QUOTE+TICKER+K_DAY 共消耗 30 个额度
- 订阅后最短持有 1 分钟才能取消订阅
- 对于多周期系统（通常监控 20-50 只股票），Level 2 额度已足够

### 2.6 K 线数据合成 vs 拉取

**历史 K 线拉取**（推荐用于回测和策略初始化）:
```python
# 日线：最长 10 年
ret, kdata = quote_ctx.get_history_kline(
    'HK.00700', ktype=KLType.K_DAY, start='2016-01-01', end='2026-06-20'
)

# 分钟线：最长 2 年
ret, kdata = quote_ctx.get_history_kline(
    'US.AAPL', ktype=KLType.K_1M, start='2024-06-20', end='2026-06-20'
)
```

**实时 K 线合成**（适用于实盘策略的实时更新）:
- 通过订阅 `SubType.RT_DATA` 获取逐笔分时数据
- 自行根据时间窗口聚合：开盘价(首笔)、收盘价(末笔)、最高、最低
- 优势：可以自定义任意周期（如 3 分钟、2 小时），不受 SDK 固定周期限制
- 参考代码片段：

```python
import datetime
from collections import defaultdict

class RealTimeKLineBuilder:
    """实时 K 线合成器"""
    def __init__(self, period_minutes=5):
        self.period = period_minutes  # 自定义周期
        self.bars = defaultdict(dict)  # code -> {timestamp: bar}

    def on_tick(self, code, price, volume, timestamp):
        """传入逐笔数据，合成 K 线"""
        # 将时间对齐到周期边界
        dt = datetime.datetime.fromtimestamp(timestamp)
        boundary = dt.replace(
            minute=(dt.minute // self.period) * self.period,
            second=0, microsecond=0
        )
        bar = self.bars[code].get(boundary)
        if not bar:
            bar = {'open': price, 'high': price, 'low': price,
                   'close': price, 'volume': 0, 'time': boundary}
            self.bars[code][boundary] = bar
        bar['high'] = max(bar['high'], price)
        bar['low'] = min(bar['low'], price)
        bar['close'] = price
        bar['volume'] += volume
        return bar
```

---

## 3. 交易接口

### 3.1 OpenTradeContext 核心用法

```python
from futu import *

# 港股交易上下文
hk_ctx = OpenHKTradeContext(host='127.0.0.1', port=11111)
# 美股交易上下文
us_ctx = OpenUSTradeContext(host='127.0.0.1', port=11111)

# 解锁交易（必要步骤！下单前必须调用）
ret, data = hk_ctx.unlock_trade('your_trade_password')
if ret != RET_OK:
    raise RuntimeError(f"解锁失败: {data}")
```

### 3.2 下单接口

```python
# 限价买入
ret, data = hk_ctx.place_order(
    price=380.0,
    qty=100,
    code='HK.00700',
    trd_side=TrdSide.BUY,
    order_type=OrderType.NORMAL,
    trd_env=TrdEnv.REAL,   # 实盘；模拟盘用 TrdEnv.SIMULATE
)

if ret == RET_OK:
    order_id = data['order_id'][0]
    print(f"下单成功，订单号: {order_id}")
else:
    print(f"下单失败: {data}")
```

### 3.3 支持的订单类型

| OrderType | 说明 | 港股 | 美股 | A股通 |
|-----------|------|:----:|:----:|:----:|
| `NORMAL` / `LIMIT` | 限价单 | Y | Y | Y |
| `MARKET` | 市价单 | Y | Y | N |
| `STOP` | 止损限价单 | Y | Y | Y |
| `STOP_MARKET` | 止损市价单 | Y | Y | N |
| `TOUCH` | 触及限价单(止盈) | Y | Y | Y |
| `TOUCH_MARKET` | 触及市价单(止盈) | Y | Y | N |
| `TRAILING_STOP` | 跟踪止损限价单 | Y | Y | N |
| `TRAILING_STOP_MARKET` | 跟踪止损市价单 | Y | Y | N |
| `AUCTION_LIMIT` | 竞价限价单 | Y | - | - |
| `AUCTION_MARKET` | 竞价市价单 | Y | - | - |
| `TWAP` | 时间加权平均算法单 | Y | Y | - |
| `VWAP` | 成交量加权平均算法单 | Y | Y | - |
| `ODD_LOT` | 碎股单 | Y | - | - |

### 3.4 订单状态管理

```python
# 获取未完成订单
ret, orders = hk_ctx.get_order_list(
    status_filter=[OrderStatus.SUBMITTED, OrderStatus.FILLED_PART],
    trd_env=TrdEnv.REAL
)

# 获取成交明细
ret, fills = hk_ctx.get_order_fill_list(
    code='HK.00700', trd_env=TrdEnv.REAL
)

# 撤单
ret, data = hk_ctx.modify_order(
    order_id=order_id,
    modify_op=ModifyOp.CANCEL
)
```

**订单状态枚举**: `SUBMITTED`(已提交) -> `FILLED_PART`(部分成交) -> `FILLED_ALL`(全部成交) / `CANCELLED`(已取消) / `FAILED`(失败)

### 3.5 港股交易时段行为

| 时段 | 时间(HKT) | 接口行为 |
|------|-----------|----------|
| 开前竞价(集合竞价) | 09:00-09:20 | 可提交竞价单(AUCTION_LIMIT/AUCTION_MARKET) |
| 持续交易时段 | 09:30-12:00, 13:00-16:00 | 限价单/市价单正常执行 |
| 收盘竞价 | 16:00-16:10 | 竞价单参与收盘定价 |
| 休市/周末 | 盘后 | 下单被拒绝或存为条件单(视配置) |

香港台风/黑雨等极端天气时，市场可能延迟或暂停开市，OpenD 会返回对应错误码。

### 3.6 异常处理策略

```python
def safe_place_order(ctx, price, qty, code, retry=3):
    for attempt in range(retry):
        try:
            ret, data = ctx.place_order(
                price=price, qty=qty, code=code,
                trd_side=TrdSide.BUY,
                order_type=OrderType.NORMAL,
                trd_env=TrdEnv.REAL
            )
            if ret == RET_OK:
                return data
            else:
                # 错误码处理
                err_code = data.get('err_code', -1)
                if err_code in [3001, 3002]:  # 频率限制
                    time.sleep(3)
                    continue
                elif err_code in [4001]:      # 资金不足
                    raise RuntimeError(f"资金不足: {data}")
                else:
                    raise RuntimeError(f"下单失败: {data}")
        except Exception as e:
            if attempt == retry - 1:
                raise
            time.sleep(2 ** attempt)  # 指数退避
```

---

## 4. Python 集成方案

### 4.1 架构设计：多周期策略系统的 OpenD 共享

```
+---------------------------------------------------+
|                   策略系统进程                        |
|                                                     |
|  +--------------+  +--------------+  +----------+   |
|  | 日线策略线程   |  | 分钟线策略线程 |  | 风控线程  |   |
|  | (get_kline)   |  | (subscribe)  |  | (监控)    |   |
|  +------+-------+  +------+-------+  +----+-----+   |
|         |                 |               |          |
|  +------+-----------------+---------------+------+   |
|  |       QuoteContext 共享实例(进程级单例)       |   |
|  |       host=127.0.0.1:11111               |   |
|  +------------------------------------------+   |
|         |                                         |
|  +------+------+                                  |
|  |   FutuOpenD  |      ----> 富途后台              |
|  +-------------+                                  |
+---------------------------------------------------+
```

**关键设计决策**：

1. **OpenD 是进程级单例** -- 一个 OpenD 实例可被多个 SDK Context 连接。多个策略线程共享同一个 QuoteContext 实例（线程安全需测试；稳妥方案是每个线程独立创建 Context，都连到同一个 OpenD）
2. **行情和交易使用独立 Context** -- `OpenQuoteContext` 只管行情，`OpenTradeContext` 只管交易
3. **行情 Context 数量建议**：1 个用于实时推送，1 个用于历史/轮询拉取，避免推送阻塞批量查询

### 4.2 同步 vs 异步

**实际结论：futu-api 不是 asyncio 原生，不要去硬套 async/await。**

SDK 的 `start()` + `set_handler()` 是基于线程回调的伪异步。推荐：

```python
import threading
import queue

class MarketDataWorker:
    """推送行情工作线程"""
    def __init__(self):
        self.quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        self.tick_queue = queue.Queue()
        self._running = False

    def start(self):
        self._running = True
        # 注册回调
        class Handler(TickerHandlerBase):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
            def on_ticker(self, code, ticker_list):
                self.parent.tick_queue.put((code, ticker_list))

        self.quote_ctx.set_handler(Handler(self))
        self.quote_ctx.start()
        # 订阅品种
        self.quote_ctx.subscribe(
            ['HK.00700', 'US.AAPL'],
            [SubType.TICKER, SubType.QUOTE]
        )

    def get_tick(self, timeout=1.0):
        """供其他线程调用：非阻塞获取最新逐笔"""
        try:
            return self.tick_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._running = False
        self.quote_ctx.stop()
        self.quote_ctx.close()
```

### 4.3 多策略共享 OpenD 的注意事项

| 场景 | 推荐方案 | 注意事项 |
|------|----------|----------|
| 多策略同机器 | 共享同一 OpenD，各策略独立建 Context | 总订阅数不超过 OpenD 额度(Level2=300) |
| 行情+交易分离 | 不同进程各自连接同一个 OpenD | 需将 OpenD 监听地址设为 0.0.0.0 并配加密 |
| Docker 部署 | OpenD 容器 + 策略容器分开 | 通过 docker network / host 模式通信 |

**多进程架构模板**:

```python
# strategy_dispatcher.py —— 统一订阅管理器
class SubscriptionManager:
    def __init__(self):
        self.ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        self._subscribed = set()
        self._lock = threading.Lock()

    def ensure_subscribed(self, codes, sub_types):
        with self._lock:
            new_codes = [c for c in codes if c not in self._subscribed]
            if new_codes:
                self.ctx.subscribe(new_codes, sub_types)
                self._subscribed.update(new_codes)

    def get_quotes(self, codes):
        # 确保已订阅
        self.ensure_subscribed(codes, [SubType.QUOTE])
        return self.ctx.get_stock_quote(codes)
```

### 4.4 错误处理与重试策略

**需处理的异常场景**：

| 异常 | 概率 | 处理方式 |
|------|------|----------|
| OpenD 未启动 | 配置期 | 启动时检测：`ctx.start()` 后 `get_global_state()` |
| 网络断开 | 运维期 | 捕获异常，sleep 后重新 `OpenQuoteContext(...)` |
| 频率超限 | 运行期 | 捕获 RET_ERROR，按 Rate Limit 表退避 |
| 订阅额度满 | 运行期 | 先 `unsubscribe_all()` 再重试 |
| 交易日历 | 策略期 | 提前通过 `get_market_state()` 判断 |

**生产级初始化**:

```python
def create_robust_quote_context(max_retries=5):
    """带重试和健康检查的 Context 创建"""
    for attempt in range(max_retries):
        try:
            ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
            # 验证连接
            ret, state = ctx.get_global_state()
            if ret == RET_OK:
                return ctx
            ctx.close()
        except Exception as e:
            print(f"连接 OpenD 失败(第{attempt+1}次): {e}")
            time.sleep(3)
    raise RuntimeError("无法连接到 OpenD，请确保网关已启动")
```

---

## 5. 成本与限制

### 5.1 实时行情费用

| 市场 | 数据等级 | 费用（港股） | 说明 |
|------|----------|-------------|------|
| 港股 | LV1 (基础) | 免费 | 延时 15 分钟，或基本报价 |
| 港股 | LV2 (实时) | 约 200-400 HKD/月 | 实时逐笔+十档盘口，官方渠道订阅 |
| 美股 | Level 1 | 免费 | 延时 15 分钟 |
| 美股 | Level 2 | 约 $10-20 USD/月 | 实时报价+盘口，需付费订阅 |
| A 股通 | 基础 | 免费 | 延时 15 分钟 |
| A 股通 | 实时 | 约 80-150 HKD/月 | 实时 Level 1 |

> 注意：具体费用以富途前台展示为准，不同账户层级可能有差异。OpenAPI 的行情权限与富途客户端共享，已在客户端订阅 LV2 的用户无需额外付费。

### 5.2 API 调用频率限制（核心限制表）

| 接口 | 限制 | 说明 |
|------|------|------|
| 下单(2202) | 15次/30s, 5次/1s | 最严格的限制，批量下单必注意 |
| 改单(2205) | 20次/30s, 5次/1s | 撤单+重挂频繁时易触发 |
| 解锁(2005) | 10次/30s | 每次下单前解锁是非常坏的习惯 |
| 历史K线(3103) | 10次/30s | 批量拉取时务必串行化 |
| 行情快照(3203) | L1=30, L2=20, L3=10 次/30s | 每次最多查 L1=400, L2=300, L3=200 只 |
| 资金持仓(2101/2102) | 10次/30s (仅refreshCache=True时) | 频繁查询建议缓存 |
| 其他查询接口 | 10次/30s | 多数查询接口统一限制 |

### 5.3 用户等级与对应权限

| 等级 | 条件 | 订阅额度 | 快照批量上限 | 历史KL上限(每次) |
|------|------|----------|-------------|-----------------|
| Level 1 | 实盘用户(联系富途) | 1000 | 400只 | 1000根 |
| Level 2 | 净资产 > 10,000 HKD | 300 | 300只 | 1000根 |
| Level 3 | 净资产 < 10,000 HKD | 100 | 200只 | 1000根 |

### 5.4 与现有量化系统的集成成本

| 项目 | 估算成本 |
|------|----------|
| 服务器 | 1台云服务器(2C4G) ~50-100 CNY/月 或 自用PC |
| 行情费 | 港股LV2 ~200-400 HKD/月(非必需，视策略需求) |
| 富途账户 | 开户免费，需满足最低入金要求 |
| 许可 | OpenD + SDK 免费，Apache 2.0 开源 |

---

## 附录：快速参考

### A. 多周期策略最小集成模板

```python
from futu import *
import threading
import time

class MultiCycleStrategy:
    """多周期策略系统 —— 富途 OpenAPI 集成示例"""

    def __init__(self, codes):
        self.codes = codes
        self.quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        self.trade_ctx = OpenHKTradeContext(host='127.0.0.1', port=11111)

        # 订阅所有需要的数据类型
        self.quote_ctx.subscribe(codes, [
            SubType.QUOTE,           # 实时报价
            SubType.K_DAY,           # 日 K 线
            SubType.K_5M,            # 5 分钟 K 线
            SubType.K_1M,            # 1 分钟 K 线
        ])

        # 解锁交易
        self.trade_ctx.unlock_trade('trade_password')

    def run_daily_check(self):
        """日线策略：每次运行一次"""
        for code in self.codes:
            ret, kdata = self.quote_ctx.get_history_kline(
                code, ktype=KLType.K_DAY, count=60
            )
            # === 策略逻辑处理 ===
            if self.should_buy(kdata):
                self.place_signal_order(code, TrdSide.BUY)

    def run_minute_loop(self):
        """分钟策略：持续运行"""
        while True:
            for code in self.codes:
                _, kdata = self.quote_ctx.get_history_kline(
                    code, ktype=KLType.K_5M, count=12
                )
                # === 策略逻辑处理 ===
                if self.should_sell(kdata):
                    self.place_signal_order(code, TrdSide.SELL)
            time.sleep(300)  # 5 分钟轮询

    def run(self):
        # 日线线程：每小时检查一次
        daily = threading.Thread(target=self.run_daily_check)
        daily.daemon = True
        daily.start()

        # 分钟线线程：主线程
        self.run_minute_loop()

    def place_signal_order(self, code, side):
        ret, data = self.trade_ctx.place_order(
            price=None, qty=100, code=code,
            trd_side=side, order_type=OrderType.MARKET,
            trd_env=TrdEnv.REAL
        )
        if ret == RET_OK:
            print(f"成交: {code} {side} order_id={data['order_id'][0]}")

    def should_buy(self, kdata): ...    # 买入信号
    def should_sell(self, kdata): ...   # 卖出信号
```

### B. Rate Limit 速查

所有查询接口统一遵守 10 次/30 秒。下单改单有二级限制（1 秒级 + 30 秒级）。建议所有批量操作前加 `time.sleep(max(0, 3 - elapsed))`。

### C. 参考资源

- SDK 包: `pip install moomoo-api` (最新 v10.7.6708)
- GitHub: https://github.com/MoomooOpen/py-moomoo-api
- 官方文档: https://openapi.futunn.com/futu-api-doc/
- OpenD 社区 Docker: `ostai/futuopend:latest` (https://hub.docker.com/r/ostai/futuopend)
- OpenD 配置文件: `OpenD.xml`（与可执行文件同目录）
