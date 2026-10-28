import unittest

from app.security import ACCESS_TOKEN_EXPIRE_SECONDS, create_token, decode_token


class TokenExpiryTests(unittest.TestCase):
    def test_jwt_expires_in_one_hour(self):
        self.assertEqual(ACCESS_TOKEN_EXPIRE_SECONDS, 3600)
        claims = decode_token(create_token(123))
        self.assertEqual(claims["exp"] - claims["iat"], 3600)
        self.assertEqual(claims["sub"], "123")
