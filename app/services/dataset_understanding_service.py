"""Explicit dataset-only AI suggestions built from current deterministic evidence."""
import hashlib
import json
import logging
import time
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import repository_transaction
from app.db import semantic_suggestion_repository as repository
from app.services.dataset_profile_service import read_profile, readiness as profile_readiness
from app.ai.semantic_handoff import build_handoff, SEMANTIC_VERSION
from app.ai.semantic_provider import GeminiSemanticProvider, ProviderFailure
from app.ai.semantic_validator import validate_output
from app.schemas.semantic_suggestion import SuggestionRecord

logger = logging.getLogger(__name__)


def get_provider():
    return GeminiSemanticProvider()


def configuration(provider):
    return hashlib.sha256(json.dumps([provider.provider,provider.model,provider.strategy,SEMANTIC_VERSION]).encode()).hexdigest()


def read_understanding(workspace,dataset,user,include_content=True):
    profile = (read_profile if include_content else profile_readiness)(workspace,dataset,user)  # validates owned scope first
    provider = get_provider()
    run = repository.matching(dataset,profile['profile_id'],SEMANTIC_VERSION,configuration(provider))
    status, failure = 'NOT_GENERATED', None
    if not profile['profile_ready']:
        status = 'STALE' if repository.has_history(dataset) else 'NOT_GENERATED'
    elif run:
        status,failure = run['status'],run['failure_code']
        if status == 'GENERATING' and not repository.active(workspace,dataset):
            status,failure = 'FAILED','INTERRUPTED'
    elif repository.has_history(dataset):
        status = 'STALE'
    result = {'workspace_id':workspace,'dataset_id':dataset,'dataset_name':profile['dataset_name'],
        'status':status,'profile_freshness':profile['freshness'],
        'can_generate':profile['profile_ready'] and status not in ('READY','GENERATING'),
        'failure_code':failure,'source_profile_id':profile['profile_id'],
        'source_state_id':profile['current_state_id'],'suggestion':None,'evidence':[]}
    if status == 'READY' and include_content:
        record = SuggestionRecord.model_validate(run).model_dump()
        after = read_profile(workspace,dataset,user)
        if not after['profile_ready'] or after['profile_id'] != profile['profile_id']:
            return {**result,'status':'STALE','can_generate':after['profile_ready'],'profile_freshness':after['freshness']}
        result['suggestion'] = record
        result['evidence'] = [{key:c[key] for key in ('original_name','canonical_type','null_ratio','distinct_ratio',
            'authoritative_business_key','business_key_position','authoritative_event_time','sensitivity_hints')} for c in profile['columns']]
    return result


def generate_understanding(workspace,dataset,user):
    # Scope is checked before coordinator acquisition or provider/configuration work.
    profile = read_profile(workspace,dataset,user)
    if not profile['profile_ready']:
        raise ValueError('Refresh the Data Profile before running AI understanding')
    started = time.monotonic()
    with repository.generation_lock(workspace,dataset):
        profile = read_profile(workspace,dataset,user)
        if not profile['profile_ready']:
            raise ValueError('Refresh the Data Profile before running AI understanding')
        provider = get_provider()
        config = configuration(provider)
        existing = repository.matching(dataset,profile['profile_id'],SEMANTIC_VERSION,config)
        if existing and existing['status']=='READY':
            return read_understanding(workspace,dataset,user)
        bundle,encoded = build_handoff(profile)
        with repository_transaction() as conn:
            c = conn.cursor(row_factory=dict_row)
            head = c.execute('SELECT current_profile_id,current_state_id,profile_status FROM datasets WHERE dataset_id=%s FOR UPDATE',(dataset,)).fetchone()
            if head['current_profile_id'] != profile['profile_id'] or head['current_state_id'] != profile['current_state_id'] or head['profile_status'] != 'READY':
                raise ValueError('Profile changed; refresh before analysis')
            if existing:
                run = c.execute("UPDATE semantic_suggestions SET status='GENERATING',completed_at=NULL,failure_code=NULL WHERE suggestion_id=%s RETURNING *",(existing['suggestion_id'],)).fetchone()
            else:
                version = c.execute('SELECT COALESCE(MAX(suggestion_version),0)+1 AS n FROM semantic_suggestions WHERE dataset_id=%s',(dataset,)).fetchone()['n']
                run = c.execute('''INSERT INTO semantic_suggestions(workspace_id,dataset_id,owner_key,source_profile_id,source_state_id,
                    source_state_version,suggestion_version,semantic_model_version,configuration_key,provider,model,status,compact_mode,input_bytes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'GENERATING',%s,%s) RETURNING *''',
                    (workspace,dataset,user['owner_key'],profile['profile_id'],profile['current_state_id'],profile['state_version'],
                    version,SEMANTIC_VERSION,config,provider.provider,provider.model,bundle['compact_mode'],len(encoded.encode()))).fetchone()
        failure = None
        try:
            output = provider.generate(encoded)
            try:
                reasoning = validate_output(output.text,profile)
            except (ValueError,TypeError):
                raise ProviderFailure('INVALID_OUTPUT') from None
            # A model response is accepted only if its pinned evidence is still current.
            with repository_transaction() as conn:
                head = conn.cursor(row_factory=dict_row).execute('SELECT current_profile_id,current_state_id,profile_status FROM datasets WHERE dataset_id=%s FOR UPDATE',(dataset,)).fetchone()
                if head['current_profile_id'] != profile['profile_id'] or head['current_state_id'] != profile['current_state_id'] or head['profile_status'] != 'READY':
                    raise ProviderFailure('GENERATION_FAILED')
                conn.execute("UPDATE semantic_suggestions SET status='READY',reasoning=%s,resolved_model=%s,completed_at=NOW() WHERE suggestion_id=%s",
                    (Jsonb(reasoning.model_dump()),output.resolved_model,run['suggestion_id']))
        except Exception as exc:
            failure = exc.code if isinstance(exc,ProviderFailure) else 'GENERATION_FAILED'
            with repository_transaction() as conn:
                conn.execute("UPDATE semantic_suggestions SET status='FAILED',failure_code=%s,completed_at=NOW() WHERE suggestion_id=%s AND status<>'READY'",(failure,run['suggestion_id']))
        logger.info('Understanding workspace=%s dataset=%s profile=%s suggestion=%s provider=%s model=%s algorithm=%s columns=%s compact=%s input_bytes=%s duration=%.3f status=%s validation=%s',
            workspace,dataset,profile['profile_id'],run['suggestion_id'],provider.provider,provider.model,SEMANTIC_VERSION,
            len(profile['columns']),bundle['compact_mode'],len(encoded.encode()),time.monotonic()-started,
            'FAILED' if failure else 'READY',failure or 'VALID')
        return read_understanding(workspace,dataset,user)
