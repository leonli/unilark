# 用户手册与 E2E 测试对照

日期：2026-09-13。本文主体保留 0.0.2 的历史审计（代码基线 `cb4524d`）；
当前[用户手册](USER-GUIDE.md) 已更新为 0.0.3，新覆盖和仍有的缺口以下面的增量表为准。

## 0.0.3 交互改进增量

新增 9 项本地用户流程检查，以及 1 项 `real_lark` 真实 API 用例。当前共 106 项：
97 本地、8 `real_agy`、1 `real_lark`。具体执行结果见 [0.0.3 说明](RELEASE-0.0.3.md)。

| 当前手册 | 新覆盖 | 证据范围 |
|---|---|---|
| U04/U05 当前标记、切换、引用 | `test_list_switch_preserves_original_queue_and_panel_has_no_quote_target` | 实际卡片按钮→Hub→SQLite；总览不误充引用目标 |
| U02/U12 新建表单 | `test_commands_form_captures_workspace_and_is_one_shot_after_restart`；`test_real_sdk_form_reaches_hub_and_creates_one_named_session` | 固定目录、重复提交、接收后重启；官方 SDK 表单字段解析到 Hub，回调为受控事件 |
| U08 撤销队列 | `test_cancel_button_keeps_original_session_after_switch` | 切换后撤销原会话的未提交项 |
| U09 停止/继续 | `test_stop_button_revalidates_native_turn_then_stops_original_session` | 旧运行代次拒绝、正确会话停止；本地 Runtime |
| U13 归档/恢复 | `test_archive_resume_and_continue_buttons_round_trip` | 卡片入口完整往返，恢复后仍暂停，显式继续 |
| U04/U06 分页和等待说明 | `test_paging_filters_stability_and_queued_reason` | 分页、归档筛选、稳定状态不重复更新、全局串行等待说明 |
| U23 身份/表单边界 | `test_wrong_identity_message_value_and_expired_actions_do_not_switch`；`test_secret_in_form_is_never_saved_and_missing_title_can_be_corrected` | 错身份/错消息/值篡改/过期拒绝、凭据不落盘、纠正表单后可提交 |
| U02/U04/U14 真正 Lark 卡片 | `test_real_lark_accepts_session_commands_and_new_form` | 真实发送与 GET 回读会话面板/命令卡/新建表单，随后仅清理测试卡；没有真实用户点击 |

本地流程见 [test_panels.py](../tests/test_panels.py)、[SDK 回调测试](../tests/test_lark_channel.py)，
真实 API 用例见 [test_real_lark_cards.py](../tests/e2e/test_real_lark_cards.py)。
手机真实点击与视觉验收、原生 `/` 输入联想、并行执行仍未据此通过。

## 0.0.2 历史审计（以下数量和缺口为当时状态）

**仍有后续开发工作。优先补齐可重复的用户流程测试，以及手册暴露的使用障碍。** 当前测试不能证明手册中的所有步骤均已在真实 Lark 上跑通。

本次逐项阅读了现有用例及其断言，重新收集测试清单，并只读核对现有实测证据与当前服务。没有重新执行发消息、点击审批、断网、升级或卸载等真实操作。2026-09-12 的通过记录与本次静态对照分开记录。

## 测试数量的准确含义

| 类别 | 当前数量/状态 | 能说明什么 |
|---|---|---|
| 全部 pytest 参数化用例 | 96 项 | 含下面 88 项本地、8 项 `real_agy` |
| 本地自动测试 | 88 项，9 月 12 日通过 | 本地逻辑、SQLite、SDK 事件/线程及受控替身；不等于真实手机操作 |
| `tests/e2e/` | 4 个文件、8 项 `real_agy`，9 月 12 日通过 | 真实 AGY API；其中 1 项为 Hub + AGY + 本地卡片记录器 |
| `real_lark` pytest | **0 项** | 标记虽已声明，但没有可收集的真实 Lark 用例 |
| 仓库外实际操作 | 有 H1–H7 证据，见下表 | 已发生的本机验收；尚未统一成可重复、可移植的测试套件 |
| 远程 CI | 只有配置，尚未连接远程仓库运行 | CI 配置排除 `real_agy`、`real_lark`，不能声称远程 CI 已验收产品 |

