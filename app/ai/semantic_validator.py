"""Reject entire invalid suggestions, never silently partially trust them."""
import re
from app.schemas.semantic_suggestion import SemanticReasoning


def validate_output(text, profile):
    if len(text.encode()) > 250000:
        raise ValueError('INVALID_OUTPUT')
    result = SemanticReasoning.model_validate_json(text)
    columns = {c['original_name']: c for c in profile['columns']}
    names = [c.column_name for c in result.columns]
    if len(names) != len(set(names)) or set(names) != set(columns):
        raise ValueError('INVALID_COLUMNS')
    for kind in (result.domain, result.subdomain, result.entity):
        if kind.primary.confidence < .5 and kind.primary.label is not None:
            raise ValueError('FORCED_LABEL')
    for suggestion in result.columns:
        evidence = columns[suggestion.column_name]
        role = suggestion.suggested_role
        roles = [role,*suggestion.secondary_hints]
        if evidence['authoritative_business_key'] and role != 'IDENTIFIER':
            raise ValueError('KEY_CONTRADICTION')
        if evidence['authoritative_event_time'] and ('TIME_DIMENSION' not in roles if evidence['authoritative_business_key'] else role != 'TIME_DIMENSION'):
            raise ValueError('TIME_CONTRADICTION')
        if 'MEASURE' in roles and (evidence['canonical_type'] not in ('INTEGER','DECIMAL')
            or evidence['identifier_candidate'] or set(evidence['tokens']) & {'id','code','year','postal','zip'}):
            raise ValueError('TYPE_ROLE_CONTRADICTION')
        if 'TIME_DIMENSION' in roles and not evidence['datetime_candidate']:
            raise ValueError('TYPE_ROLE_CONTRADICTION')
        if 'BOOLEAN_FLAG' in roles and evidence['canonical_type'] != 'BOOLEAN':
            raise ValueError('TYPE_ROLE_CONTRADICTION')
        if evidence['authoritative_business_key'] and re.search(r'\b(?:not|no|change|replace|remove|override)\b.{0,35}\b(?:key|identifier)\b',suggestion.business_meaning+' '+suggestion.rationale,re.I):
            raise ValueError('KEY_CONTRADICTION')
    prose = []
    for kind in (result.domain,result.subdomain,result.entity):
        for candidate in [kind.primary,*kind.alternatives]:
            prose.extend([candidate.label or '', candidate.rationale])
    for c in result.columns:
        prose.extend([c.business_meaning,c.rationale,*c.warnings])
    prose.extend([*result.warnings,*result.unresolved_questions])
    for value in prose:
        if len(value) > 500 or re.search(r's3://|https?://|[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b(?:api[_ -]?key|password|bearer|confirmed pii|guaranteed|foreign key|join to|references? (?:the )?(?:table|dataset)|kpi|definitely|proven|certainly)\b', value, re.I):
            raise ValueError('UNSAFE_OR_OUT_OF_SCOPE')
    return result
