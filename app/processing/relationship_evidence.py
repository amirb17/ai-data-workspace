"""Bounded exact verification with no coercion, AI, storage, or public key values."""
import hashlib
import json
from collections import defaultdict
from app.processing.append_engine import scalar
from app.processing.row_identity import normalize_value
from app.processing.semantic_evidence import name_features
from app.schemas.relationships import RelationshipEvidence

ALGORITHM_VERSION = 2
LIMITS = {'datasets': 20, 'columns': 400, 'pairs': 200, 'rows_per_dataset': 100000,
          'total_rows': 500000, 'artifact_bytes': 64000000, 'total_artifact_bytes': 128000000,
          'value_characters': 1024, 'total_value_bytes': 32000000}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()

def structure(parent, child, pc, cc):
    return digest([parent['pin']['dataset_version_id'], child['pin']['dataset_version_id'],
                   parent['policy']['business_keys'], child['policy']['business_keys'],
                   parent['policy']['normalization_version'], child['policy']['normalization_version'],
                   pc, cc, parent['policy']['schema_columns'], child['policy']['schema_columns'],
                   next((x['canonical_type'] for x in parent['profile']['columns'] if x['original_name']==pc),None),
                   next((x['canonical_type'] for x in child['profile']['columns'] if x['original_name']==cc),None)])

def name_match(parent, pc, cc):
    a, at = name_features(pc['original_name']); b, bt = name_features(cc['original_name'])
    if a == b: return ['NORMALIZED_NAME_EQUAL']
    entity = {t.rstrip('s') for t in name_features(parent['profile']['dataset_name'])[1]}
    if a in ('id','key','uuid') and set(bt) & entity and set(bt) & {'id','key','uuid'}:
        return ['ENTITY_QUALIFIED_IDENTIFIER']
    meaningful = (set(at) & set(bt)) - {'id','key','uuid'}
    if meaningful and set(at) & {'id','key','uuid'} and set(bt) & {'id','key','uuid'}:
        return ['IDENTIFIER_TOKEN_OVERLAP']
    return []

def propose(items):
    """Index reference-like columns by name/entity tokens before value IO. O(C + emitted pairs)."""
    indexes = defaultdict(list); exact = defaultdict(list)
    for item in items:
        for col in item['profile']['columns']:
            exact[col['normalized_name']].append((item,col))
            if col['canonical_type'] not in ('TEXT','INTEGER','DECIMAL'): continue
            _, tokens = name_features(col['original_name'])
            if not col['identifier_candidate'] and not set(tokens) & {'id','key','uuid'}: continue
            for token in set(tokens): indexes[token].append((item,col))
    pairs = {}; suppressed = 0
    for parent in items:
        composite = len(parent['policy']['business_keys']) > 1
        for pc in parent['profile']['columns']:
            if not pc['identifier_candidate']: continue
            tokens = set(name_features(pc['original_name'])[1])
            if pc['normalized_name'] in ('id','key','uuid'):
                tokens = {t.rstrip('s') for t in name_features(parent['profile']['dataset_name'])[1]}
            # Generic id/key tokens alone would enumerate all entities. Include exact names separately.
            lookup = tokens - {'id','key','uuid'}
            possible = {(i['pin']['dataset_id'], c['original_name']):(i,c)
                        for token in lookup for i,c in indexes.get(token, [])}
            for i,c in exact.get(pc['normalized_name'],[]):
                possible[(i['pin']['dataset_id'],c['original_name'])] = (i,c)
            for child, cc in possible.values():
                if parent['pin']['dataset_id'] == child['pin']['dataset_id']: continue
                features = name_match(parent, pc, cc)
                if not features: continue
                # A component of a composite configured key is never promoted to a whole key.
                if composite and pc['authoritative_business_key']:
                    suppressed += 1; continue
                if pc['canonical_type'] != cc['canonical_type']: continue
                if pc['canonical_type'] not in ('TEXT','INTEGER','DECIMAL'): continue
                key = digest([parent['pin']['dataset_id'],[pc['original_name']],child['pin']['dataset_id'],[cc['original_name']]])
                pairs[key] = (parent, child, pc, cc, features)
                if len(pairs) > LIMITS['pairs']: raise ValueError('Candidate pair limit exceeded')
    # Two directions of the same physical pair are one tentative relationship, not two approvals.
    selected = {}
    def priority(pair):
        parent,_,col,_,_ = pair
        return (bool(col['distinct_ratio']==1 and col['null_ratio']==0),
                parent['policy']['business_keys']==[col['original_name']],-parent['pin']['dataset_id'])
    for key,pair in sorted(pairs.items()):
        p,c,pc,cc,_ = pair
        unordered = tuple(sorted([(p['pin']['dataset_id'],pc['original_name']),(c['pin']['dataset_id'],cc['original_name'])]))
        old = selected.get(unordered)
        if not old or priority(pair)>priority(old[1]): selected[unordered] = (key,pair)
    return dict(selected.values()), suppressed

