from zchat_client.verification.comparison import normalize_text, texts_equal


def test_normalize_unicode():
    assert normalize_text("  a  b  ") == "a b"
    assert texts_equal("Testing   ZChat", "Testing ZChat") is True
    assert texts_equal("Thai", "English") is False
