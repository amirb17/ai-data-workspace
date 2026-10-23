"""Central bounded analytical-query policy."""

from dataclasses import dataclass
import os


MIB = 1024 * 1024
GIB = 1024 * MIB


def _bounded_env(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))

    if not minimum <= value <= maximum:
        raise ValueError(f"{name} outside supported governance bounds")

    return value


@dataclass(frozen=True)
class QueryPolicy:
    auto_execute_bytes: int = 256 * MIB
    hard_bytes: int = 1 * GIB

    workspace_daily_bytes: int = 5 * GIB
    project_monthly_bytes: int = 100 * GIB

    default_output_rows: int = 500
    maximum_output_rows: int = 1000

    workspace_concurrency: int = 2
    global_concurrency: int = 4

    def __post_init__(self):
        if not 1 <= self.auto_execute_bytes <= self.hard_bytes <= 1 * GIB:
            raise ValueError("Invalid query byte policy")

        if not self.hard_bytes <= self.workspace_daily_bytes <= 5 * GIB:
            raise ValueError("Invalid workspace daily query budget")

        if not self.workspace_daily_bytes <= self.project_monthly_bytes <= 100 * GIB:
            raise ValueError("Invalid project monthly query budget")

        if not 1 <= self.default_output_rows <= self.maximum_output_rows <= 1000:
            raise ValueError("Invalid result row policy")

        if not 1 <= self.workspace_concurrency <= self.global_concurrency <= 16:
            raise ValueError("Invalid concurrency policy")

    @classmethod
    def from_environment(cls):
        return cls(
            auto_execute_bytes=_bounded_env(
                "QUERY_AUTO_EXECUTE_BYTES",
                256 * MIB,
                1,
                256 * MIB,
            ),
            hard_bytes=_bounded_env(
                "QUERY_HARD_BYTES",
                1 * GIB,
                1,
                1 * GIB,
            ),
            workspace_daily_bytes=_bounded_env(
                "QUERY_WORKSPACE_DAILY_BYTES",
                5 * GIB,
                1,
                5 * GIB,
            ),
            project_monthly_bytes=_bounded_env(
                "QUERY_PROJECT_MONTHLY_BYTES",
                100 * GIB,
                1,
                100 * GIB,
            ),
            default_output_rows=_bounded_env(
                "QUERY_DEFAULT_OUTPUT_ROWS",
                500,
                1,
                500,
            ),
            maximum_output_rows=_bounded_env(
                "QUERY_MAXIMUM_OUTPUT_ROWS",
                1000,
                1,
                1000,
            ),
            workspace_concurrency=_bounded_env(
                "QUERY_WORKSPACE_CONCURRENCY",
                2,
                1,
                4,
            ),
            global_concurrency=_bounded_env(
                "QUERY_GLOBAL_CONCURRENCY",
                4,
                1,
                8,
            ),
        )
