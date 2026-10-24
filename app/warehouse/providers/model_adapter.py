"""Dispatch approved warehouse model semantics without provider duplication."""

from app.warehouse.fact_planning import (
    FactContext,
    FactLogicalPlan,
    compile_fact,
    fact_result_checksum,
)
from app.warehouse.providers import (
    fact_physical,
    physical,
)
from app.warehouse.providers.base import (
    ErrorCode,
    ProviderError,
)
from app.warehouse.providers.planning import (
    LogicalPlan,
    compile_dimension,
    result_checksum as dimension_result_checksum,
)
from app.warehouse.scd_types import ModelContext


Plan = LogicalPlan | FactLogicalPlan


def _physical(context):
    if isinstance(context, FactContext):
        return fact_physical

    if isinstance(context, ModelContext):
        return physical

    raise ProviderError(
        ErrorCode.SCHEMA_MISMATCH
    )


def compile_plan(context, manifests, before=()):
    if isinstance(context, FactContext):
        return compile_fact(
            context,
            manifests,
            before,
        )

    if isinstance(context, ModelContext):
        return compile_dimension(
            context,
            manifests,
            before,
        )

    raise ProviderError(
        ErrorCode.SCHEMA_MISMATCH
    )


def schema(context):
    return _physical(context).schema(context)


def schema_hash(context):
    return _physical(context).schema_hash(context)


def rows(plan: Plan):
    return _physical(
        plan.context
    ).rows(plan)


def states(context, physical_rows):
    return _physical(
        context
    ).states(
        context,
        physical_rows,
    )


def result_checksum(context, states):
    if isinstance(context, FactContext):
        return fact_result_checksum(states)

    if isinstance(context, ModelContext):
        return dimension_result_checksum(states)

    raise ProviderError(
        ErrorCode.SCHEMA_MISMATCH
    )


def reconciliation(plan: Plan, actual):
    """Provider-neutral reconciliation statistics only."""

    if isinstance(plan.context, FactContext):
        before = {
            state.fact_sk: state
            for state in plan.before
        }

        after = {
            state.fact_sk: state
            for state in actual
        }

        shared = (
            before.keys()
            & after.keys()
        )

        return dict(
            affected_facts=len(after),
            inserted_facts=len(
                after.keys()
                - before.keys()
            ),
            updated_facts=sum(
                before[key] != after[key]
                for key in shared
            ),
            deactivated_facts=sum(
                before[key].active
                and not after[key].active
                for key in shared
            ),
            reactivated_facts=sum(
                not before[key].active
                and after[key].active
                for key in shared
            ),
        )

    if isinstance(plan.context, ModelContext):
        before = {
            (
                state.member_sk,
                version.version_sk,
            ): version
            for state in plan.before
            for version in state.versions
        }

        after = {
            (
                state.member_sk,
                version.version_sk,
            ): version
            for state in actual
            for version in state.versions
        }

        shared = (
            before.keys()
            & after.keys()
        )

        return dict(
            affected_members=len(actual),
            physical_versions=len(after),
            inserted_versions=len(
                after.keys()
                - before.keys()
            ),
            updated_versions=sum(
                before[key] != after[key]
                for key in shared
            ),
            interval_ends_changed=sum(
                before[key].valid_to
                != after[key].valid_to
                for key in shared
            ),
        )

    raise ProviderError(
        ErrorCode.SCHEMA_MISMATCH
    )