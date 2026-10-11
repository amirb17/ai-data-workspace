"""Deterministic dates, comparisons, ranking and formatting over the safe metric engine."""
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import math
import pandas as pd
from app.processing.metric_execution import PandasMetricExecutionEngine
from app.processing.workspace_question_resolution import QuestionBlocked


def question_timezone(question, default):
    explicit = re.findall(r'\b[A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?\b', question)
    if re.search(r'\b(?:UTC|GMT)\b', question):
        explicit.append('UTC')
    if len(set(explicit)) > 1:
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which one timezone should define this analysis?')
    if explicit:
        try:
            ZoneInfo(explicit[0])
        except (KeyError, ValueError):
            raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Use a specific valid business timezone for this analysis.') from None
        return explicit[0]
    if re.search(r'\b(?:CST|EST|IST|PST)\b', question):
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which specific business timezone does this abbreviation mean?')
    return default


def calendar_range(period, timezone, now=None):
    if not period.relative:
        return period.start.isoformat(), period.end.isoformat()
    if not timezone:
        raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which timezone should define the requested calendar period?')
    today = (now or datetime.now(ZoneInfo(timezone))).astimezone(ZoneInfo(timezone)).date()
    relative = period.relative
    if relative in ('TODAY', 'YESTERDAY'):
        start = today - timedelta(days=relative == 'YESTERDAY')
        end = start + timedelta(days=1)
    elif relative.endswith('WEEK'):
        start = today - timedelta(days=today.weekday())
        if relative.startswith('LAST'):
            start -= timedelta(days=7)
        end = start + timedelta(days=7)
    else:
        months = 1 if relative.endswith('MONTH') else 3 if relative.endswith('QUARTER') else 12
        month = today.month if months == 1 else ((today.month - 1) // 3) * 3 + 1 if months == 3 else 1
        index = today.year * 12 + month - 1 - (months if relative.startswith('LAST') else 0)
        start = today.replace(year=index // 12, month=index % 12 + 1, day=1)
        end = today.replace(year=(index + months) // 12, month=(index + months) % 12 + 1, day=1)
    return start.isoformat(), end.isoformat()


def format_value(value, unit='NUMBER'):
    if value is None:
        return 'Unavailable'
    if isinstance(value, str):
        # Exact large integer strings retain precision.
        return f'{int(value):,}' if value.lstrip('-').isdigit() else value
    if not math.isfinite(value):
        raise ValueError('NONFINITE_RESULT')
    text = f'{value:,.0f}' if value == int(value) else f'{value:,.2f}'
    return text + ('%' if unit == 'PERCENTAGE' else '')


def execute(plan, intent, frames, ranges, timezone):
    periods = ranges or [None]
    outputs = []
    for period in periods:
        scoped = {k: v.copy() for k, v in frames.items()}
        definition = dict(plan.definition)
        time_ref = intent.time_field or definition.get('time_field')
        if hasattr(time_ref, 'model_dump'):
            time_ref = time_ref.model_dump()
        if time_ref:
            dataset, column = time_ref['dataset_id'], time_ref['column']
            values = scoped[dataset][column]
            if not pd.api.types.is_datetime64_any_dtype(values):
                raise QuestionBlocked('UNSUPPORTED', 'The selected date is not a supported typed date.')
            if values.dt.tz is not None:
                if not timezone:
                    raise QuestionBlocked('CLARIFICATION_REQUIRED', 'Which timezone should define this date analysis?')
                values = values.dt.tz_convert(timezone).dt.tz_localize(None)
            scoped[dataset][column] = values
            if period:
                if dataset != definition['base_dataset_id']:
                    raise QuestionBlocked('UNSUPPORTED', 'Date ranges must use the business date on the measured dataset.')
                scoped[dataset] = scoped[dataset].loc[(values >= pd.Timestamp(period[0])) & (values < pd.Timestamp(period[1]))].copy()
            if intent.time_grain in ('QUARTER', 'YEAR'):
                scoped[dataset][column] = scoped[dataset][column].dt.to_period('Q' if intent.time_grain == 'QUARTER' else 'Y').dt.start_time
                definition['time_bucket'] = 'MONTH'
            elif intent.time_grain:
                definition['time_bucket'] = intent.time_grain
            else:
                # A range-only field was validated above but should not group the answer.
                definition['time_field'] = None
                definition['time_bucket'] = None
        output = PandasMetricExecutionEngine().execute(replace(plan, definition=definition), scoped)
        if output['truncated'] and intent.question_type == 'RANKING':
            raise QuestionBlocked('UNSUPPORTED', 'Ranking needs every group; this question exceeds the supported 100-group limit.')
        if intent.question_type == 'RANKING':
            # Numeric ordering of exact integer strings without floating precision loss.
            def number(group):
                value = group['value']
                return int(value) if isinstance(value, str) else value
            valid = [g for g in output['groups'] if g['value'] is not None]
            valid.sort(key=lambda g: tuple('' if d is None else str(d) for d in g['dimensions']))
            valid.sort(key=number, reverse=intent.direction == 'DESC')
            output['groups'] = valid[:intent.top_n]
        outputs.append(output)
    if intent.question_type == 'COMPARISON':
        values = [o['value'] if o['input_rows'] else None for o in outputs]
        values = [int(v) if isinstance(v, str) else v for v in values]
        difference = None if any(v is None for v in values) else values[1] - values[0]
        change = None if difference is None or values[0] == 0 else difference / abs(values[0]) * 100
        from app.processing.metric_execution import scalar
        return {'kind': 'comparison', 'value': None, 'groups': [], 'input_rows': sum(o['input_rows'] for o in outputs),
                'comparison': {'periods': ranges, 'values': [scalar(v) for v in values],
                               'difference': scalar(difference), 'percentage_change': scalar(change)},
                'truncated': False}
    return outputs[0]
