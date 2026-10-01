from __future__ import annotations

from types import SimpleNamespace

from db_utils import build_run_payload, replace_email_runs_with_logs


class _RpcCall:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return SimpleNamespace(data=self._data)


class _Client:
    def __init__(self, data):
        self.data = data
        self.name = None
        self.params = None

    def rpc(self, name, params):
        self.name = name
        self.params = params
        return _RpcCall(self.data)


def test_build_run_payload_includes_source_run_index() -> None:
    payload, logs = build_run_payload(
        uploader="Steel University",
        file_name="result [Run 2/3]",
        data={
            "Run Information > User Id": "student@yu.ac.kr",
            "Run Information > Date": "21/09/2026 10:00:00",
        },
        logs=[],
        source="gmail",
        email_message_id=7,
        source_run_index=2,
    )

    assert payload["email_message_id"] == 7
    assert payload["source_run_index"] == 2
    assert logs == []


def test_replace_email_runs_calls_atomic_rpc() -> None:
    client = _Client([501, 502])
    entries = [
        {"run_payload": {"source_run_index": 1}, "log_payload": []},
        {"run_payload": {"source_run_index": 2}, "log_payload": []},
    ]

    result = replace_email_runs_with_logs(client, 77, entries)

    assert result == [501, 502]
    assert client.name == "replace_email_runs_with_logs"
    assert client.params["p_email_message_id"] == 77
    assert all(
        entry["run_payload"]["email_message_id"] == 77
        for entry in client.params["run_entries"]
    )
