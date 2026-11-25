"""Minimal validated deployment manifest model."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass(frozen=True, slots=True)
class Stack:
    stack_id: str
    template: Path
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Step:
    step_id: str
    handler: str
    before: tuple[str, ...]
    after: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Manifest:
    path: Path
    name: str
    stack_name_pattern: str
    stacks: tuple[Stack, ...]
    steps: tuple[Step, ...]

    @property
    def stack_ids(self) -> tuple[str, ...]:
        positions = {item.stack_id: index for index, item in enumerate(self.stacks)}
        deps = {item.stack_id: set(item.depends_on) for item in self.stacks}
        dependents = {item.stack_id: [] for item in self.stacks}
        for item in self.stacks:
            for dependency in item.depends_on:
                dependents[dependency].append(item.stack_id)
        ready = [(positions[item], item) for item, required in deps.items() if not required]
        heapq.heapify(ready)
        result: list[str] = []
        while ready:
            _, item = heapq.heappop(ready)
            result.append(item)
            for dependent in dependents[item]:
                deps[dependent].discard(item)
                if not deps[dependent]:
                    heapq.heappush(ready, (positions[dependent], dependent))
        if len(result) != len(self.stacks):
            raise ValueError("deployment manifest contains a dependency cycle")
        return tuple(result)

    def stack(self, stack_id: str) -> Stack:
        for item in self.stacks:
            if item.stack_id == stack_id:
                return item
        raise ValueError(f"unknown stack: {stack_id}")

    def stack_name(self, environment: str, stack_id: str) -> str:
        self.stack(stack_id)
        return self.stack_name_pattern.format(name=self.name, environment=environment, id=stack_id)

    def selected(self, stack: str | None = None, start: str | None = None) -> tuple[str, ...]:
        if stack and start:
            raise ValueError("--stack and --from cannot be combined")
        if stack:
            self.stack(stack)
            return (stack,)
        if not start:
            return self.stack_ids
        self.stack(start)
        selected = {start}
        changed = True
        while changed:
            changed = False
            for item in self.stacks:
                if item.stack_id not in selected and any(dep in selected for dep in item.depends_on):
                    selected.add(item.stack_id)
                    changed = True
        return tuple(item for item in self.stack_ids if item in selected)

    def dependencies(self, stack_id: str) -> tuple[str, ...]:
        needed: set[str] = set()

        def visit(current: str) -> None:
            for dependency in self.stack(current).depends_on:
                if dependency not in needed:
                    needed.add(dependency)
                    visit(dependency)

        visit(stack_id)
        return tuple(item for item in self.stack_ids if item in needed)


def load_manifest(path: str | Path) -> Manifest:
    target = Path(path).resolve()
    if yaml is None:
        raise RuntimeError("PyYAML is required to load the deployment manifest")
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("deployment manifest must be an object")
    raw_stacks = raw.get("stacks") or []
    stacks = tuple(
        Stack(str(item["id"]), (target.parent / str(item["template"])).resolve(), tuple(item.get("depends_on") or ()))
        for item in raw_stacks
    )
    known = {item.stack_id for item in stacks}
    if len(known) != len(stacks):
        raise ValueError("deployment stack IDs must be unique")
    for item in stacks:
        if not item.template.is_file():
            raise FileNotFoundError(item.template)
        if any(dependency not in known for dependency in item.depends_on):
            raise ValueError(f"unknown dependency for {item.stack_id}")
    steps = tuple(
        Step(str(item["id"]), str(item["handler"]), tuple(item.get("before") or ()), tuple(item.get("after") or ()))
        for item in raw.get("steps") or []
    )
    manifest = Manifest(target, str(raw.get("name") or "opsfusion"), str(raw.get("stack_name_pattern") or "{name}-{environment}-{id}"), stacks, steps)
    _ = manifest.stack_ids
    return manifest
