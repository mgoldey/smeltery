"""Port: the workspace's default for each method's settings.

A default is COPIED into a step when the method is added to a pipeline (`Pipeline.add(..., defaults=...)`) and is
never read at run time, so a run can always be redone from its own directory. The shape is
`{method_key: {param_name: value}}`.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MethodDefaults(Protocol):
    def all(self) -> dict[str, dict[str, Any]]:
        """Every method with at least one default set. Empty when nothing was ever set."""
        ...

    def get(self, method_key: str) -> dict[str, Any]:
        """The defaults of one method; empty when it has none."""
        ...

    def put(self, method_key: str, values: dict[str, Any]) -> None:
        """Replace the method's defaults with `values`; an empty dict clears them."""
        ...