本次 `pytest --collect-only -q -m real_agy` 收集到 8/96；`-m real_lark` 收集到 0/96，退出码 5 表示没有用例。这里没有把“收集成功”计为本次执行通过，也不以 88/96 计算用户流程覆盖率。

## 8 项现有真机用例实际测到了什么

以下 E 编号仅用于本报告映射。参数化分支分别计数。

| 编号 | 文件与函数 | 核心断言 | 未经过的用户环节 |
|---|---|---|---|
| E1 | [test_real_agy.py](../tests/e2e/test_real_agy.py) · `test_native_label_body_tag_and_reconciliation` | 原生正文/来源、tag 一次；丢失本地确认后据 tag 对账；真实回复包含标记 | 没有 Lark 入站、卡片投递或真正杀进程 |
| E2 | [test_real_agy_controls.py](../tests/e2e/test_real_agy_controls.py) · `test_real_permission_deny_allow_and_stale_fingerprint[False]` | 直接调用原生拒绝；测试文件未创建；旧指纹/重复决议拒绝 | 没有真实 Lark 拒绝按钮与回调 |
| E3 | 同上 · `test_real_permission_deny_allow_and_stale_fingerprint[True]` | 直接调用原生允许；测试文件创建；旧指纹/重复决议拒绝 | 没有真实 Lark 允许按钮与回调 |
| E4 | 同上 · `test_real_stop_running_command` | 审批后观察非空闲；直接停止后确认原生空闲 | 没经过手机 `/stop`、按钮或暂停队列后的 `/continue` |
| E5 | [test_real_agy_questions.py](../tests/e2e/test_real_agy_questions.py) · `test_real_question_workspace_and_stale_decision[False]` | 在指定目录创建会话、原生单选回答、重复回答拒绝、回复含目录及选项 | 没有问题卡片真实渲染、点击、引用文本、多选/多题 |
| E6 | 同上 · `test_real_question_workspace_and_stale_decision[True]` | 原生取消问题、重复回答拒绝、最终空闲 | 没有 Lark“取消问题”按钮或实际等待到期 |
| E7 | 同上 · `test_real_distinct_workspaces_with_same_name` | 不同路径同名目录各有独立项目；重复选择同路径返回同项目 | 没经过手机 `/cwd` 和新旧会话切换 |
| E8 | [test_real_hub_agy.py](../tests/e2e/test_real_hub_agy.py) · `test_real_hub_projects_response_and_recovers_binding` | 向 Hub 构造 `/new` 和普通消息；AGY 回复投影到记录器；重开账本后重复事件不新增输入 | Lark 通道是本地 Recorder；Hub 在同一测试进程重建，非 systemd 重启 |

E1–E7 调用真实原生 API，E8 多覆盖 Hub/账本/投影。文件名里的 `e2e` 或 `real` 不扩大这些用例实际经过的链路。

## 已有实际操作证据

原始记录位于仓库外 `../spike/evidence/`，含真实身份/会话定位，不复制到本手册或通用发行物。这里记录可复核的文件名和边界。

