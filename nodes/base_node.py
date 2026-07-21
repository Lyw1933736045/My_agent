"""节点基类。"""

from abc import ABC, abstractmethod
from typing import Any


class BaseNode(ABC):
    def __init__(self, llm_client):
        self.llm_client = llm_client

    @abstractmethod
    def run(self, input_data: Any) -> Any:
        raise NotImplementedError
