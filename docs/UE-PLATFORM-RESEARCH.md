# Rich UE：Lark 平台能力核查

核查时间：2026-09-13。范围：公开官方文档、官方 Channel SDK 源码、当前 Unilark adapter。未操作开发者后台，未发送 Lark 消息，未改变生产配置。本文为提案依据，不代表完成了真实客户端验收。

## 结论

1. **会话列表、当前会话标记、切换按钮、新建会话表单、任务控制卡均有官方卡片组件支撑。**主要缺口在 Unilark 的投影、动作模型及调度层。
2. **不能承诺在 Lark 原生聊天输入框里输入 `/` 即自动弹出本应用的命令列表。**已检查官方机器人说明、机器人 API 索引、开发指南及 SDK，未找到第三方应用注册原生 slash autocomplete、监听未发送输入内容或注入补全列表的公开接口。这是“未核实到可用接口”，不是断言所有版本或企业定制客户端永远不支持。
3. 官方明确支持的入口是**机器人自定义菜单**。菜单打开会话面板、任务面板或新建卡，可以免记命令。**发送 `/` 后返回命令卡属于发送后的交互，不是输入时自动补全**，文案及验收必须区分。
4. 若输入时 `/` 自动补全是必须达到的要求，可在由 Unilark 控制输入框的网页应用内实现，并从 Lark 菜单打开。它增加网页承载面，不应被描述成原生聊天框增强。是否采用应由整体提案决定。
5. Lark 与飞书的公开文档有差异。飞书文档已列悬浮菜单，Lark 对应菜单文档仍只明确 3 个一级、每级 5 个二级菜单的旧样式。首版设计采用两端都能保守承载的 3 个入口，悬浮常驻样式须以用户实际租户和客户端验证为准。

## 能力与限制

| 需求 | 已核实的官方能力 | 对提案的约束 |
| --- | --- | --- |
| 命令发现 | 机器人自定义菜单；菜单点击事件；链接跳转 [R1][R2] | 可设计“会话 / 新建 / 任务”三个固定入口；不能把菜单等同于 `/` 补全 |
| 可点击会话列表 | `button`、`select_static`、`column_set`、`overflow` [R3][R4] | 少量会话按行显示“当前 / 切换 / 详情”；大量会话分页，避免手机上堆积按钮 |
| 新建会话 | `form` 容器、`input`、选择组件，一次提交多个字段 [R3][R5] | 工作区、名称和首条任务可以组成短表单；无需让用户填写原生 UUID |
| 多问题回答 | 多选组件、输入框和表单 [R3][R5][R6] | 可替代“每行一个答案”的隐式协议；问题及选项身份仍由后端验证 |
| 交互即时反馈 | 回调支持 Toast、更新卡片或维持内容；应在 3 秒内响应 [R6] | 先确认接收，再异步执行；无需等待 AGY 长操作结束才回复回调 |
| 动态状态卡 | 按 message ID 全量更新；CardKit 局部/流式更新 [R7] | 使用可更新的最新面板；旧卡要有过期或刷新语义 |
| 长期面板 | 官方更新卡片时限为发送后 14 天 [R7] | 不能把一张消息视为永久可改的应用主页；过期时生成新面板 |
| JSON 2.0 | Lark 7.20 及以上；旧版显示升级提示 [R3] | 不应在未验证用户客户端前无条件迁移全部卡片；可先保留 JSON 1.0 承载基本按钮 |
| 表单输入 | JSON 1.0 输入框要求 Lark 6.8+；有多行输入，自动高度仅 PC 有效 [R8] | 手机不能依赖自动扩高；短表单、明确的提交按钮优先 |
| 跟随气泡 | 飞书文档支持最近一条机器人单聊消息后 600 秒内添加 1–3 个气泡 [R9] | 点击会变为用户消息，新消息到达后消失；不适合作为永久会话导航，Lark 对应页本次未取到正文 |

## 菜单配置与事件边界

