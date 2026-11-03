"""The suite checks itself: a test class must never shadow a method of unittest.TestCase.

Found by a mutation check in P4: a helper named `fail` overrode TestCase.fail - the method every failed assertion calls - so every
failed assertion in that class silently ran the helper instead of failing. A guard like this one is cheap and makes the next
accident loud."""
import importlib
import inspect
import pkgutil
import unittest

import tests.integration
import tests.unit

ALLOWED = {"setUp", "tearDown", "setUpClass", "tearDownClass"}                 # the hooks a suite may legitimately override


def test_classes():
    for package in (tests.unit, tests.integration):
        for info in pkgutil.iter_modules(package.__path__):
            module = importlib.import_module(f"{package.__name__}.{info.name}")
            for _, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, unittest.TestCase) and cls.__module__ == module.__name__:
                    yield cls
    from tests import kernel_fixture
    yield kernel_fixture.KernelWorld


class TestHygiene(unittest.TestCase):
    def test_no_test_class_shadows_a_public_method_of_unittest_testcase(self):
        public = {name for name in dir(unittest.TestCase) if not name.startswith("_")} - ALLOWED
        offenders = []
        for cls in test_classes():
            for name in vars(cls):
                if name in public:
                    offenders.append(f"{cls.__module__}.{cls.__name__}.{name}")
        self.assertEqual(offenders, [])

    def test_the_guard_itself_sees_a_shadowing_class(self):
        class Shadow(unittest.TestCase):
            def fail(self, *args):                                   # the very mistake
                pass
        public = {name for name in dir(unittest.TestCase) if not name.startswith("_")} - ALLOWED
        self.assertIn("fail", set(vars(Shadow)) & public)


if __name__ == "__main__":
    unittest.main()
