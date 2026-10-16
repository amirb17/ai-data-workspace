"""Gemini plans; backend resolves, validates, executes and publishes immutable answers."""
from dataclasses import asdict
from datetime import datetime, timezone
import logging
import time
import re
from fastapi import HTTPException
from app.ai.workspace_question_planner import WorkspaceQuestionPlanner, handoff, parse, SYSTEM_PROMPT
from app.ai.semantic_provider import ProviderFailure
from app.db.database import repository_transaction
from app.db import workspace_answer_repository as repository
from app.processing.relationship_evidence import digest
from app.processing.workspace_question_resolution import resolve, inherit, QuestionBlocked, literal_definition, validation_source, context_for_question
from app.processing.workspace_question_execution import execute, calendar_range, format_value, question_timezone
from app.processing.metric_execution import BoundedMetricArtifacts
from app.processing.metric_validator import validate_metric
from app.schemas.workspace_questions import WorkspaceQuestionRequest, WorkspaceQuestionPlan, QuestionIntent, WorkspaceAnswer
from app.schemas.metrics import ColumnRef
from app.services import metric_service, workspace_analytics_service as analytics
from app.services.workspace_understanding_service import validate_workspace

logger = logging.getLogger(__name__)
ALGORITHM_VERSION = 1
EXECUTION_TIMEOUT = 60


def get_provider():
    return WorkspaceQuestionPlanner()


def configuration(provider):
    return digest([provider.provider, provider.model, provider.strategy, SYSTEM_PROMPT, ALGORITHM_VERSION])


def snapshot(workspace, user, lock=False):
    with repository_transaction() as conn:
        if not lock:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        source = metric_service.gather(conn, workspace, user, lock)
        resolved = analytics.resolve(conn, workspace, user)
        return source, resolved


def owned(conn, workspace, answer_id):
    row = repository.get(conn, workspace, answer_id)
    if not row:
        raise HTTPException(404, 'Answer not found in workspace')
    return row


def public(row, source, resolved, reused=False):
    payload = dict(row['payload'])
    payload.update(answer_id=row['answer_id'], workspace_id=row['workspace_id'],
                   computed_at=row['created_at'].isoformat(), reused=reused)
    internal = row['internal']
    if payload['status'] in ('ANSWERED', 'NO_DATA'):
        try:
            intent = QuestionIntent.model_validate(payload['intent'])
            plan, _ = resolve(row['workspace_id'], intent, source, resolved, payload['question'], internal.get('chosen', False))
            current_ranges = [calendar_range(p, internal.get('timezone')) for p in intent.periods]
            if intent.question_type == 'COMPARISON':
                current_ranges.sort()
            current_key = digest([asdict(plan), intent.model_dump(mode='json'), current_ranges,
                                  internal.get('timezone'), ALGORITHM_VERSION])
            if current_key != row['cache_key']:
                raise QuestionBlocked('REFRESH_REQUIRED', 'Inputs changed')
        except (QuestionBlocked, ValueError, KeyError):
            payload.update(freshness='STALE', value=None, groups=[], comparison=None,
                           primary_answer='This historical answer needs a refresh before it can show current values.')
    return WorkspaceAnswer.model_validate(payload).model_dump(mode='json')


def base_payload(request, intent=None):
    return dict(question=request.question, intent=intent.model_dump(mode='json') if intent else None,
                status='FAILED', freshness='UNAVAILABLE', primary_answer='Could not answer this question. Retry when the service is available.',
                result_kind=None, value=None, groups=[], comparison=None, metric_reference=None,
                dimensions=[], provenance=[], relationship_versions=[], interpretation='',
                explanation='', caveats=[], follow_ups=[], choices=[])


def choices_for(intent, source):
    """Only backend-validated literal measures, scoped to a question; never approve a business metric."""
    options = []
    for item in source['eligible']:
        if intent.base_dataset_id and item['pin']['dataset_id'] != intent.base_dataset_id:
            continue
        roles = {c['column_name']: c for c in item['suggestion']['reasoning']['columns']}
        for column in item['profile']['columns']:
            role = roles.get(column['original_name'], {})
            if role.get('suggested_role') != 'MEASURE' or role.get('warnings') or column.get('sensitivity_hints'):
                continue
            candidate = intent.model_copy(update={'meaning': 'LITERAL', 'metric_candidate_id': None,
                'base_dataset_id': item['pin']['dataset_id'], 'aggregation': 'SUM',
                'measure': ColumnRef(dataset_id=item['pin']['dataset_id'], column=column['original_name'])})
            try:
                evidence = validate_metric(literal_definition(candidate, source), validation_source(source))
                if evidence['validation_status'] != 'VALID':
                    continue
            except (QuestionBlocked, ValueError, KeyError):
                continue
            options.append({'label': f"Total {column['original_name'].replace('_', ' ')} in {item['pin']['dataset_name']}",
                            'intent': candidate.model_dump(mode='json')})
    return options[:10]


