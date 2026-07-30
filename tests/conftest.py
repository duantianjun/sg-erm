# -*- coding: utf-8 -*-
"""根 conftest：在 app.* 导入前注入测试环境变量。

app/config.py 在模块导入时实例化 Settings()，要求 SG_ERM_SECRET_KEY 存在；
app/database.py 在导入时用 settings.db_url 创建引擎。
本文件由 pytest 在收集前加载，保证环境变量先就位。
"""
import os
import secrets
import tempfile

# 必须在任何 `from app.* import ...` 之前
os.environ.setdefault("SG_ERM_SECRET_KEY", "test-" + secrets.token_hex(16))
os.environ.setdefault("SG_ERM_DATA_DIR", tempfile.mkdtemp(prefix="sg-erm-test-"))
os.environ.setdefault("SG_ERM_SCHEDULER_ENABLED", "false")  # 禁调度器/健康检查
