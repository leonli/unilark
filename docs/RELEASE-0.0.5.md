# Unilark 0.0.5 · 独立会话群

2026-09-13，实验版；用户确认发布后已部署 0.0.5，生产数据库 schema 3，健康检查通过。

每个会话对应一个仅 owner 与机器人的私有群，直接在 Lark 聊天列表切换任务。机器人私聊提供新建、打开与设置入口。
新建表单包含标题、最近项目及可选首条任务；群核验成功才执行首条任务。`/new 标题` 可在私聊或原群新建并返回入口。
已有会话可建立入口，等待空闲后保存历史基线，不复制原生会话或重放旧 DM 历史。

群内输入/审批/停止固定原会话，跨群引用/卡片拒绝。群成员或权限异常暂停输入输出，在控制 DM 提示。
普通工具活动合并为状态卡，Markdown/Mermaid 沿用 0.0.4。执行并发仍为 1，未实现原生斜杠补全。

## 验证与边界

135 本地用例覆盖路由、项目表单、重启/去重、成员异常、建群不确定性、投递阻塞及兼容迁移。
真实 Lark 已接受群模式和旧模式的会话列表/命令/表单（2 项）；真实 AGY 群目标回复及重启去重（1 项）通过，投递端为本地 Recorder。
Ruff、格式、mypy 54 源文件和 pip check 通过。详细证据范围见 E2E-COVERAGE.md。

真实群 API：创建私有群、机器人群主、锁定邀请/编辑/分享以及唯一标记查找成功。
首次成员读取被 99991672 拒绝；用户发布后，两项权限均已通过实际 API 复核，验证群成员和群权限均通过。
新增 `test_real_private_group_members_markdown_mermaid_and_update` 实际验证成员、标记查找、群内富文本和图片发送、更新同一消息、回读群归属；临时测试卡均已撤回。
真实不 @ 消息入站、菜单点击、两个群之间切换及手机视觉仍待客户端操作反馈，不把 API 成功当作手机验收通过。

探针空验证群因缺少群删除权限未清理，未发送任何任务内容；其 request/chat 标记只保存在 repo 外的 0600 证据文件。
后续验收可复用该群；产品本身不需要群删除权限。

## 升级

schema 3 支持旧 1/2 数据迁移，旧 DM 消息 ID、owner 和原生绑定保持。0.0.4 及更早版本拒绝 schema 3，不能直接回退。
不修改旧版兼容声明、不用旧备份覆盖新数据；迁移后若需修复应使用支持 schema 3 的程序。
新版本经离线包与候选副本验证，已在 Lark 发布后按既有 safe-point 升级流程激活。
无需更换凭据或重新配对，也不修改 AGY、登录、web-xia 或主机配置。

## 构建后的候选验证

135 项本地完整回归通过。离线包 42 个 manifest 文件全部校验，展开 wheel 后按当前 App Secret 精确扫描无命中。
包路径：`/home/lileon/doc/unilark/releases/0.0.5/unilark-0.0.5-linux-x86_64`。
归档 SHA-256：`1bf64aa0bca27e44ccc6fd8588ab69f9e09565668f28c535580ed7e0b2eb0e23`。

隔离安装到 `spike/ue5-candidate/releases/0.0.5`，从 `/tmp` 使用安装后的解释器验证迁移及 Mermaid PNG，未依赖源码目录。
生产库副本迁移为 schema 3，owner、bindings、session_meta、selection、operations、cards 逐行相同。
候选阶段快照保留 3 个绑定和 44 个已知消息 ID；当时生产为 schema 2、0.0.4。
候选证据位于 repo 外 `spike/evidence/ue5/`（0700）。这段是构建后的补充记录，不改变不可变发行包。

## 生产部署结果

稳定 CLI 的 `upgrade --system` 成功，从 0.0.4 切换至 0.0.5；只重启网关。
备份：`~/.unilark/backups/before-0.0.5-1789287703115215659.db`。未恢复或覆盖生产数据。
升级前的最新状态为 4 个绑定、49 个已知消息 ID，均已保留；owner、session_meta、selection、operations 同样逐项核对通过。
`doctor --check-api` 返回 0，running/connected，生产 schema 3。记录：`spike/evidence/ue5/deploy-health.json`、`deploy-preservation.json`。

用户已确认菜单配置发布，菜单处理器随唯一 WebSocket 网关上线。已请用户实际点新建表单并发送群内免 @ 消息，以补真实入站证据。
API 查询的 callback_info 仅列卡片回调，不能据此判断菜单事件是否订阅；不以此误报缺少菜单。
