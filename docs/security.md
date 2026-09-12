# 安全模型

对应 PRD 10.7、12.3、8.8。本文件在 Spike 期间随实测修订。

## 实验版边界（PRD 8.8）

- **认证归 Unilark**：Lark 侧白名单，谁能点审批/停止。
- **授权归 AGY**：工具能否执行由 AGY 自身审批弹窗决定，Unilark 只把弹窗镜像到
  Lark 并限定谁能点。依赖执行前钩子的条目标「待官方 API」。

## Spike 实测的本地接口（2026-09-12，已并入 PRD 10.7）

AGY 2.13.0 桌面端把自己的 UI 跑在 `https://127.0.0.1:<随机高端口>` 上，
UI 与本地 `language_server` 之间走 gRPC-web。该 RPC 面的唯一凭据是
`x-codeium-csrf-token`，其值明文写在该端口首页 HTML 的 `window.__APP_CONFIG__` 里。

**结论：本机任意能发起 loopback HTTP 请求的进程都能读到该 token 并驱动这个
RPC 面。** 这意味着 PRD 8.8「优先 `--remote-debugging-pipe`（不开端口）」所规避的
那一类风险，在 AGY 自身的架构里本来就存在，与 Unilark 是否开调试端口无关。

产品行为要求：
- 安装向导不得宣称「不开调试端口 = 本机其他进程无法驱动 IDE」。
- FR-SC-10 的措辞需要按上述事实重写。
- AGY 自有接口与 Unilark 辅助控制面分别记录，不将其中一项的限制推广成整体安全保证。

续跑修正：旧 Spike 自己还把 CDP 管道转成了 9800 无认证 HTTP 控制面，因此不能笼统
宣称 Unilark 没有新增暴露。该端口已关闭，改用 0600 Unix socket，父目录 0700。
AGY 自有 API 的风险仍存在；本次 VM 的 `/proc` 未启用 hidepid，其他本地用户可读取
language_server 命令行中的认证资料。没有自动修改系统挂载策略。

新客户端只选择配置指定的进程树；token 不出现在 Endpoint repr、CLI discover 或
错误消息中。登录态与证据保留在仓库外。共享事件与 Lark 卡片脱敏尚待投影层实现，
不能把选择性过滤 thinking 当作通用 Secret 脱敏已完成。
