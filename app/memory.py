from collections import defaultdict
from threading import Lock


Message = dict[str, str]


class SessionMemory:
    def __init__(self, max_messages: int = 12) -> None:
        self.max_messages = max_messages
        self._messages: dict[str, list[Message]] = defaultdict(list)
        self._lock = Lock()

    def get(self, thread_id: str) -> list[Message]:
        with self._lock:
            return list(self._messages[thread_id])

    def append(self, thread_id: str, role: str, content: str) -> None:
        with self._lock:
            self._messages[thread_id].append({"role": role, "content": content})
            self._messages[thread_id] = self._messages[thread_id][-self.max_messages :]

    def clear(self, thread_id: str) -> None:
        with self._lock:
            self._messages.pop(thread_id, None)


session_memory = SessionMemory()