Lark 官方菜单说明要求在开发者后台配置并发布应用版本，发布后可能延迟约 5 分钟，支持客户端版本为 5.27+；公开 Lark 文档明确最多 3 个一级菜单，每个最多 5 个二级菜单。[R1]

飞书同一能力的较新文档区分两种样式：[R2]

- 可切换菜单：与输入框互相切换；5.27+；3 个主菜单 × 5 个子菜单。
- 悬浮菜单：位于输入框上方；7.22+；5 个主菜单 × 10 个子菜单。
- 仅单聊。菜单响应动作可为打开链接、发送菜单文字、推送事件。其中“发送文字消息”也要求 7.22+。

菜单点击事件为 `application.bot.menu_v6`。包含 `header.event_id/app_id/tenant_key`、`event.operator.operator_id.open_id` 及 `event.event_key`。**事件体没有 `chat_id`**；不能直接复用依赖聊天 ID 的消息/卡片校验，必须通过已绑定 owner 的会话关系完成路由。菜单事件本身不要求额外权限；读取用户名称、user ID 才涉及附加权限，当前产品可继续仅使用 open ID。[R10]

菜单事件页的摘要表仍写 Webhook，但同一官方页面附有 Python `lark.ws.Client` 及 `register_p2_application_bot_menu_v6` 长连接示例。可将长连接作为实施方向，但必须做 SDK/租户端到端验证，不能仅凭摘要表判断必须新增公网 webhook。[R10]

当前 `LarkChannel.raw()` 只收集消息 envelope，未处理菜单；当前 Channel SDK 公开事件名列表没有专用菜单事件。可评估原始事件入口，先用记录化事件做 owner 校验测试，再验真实菜单点击，不能假定 raw 管道必然收到任意未注册事件。[R11][R12]

## 卡片动作与当前代码的差距

当前 `src/unilark/projection/cards.py` 只生成 JSON 1.0 的标题、Markdown 正文和按钮组，适合小卡。当前 `src/unilark/adapters/lark/channel.py:action()` 只取 `event.action.value` 中的 `token` 和 `decision`，并已有应用、租户、操作者、聊天身份校验。[R12]

官方回调明确区分：[R6]

| 字段 | 用途 |
| --- | --- |
| `action.value` | 开发者绑定的上下文，例如动作 token |
| `action.option` | 表单外的单选值 |
| `action.options` | 表单外的多选值 |
| `action.input_value` | 表单外输入框提交文本 |
| `action.form_value` | 表单内按组件 name 聚合的数据 |
| `context.open_message_id/open_chat_id` | 卡片所在消息和聊天 |

因此把 UI 换成下拉框后，仅扩展 JSON 不够；adapter 必须传入真实选择值，Hub 验证这些值属于该卡片和会话允许的集合。不要把任意 `option` 当成新的可信 session ID。

官方 SDK 当前源码 `CallBackAction` 已声明上述字段。[R13] Lark 卡片文档里“SDK 暂不支持新版回调”的提示与当前 SDK 源码、当前 Unilark 已运行的卡片回调冲突，应视为文档陈旧提示，不据此替换现有 SDK。

推荐在实施时保留以下语义（产品建议，不是平台自动提供）：

- 面板刷新、分页、选择当前会话属于导航动作；停止、取消、回答、授权必须固定绑定到卡片原会话/原操作。
- 使用固定动作身份及 event ID 去重。切换当前会话不能让旧任务按钮作用于新会话。
- 表单内每个交互组件都有唯一 `name`；提交按钮设置 `form_action_type=submit`；单选选项 `value` 唯一。[R4][R5]
- 回调先快速确认，后端事务提交后更新卡片；失败时明确显示“未执行”或“状态待确认”。未知结果仍遵守不自动重发原则。
- Lark 回调中的平台更新 token 为 30 分钟、最多 2 次，与产品自定义动作 token 不是同一种 token。[R6] 持续面板更新优先沿用 message ID 更新方式。[R7]
- 当前已知消息 ID 更新、未知发送结果处理和 WebSocket 生命周期代码与新 UE 无直接矛盾，保持既有可靠性边界。

