# linux_proxy

一个适合无图形化 Linux 服务器使用的代理管理脚本。

它的目标是让 VPS、纯命令行系统、远程 SSH 环境也能快速使用代理：不用桌面客户端，不用手动改一堆 Xray/V2Ray 配置，只通过一个 `proxy` 命令完成订阅导入、节点切换、代理模式切换、出口检测和 v2rayN 订阅导出。

## 适合场景

- 服务器没有图形化界面，只能通过 SSH 管理。
- 想在 Linux 上快速启用本地 `SOCKS5` / `HTTP` 代理。
- 想把订阅节点导入 Xray/V2Ray，并用序号快速切换。
- 想临时给当前 shell 设置代理环境变量。
- 想在 VPS 之间迁移时，只复制脚本和数据文件即可继续使用。

## 主要功能

- 自动识别常见 `xray` / `v2ray` systemd 服务。
- 自动识别常见 Xray/V2Ray 配置路径。
- 支持订阅管理：添加、更新、查看、删除订阅。
- 支持从 `vless://` 订阅导入节点。
- 支持按序号或 tag 切换节点。
- 支持三种代理模式：
  - 仅入站代理
  - 仅出站代理
  - 全局代理
- 支持启动、停止代理服务。
- 支持检查本地入站端口、SOCKS5 握手、HTTP 入站连接。
- 支持检测代理出口 IP 和直连出口 IP。
- 支持一键拉取并执行官方 Xray 安装脚本。
- 支持导出 v2rayN 可用订阅文件。
- 支持多个命令组合执行。
- 配置、订阅和模式文件使用原子写入，降低意外中断导致文件损坏的风险。
- systemd 操作失败时返回非零状态，不再静默显示成功。
- 支持原始文本、标准 Base64 和 URL-safe Base64 订阅内容。

## 默认端口

脚本会生成两个本地入站代理：

```txt
SOCKS5: 127.0.0.1:10808
HTTP:   127.0.0.1:10809
```

项目不内置任何订阅地址、节点、UUID、生产服务器 IP 或固定代理出口。所有节点数据只来自使用者自己添加的订阅。

运行要求：Python 3.9+、systemd，以及已安装的 Xray 或 V2Ray。`proxy 04` 可以安装官方 Xray。

## 通用 Linux 配置

默认配置适用于常见的 systemd + Xray/V2Ray 安装。非标准服务名、配置路径、端口或数据目录可以通过环境变量覆盖：

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `LINUX_PROXY_SERVICE` | 自动识别 | 优先使用的 systemd 服务名 |
| `LINUX_PROXY_CONFIG` | 自动识别 | 优先使用的 Xray/V2Ray 配置文件 |
| `LINUX_PROXY_SOCKS_PORT` | `10808` | 本地 SOCKS5 端口 |
| `LINUX_PROXY_HTTP_PORT` | `10809` | 本地 HTTP 代理端口 |
| `LINUX_PROXY_DATA_DIR` | 脚本所在目录 | 订阅、模式和导出文件目录 |
| `LINUX_PROXY_PROFILE_FILE` | `/etc/profile.d/xray-proxy.sh` | shell 代理环境文件 |
| `LINUX_PROXY_ENV_FILE` | `/etc/environment` | 系统环境文件 |

例如使用自定义服务和配置：

```bash
sudo env \
  LINUX_PROXY_SERVICE=my-proxy \
  LINUX_PROXY_CONFIG=/etc/my-proxy/config.json \
  LINUX_PROXY_SOCKS_PORT=12080 \
  LINUX_PROXY_HTTP_PORT=12081 \
  proxy 02
```

## 快速安装

推荐把脚本放在 `/root/proxy`，再链接到系统命令路径：

```bash
cd /root
curl -fsSL -o proxy https://raw.githubusercontent.com/youshi01/linux_proxy/main/proxy
chmod +x proxy
ln -sf /root/proxy /usr/local/bin/proxy
```

查看当前状态：

```bash
proxy
```

如果机器还没有安装 Xray，可以执行：

```bash
proxy 04
```

添加订阅并导入节点：

```bash
proxy 00 <订阅链接>
```

选择代理模式并启动代理（`01` / `02` / `03` 都会自动启动服务）：

```bash
proxy 01
```

## 三种代理模式

执行 `proxy 01`、`proxy 02` 或 `proxy 03` 时，脚本会先执行 `systemctl enable <服务> --now`，并确认服务为 `active`、本地 SOCKS5 握手正常、HTTP 端口可连接。只有检查通过后才会写入模式和代理环境变量；失败时命令返回非零状态并清理代理环境变量。

### `proxy 01` 仅入站代理

只保留本地监听：

```txt
socks5://127.0.0.1:10808
http://127.0.0.1:10809
```

这个模式不会写入 shell 环境变量，也不会写入系统全局环境变量。适合只想让某些命令或程序显式指定代理时使用。

