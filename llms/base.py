"""OpenAI 兼容 LLM 客户端。"""

from datetime import datetime
from typing import Optional

from openai import OpenAI

from ..utils.retry_helper import with_retry


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: Optional[str] = None,
        timeout: float = 300.0,
    ):
        if not api_key or not model_name:
            raise ValueError("LLM API Key 和模型名不能为空")
        kwargs = {"api_key": api_key, "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model_name = model_name
        self.base_url = base_url
        self.timeout = timeout

    @with_retry(max_retries=3, initial_delay=2.0)
    def invoke(self, system_prompt: str, user_prompt: str) -> str:
        cutoff = datetime.now().astimezone().isoformat(timespec="minutes")
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"当前数据检索时间：{cutoff}\n{user_prompt}",
                },
            ],
            timeout=self.timeout,
        )
        if not response.choices:
            return ""
        return (response.choices[0].message.content or "").strip()