## 输入补全核查的范围和未决项

本次读取了 Lark/飞书官方 `llms.txt` 提供的机器人及开发指南索引、机器人概述、菜单完整文档，以及官方 Channel SDK reference 和事件模型。[R1][R2][R11][R14] 用 slash、autocomplete、补全、指令、聊天框、输入框等词查找；索引里的聊天框扩展是“+”菜单、消息快捷操作或企业原生集成，没有找到普通应用机器人注册 `/` 命令补全的文档。

该结果只足以支持“当前可实施计划不能依赖此接口”。如果后续用户提供实际客户端的 `/` 菜单示例，需确认其属于 Lark 通用机器人、Aily、文档快捷命令、企业私有集成或第三方网页，不能混为同一能力。

## 实施验收建议

1. 真实手机中点菜单能打开会话卡，包含当前会话、状态与按钮；菜单不可用时发送 `/list` 仍能得到同一卡。
2. 从卡片切换会话，随后发送文本只进入新选择的会话；旧任务继续显示自身标题且能独立停止。
3. 从选择器或表单提交有效数据，SDK → adapter → Hub 的值完整保存；非法、失效或跨会话值不执行。
4. 点击后 3 秒内有回调响应；慢原生操作不导致用户连续点击重试；重复回调只执行一次。
5. 在用户实际移动客户端验证布局、长标题、多行表单、选择列表与禁用状态。JSON 结构测试不能替代这些检查。
6. 原生聊天框的 `/` 输入自动补全单列为未实现/待平台确认；若采用网页，则在网页内验收，不能算成原生输入框通过。

## 来源

以下 URL 均在 2026-09-13 读取官方正文。`.md` 是文档站提供的纯 Markdown 入口。

- **R1** Lark《机器人菜单使用说明》：https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/bot-v3/bot-customized-menu.md
- **R2** 飞书《机器人菜单使用指南》：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/bot-v3/bot-customized-menu.md
- **R3** Lark《卡片 JSON 2.0 版本组件概述》：https://open.larksuite.com/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/component-json-v2-overview.md
- **R4** Lark《下拉选择-单选组件》：https://open.larksuite.com/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/interactive-components/single-select-dropdown-menu.md
- **R5** 飞书《表单容器》：https://open.feishu.cn/document/feishu-cards/card-json-v2-components/containers/form-container.md
- **R6** Lark《卡片回传交互》：https://open.larksuite.com/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication.md ；飞书对应新版：https://open.feishu.cn/document/feishu-cards/card-callback-communication.md
- **R7** Lark《更新卡片》：https://open.larksuite.com/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/update-feishu-card.md
- **R8** Lark《输入框组件》JSON 1.0：https://open.larksuite.com/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components/interactive-components/input.md
- **R9** 飞书《添加跟随气泡》：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/push_follow_up.md
- **R10** 飞书《机器人自定义菜单事件》：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/application-v6/bot/events/menu.md
- **R11** 官方 Channel SDK reference：https://github.com/larksuite/channel-sdk-python/blob/main/docs/reference.md ；公开事件名：https://github.com/larksuite/channel-sdk-python/blob/main/lark_channel/channel/events.py
- **R12** 当前仓库：[Lark adapter](../src/unilark/adapters/lark/channel.py)、[卡片渲染](../src/unilark/projection/cards.py)
- **R13** 官方 Channel SDK 回调模型：https://github.com/larksuite/channel-sdk-python/blob/main/lark_channel/event/callback/model/p2_card_action_trigger.py
- **R14** 官方索引：https://open.larksuite.com/llms-docs/zh-CN/llms-developer-guides.txt 、https://open.feishu.cn/llms-docs/zh-CN/llms-bot.txt 、https://open.feishu.cn/llms-docs/zh-CN/llms-developer-guides.txt ；飞书机器人概述：https://open.feishu.cn/document/client-docs/bot-v3/bot-overview.md