def ask(workspace, user, request, refresh_id=None):
    validate_workspace(workspace, user)
    started = time.monotonic()
    provider = get_provider()
    planner_key = configuration(provider)
    with repository.question_lock(workspace):
        source, resolved = snapshot(workspace, user)
        previous = None
        chosen = False
        replaying_stored_intent = False
        with repository_transaction() as conn:
            if refresh_id:
                original = owned(conn, workspace, refresh_id)
                stored = original['internal']
                request = WorkspaceQuestionRequest(question=original['payload']['question'],
                    previous_answer_id=refresh_id, timezone=stored.get('timezone'))
                chosen = stored.get('chosen', False)
                replaying_stored_intent = bool(original['payload']['intent'])
                planned = WorkspaceQuestionPlan(status='ANSWERABLE', intent=QuestionIntent.model_validate(original['payload']['intent'])) if original['payload']['intent'] else None
            elif request.clarification_answer_id:
                original = owned(conn, workspace, request.clarification_answer_id)
                options = original['internal'].get('choices', [])
                if original['payload']['status'] != 'CLARIFICATION_REQUIRED' or request.choice_id >= len(options):
                    raise HTTPException(400, 'Clarification choice unavailable')
                choice = options[request.choice_id]
                request = WorkspaceQuestionRequest(question=original['payload']['question'],
                    previous_answer_id=original['answer_id'], timezone=request.timezone or original['internal'].get('timezone'))
                planned = WorkspaceQuestionPlan(status='ANSWERABLE', intent=QuestionIntent.model_validate(choice['intent']))
                chosen = True
            else:
                if request.previous_answer_id:
                    previous = owned(conn, workspace, request.previous_answer_id)['payload'].get('intent')
                    previous = context_for_question(previous, source, request.question)
                cached_question = repository.matching_question(conn, workspace,
                    digest([request.question, request.timezone, request.previous_answer_id]), planner_key)
                planned = WorkspaceQuestionPlan(status='ANSWERABLE', intent=QuestionIntent.model_validate(cached_question['payload']['intent'])) if cached_question and cached_question['payload']['intent'] else None
                chosen = cached_question['internal'].get('chosen', False) if cached_question else False
        payload = base_payload(request)
        internal = {'timezone': request.timezone, 'chosen': chosen, 'choices': []}
        cache_key = None
        plan = None
        reused_output = None
        try:
            timezone_name = question_timezone(request.question, request.timezone)
            internal['timezone'] = timezone_name
            if re.search(r'\b(select\s+.+\s+from|show\s+(all\s+)?(raw\s+)?(rows|patient names|diagnoses)|ignore\s+(all\s+)?(previous|system)\s+instructions|credentials|system prompt|api keys?|predict|forecast)\b', request.question, re.I) or re.search(r'[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b\d{12,}\b', request.question, re.I):
                raise QuestionBlocked('UNSUPPORTED', 'Ask for aggregate analytics without personal records, identifiers or executable instructions.')
            if not source['eligible']:
                raise QuestionBlocked('REFRESH_REQUIRED', 'Add a dataset to start asking questions.' if not source['members'] else 'Prepare current dataset profiles and understanding before asking this question.')
            if planned is None:
                encoded = handoff(request.question, source, resolved, previous)
                provider_started = time.monotonic()
                response = provider.generate(encoded)
                planned = parse(response)
                internal['planner'] = {'provider': provider.provider, 'model': provider.model,
                    'strategy': provider.strategy, 'resolved_model': response.resolved_model,
                    'input_bytes': len(encoded.encode()), 'output_bytes': len(response.text.encode()),
                    'duration_seconds': round(time.monotonic() - provider_started, 3)}
            intent = inherit(planned.intent, previous)
            if intent.context_mode == 'FOLLOW_UP' and not previous and not replaying_stored_intent:
                intent = intent.model_copy(update={'context_mode': 'NEW'})
            payload['intent'] = intent.model_dump(mode='json')
            if planned.status in ('UNSUPPORTED', 'NOT_RELEVANT'):
                raise QuestionBlocked('UNSUPPORTED', 'Ask about supported measures in this workspace. Forecasts, raw-row lookups and general questions are not supported.')
            if planned.status == 'CLARIFICATION_REQUIRED' and intent.meaning != 'BUSINESS_AMBIGUOUS':
                raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which business entity or measure should this question describe?')
            plan, reference = resolve(workspace, intent, source, resolved, request.question, chosen)
            intent = intent.model_copy(update={'base_dataset_id': plan.definition['base_dataset_id']})
            payload['intent'] = intent.model_dump(mode='json')
            selected_metric = next((i for i in resolved if i['row']['candidate_id'] == intent.metric_candidate_id), None)
            if selected_metric and selected_metric['row']['evidence']['definition']['metric_type'] == 'COUNT' and plan.definition['metric_type'] == 'COUNT_DISTINCT':
                intent = intent.model_copy(update={'metric_candidate_id': None})
            if not intent.metric_candidate_id:
                operand = next((n['column'] for n in plan.definition['formula']['nodes'] if n['op'] == 'COLUMN'), None)
                intent = QuestionIntent.model_validate({**intent.model_dump(), 'aggregation': plan.definition['metric_type'],
                                                       'measure': operand, 'base_dataset_id': plan.definition['base_dataset_id']})
                payload['intent'] = intent.model_dump(mode='json')
            ranges = [calendar_range(p, timezone_name) for p in intent.periods]
            if intent.question_type == 'COMPARISON':
                ranges.sort()
                if ranges[0] == ranges[1]:
                    raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Choose two different periods to compare.')
            cache_key = digest([asdict(plan), intent.model_dump(mode='json'), ranges, timezone_name, ALGORITHM_VERSION])
            with repository_transaction() as conn:
                existing = repository.cached(conn, workspace, cache_key) if not refresh_id else None
            if existing:
                current_source, current_resolved = snapshot(workspace, user)
                answer = public(existing, current_source, current_resolved, True)
                if answer['freshness'] == 'FRESH':
                    if existing['question_hash'] == digest([request.question, request.timezone, request.previous_answer_id]):
                        return answer
                    # A paraphrase can reuse arithmetic while retaining its own immutable question/history.
                    reused_output = existing['internal'].get('execution_output')
            computation_started = time.monotonic()
            if reused_output is not None:
                output = reused_output
            elif plan.candidate_id:
                # Optional-review metrics execute without an approval interaction.
                state = analytics.refresh_analytics(workspace, user, plan.candidate_id)
                metric = next(m for m in state['metrics'] if m['candidate_id'] == plan.candidate_id)
                if metric['status'] != 'FRESH':
                    if metric['status'] == 'FAILED':
                        raise QuestionBlocked('FAILED', 'This calculation failed safely. Retry the answer when the required inputs are available.')
                    raise QuestionBlocked('REFRESH_REQUIRED', 'Refresh the required dataset analytics before answering.')
                output = metric['output']
            else:
                with repository_transaction() as conn:
                    handles = analytics.load_sources(conn, plan)
                output = execute(plan, intent, analytics.read_frames(handles, BoundedMetricArtifacts.create()), ranges, timezone_name)
            if time.monotonic() - computation_started > EXECUTION_TIMEOUT:
                raise QuestionBlocked('FAILED', 'This calculation exceeded the execution time limit. Try a smaller question.')
            internal.update(plan=asdict(plan), ranges=ranges, execution_output=output, result_reused=reused_output is not None)
            payload.update(status='NO_DATA' if not output['input_rows'] else 'ANSWERED', freshness='FRESH',
                result_kind=output['kind'], value=output['value'] if output['input_rows'] else None,
                groups=output['groups'], comparison=output.get('comparison'), metric_reference=reference,
                dimensions=[r.column for r in intent.dimensions] + ([intent.time_field.column] if intent.time_grain and intent.time_field else []),
                provenance=[{'dataset_id': p['dataset_id'], 'state_id': p['state_id'], 'gold_run_id': p['gold_run_id'],
                    'dataset_name': next(x['pin']['dataset_name'] for x in source['eligible'] if x['pin']['dataset_id'] == p['dataset_id'])} for p in plan.sources],
                relationship_versions=[{'relationship_id': j['relationship_id'], 'version': j['version']} for j in plan.joins],
                interpretation=plan.definition['name'] + ('; ' + ', '.join(f'{a} to {b} (end excluded)' for a, b in ranges) if ranges else ''),
                explanation='Calculated by the backend from current cumulative Gold using the validated metric definition.',
                caveats=(['Business interpretation selected for this question only.'] if chosen else []) +
                    (['Only the first 100 groups in dimension order are shown.'] if output.get('truncated') else []) +
                    ([f"Calendar timezone: {timezone_name}."] if intent.periods else []) +
                    ([f"Time display groups by {intent.time_grain.lower()}."] if intent.time_grain else []))
            payload['follow_ups'] = follow_ups(workspace, intent, source, resolved, timezone_name)
            if not output['input_rows']:
                payload['primary_answer'] = 'No data is available for this question and period.'
            elif output['kind'] == 'scalar':
                payload['primary_answer'] = format_value(output['value'], plan.definition['unit'])
            elif output['kind'] == 'comparison':
                comp = output['comparison']
                payload['primary_answer'] = f"{format_value(comp['values'][0])} → {format_value(comp['values'][1])}; change {format_value(comp['difference'])}"
                if comp['percentage_change'] is not None:
                    payload['primary_answer'] += f" ({format_value(comp['percentage_change'], 'PERCENTAGE')})"
                elif comp['values'][0] == 0:
                    payload['caveats'].append('Percentage change is unavailable because the baseline is zero.')
                if any(v is None for v in comp['values']):
                    payload['caveats'].append('A period has no records or no available measure; no value was imputed.')
            else:
                payload['primary_answer'] = f"{len(output['groups'])} groups calculated."
                if intent.question_type == 'TREND' and len(output['groups']) >= 2 and not intent.dimensions:
                    first, last = output['groups'][0], output['groups'][-1]
                    payload['primary_answer'] = f"First observed period: {format_value(first['value'])}; last observed period: {format_value(last['value'])}."
                if intent.time_grain:
                    payload['caveats'].append('Only observed periods are shown; missing periods were not filled with zero.')
        except QuestionBlocked as exc:
            payload.update(status=exc.status, primary_answer=exc.message, freshness='UNAVAILABLE')
            if exc.status == 'CLARIFICATION_REQUIRED' and payload['intent'] and (
                QuestionIntent.model_validate(payload['intent']).meaning == 'BUSINESS_AMBIGUOUS'
                or re.search(r'\b(revenue|profit|sales|lifetime|churn)\b', request.question, re.I)):
                options = choices_for(QuestionIntent.model_validate(payload['intent']), source)
                internal['choices'] = options
                payload['choices'] = [{'choice_id': i, 'label': o['label']} for i, o in enumerate(options)]
        except ProviderFailure as exc:
            payload['primary_answer'] = 'The planner could not prepare a safe answer. Retry later.'
            internal['failure_code'] = exc.code
        except ValueError as exc:
            limited = str(exc) in ('INPUT_LIMIT', 'OUTPUT_LIMIT', 'Artifact byte limit exceeded')
            payload.update(status='UNSUPPORTED' if limited else 'FAILED', freshness='UNAVAILABLE', value=None, groups=[], comparison=None,
                           primary_answer='This question exceeds the supported calculation size. Try a smaller question.' if limited else 'The current inputs could not be calculated safely. Retry after checking dataset analytics.')
            internal['failure_code'] = 'INPUT_LIMIT' if limited else 'EXECUTION_FAILED'
        except Exception:
            # No raw exception bodies, question text, rows, provider output or storage paths in logs.
            internal['failure_code'] = 'EXECUTION_FAILED'
            payload.update(status='FAILED', freshness='UNAVAILABLE', value=None, groups=[], comparison=None,
                           primary_answer='Could not calculate this question safely. Retry when the service is available.')
        with repository_transaction() as conn:
            # Short publication locks protect current heads; IO/provider work happened outside.
            current_source = metric_service.gather(conn, workspace, user, True)
            current_resolved = analytics.resolve(conn, workspace, user)
            if payload['status'] in ('ANSWERED', 'NO_DATA'):
                try:
                    current_plan, _ = resolve(workspace, intent, current_source, current_resolved, request.question, chosen)
                    if current_plan.signature != plan.signature:
                        raise QuestionBlocked('REFRESH_REQUIRED', 'Inputs changed during calculation')
                except QuestionBlocked:
                    payload.update(status='REFRESH_REQUIRED', freshness='UNAVAILABLE', value=None, groups=[], comparison=None,
                                   primary_answer='Inputs changed during calculation. Refresh and retry this question.')
            # Reject a malformed answer before immutable publication, not merely at API serialization.
            WorkspaceAnswer.model_validate({**payload, 'answer_id': 0, 'workspace_id': workspace,
                                            'computed_at': datetime.now(timezone.utc).isoformat()})
            row = repository.insert(conn, workspace, request, planner_key, cache_key, payload, internal)
        logger.info('Workspace answer workspace=%s answer=%s status=%s duration=%.3f algorithm=%s',
                    workspace, row['answer_id'], payload['status'], time.monotonic() - started, ALGORITHM_VERSION)
        return public(row, current_source, current_resolved, reused_output is not None)


