from server.api.routers.tasks import _stream_created_at


def test_stream_created_at_converts_redis_timestamp():
    assert _stream_created_at("1784204897722-0") == ("2026-07-16T12:28:17.722000+00:00")


def test_stream_created_at_rejects_invalid_stream_id():
    assert _stream_created_at("invalid") is None
