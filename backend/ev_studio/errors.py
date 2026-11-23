class EVStudioError(Exception):
    """Lỗi gốc."""


class RetryableError(EVStudioError):
    """Lỗi tạm thời (mạng, 5xx, quá tải) → retry với backoff."""


class PermanentError(EVStudioError):
    """Lỗi vĩnh viễn → dừng job, cần người can thiệp."""


class GateBlocked(EVStudioError):
    """Một gate biên tập/an toàn chặn tiến trình (thiếu nguồn, asset, QA...)."""

    def __init__(self, reasons: list[str] | str):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(self.reasons))


class BlockedByCredentials(PermanentError):
    """Thiếu key/OAuth cho tích hợp thật ở chế độ production."""


class PrivacyViolation(PermanentError):
    """Bất kỳ nỗ lực nào đặt privacy khác 'private'."""


class ChannelMismatch(PermanentError):
    """Kênh OAuth không khớp kênh đích."""
