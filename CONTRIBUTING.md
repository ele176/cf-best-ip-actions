# 参与贡献

感谢你改进本项目。

1. 先搜索已有 Issue，确认问题没有重复报告。
2. Fork 仓库并从 `main` 创建功能分支。
3. 保持实现仅依赖 Python 标准库；新增行为应同时增加测试。
4. 运行 `python -m unittest discover -s tests -v`。
5. 提交 Pull Request，说明动机、行为变化和测试结果。

请勿在 Issue、Pull Request、提交、截图或日志中加入 Cloudflare API Token、Zone ID、真实域名配置或其他凭据。外部候选源必须支持 HTTPS，且其结果仍须经过 Cloudflare 官方网段过滤和 SNI/TLS/HTTP 验证。

维护者可以拒绝削弱安全失败、最小权限、官方网段过滤或 Secret 隔离的改动。
