"""Central V1 SQL capabilities and deployment limits; no AI execution boundary."""
from dataclasses import dataclass, asdict
import os


def bound(name, default, ceiling):
    return max(1, min(int(os.getenv('SQL_' + name, default)), ceiling))


@dataclass(frozen=True)
class SqlLimits:
    output_rows: int = bound('OUTPUT_ROWS', 500, 1000)
    output_bytes: int = bound('OUTPUT_BYTES', 1_000_000, 2_000_000)
    input_rows: int = bound('INPUT_ROWS', 250_000, 250_000)
    total_rows: int = bound('TOTAL_ROWS', 500_000, 500_000)
    input_bytes: int = bound('INPUT_BYTES', 128_000_000, 128_000_000)
    artifact_bytes: int = bound('ARTIFACT_BYTES', 64*1024*1024, 64*1024*1024)
    footer_bytes: int = bound('FOOTER_BYTES', 1_000_000, 1_000_000)
    input_columns: int = bound('INPUT_COLUMNS', 128, 128)
    row_groups: int = bound('ROW_GROUPS', 512, 512)
    input_seconds: int = bound('INPUT_SECONDS', 30, 60)
    seconds: int = bound('TIMEOUT_SECONDS', 30, 60)
    text_length: int = bound('TEXT_LENGTH', 20_000, 20_000)
    ast_nodes: int = bound('AST_NODES', 1500, 1500)
    depth: int = bound('DEPTH', 12, 12)
    datasets: int = 2
    history: int = bound('HISTORY', 100, 500)
    saved_queries: int = bound('SAVED_QUERIES', 100, 500)
    memory_mb: int = bound('MEMORY_MB', 256, 512)
    concurrency: int = bound('CONCURRENCY', 2, 4)


LIMITS = SqlLimits()
FUNCTIONS = ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'ABS', 'ROUND', 'CEIL', 'FLOOR',
             'LOWER', 'UPPER', 'LENGTH', 'TRIM', 'COALESCE', 'NULLIF', 'CONCAT',
             'SUBSTRING', 'REPLACE', 'CAST', 'TRY_CAST', 'EXTRACT', 'DATE_TRUNC',
             'ROW_NUMBER', 'RANK', 'DENSE_RANK', 'LAG', 'LEAD', 'FIRST_VALUE', 'LAST_VALUE')


def capabilities():
    return dict(dialect='DuckDB', functions=list(FUNCTIONS), limits=asdict(LIMITS),
                join_policy='One direct current confirmed relationship; child-to-parent lookup only.',
                sensitivity_policy='Fields with current sensitivity hints are unavailable in SQL.',
                result_policy='Ad hoc SQL result; bounded authorized rows, not a trusted metric.')
