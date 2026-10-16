"""Backend-only interpretation and Phase 8A validation of literal temporary definitions."""
from copy import deepcopy
import re
from app.schemas.metrics import MetricProposal
from app.schemas.workspace_questions import QuestionIntent
from app.processing.metric_validator import validate_metric
from app.processing.metric_execution import compile_plan


class QuestionBlocked(ValueError):
    def __init__(self, status, message, choices=None):
        self.status, self.message, self.choices = status, message, choices or []
        super().__init__(status)


def words(text):
    return {word.rstrip('s') for word in re.findall(r'[a-z0-9]+', text.casefold().replace('_', ' '))}


def mentioned_datasets(question, source):
    asked = words(question)
    found = set()
    for item in source['eligible']:
        labels = [item['pin']['dataset_name'], item['suggestion']['reasoning']['entity']['primary']['label'] or '']
        for label in labels:
            meaningful = words(label) - {'dataset', 'record', 'entity', 'table', 'data'}
            if meaningful and meaningful <= asked:
                found.add(item['pin']['dataset_id'])
    return found


def context_for_question(previous, source, question):
    if not previous:
        return None
    mentioned = mentioned_datasets(question, source)
    allowed = {previous.get('base_dataset_id')} | {d['dataset_id'] for d in previous.get('dimensions', [])}
    # Explicit new business subjects must not borrow analytical context from another entity.
    return None if mentioned - allowed else previous


def grounded(intent, source, resolved, question, chosen):
    """A provider-selected reference alone cannot ground an unrelated business question."""
    if not question or chosen:
        return
    if intent.context_mode == 'FOLLOW_UP' and re.search(r'\b(this|that|it|last|previous|same)\b', question, re.I):
        return
    asked = words(question)
    if intent.context_mode == 'FOLLOW_UP' and intent.dimensions and re.match(r'^\s*what\s+about\s+by\b', question, re.I):
        dimensions = set().union(*(words(d.column) for d in intent.dimensions))
        if asked - {'what', 'about', 'by', 'and'} == dimensions:
            # This narrowly scoped elliptical follow-up names only its breakdown.
            # The service requires owned previous context; all references are still validated.
            return
    if intent.metric_candidate_id:
        metric = next((i for i in resolved if i['row']['candidate_id'] == intent.metric_candidate_id), None)
        name = words(metric['row']['evidence']['definition']['name']) if metric else set()
        if metric:
            allowed = {metric['row']['evidence']['definition']['base_dataset_id']} | {d.dataset_id for d in intent.dimensions}
            if mentioned_datasets(question, source) - allowed:
                raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which named business entity should this metric describe?')
        informative = name - {'total', 'count', 'record', 'average', 'minimum', 'maximum', 'distinct'}
        if informative and not informative <= asked:
            raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which workspace measure should answer this question?')
        return
    item = next((i for i in source['eligible'] if i['pin']['dataset_id'] == intent.base_dataset_id), None)
    if not item:
        return
    allowed = {intent.base_dataset_id} | {d.dataset_id for d in intent.dimensions}
    if intent.time_field:
        allowed.add(intent.time_field.dataset_id)
    if mentioned_datasets(question, source) - allowed:
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which named business entity should this measure describe?')
    references = words(item['pin']['dataset_name']) | words(item['suggestion']['reasoning']['entity']['primary']['label'] or '')
    if intent.measure and intent.aggregation != 'COUNT':
        keys = item['profile']['summary']['business_key']
        if intent.aggregation == 'COUNT_DISTINCT' and keys == [intent.measure.column] and asked & references:
            # A breakdown changes the output shape, not the meaning of a proven entity
            # count. Users need not spell the physical key when naming that entity.
            if intent.question_type == 'COUNT' or item['suggestion']['reasoning']['entity']['primary']['confidence'] >= .9:
                return
        if not words(intent.measure.column) <= asked:
            raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which confirmed business measure should this question use?')
    else:
        if not asked & references:
            raise QuestionBlocked('UNSUPPORTED', 'Ask about an available entity or measure in this workspace.')


def validation_source(source):
    if source.get('workspace_reasoning'):
        return source
    # Literal calculations may use current dataset evidence independently of unrelated members.
    # No invented workspace/business confidence or scope.
    return {**source, 'workspace_reasoning': {'overall_confidence': 0, 'warnings': [], 'unresolved_questions': []}}


