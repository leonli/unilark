# 安装与维护手册

[English](../en/installation.md) · [首页](../../README.zh-CN.md) · [用户手册](user-guide.md)

适用于 **0.0.6**。首次安装请按顺序执行；已有配对的环境可直接看[维护](#维护)。
示例路径和 ID 都是占位符，不能照抄其他安装的身份或凭据。

## 1. 准备环境

| 要求 | 当前支持范围 |
|---|---|
| 主机 | Linux x86_64；实际运行验证使用 Ubuntu 24.04 |
| Python | 已验证离线包使用 3.12，需带 venv；项目最低要求 3.11 |
| Agent | 已运行、已登录、拥有现成项目的 AGY 2.13.0 |
| Lark | 启用机器人的租户应用；应用可用范围包含本人 |
| 网络 | 主机可出站连接 Lark；构建和浏览器安装需下载依赖 |
| 服务 | systemd 用户服务，或显式选择系统服务 |

通过 AGY 自身流程安装、登录。Unilark 不安装 AGY、不替换模型。
适配器校验已验证的 AGY bundle，只查找与配置的可执行文件和 `--user-data-dir` 匹配、
属于同一系统用户的本机进程。任意更新后的 AGY 版本可能被拒绝。
项目目录不构成独立沙箱，工具是否需要审批由 AGY 决定。

## 2. 构建并安装

克隆公开的源码仓库，然后构建并安装：

```bash
git clone https://github.com/leonli/unilark.git
cd unilark
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,lark]'
.venv/bin/python scripts/build-release.py --output dist
cd dist
sha256sum -c SHA256SUMS
cd ..
python3 dist/unilark-0.0.6-linux-x86_64/install.py
export PATH="$HOME/.local/bin:$PATH"
unilark --version
```

构建过程按当前安装的运行依赖版本下载 wheels。产物是离线 Python 依赖包，不包含操作系统、
AGY 或 Python 解释器。也可将压缩包和 `SHA256SUMS` 转移到匹配主机，校验、解压后运行 `install.py`；
目标主机仍需 Python 和 venv。再次构建请指定新的 `--output` 目录，不覆盖已有不可变产物。

安装器拒绝覆盖已有安装；已有环境使用 `upgrade`。
可用 `--prefix /absolute/private/path`、`--bin-dir /absolute/bin/path` 自定义位置；
prefix 须属于本人且权限为 `0700`。

| 默认位置 | 用途 |
|---|---|
| `~/.local/bin/unilark` | 稳定命令入口 |
| `~/.local/share/unilark/releases/0.0.6/` | 程序和隔离 Python 环境 |
| `~/.local/share/unilark/current` | 当前版本软链接 |
| `~/.unilark/agy.toml` | 不含密钥的 AGY 配置 |
| `~/.unilark/lark.env` | 凭据，权限 `0600` |
| `~/.unilark/state.db` | 身份、绑定、队列、卡片和恢复状态 |

## 3. 图表运行环境

```bash
~/.local/share/unilark/current/.venv/bin/python -m playwright install chromium --only-shell
```

以运行网关的同一用户安装；新主机可能还需 Chromium 系统库。
离线主机需预置匹配的浏览器缓存和系统依赖，离线包不内含 Chromium。
浏览器缺失时 Markdown 正文仍可投递，图表回退为源码。本文富文本体验要求 Lark 7.20+。

渲染器使用内置 Mermaid、阻断网络请求，不接受图内覆盖安全设置的配置。
无效、过大或上传失败的图表保留源码，不阻塞其他正文。

## 4. 指定 AGY 实例

在源码目录执行：

```bash
install -d -m 700 "$HOME/.unilark"
cp config.example.toml "$HOME/.unilark/agy.toml"
chmod 600 "$HOME/.unilark/agy.toml"
```

运行 setup 前编辑为本机真实值：

```toml
[agy]
executable = "/absolute/path/to/Antigravity-x64/antigravity"
user_data = "/absolute/path/to/the/running/agy-profile"
project_id = "your-existing-native-project-id"
model = "your-verified-native-model-enum"
source_label = "Lark · Unilark"
```

`executable` 和 `user_data` 必须存在，并匹配同一系统用户下唯一运行的 AGY 进程。
`project_id` 是 AGY 原生项目 ID，`model` 是原生模型枚举值，不能用显示名称代替。
从现有已验证 AGY 实例或配置取得这两个值；目前尚未自动发现它们，示例字符串不是可运行默认值。

```bash
unilark --config "$HOME/.unilark/agy.toml" sidecar check agy
```

如实例或 bundle 不匹配，核对 AGY 和配置，不要修改校验哈希绕过版本限制。

## 5. 配置 Lark 应用

打开 [Lark 开发者后台](https://open.larksuite.com/app) 或[飞书开发者后台](https://open.feishu.cn/app)。
现有真实验收使用国际版 Lark；飞书尚无同等实测记录。

创建租户自建应用，启用机器人，将本人加入可用范围。
授予下列权限，并按控制台提示处理依赖：

| 权限 | 用途 |
|---|---|
| `im:message:send_as_bot` | 机器人回复 |
| `im:message:update` | 更新进度卡、交互卡 |
| `im:message.p2p_msg:readonly` | 私聊和配对消息 |
| `im:message.group_msg:readonly` | 群内不用重复 @ 即可接收消息 |
| `im:chat:create`、`im:chat:read`、`im:chat:update` | 建立并设置会话私有群 |
| `im:chat.members:read` | 验证群里只有本人和机器人 |
| `im:resource` 或适用的 `im:resource:upload` 授权 | 上传图表图片 |

事件与回调选择**长连接 / WebSocket**，订阅：

- `im.message.receive_v1`
- `card.action.trigger`
- `application.bot.menu_v6`

在机器人私聊菜单配置三个入口，动作选择**发送事件**：

| 菜单名称（可翻译） | 事件 ID，必须精确填写 | 命令兜底 |
|---|---|---|
| 新建会话 | `unilark.new` | `/new-form` |
| 打开会话 | `unilark.sessions` | `/list` |
| 设置 | `unilark.settings` | `/settings` |

不要在事件 ID 前加 `/`、改变拼写或把菜单名称当作 ID。
保存后创建并发布应用版本；按租户要求完成审批或安装。
权限和菜单调整也需发布，生效可能有延迟。发布后逐个点击验证；看到菜单不代表事件配置正确。

不需要公网入站 HTTP 地址，也不需要 CardKit 流式权限。
机器人私聊固定菜单与群的聊天菜单树是两种不同配置。

## 6. 配对本人身份

```bash
unilark --config "$HOME/.unilark/agy.toml" setup
```

按提示输入地区 `lark` / `feishu`、App ID、App Secret；Secret 隐藏输入，不放到聊天或命令参数中。
重复执行保留原凭据、配对和会话。

终端开启配对窗口。如果后台要求已有长连接才能保存回调，在此时切回后台保存。
私聊机器人发送终端给出的 `/pair 配对码`，回到终端核对租户、用户、聊天，
输入完整本人 `open_id` 确认。约五分钟后过期，需要时重新配对。

同一应用只运行一个消费者。后台网关已经运行时，不要启动另一个 `pair`、`run` 或 `doctor --connect-lark`。

完成配对后，setup 仍可能返回 **2**，因为前台验收或后台服务交接尚未完成。
查看 `repairs`，不要通过创建新 owner 消除状态。报告保存在 `~/.unilark/setup-report.json`。

## 7. 验证第一轮对话

```bash
unilark --config "$HOME/.unilark/agy.toml" run
```

在机器人私聊点「新建会话」，或发送 `/new-form`。创建有标题的会话并进入群，不 @ 直接发送：

```text
不要调用工具，只回复：连接成功。
```

确认看到进度卡和最终答复。在 AGY 桌面继续同一个原生会话，确认下一次答复进入对应群。
逐一测试三个菜单、`/status`，以及受控的审批和停止场景。日常操作见[用户手册](user-guide.md)。

## 8. 交给 systemd 后台运行

先 Ctrl-C 退出前台网关，AGY 和历史会保留。

```bash
unilark --config "$HOME/.unilark/agy.toml" service install
unilark service start
unilark service status
unilark --config "$HOME/.unilark/agy.toml" doctor --check-api
```

默认使用用户服务。如果主机没有可用的 user bus，显式改用系统服务：

```bash
unilark --config "$HOME/.unilark/agy.toml" service install --system
unilark service start --system
unilark service status --system
```

系统服务操作需要管理员权限，但网关仍以安装用户运行。AGY 及桌面会话的运行需另行保持。
用户服务能否在注销后持续运行取决于主机 user manager 设置。

`doctor` 返回 0 表示所检查的当前环境健康；2 表示需要处理。
setup 的 **ready** 更严格，还要求真实验收记录和匹配的后台服务。
Lark 富文本 GET 接口有时只返回兼容占位内容，无法读回 Markdown 正文：
手机实际已收到答复时，`acceptance verify` 仍可能无法记录新的富文本验收。
应保留这一差异，不应为了让 setup 通过而伪造验收记录。

## 维护

全局参数 `--config`、`--state`、`--credentials` 放在子命令**之前**，每次使用同一组状态路径。
下例使用系统服务；用户服务去掉 `--system`。

```bash
unilark service restart --system
unilark --config "$HOME/.unilark/agy.toml" doctor --check-api
unilark backup --output "$HOME/.unilark/pre-upgrade.db"
unilark --config "$HOME/.unilark/agy.toml" upgrade /absolute/path/to/new-bundle --system
unilark --config "$HOME/.unilark/agy.toml" rollback --system
```

备份目标须为私有目录中尚不存在的文件。
升级前原生任务须空闲，不能有排队、提交中或未知操作。升级器停止唯一网关后再核对，
一致性备份 SQLite，验证迁移副本，再切换程序；不会用旧数据覆盖新接收的工作。

0.0.6 使用 schema **3**，接受 schema 1–3 的升级输入。
0.0.5 可读 schema 3，0.0.4 及更早版本不可以；只允许兼容程序回退。
已配置 0.0.5 的用户升级 0.0.6 不需新增权限或重新配对。

```bash
unilark --config "$HOME/.unilark/agy.toml" uninstall --system
```

卸载移除自有程序、启动入口和服务，保留状态、备份、凭据、AGY、登录及项目目录，不自动删除业务数据。

## 故障处理与验收记录

| 现象 | 核对或下一步 |
|---|---|
| 设置菜单没反应，`/settings` 正常 | 精确的 `unilark.settings`、发送事件动作、菜单事件订阅、已发布版本 |
| 菜单还未出现 | 暂用 `/new-form`、`/list`、`/settings`；核对发布与生效延迟 |
| 会话群未就绪 | 权限、已发布的租户授权、成员核验；处理后在详情重试 |
| 群消息被忽略 | 已登记的正确群、群消息权限、连接、已发布事件订阅 |
| 卡片投递被阻塞 | doctor 的 delivery 状态、发送/更新权限、群成员 |
| AGY 失联或不兼容 | 可执行文件/目录、同一系统用户、登录及 bundle，不绕过校验 |
| UNKNOWN | 先核对原生是否已执行，避免再次提交产生重复动作 |
| 图表保持源码 | 浏览器及系统依赖、图片上传权限、图表语法和大小 |

```bash
unilark recovery list
unilark diagnostics --output "$HOME/.unilark/diagnostics.json"
unilark audit --output "$HOME/.unilark/audit.json"
```

人工裁决前先停网关，核对原生历史或 Lark 消息。
接受未知结果意味着接受不确定性并保持暂停，不表示“没有执行”：

```bash
unilark recovery acknowledge input REQUEST_ID --acknowledge-possible-execution
unilark retry-delivery CARD_ID
```

`control`、`card` 也是可核对的实体。`retry-delivery` 只重试已有 message ID 的阻塞更新，
不重建结果未知的新消息。诊断报告不含正文和凭据，但仍应私有保存；`history` 则会显式输出原生正文。

真实手机/桌面操作后，可运行：

```bash
unilark --config "$HOME/.unilark/agy.toml" acceptance verify text_roundtrip --binding BINDING_ID --confirm-native-view
unilark --config "$HOME/.unilark/agy.toml" acceptance verify desktop_ui_relay --binding BINDING_ID --confirm-native-view --confirm-desktop-ui
unilark --config "$HOME/.unilark/agy.toml" acceptance verify lark_permission_and_stop --binding BINDING_ID --confirm-native-view --confirm-tool-effect
```

确认参数表示实际观察，不是授权模拟通过。富文本 GET 限制见上文，已有证据边界见 [E2E 对照](../E2E-COVERAGE.md)。
