import unittest

from par.context.harness import HarnessContextAdapter
from par.context.interface import Context
from par.context.resolver import default_context
from par.context.types import ContextManifest, FileFacts, ProjectView, WriteReadiness
from par.core.kernel import Refused
from tests.kernel_fixture import KernelWorld, r1_contract


def _view(root: str) -> ProjectView:
    return ProjectView(id="fixture", root=root, exists=True,
                       contracts=(FileFacts(".ai/tasks/active/TASK-1.md", 10, "0" * 64),),
                       git={"is_repo": True, "head_full": "a" * 40, "branch": "main", "modified": 0, "staged": 0,
                            "untracked": 0, "dirty_digest": "d" * 64})


def _manifest(root: str) -> ContextManifest:
    return ContextManifest(1, "fixture", root, "2026-11-06T00:00:00",
                           {"vcs": "git", "head": "a" * 40, "branch": "main",
                            "dirty": {"modified": 0, "staged": 0, "untracked": 0}, "dirty_digest": "d" * 64},
                           (), {}, (), 0, "e" * 64)


class FakeContext:
    def __init__(self, view: ProjectView, manifest: ContextManifest) -> None:
        self._view, self._manifest = view, manifest
        self.calls: "list[str]" = []

    def view(self) -> ProjectView:
        self.calls.append("view")
        return self._view

    def manifest(self, view: "ProjectView | None" = None) -> ContextManifest:
        self.calls.append("manifest")
        return self._manifest

    def render(self, manifest: ContextManifest) -> str:
        self.calls.append("render")
        return ""

    def write_readiness(self, view: "ProjectView | None" = None, *, assume_enabled: bool = False) -> WriteReadiness:
        self.calls.append("write_readiness")
        return WriteReadiness(True, ())


class TheDefaultContext(KernelWorld):
    default_files = None

    def test_default_context_is_the_harness_adapter_with_the_same_manifest_digest(self):
        project = self.projects["fixture"]
        direct = HarnessContextAdapter(project, self.home)
        resolved = default_context(project, self.home)
        self.assertIsInstance(resolved, HarnessContextAdapter)
        self.assertIsInstance(resolved, Context)
        self.assertEqual(resolved.manifest().digest, direct.manifest().digest)

    def test_an_object_missing_part_of_the_contract_is_not_a_context(self):
        class Half:
            def view(self):
                return None

            def manifest(self, view=None):
                return None

        self.assertNotIsInstance(Half(), Context)


class TheKernelTalksOnlyThroughTheInterface(KernelWorld):
    default_files = None

    def test_validate_uses_the_injected_context_and_nothing_else(self):
        fake = FakeContext(_view(str(self.project_path)), _manifest(str(self.project_path)))
        kernel = self.new_kernel(context_factory=lambda project: fake)
        kernel.validate(r1_contract())
        self.assertEqual(fake.calls, ["view", "manifest", "write_readiness"])

    def test_a_factory_returning_something_that_is_not_a_context_is_refused_by_name(self):
        kernel = self.new_kernel(context_factory=lambda project: object())
        with self.assertRaises(Refused) as caught:
            kernel.validate(r1_contract())
        self.assertEqual(caught.exception.code, "context_invalid")

    def test_a_context_failure_is_not_swallowed_and_no_task_is_created(self):
        class Broken(FakeContext):
            def manifest(self, view: "ProjectView | None" = None) -> ContextManifest:
                raise RuntimeError("the context could not be built")

        kernel = self.new_kernel(context_factory=lambda project: Broken(_view(str(self.project_path)), _manifest(str(self.project_path))))
        with self.assertRaises(RuntimeError):
            kernel.submit(r1_contract())
        self.assertEqual(kernel.store.list_tasks(), [])


if __name__ == "__main__":
    unittest.main()
