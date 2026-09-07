# Cloudflare 优选 IP 自动更新器

零成本、无需在本地运行的 GitHub Actions 方案。它按运营商汇总公开优选结果，验证候选 IP 属于 Cloudflare 官方网段，并用你自己的 SNI 域名完成 TLS/HTTPS 探测，最后更新 Cloudflare 的灰云 A 记录。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/ele176/cf-best-ip-actions/actions/workflows/ci.yml/badge.svg)](https://github.com/ele176/cf-best-ip-actions/actions/workflows/ci.yml)

> 重要说明：GitHub Runner 在海外，因此本项目不会把 Runner 延迟伪装成中国家宽延迟。候选优先级主要来自公开的移动/联通/电信优选数据；GitHub 侧延迟只用于同分候选的可用性验证和次要排序。

## 工作流程

1. 每 6 小时从多个上游汇总移动、联通、电信优选 IPv4，并保留两个独立备用源。
2. 从 Cloudflare 官方地址获取当前 IPv4 网段，过滤非 Cloudflare IP。
3. 使用你的 Worker/代理入口域名作为 SNI，对候选 IP 做真实 TLS 和 HTTP 探测。
4. 当前 IP 失效时立即切换；当前 IP 可用时默认 24 小时内不重复切换。
5. 通过最小权限 API Token 更新指定灰云 A 记录。
6. 任一步骤失败都保留原 DNS，不会写入未经验证的 IP。

## 一、创建 Cloudflare DNS 记录

假设你的主域名是 `example.com`：

| 记录 | 状态 | 用途 |
| --- | --- | --- |
| `best.example.com` | 仅 DNS（灰云） | 返回自动选择的 Cloudflare IP |
| `edge.example.com` | 已代理（橙云） | 绑定 Worker/路由，并作为 SNI 和 Host |

先手动创建一条 A 记录：

```text
类型：A
名称：best
IPv4：1.1.1.1（临时值，首次运行会替换）
代理状态：仅 DNS/灰云
TTL：自动
```

不要把 `best.example.com` 打开橙云，否则客户端解析到的是 Cloudflare 自动分配的地址，而不是程序写入的地址。

## 二、创建最小权限 API Token

在 Cloudflare 控制台进入 **我的个人资料 → API 令牌 → 创建令牌**：

1. 使用“编辑区域 DNS”模板。
2. 权限只保留 `Zone / DNS / Edit`。
3. 区域资源选择“包括 / 特定区域 / example.com”。
4. 创建并复制 Token。

不要使用 Global API Key。

Zone ID 位于 Cloudflare 对应域名的概述页面右侧。

## 三、上传到 GitHub

1. 新建一个 **Public 公开仓库**（若只想自己使用，也可以保留为 Private）。
2. 解压本项目，把解压后的全部文件上传到仓库根目录。
3. 打开仓库的 **Settings → Secrets and variables → Actions**。

### Secrets

| 名称 | 内容 |
| --- | --- |
| `CF_API_TOKEN` | 上一步创建的 Cloudflare API Token |
| `CF_ZONE_ID` | Cloudflare Zone ID |

### Variables

| 名称 | 必填 | 示例 | 说明 |
| --- | ---: | --- | --- |
| `DNS_RECORD` | 是 | `best.example.com` | 要自动修改的灰云 A 记录 |
| `SNI_DOMAIN` | 是 | `edge.example.com` | Worker/橙云入口域名 |
| `ISP` | 否 | `cmcc` | 运营商，见下表；默认 `all` |
| `VERIFY_PATH` | 否 | `/` | 用于验证的 HTTP 路径，默认 `/` |
| `EXPECTED_STATUSES` | 否 | `200,204,400,426` | 留空表示接受 200–499 |
| `MIN_SWITCH_AGE_HOURS` | 否 | `24` | 当前 IP 可用时的最短保持时间 |
| `EXTRA_SOURCE_URLS` | 否 | 每行一个 URL | 补充纯文本候选 IP 来源 |

`ISP` 可选值：

| 值 | 线路 |
| --- | --- |
| `cmcc` | 中国移动 |
| `cucc` | 中国联通 |
| `ctcc` | 中国电信 |
| `all` | 三网综合，无法确定运营商时使用 |

如果你主要使用校园网，可以先用 `all`。

## 四、首次运行

1. 打开仓库顶部的 **Actions**。
2. 选择“Cloudflare 优选 IP”。
3. 点击 **Run workflow**。
4. 第一次建议把 `dry_run` 设为 `true`，确认日志中候选、SNI 和目标记录正确。
5. 再以 `dry_run=false` 运行一次，DNS 才会真正更新。

之后工作流会在 UTC 时间每隔 6 小时的第 17 分钟运行。GitHub 定时任务可能延迟，这是正常现象。

### 公开仓库注意事项

- Secret 不会因为仓库公开而显示，但绝不能写进代码、README、Issue、Actions 日志或提交历史。
- 公开仓库的 Actions 日志会显示 `DNS_RECORD`、`SNI_DOMAIN`、当前 IP 和候选结果。若不想公开这些运行信息，请把本仓库作为公开源码，另建一个私有运行仓库部署；GitHub Free 的私有仓库 Actions 有免费额度。
- Fork 创建后，定时工作流默认处于停用状态；使用者需要在自己的 Fork 中手动启用 Actions 并配置自己的 Secrets。
- GitHub 会自动停用连续 60 天没有仓库活动的公开仓库定时工作流。维护者需要重新启用，或在此期间进行正常的代码/文档维护。
- 外部 Pull Request 只运行不接触 Cloudflare 凭据的 `CI`；DNS 更新工作流不监听 `pull_request`。
- 合并陌生贡献前必须审查其对 `.github/workflows/` 和 `scripts/` 的改动，因为合并后的定时任务能够读取仓库 Secrets。

## 五、客户端填写

通用填写方式：

```yaml
address: best.example.com
port: 443
tls: true
serverName: edge.example.com
host: edge.example.com
```

其中 `serverName`/`SNI` 和 WebSocket `Host` 应保持为真正绑定 Worker 的橙云域名，不要填写 `best.example.com`。

## 选择与防抖逻辑

- 公开来源只负责提供候选，不具备直接写 DNS 的能力。
- 每个候选必须位于 Cloudflare 官方 IPv4 网段。
- 每个候选必须能用你的 SNI 完成证书校验并返回 HTTP 响应。
- 多来源共同推荐优先，其次比较上游排名，最后才比较 GitHub Runner 探测耗时。
- 当前 IP 探测失败：立即换成最佳可用候选。
- 当前 IP 正常且修改不足 24 小时：保持不动。
- 当前 IP 正常且仍属于较优候选：没有明显优势时不切换。
- 无可靠候选、上游异常或 Cloudflare API 异常：退出并保留当前记录。

## 长期维护机制

- `.github/workflows/maintenance.yml` 每周检查所有默认数据源、解析格式、Cloudflare 官方网段和单元测试。
- 健康检查失败时，会在你自己的仓库中自动创建一个标题为 `Automated maintenance alert` 的 Issue；已有同名未关闭 Issue 时不会重复创建。
- `.github/dependabot.yml` 每周检查 GitHub Actions 依赖更新，并以 Pull Request 形式提交，不会未经确认直接合并。
- 所有外部 Actions 均固定到完整提交 SHA，并关闭 checkout 凭据持久化。
- `CI` 对推送和 Pull Request 运行无凭据单元测试，贡献代码不会接触 Cloudflare Secret。
- 主优化任务自身采用安全失败设计：维护检查出问题不会导致错误 IP 被写入 DNS。
- 上游 URL 集中定义在 `scripts/optimizer.py`，额外来源可直接通过 `EXTRA_SOURCE_URLS` 变量添加，不必修改主流程。

收到自动维护 Issue 后，把 Issue 日志发到本对话即可继续修复。仓库能够自动发现和报告故障，但第三方接口发生无法兼容的结构变化时，仍需要代码更新；任何项目都无法诚实地承诺完全无人维护且永久可用。

## 常见报错

### `DNS_RECORD and SNI_DOMAIN are required`

没有创建对应的 GitHub Variables，或者变量名拼写错误。

### `Cloudflare API error 9109/10000`

Token 无效或权限不足。重新创建仅限特定 Zone 的 `DNS Edit` Token。

### `No validated candidate`

候选源暂时不可用、SNI 域名未正确绑定、验证路径返回 5xx，或者所有候选都连接失败。程序不会修改原 DNS。

### DNS 已更新但客户端仍连接旧地址

DNS 解析器或客户端可能缓存旧结果。重启客户端或清理 DNS 缓存后再试。

## 数据来源与边界

默认候选读取自 `DustinWin/BestCF` 维护的三网聚合文件，其中包含 CMLiu、VPS789、CloudFlareYes、微测网等公开来源；同时使用 `LancelotRar/best-cf-ips` 作为独立备用。IP 所属网段始终以 Cloudflare 官方列表为准。上游结构变化时，工作流会安全失败并保留当前 DNS。

## 参与贡献与安全报告

欢迎提交 Issue 和 Pull Request。开始前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)；安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要在公开 Issue 中粘贴凭据或漏洞利用细节。

本项目只解决候选聚合、可用性验证和 DNS 自动更新，不保证某个海外 Runner 选出的 IP 一定是你所在城市的绝对最快 IP。请遵守 Cloudflare、GitHub、网络服务提供商及所在地适用规则。

## 本地测试（仅开发者需要）

```bash
python -m unittest discover -s tests -v
```

程序只使用 Python 标准库，无需安装 pip 依赖。
