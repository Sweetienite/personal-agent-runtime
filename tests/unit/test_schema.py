import copy
import json
import re
import unittest

from par import schema as S
from tests.world import sample_contract, sample_result


class MiniValidator(unittest.TestCase):
    def test_types_and_bool_is_not_a_number(self):
        self.assertEqual(S.validate(1, {"type": "integer"}), [])
        self.assertTrue(S.validate(True, {"type": "integer"}))
        self.assertTrue(S.validate(True, {"type": "number"}))
        self.assertTrue(S.validate(1.5, {"type": "integer"}))
        self.assertEqual(S.validate(1.5, {"type": "number"}), [])
        self.assertEqual(S.validate(None, {"type": ["string", "null"]}), [])
        self.assertTrue(S.validate(3, {"type": ["string", "null"]}))
        self.assertTrue(S.validate("x", {"type": "array"}))

    def test_const_and_enum_are_type_strict(self):
        self.assertEqual(S.validate(1, {"const": 1}), [])
        self.assertTrue(S.validate(True, {"const": 1}))
        self.assertTrue(S.validate("1", {"enum": [1, 2]}))
        self.assertEqual(S.validate("a", {"enum": ["a", "b"]}), [])

    def test_strings_numbers_arrays(self):
        self.assertTrue(S.validate("ab", {"minLength": 3}))
        self.assertTrue(S.validate("abcd", {"maxLength": 3}))
        self.assertTrue(S.validate("abc", {"pattern": "^[0-9]+$"}))
        self.assertTrue(S.validate(0, {"minimum": 1}))
        self.assertTrue(S.validate(9, {"maximum": 5}))
        self.assertTrue(S.validate([], {"minItems": 1}))
        self.assertTrue(S.validate([1, 2], {"maxItems": 1}))
        self.assertTrue(S.validate([1, 1], {"uniqueItems": True}))
        self.assertEqual(S.validate([1, 2], {"uniqueItems": True, "items": {"type": "integer"}}), [])
        self.assertEqual(S.validate([1, "x"], {"items": {"type": "integer"}}), ["$[1]: expected integer, found str"])

    def test_objects(self):
        schema = {"type": "object", "required": ["a"], "additionalProperties": False, "properties": {"a": {"type": "string"}}}
        self.assertEqual(S.validate({"a": "x"}, schema), [])
        self.assertEqual(sorted(S.validate({"b": 1}, schema)), ["$: missing required 'a'", "$: unexpected 'b'"])
        free = {"additionalProperties": {"type": "integer"}}
        self.assertEqual(S.validate({"x": 1}, free), [])
        self.assertTrue(S.validate({"x": "no"}, free))

    def test_references_and_paths(self):
        schema = {"definitions": {"n": {"type": "integer"}}, "properties": {"v": {"type": "array", "items": {"$ref": "#/definitions/n"}}}}
        self.assertEqual(S.validate({"v": [1, 2]}, schema), [])
        self.assertEqual(S.validate({"v": [1, "x"]}, schema), ["$.v[1]: expected integer, found str"])
        with self.assertRaises(ValueError):
            S.validate(1, {"$ref": "http://elsewhere/x.json"})

    def test_unknown_keywords_are_ignored(self):
        self.assertEqual(S.validate(1, {"type": "integer", "format": "whatever", "x-note": "hi"}), [])


class ShippedSchemas(unittest.TestCase):
    def test_all_three_load_and_every_reference_resolves(self):
        for name in ("task", "result", "event"):
            schema = S.load_schema(name)
            text = json.dumps(schema)
            for ref in re.findall(r'"\$ref": "([^"]+)"', text):
                S._resolve(ref, schema)

    def test_valid_documents(self):
        self.assertEqual(S.check(sample_contract(), "task"), [])
        self.assertEqual(S.check(sample_result(), "result"), [])
        event = {"id": 1, "ts": "2026-10-14T03:00:00.000Z", "task_id": "fixture-0001", "attempt": None,
                 "type": "STATUS_CHANGED", "from_status": "QUEUED", "to_status": "STARTING", "reason": "x", "data": {"version": 3}}
        self.assertEqual(S.check(event, "event"), [])

    def test_contract_rejections(self):
        base = sample_contract()
        bad = {
            "project id with capitals": {"project_id": "Fixture"},
            "risk out of range": {"risk": "R9"},
            "missing idempotency key": {"idempotency_key": None},
            "key with spaces": {"idempotency_key": "has space"},
            "empty title": {"title": ""},
            "description too long": {"description": "x" * 20001},
            "too many paths": {"allowed_paths": ["a/"] * 51},
            "unknown top-level key": {"surprise": 1},
            "check without argv": {"acceptance": {"checks": [{"name": "unit"}]}},
            "check timeout too large": {"acceptance": {"checks": [{"name": "unit", "argv": ["x"], "timeout": 9999}]}},
            "check name with capitals": {"acceptance": {"checks": [{"name": "Unit", "argv": ["x"]}]}},
            "bad dependency id": {"dependencies": ["not an id"]},
            "duplicate dependencies": {"dependencies": ["fixture-0001", "fixture-0001"]},
            "timeout out of range": {"timeouts": {"startup": 0}},
            "wrong schema version": {"schema_version": 2},
        }
        for label, change in bad.items():
            doc = copy.deepcopy(base)
            for k, v in change.items():
                if v is None:
                    doc.pop(k, None)
                else:
                    doc[k] = v
            self.assertTrue(S.check(doc, "task"), label)

    def test_result_rejections(self):
        bad = {
            "worker statement marked verified": {"summary_verified": True},
            "summary too long": {"summary": "x" * 1201},
            "bad test status": {"tests": [{"name": "u", "status": "MAYBE", "exit_code": 0, "duration_s": 0}]},
            "negative diff": {"diff_stat": {"files": -1, "additions": 0, "deletions": 0}},
            "unknown outcome": {"outcome": "great"},
            "bad task id": {"task_id": "T1"},
            "attempt zero": {"attempt": 0},
            "extra key": {"claimed_tests": "PASS"},
        }
        for label, change in bad.items():
            self.assertTrue(S.check(sample_result(**change), "result"), label)
        doc = sample_result()
        del doc["boundary"]
        self.assertTrue(S.check(doc, "result"))


if __name__ == "__main__":
    unittest.main()
