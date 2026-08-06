"""The in-process backend: no external dependency, for CI and local dev."""

from adopt_workflow.inproc.client import InProcessStepContext, InProcessWorkflowClient
from adopt_workflow.inproc.journal import Journal

__all__ = ["InProcessStepContext", "InProcessWorkflowClient", "Journal"]