def values(frame, column, budget):
    encoded = []; nulls = 0
    for raw in frame[column].tolist():
        value = scalar(raw)
        if value is None: nulls += 1; continue
        if isinstance(value, float) and abs(value) >= 2**53:
            raise ValueError('Unsafe numeric identity precision')
        if isinstance(value, str) and len(value) > LIMITS['value_characters']:
            raise ValueError('Identifier length limit exceeded')
        key = json.dumps(normalize_value(value), ensure_ascii=True, separators=(',', ':'))
        budget[0] += len(key.encode())
        if budget[0] > LIMITS['total_value_bytes']: raise ValueError('Value memory budget exceeded')
        encoded.append(key)
    unique = set(encoded); rows = len(frame); nonnull = rows - nulls
    return unique, {'rows':rows,'non_null_rows':nonnull,'distinct_keys':len(unique),
                    'duplicate_rows':nonnull-len(unique),'null_ratio':nulls/rows if rows else None,
                    'uniqueness_ratio':len(unique)/nonnull if nonnull else None}

def verify(workspace, pairs, frames):
    budget = [0]; cache = {}; evidence = []
    def side(item, col):
        key = (item['pin']['dataset_id'],col['original_name'])
        if key not in cache: cache[key] = values(frames[key[0]],key[1],budget)
        return cache[key]
    for key,(p,c,pc,cc,features) in sorted(pairs.items()):
        pv, ps = side(p,pc); cv, cs = side(c,cc); matched = len(pv & cv)
        coverage = matched/len(cv) if cv else None
        parent_unique = bool(pv) and ps['duplicate_rows']==0 and ps['null_ratio']==0
        child_unique = bool(cv) and cs['duplicate_rows']==0
        cardinality = ('ONE_TO_ONE' if child_unique else 'ONE_TO_MANY') if parent_unique else (
            'MANY_TO_MANY_CANDIDATE' if pv and cv and ps['duplicate_rows'] and cs['duplicate_rows'] else 'UNKNOWN')
        if matched==0: cardinality='UNKNOWN'
        configured = p['policy']['business_keys'] == [pc['original_name']]
        components = [
            {'name':'name','points':20 if 'NORMALIZED_NAME_EQUAL' in features else 15,'explanation':', '.join(features)},
            {'name':'type','points':15,'explanation':'Exact canonical type; no coercion'},
            {'name':'parent_uniqueness','points':20 if parent_unique else 0,'explanation':'Exact unique non-null parent values required'},
            {'name':'coverage','points':round(30*(coverage or 0)),'explanation':'Distinct non-null child coverage'},
            {'name':'configured_key','points':15 if configured else 0,'explanation':'Whole configured single-column business key'}]
        warnings = []
        if not parent_unique: warnings.append('Parent values are empty, repeated or null; a simple relationship cannot be confirmed.')
        if coverage is None: warnings.append('No non-null child values; coverage is unavailable.')
        elif coverage < 1: warnings.append('Some distinct non-null child references are absent from the parent.')
        if cs['null_ratio']: warnings.append('Null child references are reported separately; optionality requires review.')
        if not configured: warnings.append('Unique-side candidate only; no configured parent business key.')
        if not p.get('semantic_roles') or not c.get('semantic_roles'):
            warnings.append('Current dataset AI context is unavailable; structural evidence remains deterministic.')
        def endpoint(item,col):
            return {'dataset_id':item['pin']['dataset_id'],'dataset_name':item['profile']['dataset_name'],
                    'columns':[col['original_name']],'normalized_columns':[col['normalized_name']]}
        result = {'workspace_id':workspace,'candidate_key':key,'parent':endpoint(p,pc),'child':endpoint(c,cc),
                  'candidate_direction':'PARENT_TO_CHILD','candidate_cardinality':cardinality,
                  'signals':{'name_features':features,'datatype_compatible':True,'parent_type':pc['canonical_type'],
                    'child_type':cc['canonical_type'],'configured_parent_key':configured,'parent':ps,'child':cs,
                    'overlap':{'method':'EXACT_BOUNDED','child_non_null_distinct_keys':len(cv),'matched_distinct_keys':matched,
                        'missing_distinct_keys':len(cv)-matched,'child_to_parent_coverage':coverage,
                        'parent_referenced_ratio':matched/len(pv) if pv else None},
                    'parent_semantic_role':p.get('semantic_roles',{}).get(pc['original_name']),
                    'child_semantic_role':c.get('semantic_roles',{}).get(cc['original_name'])},
                  'deterministic_score':sum(x['points'] for x in components),'score_components':components,
                  'warnings':warnings,'can_confirm':bool(parent_unique and coverage==1),
                  'structural_signature':structure(p,c,pc['original_name'],cc['original_name']),
                  'source_pins':[p['pin'],c['pin']]}
        evidence.append(RelationshipEvidence.model_validate(result).model_dump())
    return evidence
