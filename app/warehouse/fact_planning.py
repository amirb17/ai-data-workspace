"""Pure provider-neutral FACT planning from trusted accepted evidence.

No database access, provider access, SQL generation or consumer execution.
"""

from dataclasses import dataclass
from datetime import date, datetime
import re
from uuid import UUID

from app.schemas.warehouse_modeling import (
    FactConfiguration,
    SourceSchema,
)
from app.warehouse.accepted_changes import (
    digest,
    projection,
    verify_manifest,
)
from app.warehouse.keys import canonical_value, surrogate_key
from app.warehouse.model_validation import validate_model
from app.warehouse.providers.types import scalar
from app.warehouse.reliability import Result
from app.warehouse.scd_types import SourceContractPin
from app.warehouse.time_policy import normalize_time


HEX64 = re.compile(r"^[a-f0-9]{64}$")


FactScalar = str | int | bool | date | datetime | object | None
FactValues = tuple[tuple[str, FactScalar], ...]


@dataclass(frozen=True)
class FactContext:
    model_id: int
    model_version_id: int
    namespace: UUID
    configuration: FactConfiguration
    source_schema: SourceSchema
    source_contract: SourceContractPin
    key_algorithm_version: int = 1
    configuration_sha256: str | None = None

    def __post_init__(self):
        if (
            self.configuration_sha256 is not None
            and (
                not isinstance(
                    self.configuration_sha256,
                    str,
                )
                or not HEX64.fullmatch(
                    self.configuration_sha256
                )
            )
        ):
            raise ValueError(
        "Pinned FACT configuration hash required"
    )
        if (
            type(self.model_id) is not int
            or self.model_id < 1
            or type(self.model_version_id) is not int
            or self.model_version_id < 1
            or not isinstance(self.namespace, UUID)
            or not isinstance(self.configuration, FactConfiguration)
            or not isinstance(self.source_schema, SourceSchema)
            or not isinstance(self.source_contract, SourceContractPin)
            or self.key_algorithm_version != 1
        ):
            raise ValueError("Invalid FACT context")

        validate_model(
            self.configuration,
            self.source_schema,
        )


@dataclass(frozen=True)
class FactState:
    context: FactContext
    fact_sk: str
    identity: FactValues
    measures: FactValues
    attributes: FactValues
    event_time: date | datetime | None
    active: bool
    source_position: int
    last_event_id: str
    last_event_checksum: str

    def __post_init__(self):
        if not isinstance(self.context, FactContext):
            raise ValueError("FACT context required")

        if not HEX64.fullmatch(self.fact_sk):
            raise ValueError("Invalid FACT surrogate key")

        if (
            type(self.active) is not bool
            or type(self.source_position) is not int
            or self.source_position < 1
        ):
            raise ValueError("Invalid FACT state metadata")

        if (
            not HEX64.fullmatch(self.last_event_id)
            or not HEX64.fullmatch(self.last_event_checksum)
        ):
            raise ValueError("Pinned FACT event evidence required")


def _fields(values):
    result = []

    for name, value in sorted(values):
        result.append(
            [name, canonical_value(value)]
        )

    return result


def _context_payload(context: FactContext):
    return [
        [
            "configuration_sha256",
            _configuration_digest(context),
        ],
        context.model_id,
        context.model_version_id,
        str(context.namespace),
        context.key_algorithm_version,
        context.configuration.model_dump(mode="json"),
        context.source_schema.model_dump(mode="json"),
        [
            context.source_contract.dataset_id,
            context.source_contract.dataset_version_id,
            context.source_contract.policy_id,
            context.source_contract.rule_version,
        ],
        
    ]
def _configuration_digest(
    context: FactContext,
):
    if context.configuration_sha256 is not None:
        return context.configuration_sha256

    # Pure callers/tests without persisted metadata retain deterministic
    # behavior. Provider-backed execution always supplies the stored hash.
    return digest(
        context.configuration.model_dump(
            mode="json"
        )
    )

