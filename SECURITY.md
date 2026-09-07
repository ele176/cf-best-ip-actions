# 安全政策

## 支持版本

仅最新发布版本接受安全修复。

## 报告漏洞

请使用 GitHub 仓库的 **Security → Report a vulnerability** 私下报告。不要在公开 Issue、Discussion 或 Pull Request 中披露可利用细节，也不要提交任何真实 Cloudflare 凭据。

报告请包含受影响版本、复现条件、可能影响和建议修复；所有示例必须使用已撤销的测试凭据或占位符。若你怀疑凭据已泄露，请立即在 Cloudflare 撤销并轮换 Token，同时删除相关 Actions 日志。

## 安全边界

- 项目只需要限定到单个 Zone 的 `Zone / DNS / Edit` API Token。
- DNS 更新工作流不在外部 Pull Request 上运行。
- Cloudflare 官方网段候选必须通过目标 SNI 的 TLS/HTTP 验证；反代/BYOIP 候选还必须通过系统 CA 证书校验和多轮成功阈值，不因来源声称而放行。
- 上游、验证或 API 异常时保留现有 DNS 记录。