def history(workspace, user, answer_id=None):
    validate_workspace(workspace, user)
    source, resolved = snapshot(workspace, user)
    with repository_transaction() as conn:
        rows = [owned(conn, workspace, answer_id)] if answer_id else repository.recent(conn, workspace)
    values = [public(row, source, resolved) for row in rows]
    return values[0] if answer_id else {'workspace_id': workspace, 'answers': values}


def readiness(workspace, user):
    validate_workspace(workspace, user)
    source, resolved = snapshot(workspace, user)
    questions = []
    for metric in resolved:
        if metric['plan']:
            questions.append(f"What is {metric['plan'].definition['name']}?")
    for item in source['eligible']:
        i = QuestionIntent(base_dataset_id=item['pin']['dataset_id'], aggregation='COUNT', question_type='COUNT')
        try:
            resolve(workspace, i, source, resolved)
            questions.append(f"How many {item['suggestion']['reasoning']['entity']['primary']['label']} are there?")
        except QuestionBlocked:
            pass
        record = QuestionIntent(base_dataset_id=item['pin']['dataset_id'], aggregation='COUNT')
        try:
            resolve(workspace, record, source, resolved)
            questions.append(f"What is the record count for {item['pin']['dataset_name']}?")
        except QuestionBlocked:
            pass
    state = 'PARTIAL' if questions and source['coverage']['partial'] else 'READY' if questions else 'NEEDS_REFRESH' if source['members'] else 'NOT_READY'
    return {'workspace_id': workspace, 'suggested_questions': list(dict.fromkeys(questions))[:6],
            'status': state, 'ready': bool(questions), 'message': 'Ask about current trusted workspace measures.' if questions else 'Current trusted measures are unavailable. Refresh dataset profiles, understanding and analytics as needed.' if source['members'] else 'Add a dataset to start asking questions.'}


