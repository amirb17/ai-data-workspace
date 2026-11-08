"""Offline inventory of the actual registered app, not an OpenAPI allowlist.

Use inventory(app) under the existing offline runner. No network/DB work here.
"""
from app.security.public_launch import permitted, http_routes


def dependencies(node):
    result = []
    for child in getattr(node, 'dependencies', []):
        call = child.call
        result.append(call.__module__+'.'+call.__qualname__)
        result.extend(dependencies(child))
    return sorted(set(result))


def inventory(app):
    result = []
    for route in http_routes(app.routes):
        for method in sorted(getattr(route, 'methods', [])):
            endpoint = route.endpoint.__module__+'.'+route.endpoint.__qualname__
            allowed = permitted(route, method)
            diagnostic = route.path in ('/health','/health/db','/docs','/redoc','/openapi.json','/docs/oauth2-redirect')
            auth = route.path.startswith('/auth/')
            classification = (5 if diagnostic else 1 if auth and route.path not in ('/auth/profile','/auth/change-password')
                              else 3 if auth else 2 if allowed else 4)
            if method=='POST' and route.path in ('/workspaces','/workspaces/{workspace_id}/datasets'):
                classification=3
            deps = dependencies(getattr(route,'dependant',None))
            authority = ('Session/CSRF handshake or one-use OIDC state; account-changing auth uses existing durable limits'
                         if auth else 'Authenticated user + current workspace/dataset ownership' if allowed and 'workspace_id' in route.path
                         else 'Authenticated user + upload-derived workspace/dataset ownership' if allowed and route.path.startswith('/files/')
                         else 'Authenticated current user' if route.path == '/me'
                         else 'Internal diagnostics; no resource authority' if diagnostic
                         else 'Legacy handler/service ownership checks; not approved for public exposure')
            if not allowed and not diagnostic and not auth:
                if route.path.startswith('/analytics/'):
                    authority='Router authorize_version: authenticated schema version resolves dataset/workspace, then validate_scope'
                elif route.path.startswith('/files/dataset-version-files/'):
                    authority='owned_rule_context/rule_mutation: association resolves dataset/workspace and current owners'
                elif route.path.startswith('/files/'):
                    authority='Authenticated principal; upload-derived or supplied workspace/dataset validated by service; blocked regardless'
                elif '{dataset_id}' in route.path:
                    authority='Authenticated principal + dataset/workspace validate_scope in service; legacy approval is not a public grant'
                elif '{workspace_id}' in route.path:
                    authority='Authenticated principal + workspace/model/query ownership in service; child membership/pins vary by operation'
            effects = ('Database/session/OIDC/email side effects; Google start/callback GET intentionally stateful'
                       if auth else 'Database liveness query' if route.path=='/health/db'
                       else 'Bounded read/preview; session activity timestamp may update' if allowed
                       else 'Legacy computation, mutation, object I/O or unreviewed response; denied regardless of HTTP verb')
            if 'suggestions' in route.path or (route.path.startswith('/analytics/') and method=='GET'):
                effects += '; GET may invoke AI or artifact-backed analytics (not a safe read grant)'
            if 'business-rules' in route.path and method=='GET':
                effects += '; rule questions call generate_rule_suggestions'
            if route.path in ('/files/initiate','/files/complete','/files/upload'):
                effects += '; legacy S3 ingestion is not an approved durable public upload path with processing off'
            result.append(dict(method=method,path=route.path,endpoint=endpoint,router=route.endpoint.__module__,
                classification=classification,public_allowed=allowed,dependencies=deps,
                csrf='Existing signed double-submit + exact Origin for every non-GET/HEAD/OPTIONS in session mode' if method not in ('GET','HEAD','OPTIONS') else 'No mutation CSRF; OIDC state/PKCE for callback',
                authority=authority,side_effects=effects))
    return result
