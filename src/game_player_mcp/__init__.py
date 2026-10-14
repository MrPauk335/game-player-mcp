"""MCP-сервер, дающий агенту глаза и руки для управления игрой в окне.

Экспортирует минимум: сервер, конфиг и исполнитель действий. Захват и работа с
Win32 лежат рядом и не предназначены для прямого использования извне.
"""

from .config import Config
from .executor import ActionExecutor, AgentError
from .server import server

__all__ = ["Config", "ActionExecutor", "AgentError", "server"]
__version__ = "1.0.0"