def fact_state_digest(state: FactState) -> str:
    return digest(
        [
            "datarise-warehouse-fact-state",
            1,
            _context_payload(state.context),
            state.fact_sk,
            _fields(state.identity),
            _fields(state.measures),
            _fields(state.attributes),
            (
                canonical_value(state.event_time)
                if state.event_time is not None
                else None
            ),
            state.active,
            state.source_position,
            state.last_event_id,
            state.last_event_checksum,
        ]
    )


def fact_result_checksum(states) -> str:
    ordered = sorted(
        states,
        key=lambda state: state.fact_sk,
    )

    return digest(
        [
            "datarise-warehouse-fact-result",
            1,
            [
                [
                    state.fact_sk,
                    fact_state_digest(state),
                ]
                for state in ordered
            ],
        ]
    )


@dataclass(frozen=True)
class FactLogicalPlan:
    context: FactContext
    manifests: tuple[dict, ...]
    before: tuple[FactState, ...]
    after: tuple[FactState, ...]

    @property
    def result(self):
        events = [
            event
            for manifest in self.manifests
            for event in manifest["events"]
        ]

        counts = {
            name.lower(): sum(
                event["outcome"] == name
                for event in events
            )
            for name in (
                "INSERTED",
                "UPDATED",
                "UNCHANGED",
                "REACTIVATED",
                "DEACTIVATED",
            )
        }

        return Result(
            manifests=len(self.manifests),
            events=len(events),
            rejected=0,
            checksum=fact_result_checksum(self.after),
            **counts,
        )

    @property
    def checksum(self):
        return digest(
            [
                "datarise-warehouse-fact-plan",
                1,
                self.result.model_dump(mode="json"),
                [
                    fact_state_digest(state)
                    for state in self.before
                ],
                [
                    [
                        manifest["manifest_id"],
                        manifest["checksum"],
                    ]
                    for manifest in self.manifests
                ],
            ]
        )


def fact_values(context, values):
    config = context.configuration

    identity = tuple(
        (name, values[name])
        for name in sorted(
            config.grain.identity_columns
        )
    )

    measures = tuple(
        (name, values[name])
        for name in sorted(config.measures)
    )

    attributes = tuple(
        (name, values[name])
        for name in sorted(config.attributes)
    )

    event_time = None

    if config.event_time:
        raw = values[config.event_time.column]
        event_time = normalize_time(
            raw,
            config.event_time,
        )

    return identity, measures, attributes, event_time


def _same_business_state(
    state,
    measures,
    attributes,
    event_time,
):
    return (
        state.measures == measures
        and state.attributes == attributes
        and state.event_time == event_time
    )