def inherit(intent, previous):
    if intent.context_mode != 'FOLLOW_UP' or not previous:
        return intent
    old = QuestionIntent.model_validate(previous).model_dump()
    new = intent.model_dump()
    # Switching explicit base entities starts a new topic, regardless of provider context claim.
    if new['base_dataset_id'] and old['base_dataset_id'] and new['base_dataset_id'] != old['base_dataset_id']:
        new['context_mode'] = 'NEW'
        return QuestionIntent.model_validate(new)
    if 'question_type' not in intent.model_fields_set:
        new['question_type'] = old['question_type']
    for key in ('metric_candidate_id', 'base_dataset_id', 'aggregation', 'measure', 'time_field', 'time_grain'):
        if new[key] is None:
            new[key] = old[key]
    for key in ('dimensions', 'relationship_ids', 'periods'):
        if not new[key]:
            new[key] = old[key]
    return QuestionIntent.model_validate(new)


def literal_definition(intent, source):
    items = {x['pin']['dataset_id']: x for x in source['eligible']}
    base = items.get(intent.base_dataset_id)
    if not base:
        raise QuestionBlocked('REFRESH_REQUIRED', 'Refresh the dataset profile and understanding before asking about it.')
    entity = base['suggestion']['reasoning']['entity']['primary']['label']
    if not entity:
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which business entity should this question describe?')
    op = intent.aggregation
    if not op:
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'What would you like to measure?')
    measure = intent.measure
    keys = base['profile']['summary']['business_key']
    # An entity question never silently falls back to counting records.
    if (intent.question_type == 'COUNT' and op == 'COUNT') or (op == 'COUNT_DISTINCT' and not measure):
        if base['suggestion']['reasoning']['entity']['primary']['confidence'] < .9:
            raise QuestionBlocked('CLARIFICATION_REQUIRED', 'This dataset entity is still uncertain; confirm its business meaning first.')
        if len(keys) != 1:
            raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Should this count records or distinct business entities?')
        from app.schemas.metrics import ColumnRef
        measure = ColumnRef(dataset_id=intent.base_dataset_id, column=keys[0])
        op = 'COUNT_DISTINCT'
    if op != 'COUNT' and (not measure or measure.dataset_id != intent.base_dataset_id):
        raise QuestionBlocked('UNSUPPORTED', 'This measure is not available at the selected dataset grain.')
    nodes = [{'id': 'value', 'op': 'COLUMN', 'column': measure.model_dump()}] if op != 'COUNT' else []
    nodes.append({'id': 'aggregate', 'op': op, 'args': ['value'] if nodes else []})
    name = f'{op}({measure.column if op != "COUNT" else ""})'
    # A range-only time reference is validated as a time field, even without output time grouping.
    field = intent.time_field
    if intent.periods and not field:
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which date should define this period?')
    return MetricProposal(name=name, description='Literal question-local calculation', metric_type=op,
                          base_dataset_id=intent.base_dataset_id, base_entity=entity,
                          grain='ENTITY' if op == 'COUNT_DISTINCT' and measure.column in keys and len(keys) == 1 else 'RECORD',
                          grain_keys=keys if op == 'COUNT_DISTINCT' and measure.column in keys and len(keys) == 1 else [],
                          formula={'nodes': nodes, 'root': 'aggregate'}, dimensions=intent.dimensions,
                          time_field=field, time_bucket='MONTH' if field else None,
                          relationship_ids=intent.relationship_ids, unit='NUMBER', confidence=1,
                          rationale='Validated literal calculation; no business definition is persisted.').model_dump()


