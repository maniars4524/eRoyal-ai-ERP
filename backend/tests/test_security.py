from app.security import hash_password, verify_password


def test_password_hash_is_not_plaintext():
    password = "A-secure-password-123!"
    encoded = hash_password(password)
    assert encoded != password
    assert verify_password(password, encoded)
    assert not verify_password("wrong", encoded)
