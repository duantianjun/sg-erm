# -*- coding: utf-8 -*-
"""日志配置。

提供文件日志和控制台日志：
- sg-erm.log: 应用主日志（RotatingFileHandler，10MB 轮转，保留 5 个备份）
- sg-erm-task.log: 任务执行专用日志（同步任务、调度器、健康检查）
- 控制台: 同时输出 INFO 级别及以上日志

日志文件存放于 {data_dir}/logs/ 目录下。
"""
import logging
import logging.handlers
import sys
from pathlib import Path

from app.config import settings


def setup_logging() -> None:
    """初始化日志配置。"""
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 根日志器配置
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 清除已有 handler（避免 uvicorn 重复）
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # ─── 格式器 ─────────────────────────────────────────────
    detailed_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    simple_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ─── 控制台 Handler ─────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(detailed_formatter)
    root_logger.addHandler(console_handler)

    # ─── 应用主日志文件 ─────────────────────────────────────
    app_log_path = log_dir / "sg-erm.log"
    app_file_handler = logging.handlers.RotatingFileHandler(
        app_log_path,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    app_file_handler.setLevel(logging.DEBUG)
    app_file_handler.setFormatter(detailed_formatter)
    root_logger.addHandler(app_file_handler)

    # ─── 任务执行专用日志 ───────────────────────────────────
    task_log_path = log_dir / "sg-erm-task.log"
    task_file_handler = logging.handlers.RotatingFileHandler(
        task_log_path,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=10,
        encoding="utf-8",
    )
    task_file_handler.setLevel(logging.DEBUG)
    task_file_handler.setFormatter(detailed_formatter)

    # 任务日志器：只写入 task 文件，不重复写入 app 文件
    task_logger = logging.getLogger("sgerm.task")
    task_logger.setLevel(logging.DEBUG)
    task_logger.propagate = False  # 不向父日志器传播
    task_logger.addHandler(task_file_handler)

    # 也加一个控制台输出（方便调试）
    task_console = logging.StreamHandler(sys.stdout)
    task_console.setLevel(logging.INFO)
    task_console.setFormatter(simple_formatter)
    task_logger.addHandler(task_console)

    logging.getLogger("app").info(
        f"日志初始化完成: app={app_log_path}, task={task_log_path}"
    )


def get_task_logger() -> logging.Logger:
    """获取任务执行专用日志器。"""
    return logging.getLogger("sgerm.task")