| 编号 | 证据 | 已验证 / 限制 |
|---|---|---|
| H1 | `m1-real-lark-acceptance.json`；[M1 报告](M1-PROGRESS.md) | 两轮真实 Lark 输入/回复、允许与运行中停止、待审批重启；不包含所有命令、真实拒绝/到期或新问答流程 |
| H2 | `m1-desktop-ui-relay.json` | 真实桌面输入框发送并从 Lark 回读输入/回答；当前选择保留，无回灌；不是对全部多轮上下文语义的测试 |
| H3 | `m1-service-recovery.json` | 空闲网关 SIGKILL 后 systemd 自动恢复，卡片/绑定/输入/选择保持；不代表整机重启 |
| H4 | `m2-lark-outage.json` | 网关限网 11 分钟，原生离线完成，SDK 重连后 Lark 回读成功；不验证离线期间用户入站补发或真实 429 |
| H5 | `m2-crash-matrix.json` | 三处实际 SIGKILL，真实 AGY 接收一次；投递端是本地记录器 |
| H6 | `m3-install-lifecycle.json` | 干净目录安装→版本修改测试包升级→回退→卸载，新增数据保留；使用已有 AGY/凭据和复制状态，未运行该测试安装的系统服务 |
| H7 | `m4-installed-service.json`、`m4-installed-setup.json` | 正式安装包交接系统服务，22 张原卡片等保留，setup ready；对既有收发/桌面记录做正式验收回读；不是第二位用户从零完成全部 setup |

9 月 13 日本次只读 `doctor` 返回 0，版本 0.0.2、Lark connected、网关运行、未解决操作为 0。这证明检查时在线，不能补齐表中的操作测试缺口。

## 从手册逐项对照

“部分”指只覆盖用户流程中的某一层或部分步骤；“无”指上述 E1–E8 没有对应旅程断言，不表示代码未实现。L 编号对应后文的本地测试索引。

