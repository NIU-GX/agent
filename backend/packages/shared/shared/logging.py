from __future__ import annotations

"""结构化日志：统一格式，便于与 OTel / 容器日志对接。"""

import logging
import sys

from shared.config import settings


def get_logger(name: str) -> logging.Logger:
    """获取带统一 handler 的 logger。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.setLevel(settings.log_level.upper())
    logger.propagate = False
    return logger
