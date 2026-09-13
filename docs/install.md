# 安装与程序维护

适用于 0.0.4 实验版。只实测 Linux x86_64 / Python 3.12，要求主机提供 Python 与 venv。
隔离的虚拟环境与离线 wheels 安装在版本目录；不内嵌 Python 解释器、不自动安装 AGY。
安装前使用同目录的 `SHA256SUMS` 验证压缩包。校验保证内容完整性，不是发行方签名。

## 安装布局

```bash
python3 unilark-0.0.4-linux-x86_64/install.py
# 自定义位置（目录需由本人拥有且 mode 0700）：
python3 unilark-0.0.4-linux-x86_64/install.py \
  --prefix /private/path/unilark --bin-dir /private/path/bin
```

默认程序位于 `~/.local/share/unilark/releases/0.0.4/`；`current` 是激活链接。
`~/.local/bin/unilark` 是稳定入口。已有安装或同名入口不会被覆盖，升级使用下方专用命令。
发行包包含 `requirements.lock`、`sbom.cdx.json`、每文件哈希和操作文档。

## 图表运行环境

0.0.4 的 Markdown 回复要求 Lark 7.20+。Mermaid 11.17.2、许可证和来源校验随 wheel 分发；
Playwright 1.62.0 Python wheel 在离线依赖包内，Chromium 可执行程序需要单独安装：

```bash
~/.local/share/unilark/current/.venv/bin/python -m playwright install chromium --only-shell
```

以运行网关的同一用户安装，默认缓存在该用户 `~/.cache/ms-playwright/`。新系统可能还需要 Chromium 系统库，
可由主机管理员安装 Playwright 对应的系统依赖。离线安装要预置匹配的浏览器缓存；发行包没有包含浏览器。
当前主机已安装匹配的 headless shell，并验证中文字体、流程图和时序图；未依赖 web-xia 的浏览器版本。
浏览器缺失时正文仍可投递，图表回退为源码。需要图表上传权限 `im:resource` 或 `im:resource:upload`，
当前应用上传已实测通过，不需再次配置。

浏览器使用随包脚本、严格配置、网络请求拦截和 CSP；不下载远程图片或字体，不允许图内 init/frontmatter 改写安全配置。
单图最多 6000 字符、300 条边、渲染 12 秒、渲染与上传合计 18 秒；超限保留源码。
同一进程按源码哈希缓存最多 128 个图像 key；失败冷却 30 秒，后续正文更新时可重新尝试。

默认状态目录 `~/.unilark/` 为 0700，凭据和数据库为 0600。
全局参数 `--config`、`--state`、`--credentials` 必须放在子命令之前。
以下示例使用默认状态/凭据；自定义安装需每次指定同一组路径。

## 配置与前台验收

1. 安装并登录 AGY，准备已有项目；从 `config.example.toml` 填好指定实例配置。
2. `unilark --config /private/path/agy.toml setup` 检查环境、应用、owner 和历史验收。
   首次凭据隐藏输入，首次配对需要在本机核对身份。非交互巡检用 `--non-interactive`。
3. 按 [Lark 配置](LARK-SETUP.md) 启用机器人、最小权限和长连接事件，发布到本人范围。
4. `unilark --config /private/path/agy.toml run` 前台运行，完成手机普通收发、桌面接力和受控工具允许/停止。

setup 会保存 `setup-report.json`，缺步骤时返回 2；重复执行保留 owner 与会话。
它不会冒充用户完成外部登录/控制台授权，也不会把心跳在线自动算作跨端验收。

## 保存验收证据

`unilark sessions` 显示网关 binding ID。完成实际操作后，在本机运行：

```bash
unilark --config /private/path/agy.toml acceptance verify text_roundtrip \
  --binding BINDING_ID --confirm-native-view
unilark --config /private/path/agy.toml acceptance verify desktop_ui_relay \
  --binding BINDING_ID --confirm-native-view --confirm-desktop-ui
unilark --config /private/path/agy.toml acceptance verify lark_permission_and_stop \
  --binding CONTROL_BINDING_ID --confirm-native-view --confirm-tool-effect
```

