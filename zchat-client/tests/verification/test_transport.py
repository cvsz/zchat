from zchat_client.verification.transport import EvidenceTransport, TransportResult


def test_transport_retry():
    calls = {"n": 0}

    def sender(url, payload, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return TransportResult(ok=False, error="fail")
        return TransportResult(ok=True, status_code=200)

    transport = EvidenceTransport("http://localhost", max_retries=3)
    result = transport.submit("/x", {"a": 1}, sender)
    assert result.ok is True
    assert calls["n"] == 3