def resolve(workspace, intent, source, resolved, question='', chosen=False):
    grounded(intent, source, resolved, question, chosen)
    if intent.question_type in ('SCALAR', 'COUNT') and (intent.dimensions or intent.time_grain):
        raise QuestionBlocked('UNSUPPORTED', 'A single-value question cannot include a breakdown.')
    if intent.question_type == 'COUNT' and not intent.metric_candidate_id and intent.aggregation not in ('COUNT', 'COUNT_DISTINCT'):
        raise QuestionBlocked('UNSUPPORTED', 'An entity count requires a supported count measure.')
    if len({(d.dataset_id, d.column) for d in intent.dimensions}) != len(intent.dimensions):
        raise QuestionBlocked('UNSUPPORTED', 'Choose each breakdown dimension once.')
    if intent.periods and not intent.time_field:
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which business date should define this period?')
    if intent.question_type == 'COMPARISON' and (len(intent.periods) != 2 or intent.dimensions or intent.time_grain):
        raise QuestionBlocked('UNSUPPORTED', 'Compare two periods for a single value; grouped comparisons are not supported yet.')
    if intent.question_type != 'COMPARISON' and len(intent.periods) > 1:
        raise QuestionBlocked('UNSUPPORTED', 'This question supports one date range.')
    if intent.question_type in ('TIME_SERIES', 'TREND') and not (intent.time_field and intent.time_grain):
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which business date should describe this trend?')
    if intent.question_type in ('GROUPED', 'BREAKDOWN', 'RANKING') and not intent.dimensions:
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'How would you like to break this measure down?')
    if intent.meaning == 'UNRESOLVED_CATEGORY' or re.search(r'\b(active|completed|cancelled|canceled|paid|unpaid|successful)\b', question, re.I):
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'The requested status needs a confirmed business definition before it can be filtered.')
    metric = next((x for x in resolved if x['row']['candidate_id'] == intent.metric_candidate_id), None)
    if intent.metric_candidate_id:
        if not metric:
            raise QuestionBlocked('UNSUPPORTED', 'The requested metric is not available in this workspace.')
        if not metric['plan']:
            raise QuestionBlocked('REFRESH_REQUIRED' if metric['reason'] == 'REFRESH_REQUIRED' else 'CLARIFICATION_REQUIRED',
                                  'This metric needs current analytics.' if metric['reason'] == 'REFRESH_REQUIRED' else 'This metric has an unresolved definition; it cannot answer this question yet.')
        definition = deepcopy(metric['plan'].definition)
        if intent.question_type == 'COUNT' and definition['metric_type'] not in ('COUNT', 'COUNT_DISTINCT'):
            raise QuestionBlocked('UNSUPPORTED', 'The selected metric is not a count.')
        if intent.question_type == 'COUNT' and definition['metric_type'] == 'COUNT' and not words(question) & {'record', 'row'}:
            # Record count cannot answer an entity count merely because both are numeric.
            entity_intent = intent.model_copy(update={'metric_candidate_id': None,
                'base_dataset_id': definition['base_dataset_id'], 'aggregation': 'COUNT', 'measure': None})
            return resolve(workspace, entity_intent, source, resolved, question, chosen)
        # A validated literal metric does not confer trust on a new business label.
        for word in ('revenue', 'profit', 'sales', 'lifetime', 'churn'):
            if re.search(r'\b' + word + r'\b', question, re.I) and word not in definition['name'].casefold():
                raise QuestionBlocked('CLARIFICATION_REQUIRED', f'What should {word} mean for this question?')
        if intent.base_dataset_id and intent.base_dataset_id != definition['base_dataset_id']:
            raise QuestionBlocked('UNSUPPORTED', 'The metric does not belong to this question entity.')
        # Reuse exact validated definitions; modifications are temporary and revalidated below.
        if intent.question_type in ('SCALAR', 'COUNT') and not intent.dimensions and not intent.time_field and not intent.periods and not intent.relationship_ids:
            if definition['dimensions'] or definition['time_field']:
                raise QuestionBlocked('UNSUPPORTED', 'This metric produces grouped values; ask for its supported breakdown.')
            return metric['plan'], {'candidate_id': metric['row']['candidate_id'], 'definition_version': metric['row']['run_id'], 'temporary': False}
        definition.update(dimensions=[r.model_dump() for r in intent.dimensions],
                          time_field=intent.time_field.model_dump() if intent.time_field else None,
                          time_bucket='MONTH' if intent.time_field else None,
                          relationship_ids=intent.relationship_ids)
    else:
        if not chosen and (intent.meaning == 'BUSINESS_AMBIGUOUS' or re.search(r'\b(revenue|profit|sales|lifetime|churn)\b', question, re.I)):
            raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which confirmed business measure should this question use?')
        literal_intent = intent.model_copy(update={'question_type': 'SCALAR'}) if intent.question_type == 'COUNT' and intent.aggregation == 'COUNT' and words(question) & {'record', 'row'} else intent
        definition = literal_definition(literal_intent, source)
    try:
        checked = validate_metric(definition, validation_source(source))
        if checked['validation_status'] != 'VALID' or checked['decision'] == 'REVIEW_REQUIRED':
            raise QuestionBlocked('CLARIFICATION_REQUIRED', 'The requested calculation has unresolved meaning or grain and cannot be calculated safely.')
        plan = compile_plan(workspace, {'candidate_id': 0, 'evidence': checked}, validation_source(source))
        return plan, {'temporary': True, 'name': definition['name']}
    except QuestionBlocked:
        raise
    except (KeyError, ValueError):
        stale = 'checked' in locals() and any(
            source['members'].get(i, {}).get('state_analytics_status') != 'FRESH'
            or source['members'].get(i, {}).get('gold_status') != 'SUCCESS'
            or source['members'].get(i, {}).get('gold_state_id') != source['members'].get(i, {}).get('current_state_id')
            for i in checked['required_datasets'])
        if stale:
            raise QuestionBlocked('REFRESH_REQUIRED',
                                  'Required cumulative dataset analytics are missing or stale. Refresh analytics for the source datasets, then refresh this answer.') from None
        raise QuestionBlocked('UNSUPPORTED',
                              'This calculation cannot form a supported validated execution plan. Use a supported measure and at most one confirmed direct lookup.') from None
