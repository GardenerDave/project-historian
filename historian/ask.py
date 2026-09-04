from __future__ import annotations

from pathlib import Path

from historian import acceptance_retrieval, reasoner_client  # backward-compatible test hooks
from historian.service import ask as service_ask


def ask(question: str, endpoint: str | None = None, work_root: Path | None = None, max_tokens: int = 1536):
    return service_ask(question, endpoint=endpoint, work_root=work_root, max_tokens=max_tokens)