### `proxy 02` 仅出站代理

保留本地监听，并写入：

```txt
/etc/profile.d/xray-proxy.sh
```

新登录的 shell 会带上这些环境变量：

```txt
HTTP_PROXY=http://127.0.0.1:10809
HTTPS_PROXY=http://127.0.0.1:10809
ALL_PROXY=socks5h://127.0.0.1:10808
```

当前 shell 如果想立即生效，可以执行：

```bash
source /etc/profile.d/xray-proxy.sh
```

### `proxy 03` 全局代理

保留本地监听，并同时写入：

```txt
/etc/profile.d/xray-proxy.sh
/etc/environment
```

适合希望系统环境里也带代理变量的场景。

## 命令一览

```bash
proxy                         # 显示状态、模式、节点、服务、配置路径
proxy 000                     # 更新所有订阅并刷新节点
proxy 00 <订阅链接>           # 添加订阅并立即更新
proxy 00 <订阅链接> <名称>    # 添加订阅并指定名称
proxy 001                     # 查看订阅列表
proxy 002 <订阅编号>          # 删除订阅

proxy 01                      # 切到仅入站代理，自动启动并检查服务
proxy 02                      # 切到仅出站代理，自动启动并检查服务
proxy 03                      # 切到全局代理，自动启动并检查服务

proxy 04                      # 安装官方 Xray
proxy 05                      # 导出 v2rayN 订阅文件
proxy 06                      # 检查入站监听、SOCKS5 握手、HTTP 入站连接

proxy 0                       # 测试当前代理节点出口 IP
proxy check                   # 检查系统直连出口和代理环境变量

proxy <序号>                  # 切换节点，例如 proxy 2
proxy <tag>                   # 切换节点，例如 proxy CL-JP
proxy test <序号|tag>         # 临时切换测试，测试后恢复原节点

proxy on                      # 重新按当前模式启动并检查服务
proxy off                     # 停止服务并清理代理环境变量
```

## 组合执行

脚本支持一次执行多个动作，按顺序处理：

```bash
proxy 01 5
proxy 02
proxy 03 000
proxy 04 000 01
proxy 05
```

示例说明：

- `proxy 01 5`：先启动服务并切到仅入站模式，再切换到第 5 个节点。
- `proxy 02`：启动服务并切到仅出站模式。
- `proxy 03 000`：先切到全局模式，再更新所有订阅。
- `proxy 04 000 01`：先安装官方 Xray，再更新订阅，最后切回仅入站模式。
- `proxy 05`：把当前节点集合导出成 v2rayN 可用订阅文件。

## 自动识别范围

优先识别这些 systemd 服务名：

```txt
xray-proxy
xray
v2ray
v2ray-proxy
```

优先识别这些配置文件：

```txt
/root/xray_proxy_config.json
/usr/local/etc/xray/config.json
/etc/xray/config.json
/usr/local/etc/v2ray/config.json
/etc/v2ray/config.json
```

如果没有识别到现有配置，脚本默认使用：

```txt
/usr/local/etc/xray/config.json
```

## 运行时文件

脚本默认把订阅和模式文件放在脚本同目录；也可以通过 `LINUX_PROXY_DATA_DIR` 修改：

```txt
proxy_subscriptions.json
proxy_mode.json
```

导出 v2rayN 订阅时，会生成：

```txt
v2rayn_subscription_raw.txt
v2rayn_subscription.txt
```

这些文件默认不会提交到 Git 仓库。

## 迁移

如果脚本放在 `/root/proxy`，迁移到另一台机器时通常复制这些文件即可：

```txt
/root/proxy
/root/proxy_subscriptions.json
/root/proxy_mode.json
```

复制后重新创建命令链接：

```bash
chmod +x /root/proxy
ln -sf /root/proxy /usr/local/bin/proxy
```

然后执行：

```bash
proxy
proxy on
```

## 注意事项

1. 脚本会重写识别到的 Xray/V2Ray 主配置文件，更适合纯代理机使用。
2. 如果目标机器原本有复杂 Xray/V2Ray 业务配置，请先备份配置文件。
3. `proxy 04` 会下载并执行官方 Xray 安装脚本，需要 root 权限和 GitHub 访问能力。
4. 当前主要面向 `vless://` 订阅导入。
5. 不要把真实订阅链接提交到公开仓库。
6. 订阅和生成的配置默认以仅所有者可读写的权限保存，但仍应把服务器账号和备份目录视为敏感环境。

## 测试

本地运行：

```bash
python3 -m py_compile proxy tests/test_proxy_modes.py
python3 -m unittest discover -s tests -v
```

GitHub Actions 会在 Python 3.9 至 3.13 上运行相同测试。测试使用随机本地端口和示例域名，不依赖真实订阅或生产代理。

## 许可证

本项目使用 [MIT License](LICENSE)。
