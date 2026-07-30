# SG-ERM 测试体系 Phase 3 — 剩余 API 测试设计

> 日期: 2026-07-30
> 状态: 已通过
> 前置: Phase 1 + Phase 2 已完成（86 测试，45% 覆盖率）

---

## 1. 范围

Phase 3 覆盖 Phase 1/2 未涉及的 API 端点，全部为 HTTP API 集成测试：

| 测试文件 | API 端点 | 关键路径 |
|---------|---------|---------|
| `test_tokens_api.py` | `/api/v1/tokens` | CRUD；prefix 索引查询；过期验证；权限隔离（需 admin） |
| `test_sources_api.py` | `/api/v1/sources` | CRUD；health-check 触发；aggregate-index 聚合 |
| `test_sync_api.py` | `/api/v1/sync` | trigger 同步任务；tasks 列表；policies 管理 |
| `test_extensions_api.py` | `/api/v1/extensions` | 扩展列表；详情查询；过滤（按 publisher/version） |
| `test_audit_api.py` | `/api/v1/audit` | 日志筛选（时间范围/actor/action）；统计汇总 |

---

## 2. 架构

沿用 Phase 1 fixture：
- `client` — httpx.ASGITransport + 内存 SQLite
- `db_session` — AsyncSession
- `_admin_token(db_session)` / `_login_as(db_session)` — 构造 JWT

所有测试通过 HTTP 调用验证：
- 响应状态码（200/400/401/403/404）
- JSON 结构（`code`/`data`/`message`）
- 数据库记录状态

---

## 3. 新增测试文件

```
tests/integration/
├── test_tokens_api.py      # ~6 tests
├── test_sources_api.py     # ~6 tests
├── test_sync_api.py        # ~6 tests
├── test_extensions_api.py  # ~5 tests
└── test_audit_api.py       # ~5 tests
```

预计新增 **~32 个测试**。

---

## 4. Mock 边界

- 无需 mock 上游 HTTP（Phase 3 不涉及 sync_engine/proxy_engine 的网络调用）
- 无需 mock 时间（过期验证可通过设置 `expires_at` 字段模拟）
- 测试数据通过 `db_session` 直接插入

---

## 5. 排除项

- 不测试 WebSocket 实时推送（同步进度）
- 不测试 scheduler 定时任务触发
- 不测试文件上传大文件场景

---

## 6. 验证标准

- `python -m pytest -v` 全绿
- 整体覆盖率 ≥ 50%
- API 端点响应格式符合 OpenAPI 规范