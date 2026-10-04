from abc import ABC, abstractmethod

class AIService(ABC):
    """Providers must be explicitly configured; never send data automatically."""
    @abstractmethod
    def organize(self, records: list[dict], operation: str) -> dict:
        """Summarize, classify, tag, extract entities/tasks, or generate reviews."""

class EmbeddingService(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        pass

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[str]:
        """Return stable entry UUIDs; the database remains the source of truth."""