def compile_fact(
    context: FactContext,
    manifests,
    before=(),
):
    if not isinstance(context, FactContext):
        raise ValueError("Typed FACT context required")

    if not 1 <= len(manifests) <= 1000:
        raise ValueError(
            "Bounded accepted manifests required"
        )

    manifests = tuple(
        sorted(
            (
                verify_manifest(manifest)
                for manifest in manifests
            ),
            key=lambda manifest: manifest["position"],
        )
    )

    if (
        len(
            {
                manifest["manifest_id"]
                for manifest in manifests
            }
        )
        != len(manifests)
    ):
        raise ValueError("Duplicate manifest")

    if (
        sum(
            manifest["event_count"]
            for manifest in manifests
        )
        > 1000
    ):
        raise ValueError(
            "Bounded accepted events required"
        )

    states = {}

    for state in before:
        if (
            not isinstance(state, FactState)
            or state.context != context
            or state.fact_sk in states
        ):
            raise ValueError(
                "Invalid prior FACT state"
            )

        states[state.fact_sk] = state

    touched = set()
    required_columns = set(
        projection(context.configuration)
    )

    configuration_sha256 = (
            _configuration_digest(context)
        )

    for manifest in manifests:
        pin = manifest["pin"]

        if (
            manifest["configuration_sha256"]
            != configuration_sha256
            or manifest["model_version_id"]
            != context.model_version_id
            or (
                pin["dataset_id"],
                pin["dataset_version_id"],
                pin["policy_id"],
                pin["rule_version"],
            )
            != (
                context.source_contract.dataset_id,
                context.source_contract.dataset_version_id,
                context.source_contract.policy_id,
                context.source_contract.rule_version,
            )
        ):
            raise ValueError(
                "FACT source/model contract mismatch"
            )
        prepared = []
        seen = set()

        # First pass: validate the entire manifest.
        for accepted in manifest["events"]:
            values = {
                name: scalar(value)
                for name, value in accepted["payload"]
            }

            if set(values) != required_columns:
                raise ValueError(
                    "FACT evidence projection mismatch"
                )

            fact_sk = surrogate_key(
                context.namespace,
                "FACT",
                context.configuration.grain.identity_columns,
                values,
                algorithm_version=context.key_algorithm_version,
            )

            if fact_sk in seen:
                raise ValueError(
                    "Multiple same-fact events lack "
                    "an intra-manifest accepted order"
                )

            seen.add(fact_sk)

            prepared.append(
                (
                    accepted,
                    values,
                    fact_sk,
                )
            )

        # Second pass: apply outcomes only after the whole manifest is valid.
        for accepted, values, fact_sk in prepared:
            touched.add(fact_sk)

            (
                identity,
                measures,
                attributes,
                event_time,
            ) = fact_values(
                context,
                values,
            )

            previous = states.get(fact_sk)
            position = manifest["position"]

            # Exact retry of the already-applied accepted event.
            if (
                previous is not None
                and previous.last_event_id
                == accepted["event_id"]
                and previous.last_event_checksum
                == accepted["checksum"]
                and previous.source_position
                == position
            ):
                continue

            if (
                previous is not None
                and position <= previous.source_position
            ):
                raise ValueError(
                    "FACT accepted order conflict"
                )

            outcome = accepted["outcome"]

            if outcome == "INSERTED":
                if previous is not None:
                    raise ValueError(
                        "FACT insert conflicts with existing identity"
                    )

                active = True

            elif outcome == "UPDATED":
                if previous is None or not previous.active:
                    raise ValueError(
                        "FACT update requires active prior state"
                    )

                active = True

            elif outcome == "UNCHANGED":
                if (
                    previous is None
                    or not _same_business_state(
                        previous,
                        measures,
                        attributes,
                        event_time,
                    )
                ):
                    raise ValueError(
                        "FACT unchanged evidence conflicts "
                        "with prior state"
                    )

                active = previous.active

            elif outcome == "DEACTIVATED":
                if (
                    previous is None
                    or not previous.active
                    or not _same_business_state(
                        previous,
                        measures,
                        attributes,
                        event_time,
                    )
                ):
                    raise ValueError(
                        "FACT deactivation conflicts "
                        "with prior state"
                    )

                active = False

            elif outcome == "REACTIVATED":
                if previous is None or previous.active:
                    raise ValueError(
                        "FACT reactivation requires "
                        "inactive prior state"
                    )

                active = True

            else:
                raise ValueError(
                    "Unsupported FACT source outcome"
                )

            states[fact_sk] = FactState(
                context=context,
                fact_sk=fact_sk,
                identity=identity,
                measures=measures,
                attributes=attributes,
                event_time=event_time,
                active=active,
                source_position=position,
                last_event_id=accepted["event_id"],
                last_event_checksum=accepted["checksum"],
            )

    if set(states) - touched:
        raise ValueError(
            "Prior FACT state must be bounded "
            "to affected identities"
        )

    return FactLogicalPlan(
        context=context,
        manifests=manifests,
        before=tuple(
            sorted(
                before,
                key=lambda state: state.fact_sk,
            )
        ),
        after=tuple(
            states[key]
            for key in sorted(states)
        ),
    )