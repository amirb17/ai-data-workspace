"""414F0-C: conservative offline BigQuery IAM review, never cloud approval.

Only validates a reviewed permission inventory. Does not infer permissions
from role names, fetch actual IAM policy, call Google APIs or grant roles.
Passing this inspection cannot establish effective IAM or cloud retention.
"""
from dataclasses import dataclass
import re

_READ = frozenset({
    "bigquery.jobs.get",
    "bigquery.tables.getData",
})
_MUTATION = frozenset({
    "bigquery.tables.delete",
    "bigquery.tables.update",
    "bigquery.tables.updateData",
    "bigquery.tables.create",
    "bigquery.tables.createSnapshot",
    "bigquery.tables.deleteSnapshot",
    "bigquery.tables.restoreSnapshot",
    "bigquery.tables.setIamPolicy",
    "bigquery.datasets.create",
    "bigquery.datasets.delete",
    "bigquery.datasets.update",
    "bigquery.datasets.setIamPolicy",
    "bigquery.jobs.create",
    "bigquery.jobs.update",
    "bigquery.jobs.delete",
    "bigquery.routines.create",
    "bigquery.routines.delete",
    "bigquery.routines.update",
    "resourcemanager.projects.setIamPolicy",
})
_BROAD = frozenset({
    "roles/owner","roles/editor","roles/bigquery.admin",
    "roles/bigquery.dataOwner","roles/bigquery.dataEditor",
    "roles/bigquery.user","roles/bigquery.jobUser",
    "roles/bigquery.studioAdmin","roles/iam.securityAdmin",
})


@dataclass(frozen=True)
class ResultIamInventory:
    project: str
    location: str
    principal: str
    job_permissions: frozenset[str]
    destination_table_permissions: frozenset[str]
    other_effective_permissions: frozenset[str]
    role_bindings: frozenset[str]
    job_creator: bool

    def __post_init__(self):
        if (not isinstance(self.project,str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]",self.project)
                or self.location!="asia-south1"
                or not isinstance(self.principal,str)
                or not re.fullmatch(
                    r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com",
                    self.principal,
                )
                or type(self.job_creator) is not bool
                or any(type(v) is not frozenset or any(type(item) is not str or not item for item in v)
                       for v in (
                           self.job_permissions,self.destination_table_permissions,
                           self.other_effective_permissions,self.role_bindings,
                       ))):
            raise ValueError("COMPLETE_REVIEWED_IAM_INVENTORY_REQUIRED")


@dataclass(frozen=True)
class ResultIamPreflight:
    classification: str
    findings: tuple[str,...]
    required_job_permission: str
    required_destination_permission: str = "bigquery.tables.getData"
    cloud_verified: bool = False
    deployment_authorized: bool = False


def inspect_result_reader_iam(inventory: ResultIamInventory) -> ResultIamPreflight:
    """Require only narrowly scoped result-reading capabilities.

    Explicit effective permissions are necessary but not sufficient;
    inherited IAM, deny rules, job ownership, destination-table access,
    credential impersonation and live behavior must be independently tested.
    """
    if type(inventory) is not ResultIamInventory:
        raise TypeError("TYPED_IAM_INVENTORY_REQUIRED")
    # A dedicated reader should not create jobs. Google's creator exception
    # permits jobs.create, but using it would merge query execution and result
    # reading identities, contrary to this reviewed separation.
    required_job="bigquery.jobs.get"
    failures=[]
    if inventory.job_creator:
        failures.append("RESULT_READER_MUST_NOT_BE_JOB_CREATOR")
    if required_job not in inventory.job_permissions:
        failures.append("MISSING_ORIGINAL_JOB_READ")
    if "bigquery.tables.getData" not in inventory.destination_table_permissions:
        failures.append("MISSING_DESTINATION_TABLE_DATA_ACCESS")
    extra_job=inventory.job_permissions-{"bigquery.jobs.get"}
    extra_table=inventory.destination_table_permissions-{"bigquery.tables.getData"}
    extra_other=inventory.other_effective_permissions
    if extra_job or extra_table or extra_other:
        failures.append("UNREVIEWED_EFFECTIVE_PERMISSIONS")
    if inventory.role_bindings & _BROAD:
        failures.append("BROAD_ROLE_BINDING_REQUIRES_REMOVAL")
    if ((inventory.job_permissions | inventory.destination_table_permissions |
         inventory.other_effective_permissions) & _MUTATION):
        failures.append("MUTATION_OR_JOB_SUBMISSION_PERMISSION")
    if not failures:
        failures.append("LIVE_CLOUD_IAM_AND_RETENTION_EVIDENCE_REQUIRED")
    return ResultIamPreflight(
        classification="NO_GO",
        findings=tuple(dict.fromkeys(failures)),
        required_job_permission=required_job,
    )