| 手册场景 | 现有真机自动测试 | 其他已有覆盖 | 缺少的验收 |
|---|---|---|---|
| [U01 快速上手](USER-GUIDE.md#u01) | E1/E8 部分 | H1；L1 | 实际 Lark 发命令、收回执、最终卡片回读固化为可重复流程 |
| [U02 新建会话](USER-GUIDE.md#u02) | E8 的 `/new` 成功路径 | L1；H1 | 真实卡片中的两个 ID、创建失败/超时、重复事件与准备中输入 |
| [U03 双端接力/attach](USER-GUIDE.md#u03) | E1 来源、E8 绑定部分 | H2；L1 | 手机 `/attach`、空历史/错误实例拒绝，以及含前文信息的双向多轮接续 |
| [U04 列表/切换](USER-GUIDE.md#u04) | 无 | L1/L2 验证部分选择/路由状态 | `/sessions`、`/list`、`/switch` 文本入口和真实展示；当前选择提示 |
| [U05 引用旧卡片](USER-GUIDE.md#u05) | 无 | L1 固定引用目标和未知引用拒绝 | 在第二会话选中时，真正引用第一会话的 Lark 卡片，并验证原生目标 |
| [U06 排队](USER-GUIDE.md#u06) | 无忙时用例 | L1/L2，含 100 组受控竞争 | 真实桌面忙时手机排队、跨会话阻塞、位置/回执变化；100 组真实双端竞争 |
| [U07 steer](USER-GUIDE.md#u07) | 无 | L1/L2，替身检查 steer 路径 | 实际手机补充到当前原生任务，区别于下一任务；当前已有副作用不回滚 |
| [U08 cancel](USER-GUIDE.md#u08) | 无 | 未发现专门 `/cancel` 用户路径用例 | 尚未提交成功撤销；已提交/未知/错误请求编号拒绝；原生未收到被撤销输入 |
| [U09 停止/继续](USER-GUIDE.md#u09) | E4 仅原生停止 | H1 真实停止按钮；L1/L2 暂停和 continue | 手机 `/stop` 与按钮各走一遍，排队 B 在确认停止后仍不启动，`/continue` 后仅启动一次 |
| [U10 审批](USER-GUIDE.md#u10) | E2/E3 仅原生批准/拒绝 | H1 真实允许；L1/L8 错卡/旧指纹/到期 | 真人拒绝、实际到期、错身份负向、命令副作用与原卡状态一致 |
| [U11 问答](USER-GUIDE.md#u11) | E5/E6 仅原生单选/取消 | L3 引用正向、按钮负向、解析/到期 | 真人单选、自由文本、多题/多选、取消/过期。现有 Hub 按钮用例主要测拒绝路径，缺当前有效按钮的正向调用断言 |
| [U12 工作目录](USER-GUIDE.md#u12) | E5–E7 原生目录部分 | L3 `/cwd` 与 `/new` 接收时固定目录 | Lark 改目录→新建→原生读写在预期目录；旧会话目录不变；无效路径反馈 |
| [U13 归档/恢复](USER-GUIDE.md#u13) | 无 | L2 同 binding 恢复、暂停、旧卡输入拒绝 | 真实历史恢复和暂停；忙碌/排队/未知/空会话边界；归档后当前选择变化 |
| [U14 帮助/身份/状态](USER-GUIDE.md#u14) | 无 | L2 受限能力负向，未覆盖这些正常命令 | 每个命令真实返回及错误提示；不把总览误认成当前会话状态 |
| [U15 断线/退出恢复](USER-GUIDE.md#u15) | E1/E8 仅对账/重开账本 | H3/H4/H5；L1/L2/L8 | 把私有脚本变成可重复恢复旅程；离线入站、AGY 重启/失联、睡眠/整机重启分别验收 |
| [U16 UNKNOWN/投递修复](USER-GUIDE.md#u16) | E1 仅 tag 确认路径 | L2 本机 input/card 裁决、L8 投递分类 | 从实际 CLI 裁决到服务重启、保持暂停、显式继续；未知新建绑定如何恢复；已知卡片更新重试正向流程 |
| [U17 安装/配对](USER-GUIDE.md#u17) | 无 | H1 既有本人配对、H6 包安装；L4/L7 | 全新数据/未配对账号从零走完，含错误凭据、过期窗口和中断后继续 |
| [U18 验收/后台交接](USER-GUIDE.md#u18) | 无 | H7；L5/L6 | 新状态完成真实验收再交接；重跑 setup 保留证据；前台正常但服务失败须返回未就绪 |
| [U19 服务管理](USER-GUIDE.md#u19) | 无 systemd 用例 | H3/H7；L6 对服务管理使用替身 | 安装包用户级/系统级 start/restart/stop/status、第二消费者、注销条件 |
| [U20 诊断/日志/导出](USER-GUIDE.md#u20) | 无 | H7 和本次只读 doctor；L5/L7；既有导出记录 | 从安装入口验证 sidecar/history/diagnostics/audit；多子系统故障同时报告、实际路径/权限错误和脱敏 |
| [U21 备份/升级/回退](USER-GUIDE.md#u21) | 无安装生命周期用例 | H6；L6/L7 | 隔离环境实际后台升级、启动失败、迁移失败、回退后保留升级期间新数据；不能只 mock 候选/服务 |
| [U22 卸载保留数据](USER-GUIDE.md#u22) | 无 | H6 正向手工生命周期；L7 部分归属检查 | 正式包驱动卸载后数据保留、其他安装/Agent 不受影响；不同 prefix/服务归属和失败路径 |
| [U23 使用范围/保护](USER-GUIDE.md#u23) | 无真实 Lark 身份负向 | L4/L7/L8；既有依赖/密钥扫描 | 受控第二身份、群/附件、真实过期回调，不泄露正文/凭据；独立沙箱仍不是本版承诺 |

所有 U01–U23 都有对应行，但没有任何一行据此宣称“完整用户流程已由真实 Lark pytest 自动覆盖”。H1/H2 等历史实测仍然有效，只是不能替代下一版本可重复执行的回归套件。

## 本地测试索引

这些测试在 88 项中计数，不另算端到端测试。下面只列对照表依赖的代表性断言。

| 编号 | 文件 | 代表用例 / 可证明的边界 |
|---|---|---|
| L1 | [test_gateway.py](../tests/test_gateway.py)、[test_ledger.py](../tests/test_ledger.py) | `test_duplicate_and_quoted_reply_keep_original_binding`、`test_busy_queue_steer_stop_and_continue`、`test_cards_stable_permissions_bound_and_expiry_denies_once`、`test_ambiguous_stop_only_observes_and_never_retries`；使用 Runtime/Channel 替身 |
| L2 | [test_reliability.py](../tests/test_reliability.py) | `test_one_hundred_busy_input_races_preserve_queue_and_fixed_routing`、`test_archive_rejects_quoted_input_and_resume_keeps_original_native`、`test_acknowledge_unknown_does_not_resend_or_claim_no_execution`、`test_unknown_new_card_cannot_be_retried_after_manual_dismissal`；100 组是一个测试内的循环 |
| L3 | [test_questions.py](../tests/test_questions.py) | `test_multiple_question_text_is_mapped_without_guessing`、`test_quoted_question_answer_is_not_a_new_task_and_is_once_only`、`test_question_button_is_bound_to_owner_message_and_original_request`、`test_question_expiry_cancels_original_question_without_granting_permission`、`test_workspace_is_pinned_when_new_session_is_received` |
| L4 | [test_onboarding.py](../tests/test_onboarding.py) | `test_remote_nonce_needs_local_confirmation_and_expires`、`test_credentials_are_data_and_require_owner_only_permissions`、`test_instance_lock_is_exclusive_and_releases`；不是交互 setup 的完整演练 |
| L5 | [test_health.py](../tests/test_health.py)、[test_acceptance.py](../tests/test_acceptance.py) | `test_doctor_separates_live_health_from_historical_acceptance`、`test_readonly_diagnostics_never_migrate_or_create_a_database`、`test_acceptance_requires_native_receipt_and_delivered_reply`、`test_idle_stop_is_not_running_stop_acceptance`；未通过实际 Lark 网络 |
| L6 | [test_lifecycle.py](../tests/test_lifecycle.py) | `test_candidate_migrates_only_new_copy_and_preserves_m1_owner`、`test_upgrade_then_rollback_preserves_data_accepted_after_upgrade`、`test_candidate_startup_failure_rolls_program_back_without_restoring_data`、`test_foreground_process_does_not_pass_service_handoff`；核心升级用例替换了 safe_point、prepare、子进程和服务调用 |
| L7 | [test_maintenance.py](../tests/test_maintenance.py)、[test_packaging.py](../tests/test_packaging.py) | `test_backup_includes_committed_wal_and_preserves_original`、`test_service_paths_cannot_inject_directives_or_expand_tokens`、`test_rotated_logs_remain_private_and_omit_sdk_payloads`、`test_bundle_checksums_and_path_traversal_are_rejected`、`test_cli_runs_as_module`；入口点检查不是完整构建/安装 E2E |
| L8 | [test_lark_channel.py](../tests/test_lark_channel.py)、[test_lark_startup.py](../tests/test_lark_startup.py) | `test_envelope_and_normalized_identity_are_both_required`、`test_real_sdk_card_envelope_keeps_tenant_and_origin`、`test_delivery_distinguishes_ambiguous_create_from_update`、`test_real_sdk_receiver_and_reconnect_cleanup_leave_no_pending_tasks`；SDK 是真的，事件/网络由测试构造，不能当真实 Lark 收发 |

## 手册暴露的使用障碍

这是后续开发候选，不以“未测试”直接断言实现错误；下列边界已经从当前实现核对，并在手册说明。

1. `/status` 与会话列表共用输出，没有明确标出当前选中项。建议加入当前会话标记、原生编号和明确的目标确认，减少误投任务。
2. 连接桌面会话需要用户取得原生 UUID，机器人不能展示全部未绑定会话。需要在真实可枚举范围内改善选择入口，并保留显式 ID 的兜底方式。
3. 空历史会话可归档，但 `/resume` 要求非空历史，无法按普通恢复流程继续。需统一归档/恢复规则或提供明确修复入口。
4. 新建/连接 UNKNOWN 被本机裁决为不可用后，`/resume` 只处理归档、`/attach` 拒绝非活动旧绑定。需要设计“重新核验原绑定”的显式流程，保留同一原生会话和不重发原则。
5. 长问题卡片会截断，多题回答依靠每题一行，单选超过 5 项使用引用文本。先验证真人操作是否清晰，再决定交互改进。

## 后续开发顺序

以下是根据手册重新收敛的开发计划，**本次交付的是手册和对照，下面新增用例及功能尚未实现**。

| 优先级 | 工作 | 完成标准 |
|---|---|---|
| P0-1 | 补命令入口和维护 CLI 的流程回归 | 覆盖 `/attach`、`/sessions`、`/list`、`/switch`、`/cancel`、`/help`、`/whoami`、`/capabilities`，以及问答有效按钮正向路径；包含目标错误、重复、过期和明确拒绝；先作为本地集成测试计数 |
| P0-2 | 固化真实 Lark 用户旅程 | 把新建/双向接力/引用路由/队列/steer/停止继续/审批/问答做成可重复的带证据流程；明确需要真人发送或点击的步骤，自动检查原生结果及 Lark 消息回读 |
| P0-3 | 安装包与维护的真实隔离测试 | 从构建出的包运行，不 mock 安装、候选进程或服务管理器；在独立环境完成首次配对前后的 setup、后台交接、失败升级/回退/卸载和数据保留 |
| P1-1 | 修复手册中的使用障碍 | 当前选中提示、空会话归档规则、不可用绑定重新核验；每项修改对应一个成功及拒绝/恢复回归 |
| P1-2 | 可靠性长期回归 | 将限网和 SIGKILL 私有脚本改为可重复、带清理/自动恢复的测试；补 100 组真实双端竞争、AGY 失联、入站补发观察；真实上游错误与受控注入分别报告 |
| P1-3 | 支持矩阵与发行 | 在独立机器实测重启/睡眠、用户服务条件及拟支持平台；新用户验收；许可证/可见性确定后再公开发行 |
| P2 / M5 | 新引擎、群聊、附件等扩展 | 在当前版本用户旅程稳定后另行排序，每个引擎独立通过共享会话和恢复门禁 |

P0-2 的首批新增流程应优先包含：

| 计划场景 | 必须观测的结果 |
|---|---|
| 真实引用路由 | 当前选中 B，引用 A 卡片发送；仅 A 的原生历史增加一次输入，B 与当前选择保持不变 |
| 队列 → 撤销/停止 → 继续 | A 忙时 B 排队；撤销未提交项成功；Stop 后待执行项仍暂停；Continue 后只提交一次 |
| 真实问答 | 单选点击、自由文本、多题/多选、取消和到期各独立用例；只解决原问题，不新增普通输入；旧按钮不能再次提交 |
| 审批中重启 | 服务重启前后绑定/卡片/指纹相同；真实点击仍对应原工具；拒绝/到期没有获准副作用 |
| 桌面与手机多轮 | 使用带唯一标记的前文，另一端继续时保留同一 native ID，并实际引用前文信息；分别验证正文、次数与目标 |

每次真实流程应保留：测试场景/版本、步骤及真人参与标记、运行前后绑定/选择/队列、原生输入次数、消息回读结果，以及清理/恢复结果。原始身份与凭据不写入通用仓库。
失败后先核对可能已发生的动作，不用重跑覆盖失败证据。没有实际运行的场景标为未验收，不能由本地替身或历史 pass 自动转绿。

## 复核方法

以下只收集用例，不会执行真实任务：

```bash
.venv/bin/pytest --collect-only -q -m real_agy
.venv/bin/pytest --collect-only -q -m real_lark
```

实际原生测试需显式指定实例，会创建会话、请求受控工具并处理审批；它仍不经过真实 Lark：

```bash
UNILARK_REAL_AGY_CONFIG=/private/path/agy.toml .venv/bin/pytest -q -m real_agy
```

目前不能给出一个已经存在的“完整真实 Lark pytest 命令”：P0-2 正是要补齐这一缺口。当前可用的 `acceptance verify` 是实际操作后的证据核验入口，不会自动驱动用户完成整段流程。
