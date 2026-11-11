"""414F5: BigQuery-shaped first-page transport bridge, no cloud credentials.

Wraps explicitly supplied fake getQueryResults into the existing bounded
result-service contract. Caller scope comes solely from settled job metadata.
"""
from dataclasses import dataclass

from app.warehouse.query_bigquery_first_page_adapter import (
    BigQueryResultTransport,validate_bigquery_first_page,
)


@dataclass(frozen=True)
class SimulatedBigQueryFirstPage:
    """Only internal tests may instantiate with a fake provider transport."""
    provider: BigQueryResultTransport
    approved_schema: tuple[tuple[str,str], ...]

    def get_first_page_once(self, *, project: str, location: str,
                            job_id: str, max_results: int) -> dict:
        raw=self.provider.get_query_results_once(
            project=project,location=location,
            job_id=job_id,max_results=max_results,
        )
        page=validate_bigquery_first_page(
            project=project,location=location,job_id=job_id,
            approved_schema=self.approved_schema,requested_rows=max_results,
            raw=raw,
        )
        return {
            "job_id":page.job_id,
            "schema":[{"name":name,"type":kind} for name,kind in self.approved_schema],
            "rows":[list(row) for row in page.rows],
            "next_page_token":page.next_page_token,
        }
