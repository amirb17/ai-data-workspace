"""Proposed reviewed Sydney contract only; existing Mumbai adapters unchanged.

No environment loader, credentials, resource provisioning or enabled registry.
Real project, dataset and identities require separate approval in 158E2.
"""
from dataclasses import dataclass
import re
from app.warehouse.accepted_changes import digest


@dataclass(frozen=True,slots=True)
class Approval:
    project:str
    workspace_id:int
    loader_identity:str
    query_identity:str
    revision:int
    aws_region:str='ap-southeast-2'
    location:str='australia-southeast1'
    environment:str='production'

    def __post_init__(self):
        if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]',self.project):raise ValueError('Reviewed project required')
        if self.aws_region!='ap-southeast-2' or self.location!='australia-southeast1' or self.environment!='production':raise ValueError('Explicit Sydney production profile required')
        if any(type(v) is not int or v<=0 for v in (self.workspace_id,self.revision)):raise ValueError('Reviewed scope/revision required')
        for identity in (self.loader_identity,self.query_identity):
            if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]@'+re.escape(self.project)+r'\.iam\.gserviceaccount\.com',identity):raise ValueError('Project-owned role identity required')
        if self.loader_identity==self.query_identity:raise ValueError('Loader/query identities must differ')

    @property
    def serving_dataset(self):return f'dr_prod_w{self.workspace_id}'
    @property
    def staging_dataset(self):return f'dr_prod_stage_w{self.workspace_id}'
    @property
    def fingerprint(self):return digest(self.__dict__ if hasattr(self,'__dict__') else {field:getattr(self,field) for field in self.__dataclass_fields__})


class Registry:
    def __init__(self,approvals=()):
        self._entries={}
        for entry in approvals:
            if not isinstance(entry,Approval) or entry.workspace_id in self._entries:raise ValueError('One reviewed binding per workspace')
            self._entries[entry.workspace_id]=entry

    def verify_persisted(
        self,
        workspace_id,
        record,
        *,
        expected_fingerprint,
    ):
        """Match one persisted approval to the reviewed registry."""

        if type(workspace_id) is not int or workspace_id <= 0:
            raise ValueError("Valid workspace required")

        if not isinstance(record, dict):
            raise PermissionError("Persisted approval required")

        approval_id = record.get("approval_id")

        if type(approval_id) is not int or approval_id <= 0:
            raise PermissionError("Valid persisted approval ID required")

        if (
            type(record.get("workspace_id")) is not int
            or record["workspace_id"] != workspace_id
        ):
            raise PermissionError("Approval workspace mismatch")

        reviewed = self._entries.get(workspace_id)

        if (
            reviewed is None
            or reviewed.fingerprint != expected_fingerprint
        ):
            raise PermissionError("Reviewed approval not found")

        try:
            persisted = Approval(
                project=record["project"],
                workspace_id=record["workspace_id"],
                loader_identity=record["loader_identity"],
                query_identity=record["query_identity"],
                revision=record["approval_revision"],
                aws_region=record["aws_region"],
                location=record["location"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PermissionError(
                "Invalid persisted approval"
            ) from exc

        if persisted.fingerprint != reviewed.fingerprint:
            raise PermissionError(
                "Persisted approval differs from reviewed registry"
            )

        return {
            "approval_id": approval_id,
            "approval_revision": persisted.revision,
            "approval_sha256": persisted.fingerprint,
        }

    def resolve(self,w,*,expected_fingerprint,model_id,version_id,generation,publication_id,revision,schema_sha256,source_fingerprint):
        if type(w) is not int or w<=0:raise ValueError('Reviewed workspace required')
        approved=self._entries.get(w)
        if approved is None or approved.fingerprint!=expected_fingerprint:raise PermissionError('No approved production binding')
        if any(type(v) is not int or v<=0 for v in (model_id,version_id,generation,publication_id,revision)):raise ValueError('Pinned serving generation required')
        if any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in (schema_sha256,source_fingerprint)):
            raise ValueError('Pinned schema/source fingerprint required')
        table=f'm{model_id}_v{version_id}_g{generation}_p{publication_id}_r{revision}'
        return dict(project=approved.project,location=approved.location,environment=approved.environment,
            dataset=approved.serving_dataset,staging_dataset=approved.staging_dataset,table=table,
            physical_table=f'{approved.project}.{approved.serving_dataset}.{table}',approval_sha256=approved.fingerprint,
            schema_sha256=schema_sha256,source_fingerprint=source_fingerprint)
