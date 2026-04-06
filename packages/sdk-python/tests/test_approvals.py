import tempfile
import time
import threading
import pytest
from pathlib import Path

from wickd.approvals import (
    ApprovalGate,
    ApprovalDenied,
    ApprovalRequired,
    approval,
    auto_approve_handler,
    auto_deny_handler,
    file_approval_handler,
)


class TestApprovalGate:
    def test_auto_approve(self):
        gate = ApprovalGate(fn=lambda x: x * 2, name="test", handler=auto_approve_handler)
        assert gate(5) == 10

    def test_auto_deny(self):
        gate = ApprovalGate(fn=lambda x: x * 2, name="test", handler=auto_deny_handler)
        with pytest.raises(ApprovalDenied) as exc_info:
            gate(5)
        assert exc_info.value.name == "test"

    def test_decorator_approve(self):
        @approval("db_write", handler=auto_approve_handler)
        def update_db(data):
            return f"updated: {data}"
        assert update_db("record_1") == "updated: record_1"

    def test_decorator_deny(self):
        @approval("dangerous_action", handler=auto_deny_handler)
        def delete_everything():
            return "deleted"
        with pytest.raises(ApprovalDenied):
            delete_everything()


class TestExceptions:
    def test_denied_message(self):
        e = ApprovalDenied("db_write", reason="Too risky")
        assert "db_write" in str(e)
        assert "Too risky" in str(e)

    def test_denied_no_reason(self):
        assert "db_write" in str(ApprovalDenied("db_write"))

    def test_required_message(self):
        assert "send_email" in str(ApprovalRequired("send_email"))


class TestFileApprovalHandler:
    def _make_context(self):
        return {"name": "test_action", "function": "test_func", "trace_id": "abc12345"}

    def test_approved_via_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handler = file_approval_handler(approval_dir=tmpdir, poll_interval=0.1, timeout=5)

            def approve_later():
                time.sleep(0.3)
                for p in Path(tmpdir).glob("*.pending"):
                    p.rename(p.with_suffix(".approved"))

            t = threading.Thread(target=approve_later)
            t.start()
            assert handler(self._make_context()) is True
            t.join()

    def test_denied_via_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handler = file_approval_handler(approval_dir=tmpdir, poll_interval=0.1, timeout=5)

            def deny_later():
                time.sleep(0.3)
                for p in Path(tmpdir).glob("*.pending"):
                    p.rename(p.with_suffix(".denied"))

            t = threading.Thread(target=deny_later)
            t.start()
            assert handler(self._make_context()) is False
            t.join()

    def test_timeout_denies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handler = file_approval_handler(approval_dir=tmpdir, poll_interval=0.1, timeout=0.5)
            assert handler(self._make_context()) is False
