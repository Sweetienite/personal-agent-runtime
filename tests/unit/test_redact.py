import unittest

from par.core.redact import REDACTED, contains_secret, is_sensitive_key, redact_obj, redact_text

# Fake secrets are assembled at run time so this file never contains a literal key.
SK = "sk-" + "A1b2C3d4" * 4
SK_ANT = "sk-ant-" + "Zy9X8w7V" * 4
GOOGLE = "AIza" + "Sy" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
GHP = "ghp_" + "a1B2c3D4e5" * 4
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
JWT = "eyJhbGciOiJI" + "." + "eyJzdWIiOiIxMjM0" + "." + "SflKxwRJSMeKKF2QT4fw"
PEM = "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END " + "PRIVATE KEY-----"


class VendorTokenTests(unittest.TestCase):
    def test_vendor_keys_are_masked(self):
        for secret in (SK, SK_ANT, GOOGLE, GHP, AWS):
            out = redact_text(f"calling with {secret} now")
            self.assertNotIn(secret, out, secret[:6])
            self.assertIn("[REDACTED:api-key]", out)

    def test_jwt_and_pem(self):
        self.assertEqual(redact_text(f"t={JWT}!"), "t=[REDACTED:jwt]!")
        out = redact_text(f"before\n{PEM}\nafter")
        self.assertEqual(out, "before\n[REDACTED:private-key]\nafter")

    def test_bearer_keeps_scheme_hides_token(self):
        out = redact_text("curl -H 'Bearer abcdef1234567890xyz'")
        self.assertIn("Bearer [REDACTED:token]", out)
        self.assertNotIn("abcdef1234567890xyz", out)

    def test_authorization_and_cookie_header_lines_are_fully_masked(self):
        text = "Authorization: Bearer abcdef1234567890xyz\nCookie: sid=abc; theme=dark\nHost: example.org"
        out = redact_text(text)
        self.assertEqual(out.splitlines()[0], "Authorization: " + REDACTED)
        self.assertEqual(out.splitlines()[1], "Cookie: " + REDACTED)
        self.assertEqual(out.splitlines()[2], "Host: example.org")
        self.assertNotIn("theme=dark", out)


class AssignmentTests(unittest.TestCase):
    def test_env_and_json_forms(self):
        self.assertEqual(redact_text("OPENAI_API_KEY=abc123"), f"OPENAI_API_KEY={REDACTED}")
        self.assertEqual(redact_text('{"api_key": "abc123", "model": "m1"}'), f'{{"api_key": {REDACTED}, "model": "m1"}}')
        self.assertEqual(redact_text("password: hunter2"), f"password: {REDACTED}")
        self.assertEqual(redact_text("export ANTHROPIC_AUTH_TOKEN='abc xyz'"), f"export ANTHROPIC_AUTH_TOKEN={REDACTED}")

    def test_non_secret_look_alikes_survive(self):
        for text in ("max_tokens=100", "token_count: 5", "tokenizer=bpe", "model: gpt", "keys=3", "PATH=/usr/bin"):
            self.assertEqual(redact_text(text), text, text)

    def test_placeholders_and_references_survive(self):
        for text in ("api_key=$OPENAI_API_KEY", "password=<your-password>", "token=[REDACTED]", "secret: null", "cookie=false"):
            self.assertEqual(redact_text(text), text, text)


class IdentifierTests(unittest.TestCase):
    def test_email_is_masked(self):
        self.assertEqual(redact_text("owner a.b+c@example.co.uk here"), "owner [REDACTED:email] here")

    def test_git_sha_and_uuid_survive(self):
        sha = "2e402f751ee1a3b9c0d4e5f60718293a4b5c6d7e"
        uuid = "123e4567-e89b-12d3-a456-426614174000"
        self.assertEqual(redact_text(f"HEAD {sha} id {uuid}"), f"HEAD {sha} id {uuid}")

    def test_opaque_mixed_case_blob_is_masked(self):
        blob = "Qw3rTy" * 7  # 42 chars, upper+lower+digit
        self.assertEqual(redact_text(f"x {blob} y"), "x [REDACTED:token] y")

    def test_home_is_shortened_only_at_a_path_boundary(self):
        self.assertEqual(redact_text("/Users/me/Documents/x", home="/Users/me"), "~/Documents/x")
        self.assertEqual(redact_text("/Users/me2/x", home="/Users/me"), "/Users/me2/x")
        self.assertEqual(redact_text("/Users/me.old/x", home="/Users/me"), "/Users/me.old/x")
        self.assertEqual(redact_text("at /Users/me.", home="/Users/me"), "at ~.")
        self.assertEqual(redact_text("see /Users/me, ok", home="/Users/me"), "see ~, ok")


class StructureTests(unittest.TestCase):
    def test_idempotent(self):
        samples = [
            f"{SK} {GHP} a@b.io Authorization: Bearer abcdef1234567890xyz",
            'api_key="v" password=p token=t',
            PEM,
            "plain text with nothing",
        ]
        for s in samples:
            once = redact_text(s)
            self.assertEqual(redact_text(once), once, s[:20])

    def test_contains_secret(self):
        self.assertTrue(contains_secret(SK))
        self.assertTrue(contains_secret("password=zzz"))
        self.assertTrue(contains_secret("someone@example.com"))
        self.assertFalse(contains_secret("model = gpt-x, max_tokens = 10"))
        self.assertFalse(contains_secret(None))

    def test_sensitive_key_names(self):
        for k in ("token", "access_token", "AUTH_TOKEN", "api_key", "x-api-key", "secret_key", "Password", "cookie"):
            self.assertTrue(is_sensitive_key(k), k)
        for k in ("max_tokens", "token_count", "tokenizer", "model", "enabled", "keys"):
            self.assertFalse(is_sensitive_key(k), k)

    def test_redact_obj_nested(self):
        obj = {
            "model": "m1",
            "auth": {"api_key": "abc", "token": 12345, "enabled": True, "password": None},
            "items": [{"note": f"see {SK}"}, ("a@b.io", 3)],
            "count_tokens": 7,
        }
        out = redact_obj(obj)
        self.assertEqual(out["model"], "m1")
        self.assertEqual(out["auth"]["api_key"], REDACTED)
        self.assertEqual(out["auth"]["token"], REDACTED)
        self.assertIs(out["auth"]["enabled"], True)
        self.assertIsNone(out["auth"]["password"])
        self.assertIn("[REDACTED:api-key]", out["items"][0]["note"])
        self.assertEqual(out["items"][1], ["[REDACTED:email]", 3])
        self.assertEqual(out["count_tokens"], 7)
        self.assertEqual(obj["auth"]["api_key"], "abc", "input must not be mutated")

    def test_non_string_passthrough(self):
        self.assertEqual(redact_text(5), 5)
        self.assertEqual(redact_obj(3.5), 3.5)


if __name__ == "__main__":
    unittest.main()
