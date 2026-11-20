"""Deployment settings for `smeltery serve` and `smeltery worker` (and the Temporal flags of `task-worker`,
`temporal-worker` and `run --temporal`), resolved in ONE place.

Precedence, highest first: a command-line flag, a `SMELTERY_*` environment variable, the default below. `resolve` is
pure: an environment mapping and the flags' values in, a frozen `Settings` out, nothing read from `os.environ` and
nothing started. A bad value raises `SettingsError` naming the variable (or the flag) that carried it; callers print
it and exit 2.

| Variable | Default | Meaning |
|---|---|---|
| SMELTERY_BACKEND | local | `local` (SQLite + subprocess supervisors) or `temporal` |
| SMELTERY_TEMPORAL_ADDRESS | localhost:7233 | Temporal frontend, host:port |
| SMELTERY_TEMPORAL_NAMESPACE | default | Temporal namespace |
| SMELTERY_TASK_QUEUE | smeltery-tasks | queue for Bench's shell tasks |
| SMELTERY_PIPELINE_QUEUE | smeltery-pipeline | queue for the SCF legs of `run --temporal` |
| SMELTERY_HOST / SMELTERY_PORT | Bench's own (127.0.0.1 / 8765) | `serve` bind address |
| SMELTERY_ROOT | Bench's own root | workspace directory |
| SMELTERY_WORKERS | 2 | pipeline SCFs at once (Temporal) |
| SMELTERY_THREADS_PER_WORKER | 1 | RAYON_NUM_THREADS per SCF |
| SMELTERY_TASK_CONCURRENCY | 2 | concurrent shell tasks per task worker (Temporal) |
| SMELTERY_MAX_RUNS | 1 | launched runs at once (local backend) |
| SMELTERY_MAX_CPU_HOURS | unset | ceiling on a screen's predicted CPU-hours (`smeltery mcp`); unset = none |
| SMELTERY_MAX_CANDIDATES | unset | ceiling on a screen's candidate count (`smeltery mcp`); unset = none |

The last two are CEILINGS, not sizes: unset means nothing is capped, and a screen above either one is refused
unless the caller passes `confirm=True` (docs/mcp.md).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .temporal_defaults import DEFAULT_NAMESPACE
from .temporal_defaults import PIPELINE_QUEUE as DEFAULT_PIPELINE_QUEUE
from .temporal_defaults import TASKS_QUEUE as DEFAULT_TASK_QUEUE

BACKENDS = ("local", "temporal")
DEFAULT_TEMPORAL_ADDRESS = "localhost:7233"


class SettingsError(ValueError):
    """A setting's value is unusable; the message names the variable (or flag) that supplied it."""


@dataclass(frozen=True)
class Settings:
    backend: str = "local"
    temporal_address: str = DEFAULT_TEMPORAL_ADDRESS
    namespace: str = DEFAULT_NAMESPACE
    task_queue: str = DEFAULT_TASK_QUEUE
    pipeline_queue: str = DEFAULT_PIPELINE_QUEUE
    host: str | None = None  # None: `smeltery bench` resolves it (SMELTERY_BENCH_HOST, else 127.0.0.1)
    port: int | None = None  # None: SMELTERY_BENCH_PORT, else 8765
    root: str | None = None  # None: SMELTERY_BENCH_ROOT, else the per-user data directory
    workers: int = 2
    threads_per_worker: int = 1
    task_concurrency: int = 2
    max_runs: int = 1
    # Ceilings for the MCP screening surface (docs/mcp.md). None = no ceiling, which is why they are not
    # numbers with a default: "unset" and "set to something large" are different answers to a caller asking
    # whether a screen is over budget.
    max_cpu_hours: float | None = None
    max_candidates: int | None = None


# field -> (environment variable, command-line flag shown in errors, kind)
_FIELDS: dict[str, tuple[str, str, str]] = {
    "backend": ("SMELTERY_BACKEND", "--backend", "backend"),
    "temporal_address": ("SMELTERY_TEMPORAL_ADDRESS", "--host/--temporal-address", "address"),
    "namespace": ("SMELTERY_TEMPORAL_NAMESPACE", "--namespace", "text"),
    "task_queue": ("SMELTERY_TASK_QUEUE", "--task-queue", "text"),
    "pipeline_queue": ("SMELTERY_PIPELINE_QUEUE", "--task-queue", "text"),
    "host": ("SMELTERY_HOST", "--host", "text"),
    "port": ("SMELTERY_PORT", "--port", "port"),
    "root": ("SMELTERY_ROOT", "ROOT", "text"),
    "workers": ("SMELTERY_WORKERS", "--workers", "positive"),
    "threads_per_worker": ("SMELTERY_THREADS_PER_WORKER", "--threads-per-worker", "positive"),
    "task_concurrency": ("SMELTERY_TASK_CONCURRENCY", "--concurrency", "positive"),
    "max_runs": ("SMELTERY_MAX_RUNS", "--max-runs", "positive"),
    "max_cpu_hours": ("SMELTERY_MAX_CPU_HOURS", "--max-cpu-hours", "positive_float"),
    "max_candidates": ("SMELTERY_MAX_CANDIDATES", "--max-candidates", "positive"),
}
ENV_VARS = tuple(v[0] for v in _FIELDS.values())


def _coerce(kind: str, raw: object, label: str) -> object:
    if kind == "backend":
        value = str(raw).strip().lower()
        if value not in BACKENDS:
            raise SettingsError(f"{label}={raw!r} is not a backend; use one of: {', '.join(BACKENDS)}")
        return value
    if kind == "positive_float":
        try:
            x = float(str(raw).strip())
        except ValueError:
            raise SettingsError(f"{label}={raw!r} is not a number") from None
        if not x > 0:
            raise SettingsError(f"{label}={raw!r} must be greater than 0")
        return x
    if kind in ("positive", "port"):
        try:
            n = int(str(raw).strip())
        except ValueError:
            raise SettingsError(f"{label}={raw!r} is not an integer") from None
        if kind == "port" and not 1 <= n <= 65535:
            raise SettingsError(f"{label}={raw!r} is not a port number (1-65535)")
        if n < 1:
            raise SettingsError(f"{label}={raw!r} must be at least 1")
        return n
    value = str(raw).strip()
    if not value:
        raise SettingsError(f"{label} is set but empty; unset it to use the default")
    if kind == "address" and (":" not in value or value.startswith(":") or value.endswith(":")):
        raise SettingsError(f"{label}={raw!r} must be host:port, e.g. {DEFAULT_TEMPORAL_ADDRESS}")
    return value


def resolve(env: Mapping[str, str], **flags: object) -> Settings:
    """Settings from `env` (a mapping, normally `os.environ`) and flag values; a flag of None is "not given".

    Raises SettingsError for a bad value and TypeError for a field name that is not a setting (a caller bug)."""
    unknown = sorted(set(flags) - set(_FIELDS))
    if unknown:
        raise TypeError(f"not a setting: {', '.join(unknown)}")
    values: dict[str, object] = {}
    for name, (var, flag, kind) in _FIELDS.items():
        if flags.get(name) is not None:
            values[name] = _coerce(kind, flags[name], flag)
        elif var in env:
            values[name] = _coerce(kind, env[var], var)
    return Settings(**values)  # type: ignore[arg-type]