def follow_ups(workspace, intent, source, resolved, timezone_name):
    proposals = []
    base_id = intent.base_dataset_id
    if not base_id and intent.metric_candidate_id:
        item = next((i for i in resolved if i['row']['candidate_id'] == intent.metric_candidate_id and i['plan']), None)
        base_id = item['plan'].definition['base_dataset_id'] if item else None
    base = next((i for i in source['eligible'] if i['pin']['dataset_id'] == base_id), None)
    if not base:
        return []
    for column in base['suggestion']['reasoning']['columns']:
        ref = ColumnRef(dataset_id=base_id, column=column['column_name'])
        if column['suggested_role'] in ('CATEGORY', 'DIMENSION', 'STATUS', 'CODE', 'BOOLEAN_FLAG'):
            proposals.append((f"Break this down by {ref.column}", intent.model_copy(update={'question_type': 'BREAKDOWN', 'dimensions': [ref]})))
        elif column['suggested_role'] == 'TIME_DIMENSION' and timezone_name:
            proposals.append((f"Show this by month using {ref.column}", intent.model_copy(update={'question_type': 'TIME_SERIES', 'time_field': ref, 'time_grain': 'MONTH'})))
    usable = []
    for text, proposed in proposals:
        try:
            resolve(workspace, proposed, source, resolved, chosen=True)
            usable.append(text)
        except QuestionBlocked:
            pass
    return usable[:4]