命令只读取原生会话与真实 Lark 消息，再保存不含正文的验收结果；不会发送任务或批准工具。
标志表示本机操作者已经查看同一原生会话、确实用桌面输入，以及核验受控工具的实际效果。
审批/停止验收还要求网关记录真实 Allow 已应用、其后 Stop 前忙碌和 Stop 后空闲。
单纯空闲时 `/stop`、旧版没有观测证据的控制记录、未送达卡片都不能新通过此检查。
前版独立真机验收记录仍作为历史证据保留。

当前租户对 JSON 2.0 消息的 GET 回读只返回兼容占位内容，不含实际 Markdown 正文。
旧 CLI 的正文匹配检查不能新记录富文本验收通过；已有证据保留，API 接受不能替代手机视觉验收。
新版测试范围见 [0.0.4 说明](RELEASE-0.0.4.md)。

## 后台交接

结束前台 `run` 后：

```bash
unilark --config /private/path/agy.toml service install
unilark service start
unilark service status
unilark --config /private/path/agy.toml setup --non-interactive
```

默认使用 systemd 用户服务。无用户 bus 的主机可以显式安装系统服务：

```bash
unilark --config /private/path/agy.toml service install --system
unilark service start --system
unilark --config /private/path/agy.toml setup --non-interactive --system
```

系统安装需要管理员权限，以当前用户运行网关，不把 Secret 写进 unit。
setup 要求已启用服务的 MainPID、实时心跳和配置路径一致；前台进程在线不算后台交接完成。
服务入口使用 `PREFIX/current/.venv/bin/unilark`，升级后不会固定在旧版本解释器。
用户服务在注销后的运行依赖宿主 user manager/linger；未经实测不承诺。

## 升级、回退和卸载

```bash
unilark --config /private/path/agy.toml backup --output /private/path/pre-upgrade.db
unilark --config /private/path/agy.toml upgrade /path/to/new-bundle --system
unilark --config /private/path/agy.toml rollback --system
unilark --config /private/path/agy.toml uninstall --system
```

用户服务省略 `--system`；自定义 prefix 加 `--prefix`。升级需要原生任务空闲，且没有 QUEUED/SUBMITTING/UNKNOWN 输入或控制。
流程会核验包、停止唯一网关、重查安全点、生成一致性备份，在新副本验证迁移和原生配置，然后切换程序并检查服务健康。
0.0.4 接受 schema 1/2 升级输入，产生 schema 2；doctor 和只读列表不会迁移数据库。

回退只切换兼容程序，**绝不拿旧备份覆盖升级后接收的数据**。0.0.1 不认识 schema 2，不能自动回退到它。
兼容候选启动失败时尝试切回前一程序；不兼容时保留现场，需要本机核对。
升级前的备份和候选迁移副本在状态目录 `backups/`，保留用于诊断。

卸载移除自有启动入口、版本程序和匹配服务；默认保留状态、备份、凭据、外部 AGY、登录及 workspace。
本版本不提供自动清除业务数据的选项。不要通过删除状态文件解决投递或执行不确定性。

## 修复与诊断

```bash
unilark --config /private/path/agy.toml doctor --check-api
unilark --config /private/path/agy.toml sidecar check agy
unilark recovery list
unilark diagnostics --output /private/path/diagnostics.json
unilark audit --output /private/path/audit.json
```

输入、控制和新卡片 UNKNOWN 必须核对可能已发生的外部动作。确认接受这份不确定性后，先停止网关，再执行：

```bash
unilark recovery acknowledge input REQUEST_ID --acknowledge-possible-execution
# entity 也可为 control 或 card
```

结果为 DISMISSED，记录审计并保持暂停，既不声称“没执行”也不重发。
已知 message ID 的 BLOCKED 更新在修复权限后可 `unilark retry-delivery CARD_ID`；不允许重建未知结果的新卡片。
诊断和审计默认不带消息正文/凭据；`history` 是显式查看正文的命令。
