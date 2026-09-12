# Lark / 飞书配置与真实接力验收

适用锁定的 `lark-channel-sdk==1.4.0`，资料核对：2026-09-12。
向导已做静态语法检查，尚未用真实租户走完；界面的中英文名称可能不同。

## 本机已完成配置

本机凭据、Lark 权限/事件/发布和 owner 配对均已完成，网关由 systemd 管理。
已验收普通消息、真实允许/停止、待审批时重启和桌面输入同步；无需重跑下方流程。
查看 [运行维护](OPERATIONS.md) 和 [本次验收](M1-PROGRESS.md)。

## 首次配置流程

依赖已安装，AGY 已保留登录，直接在可交互的 VM 终端运行：

```bash
cd /home/lileon/doc/unilark/repo
./scripts/setup-lark.sh ../spike/runtime/unilark.toml
```

App Secret 只在向导中隐藏输入，不作为聊天内容或命令参数。
默认 `~/.unilark/` 为 0700、`lark.env` 为 0600。中断后可重跑，已有值可按 Enter 保留，
已配对 owner 不会被替换。设置 `UNILARK_DATA_DIR` 可改数据目录，运行时也须指定同一 `--state`。

## 本人操作的五步

1. 选择国际版 Lark 或中国版飞书。控制台分别是
   <https://open.larksuite.com/app> 和 <https://open.feishu.cn/app>。
2. 创建本机专用企业自建应用并启用机器人。凭证页面复制 App ID、App Secret，
   在本机向导保存。应用名称可用 Unilark。
3. 开通 `im:message:send_as_bot`、`im:message:update` 及私聊接收所需的
   `im:message.p2p_msg:readonly`，按控制台处理依赖。
   当前使用普通消息卡片，无 CardKit 流式权限需求。
4. 事件与回调选择长连接，订阅 `im.message.receive_v1` 和 `card.action.trigger`。
   如果控制台要求已有连接，向导进入 `pair` 后切回浏览器保存。
   创建/发布版本，可用范围包含本人，按租户要求审批/安装；改权限后重新发布/安装。
   私聊机器人发送终端生成的 `/pair 配对码`，回本机核对租户、open_id、chat_id，
   粘贴完整本人 open_id 才保存身份。5 分钟过期后重跑。
5. 启动网关，完成下方清单。配置完成、连接建立、跨端就绪分别核验。

配对连接不会执行 Agent 输入、发卡片或接受审批。同一应用不要同时运行其他消费者；
`run` 运行时不要启动 `pair` 或 `doctor --connect-lark`。

## 启动

```bash
cd /home/lileon/doc/unilark/repo
.venv/bin/unilark --config ../spike/runtime/unilark.toml run
```

这是未托管环境的前台运行方式，Ctrl-C 退出保留 AGY 和全部会话。
本机已有 `unilark.service`，不要同时再启动前台消费者。

## 真实手机/桌面验收

本次通过普通收发、桌面来源显示、桌面输入同步、真实允许/停止、重启恢复。
下表保留完整验收清单；跨会话引用、忙时排队/steer、手机接续桌面上下文、
真实拒绝/过期与异用户/群聊等仍需补充真实 Lark 证据，不能仅凭离线测试勾选。

| 操作 | 预期 |
|---|---|
| `/new 测试`，再发“不要调用工具，只回复 UNILARK-READY” | 新原生会话，输入只出现一次，手机收到实际回答 |
| AGY 桌面打开卡片所示原生会话 | 原正文不变，来源显示 Lark · Unilark |
| 桌面继续输入不同测试语句 | 手机收到原会话的输入/回答，不回灌 |
| 手机接着要求引用上一轮内容 | 同一上下文，无 clone 或新会话 |
| 新建第二会话并切换，再引用第一会话卡片 | 引用固定投向第一会话，普通新消息投向第二会话 |
| 长任务运行时发送第二任务 | 第二任务本地排队；明确 `/steer` 才插入 |
| 点停止按钮 | 先暂停；AGY 确认空闲后显示停止确认；`/continue` 后继续队列 |
| 发起需要审批的无害临时文件操作 | 脱敏命令、允许/拒绝对应原生状态，重复/旧按钮不执行 |
| 5 分钟不处理审批 | 按钮过期；核对同一请求后提交拒绝，不授权其他动作 |
| 重启网关，继续旧会话 | 绑定与队列恢复；已接受输入不重发，UNKNOWN 不盲目再发 |
| 不同用户私聊/点卡片，或在群中发消息 | 不启动任务、不执行控制 |

断网、429 和发送超时等故障也要在真实 Lark 验证；离线覆盖不能勾掉真机项。

## 诊断

```bash
.venv/bin/unilark --config ../spike/runtime/unilark.toml doctor
# 先退出网关，再做不发送消息的连接探测：
.venv/bin/unilark --config ../spike/runtime/unilark.toml doctor --connect-lark
```

- `credentials=missing`：运行向导保存凭据，不要发送 Secret 到聊天。
- `owner=not_paired`：重新运行配对，本机确认才生效。
- 能连接但收不到私聊：检查事件订阅、发布/安装、本人可用范围与消息权限。
- 收到消息但没有卡片：检查发送/更新权限，用 `/status` 或 doctor 查看 `delivery`。
- `UNKNOWN`：保留记录。请求可能已送达，不要删除账本强制重发。
- `RETRY`：显式限流或已有 message ID 的更新，退避后重试；超过上限转 `BLOCKED`。
- AGY 不匹配：检查路径、登录和 bundle，不要改哈希绕过校验。
- 配对超时：重跑即可，旧码不能再用，未确认候选不会成为 owner。

doctor 用 `service` 报告实时健康、`acceptance` 展示独立保存的真实验收记录。
当前服务健康且没有待确认操作或阻塞投递时退出码为 0；停机、心跳过期或降级为 2。
它尚不自动查询全部租户权限或手机可达性，`lark.permissions` 的未验证值不抹掉
已经记录的真实收发/控制证据；两个端点连接成功也不会自动产生验收记录。

## 来源

- [SDK Quickstart](https://github.com/larksuite/channel-sdk-python/blob/main/docs/quickstart.md)
- [SDK Reference](https://github.com/larksuite/channel-sdk-python/blob/main/docs/reference.md)
- 锁定安装包的事件模型、`channel/channel.py` 和安全队列；离线契约测试在 `tests/test_lark_channel.py`。
