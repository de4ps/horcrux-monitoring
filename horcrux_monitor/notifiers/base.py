import abc


class BaseNotifier(abc.ABC):
    @abc.abstractmethod
    def send(self, message: str) -> bool:
        """Send a message. Returns True on success."""
        ...
