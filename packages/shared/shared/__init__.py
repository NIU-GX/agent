"""共享配置、Schema、日志与基础设施客户端。

所有 apps / packages 依赖本包，避免循环依赖与配置散落。
"""

from shared.config import settings
from shared.logging import get_logger

__all__ = ["settings", "get_logger"]
