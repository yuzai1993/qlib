# QMT 内置桥接策略部署说明（Windows）

对应设计：`docs/superpowers/specs/2026-07-11-qmt-live-signal-bridge-design.md`  
Mac 侧部署与日常运维：`live_trading/README.md` 第二节（建议先完成本文件的 Windows 侧，再做 Mac 侧）

## 1. 目录准备

在 Windows 上创建共享目录（与 `qmt_signal_bridge.py` 中 `BRIDGE_ROOT` 一致，默认 `D:\qmt_bridge`）：

```
D:\qmt_bridge\
  inbox\        # 研究机写入信号（SMB 共享此目录给 Mac）
  processing\
  outbound\
  archive\
  state\         # processed_batches、当日 LIVE_OK、执行中 active_*.json
  logs\
```

策略首次运行会自动补齐缺失子目录。将整个 `D:\qmt_bridge` 设为 SMB 共享（步骤见下节），Mac 挂载后路径写入
`live_trading/configs/csi300_topk10_live.yaml` 的 `live.bridge_root`。

## 1.5 设置 SMB 共享（Windows 侧）

### 第一步：建议先建一个专用账号（更安全，可选但推荐）

不想把 Windows 登录密码给 Mac 用，就建一个只用于共享的本地账号：

1. `Win+R` → `netplwiz` →「添加」→ 选「不使用 Microsoft 账户登录」→ 本地帐户
2. 用户名如 `qmtshare`，设置密码（Mac 挂载时用它）
3. 该账号不需要管理员权限

### 第二步：共享文件夹

1. 右键 `D:\qmt_bridge` →「属性」→「共享」选项卡 →「高级共享」
2. 勾选「共享此文件夹」，共享名保持 `qmt_bridge`
3. 点「权限」→ 移除 `Everyone`，添加 `qmtshare`（或你的登录账号），勾选「完全控制」
   —— Mac 需要**写** inbox、**读** outbound，必须给写权限
4. 回到「安全」选项卡确认该账号对文件夹也有「修改」权限（NTFS 权限与共享权限取交集）

### 第三步：网络与防火墙

1. 设置 → 网络和 Internet → 当前网络 → 网络配置文件设为「**专用**」（公用网络默认禁共享）
2. 控制面板 →「网络和共享中心」→「高级共享设置」→ 专用网络下：
   - 启用「网络发现」
   - 启用「文件和打印机共享」
3. 防火墙一般会自动放行「文件和打印机共享(SMB-In, TCP 445)」；如被安全软件拦截需手动放行

### 第四步：拿到 IP 并固定

```bat
ipconfig | findstr IPv4
```

记下局域网 IP（如 `192.168.1.100`）。**建议在路由器上给这台 Windows 绑定静态 DHCP**，
否则 IP 变化后 Mac 挂载会失效、晚间发布信号会写不进去。

### 第五步：在 Windows 本机自测

```bat
net share            REM 应能看到 qmt_bridge
```

然后在 Mac 上验证（见 `live_trading/README.md` §2.1）：

```bash
# Finder ⌘K → smb://192.168.1.100/qmt_bridge，用 qmtshare 登录
ls /Volumes/qmt_bridge
touch /Volumes/qmt_bridge/inbox/_t && rm /Volumes/qmt_bridge/inbox/_t
```

常见失败：

| 现象 | 原因 |
|------|------|
| Mac 连不上 445 端口 | 网络配置文件是「公用」/ 防火墙拦截 / 不在同一网段 |
| 能连上但要密码反复失败 | 用了 Microsoft 账户在线密码；改用本地账号 `qmtshare` |
| 能读不能写 | 共享权限或 NTFS 安全权限少了「写入/修改」 |
| 第二天挂载失效 | Windows IP 变了（去路由器固定）或 Windows 睡眠断网（电源设置改为不睡眠） |

> 注意：QMT 实盘机白天不能睡眠、不能自动更新重启。电源计划设「高性能」，
> Windows 更新设置活动时间避开交易时段。

## 2. 导入策略

1. 打开大 QMT →「模型交易」/ 策略编辑器 → 新建 Python 策略
2. 将 `qmt_signal_bridge.py` 全文粘贴（或本地导入）；文件头 `#coding:gbk` 必须保留
3. 顶部配置项按需修改：
   - `BRIDGE_ROOT`：共享目录
   - `ACCOUNT_ID`：留空则使用信号文件 header 中的账号；填写则强制覆盖
4. 编译通过后关闭编辑器

## 3. 挂载运行

1. 模型交易 → 新建策略交易，选择本策略
2. 主图代码任选（如 `000001.SZ`），周期 `1m`（实盘按 tick 驱动，周期不影响本策略）
3. 账号选目标柜台账号（极速柜台需资金已划入）
4. 运行模式先选「**模拟**」

## 4. 模拟验收（上线前必做）

1. 研究机发布一个 `mode=SIMULATE` 批次：

```bash
python live_trading/scripts/run_publish_signals.py \
  --config csi300_topk10_live --trade-date <今天> --mode SIMULATE
```

2. 观察 QMT 策略输出日志（`[qlib_bridge]` 前缀）：应看到 claimed batch
   - 批次执行期间信号保留在 `processing`；完成后才移入 `archive`
   - QMT/策略重启后会从 `processing` 和 `state/active_*.json` 恢复，已标记提交的订单不会重提
3. 检查 `outbound\fills_*.jsonl`：每单一条 `SKIPPED simulated`，且有 `.done`
4. 研究机导入：`python live_trading/scripts/run_import_fills.py --config csi300_topk10_live`
5. 验证幂等：把 archive 里的信号文件复制回 inbox → 应整批 `SKIPPED duplicate`
6. 验证过期：发布 `--trade-date` 为昨天的批次 → 应整批 `SKIPPED expired`

## 5. 切换实盘（双开关）

真实下单需要**同时**满足：

1. 信号文件 `mode=LIVE`（研究机 `--mode LIVE` + 环境变量 `LIVE_TRADING_CONFIRM=YES`）
2. Windows 当日开关文件存在：`D:\qmt_bridge\state\LIVE_OK_2026-07-14`（每天人工创建，内容任意）

人工核对项（程序不检测）：策略交易界面的运行模式已切到「实盘」。

首次实盘建议：手工构造只含 1 只股票 100 股的批次，确认委托、成交、回执、导入全链路后再放开。

进入买入阶段时，策略只读取一次可用资金，并逐单预占委托金额、最低佣金和过户费；这用于隔离
QMT 本地资金缓存的刷新延迟。预算不足时按整手缩单，不足一手则跳过。

## 6. 日常排障

| 现象 | 排查 |
|------|------|
| 策略无输出 | 是否有行情 tick（非交易时间 handlebar 不触发）；`is_last_bar` |
| 批次不消费 | inbox 是否有 `.done`；`trade_date` 是否为当日；state 里是否已 processed |
| 重启后批次未恢复 | `processing` 中信号对是否完整；`state/active_<batch>.json` 是否可读 |
| 下单无委托 | LIVE_OK 文件是否存在；QMT 界面消息栏被拒原因；账号资金是否在所选柜台 |
| 回执缺失 | 14:55 前策略必须在运行且有 tick；查 `XtClient_FormulaOutput_*.log` |
| 中文乱码 | 策略文件必须 GBK；本文件内策略源码为纯 ASCII 可避免 |

日志位置：`{QMT安装目录}\userdata\log\XtClient_FormulaOutput_*.log`
