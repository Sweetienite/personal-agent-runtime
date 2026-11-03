"""Redaction must stay linear: the text it reads is output a Worker produced, so one odd or hostile line must not be able to stall the Kernel.

P6 finding: several patterns restarted an unbounded scan at every position (an e-mail local part, an assignment key, a lookahead over the rest of
a run, a lazy PEM block), so a 200 KB run of letters or a few thousand "-----BEGIN" markers took many seconds. The patterns are now bounded or
done by hand; this file feeds each of them text built to be the worst case."""
import signal
import time
import unittest

from par.core.redact import redact_obj, redact_text

N = 300_000
LIMIT = 4.0          # seconds; the linear implementation needs well under one, the quadratic one needed minutes
GIVE_UP = 12         # seconds; a quadratic regression ends here instead of hanging the suite

ADVERSARIAL = {
    "a long run of letters": "a" * N,
    "a long mixed run": "aA1" * (N // 3),
    "hyphens": "-" * N,
    "dots": "a." * (N // 2),
    "at signs": "a@" * (N // 2),
    "one address with a very long domain": "x@" + "y." * (N // 2),
    "very long local parts": ("a" * 60 + "@") * (N // 61),
    "assignments": "key=" * (N // 4),
    "empty assignments with spaces": "a: " * (N // 3),
    "quotes": 'a="' * (N // 3),
    "bearer words": "Bearer " * (N // 7),
    "authorization headers": "Authorization: " * (N // 15),
    "vendor prefixes": "sk-" * (N // 3) + "ghp_" * (N // 4) + "xoxb-" * (N // 5) + "github_pat_" * (N // 11),
    "token-like dotted words": "eyJ." * (N // 4),
    "token-like hyphenated words": "eyJ-" * (N // 4),
    "private key markers, all closed": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n" * (N // 60),
    "private key markers, none closed": "-----BEGIN RSA PRIVATE KEY-----" * (N // 31),
    "one private key marker and a huge tail": "-----BEGIN RSA PRIVATE KEY-----" + "A" * N,
    "spaces": " " * N,
    "words": "ab " * (N // 3),
}


class Alarm:
    """Turn a hang into a failure: after ``seconds`` the block raises instead of running on."""

    def __init__(self, seconds):
        self.seconds = seconds

    def __enter__(self):
        signal.signal(signal.SIGALRM, self._fire)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)

    def __exit__(self, *exc):
        signal.setitimer(signal.ITIMER_REAL, 0)
        return False

    @staticmethod
    def _fire(*_):
        raise AssertionError(f"redaction was still running after {GIVE_UP} s: a pattern has become quadratic")


class StaysLinear(unittest.TestCase):
    def test_every_adversarial_text_is_redacted_quickly(self):
        for name, text in ADVERSARIAL.items():
            with self.subTest(name):
                started = time.monotonic()
                with Alarm(GIVE_UP):
                    redact_text(text)
                self.assertLess(time.monotonic() - started, LIMIT, name)

    def test_a_structure_of_long_strings_is_just_as_quick(self):
        started = time.monotonic()
        with Alarm(GIVE_UP):
            redact_obj({"a": ["x" * N], "b": {"c": "y@" * (N // 4)}, "d": "-----BEGIN PRIVATE KEY-----" * (N // 40)})
        self.assertLess(time.monotonic() - started, LIMIT)


class StillRedacts(unittest.TestCase):
    """The rewrite changes how patterns are applied, not what they catch."""

    def test_a_complete_private_key_block_is_replaced_and_its_surroundings_stay(self):
        text = "before\n-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nmore\n-----END RSA PRIVATE KEY-----\nafter"
        self.assertEqual(redact_text(text), "before\n[REDACTED:private-key]\nafter")

    def test_two_blocks_and_text_between_them(self):
        block = "-----BEGIN PRIVATE KEY-----\nAAA\n-----END PRIVATE KEY-----"
        self.assertEqual(redact_text(f"1 {block} 2 {block} 3"), "1 [REDACTED:private-key] 2 [REDACTED:private-key] 3")

    def test_a_block_that_was_cut_short_is_redacted_to_the_end_of_what_could_be_key_material(self):
        text = "head -----BEGIN PRIVATE KEY-----\n" + "A" * 100 + "\n" + "tail text " * 2000
        out = redact_text(text)
        self.assertTrue(out.startswith("head [REDACTED:private-key]"))
        self.assertNotIn("AAAAAAAAAA", out)
        self.assertTrue(out.endswith("tail text "))                                              # text far past the key is not swallowed
        self.assertLess(len(out), len(text))

    def test_text_that_only_mentions_the_marker_in_passing_keeps_what_follows_a_closed_block(self):
        text = "docs say -----BEGIN PRIVATE KEY----- then -----END PRIVATE KEY----- and the rest is fine"
        self.assertEqual(redact_text(text), "docs say [REDACTED:private-key] and the rest is fine")

    def test_the_other_patterns_still_catch_what_they_did(self):
        secret = "sk-proj-abcdefghijklmnopqrstuvwxyz012345"
        samples = {
            "password=hunter2": "password=[REDACTED]",
            f"openai {secret}": "openai [REDACTED:api-key]",
            "mail me@example.com now": "mail [REDACTED:email] now",
            "mail first.last+tag@sub.example.co.uk now": "mail [REDACTED:email] now",
            "Authorization: Bearer abcdefghijklmnop1234": "Authorization: [REDACTED]",
            "blob a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8 end": "blob [REDACTED:token] end",
        }
        for source, expected in samples.items():
            self.assertEqual(redact_text(source), expected, source)

    def test_hex_hashes_and_uuids_are_not_mistaken_for_secrets(self):
        keep = "commit 0123456789abcdef0123456789abcdef01234567 id 123e4567-e89b-12d3-a456-426614174000"
        self.assertEqual(redact_text(keep), keep)

    def test_a_jwt_is_still_caught_even_when_it_has_hyphens_and_underscores(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        self.assertEqual(redact_text("token " + jwt + " end"), "token [REDACTED:jwt] end")

    def test_redaction_is_idempotent(self):
        for text in ("password=hunter2 mail me@example.com", "-----BEGIN PRIVATE KEY-----\nA\n-----END PRIVATE KEY-----", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"):
            once = redact_text(text)
            self.assertEqual(redact_text(once), once)


if __name__ == "__main__":
    unittest.main()
