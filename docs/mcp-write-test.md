# MCP 写入链路验证

本文件由 **DeepSeek Harness** 通过 GitHub MCP 服务端自动创建，用于验证 token 的写权限（建分支 / 提交 / 开 PR）。

| 项目 | 值 |
|------|-----|
| 服务端 | `github-2` |
| 分支 | `docs/mcp-write-test` |
| 创建时间 | 2026-11-15 15:25 (UTC+8) |

## 说明

- 这是一个纯粹的连通性验证文件，**不包含任何项目逻辑**，删掉不会影响 vps-monitor 的任何功能。
- 验证通过后可安全删除本文件并关闭对应 PR。

## 验证路线的用途

后续如需让 AI 协作维护本仓库（例如修文档、整理 issue、提 PR），需要 token 至少具备：

- `Contents: Read and write`（建分支、提交）
- `Pull requests: Read and write`（开 PR、合并）
- `Issues: Read and write`（建 issue、评论）
