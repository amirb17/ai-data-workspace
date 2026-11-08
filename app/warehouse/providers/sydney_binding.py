
"""Sydney-specific warehouse binding.

Pure immutable metadata. This class does not authorize
execution, acquire credentials, or enable BigQuery.

Production authorization must occur independently before
constructing or using this binding.
"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SydneyBinding:
    project: str
    workspace_id: int
    model_id: int
    version_id: int
    resource_id: int
    generation: int
    approval_sha256: str

    environment: str = "production"
    location: str = "australia-southeast1"

    def __post_init__(self):
        if not re.fullmatch(
            r"[a-z][a-z0-9-]{4,28}[a-z0-9]",
            self.project,
        ):
            raise ValueError("Valid Sydney project required")

        if any(
            type(value) is not int or value <= 0
            for value in (
                self.workspace_id,
                self.model_id,
                self.version_id,
                self.resource_id,
                self.generation,
            )
        ):
            raise ValueError("Positive owned metadata IDs required")

        if not re.fullmatch(
            r"[a-f0-9]{64}",
            self.approval_sha256,
        ):
            raise ValueError("Reviewed approval fingerprint required")

        if (
            self.environment != "production"
            or self.location != "australia-southeast1"
        ):
            raise ValueError("Sydney production binding required")

    @property
    def serving(self):
        return f"dr_prod_w{self.workspace_id}"

    @property
    def staging(self):
        return f"dr_prod_stage_w{self.workspace_id}"

    @property
    def table(self):
        return (
            f"m{self.model_id}_v{self.version_id}"
            f"_g{self.generation}"
        )

    def publication_table(self, publication_id, revision):
        if (
            type(publication_id) is not int
            or publication_id <= 0
            or type(revision) is not int
            or revision <= 0
        ):
            raise ValueError(
                "Positive publication identity required"
            )

        return (
            f"{self.table}"
            f"_p{publication_id}"
            f"_r{revision}"
        )

    def publication_physical(self, publication_id, revision):
        return (
            f"{self.project}."
            f"{self.serving}."
            f"{self.publication_table(publication_id, revision)}"
        )

    def physical(self, suffix, *, serving=False):
        if not re.fullmatch(r"[a-z0-9_]{1,100}", suffix):
            raise ValueError("Backend-generated suffix required")

        dataset = self.serving if serving else self.staging

        return (
            f"{self.project}."
            f"{dataset}."
            f"{self.table}_{suffix}"
        )
