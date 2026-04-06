from wickd.notify import console


class TestConsoleNotify:
    def test_budget_kill(self, capsys):
        console("[test]")({"type": "budget_kill", "spend": 2.50, "trigger": "per_run"})
        out = capsys.readouterr().out
        assert "BUDGET KILLED" in out
        assert "$2.5000" in out

    def test_approval_request(self, capsys):
        console("[test]")({"type": "approval_request", "name": "db_write", "trace_id": "abc12345"})
        out = capsys.readouterr().out
        assert "APPROVAL NEEDED" in out
        assert "db_write" in out

    def test_run_complete(self, capsys):
        console("[test]")({"type": "run_complete", "summary": {"run_spend": 0.50, "call_count": 3}})
        assert "Run complete" in capsys.readouterr().out

    def test_unknown_event(self, capsys):
        console()({"type": "something_new", "data": "test"})
        assert "something_new" in capsys.readouterr().out
