# Authentication configuration

Use `.env.example` as a variable inventory; it contains no working credentials.
Keep real values in private environment configuration. Apply the separately
approved 0027 before enabling session authentication against public.

Set AUTH_MODE=session and a cryptographically random AUTH_SECRET of at least
32 bytes. Production requires APP_ENV=production, AUTH_COOKIE_SECURE=true and
HTTPS AUTH_FRONTEND_URL / AUTH_BACKEND_URL with no path/query. Frontend and API
must be same-site for the V1 SameSite=Lax cookies (same-site HTTPS subdomains
are supported). VITE_API_BASE_URL must point to that API. Local development
uses matching localhost hosts with separate ports. Do not mix localhost and
127.0.0.1 in frontend/API origins. CORS accepts only the configured frontend
origin with credentials; mutations additionally require that exact Origin and
a valid signed CSRF cookie/header pair.

Session cookies are host-only, HttpOnly, SameSite=Lax, path=/, with Secure and
the __Host- prefix under HTTPS. Idle expiry is thirty minutes; absolute expiry
is twelve hours. Reset/change password revokes all sessions. No refresh token
or long-lived authentication secret is stored in browser persistence. A
changed AUTH_SECRET invalidates CSRF and OIDC handshakes; existing opaque
sessions require explicit revocation if rotating after a compromise.

SMTP delivery: AUTH_EMAIL_BACKEND=smtp, SMTP_HOST, SMTP_FROM and SMTP_PORT;
SMTP_USERNAME / SMTP_PASSWORD if required. STARTTLS is mandatory by default.
Use SMTP_SSL=true with the provider's implicit-TLS port where appropriate.
Delivery runs after the generic HTTP response so network delivery latency does
not identify existing accounts. Failures produce a safe audit category.
Background delivery is in-process, not a durable email queue; process failure
can require a resend after the cooldown.

For explicit local development only, AUTH_EMAIL_BACKEND=development and
AUTH_DEV_MAILBOX_DIR may point to a private absolute directory outside every
web-served location. The adapter refuses frontend/. On Windows use a directory
under the current user's private profile with restricted ACLs; POSIX creation
modes do not establish Windows ACLs. Codes are not returned by HTTP or printed
to ordinary logs. This operator mailbox is for synthetic testing and cannot
establish real email ownership for adoption. Never enable it in production.

Google: configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET for a Google Cloud
web application, consent/test users as required, and register exactly
AUTH_BACKEND_URL/auth/google/callback. These credentials are separate from
Gemini API keys. Authlib handles discovery, signing keys, issuer/audience,
expiry, state, nonce and S256 PKCE validation. No Google token is persisted or
sent to the frontend. Existing identities resolve by Google sub. A matching
verified password account auto-links only for Google-authoritative Gmail or
hosted-domain identities. Other matching emails require the signed-in same
account's explicit Settings connection. Unverified or disabled accounts never
silently link. Callback errors are generic and callback queries are redacted
from Uvicorn access logs; do not configure infrastructure to log auth bodies,
cookie headers or callback query strings.

Production deployment must restrict trusted reverse proxies, terminate HTTPS
correctly, protect environment values and keep operational HTML/API responses
out of shared caches. Forwarded client addresses should be accepted only from
known proxies; application code does not trust arbitrary forwarding headers.
Persistent auth tables need an operator retention policy for expired sessions,
challenges, rate buckets and security events. This phase adds no roles, billing
or organization administration.

An explicitly enabled legacy development principal remains available only for
trusted local development. It does not regain access to adopted resources. AUTH_MODE=development is
forbidden outside APP_ENV=development and still requires ENABLE_DEV_IDENTITY
and a stable DEV_USER_KEY. Production and real sign-in acceptance use session
mode. No user can select a development owner through a request.

References: [Authlib Starlette integration](https://docs.authlib.org/en/latest/client/starlette.html),
[Google OIDC](https://developers.google.com/identity/openid-connect),
[OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html),
[OWASP sessions](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

Verified Google accounts may establish a first password through the same real
email recovery flow after the user-approved Phase 10 policy update. The account
must be ACTIVE with a verified email and an existing Google identity (or an
existing password credential). Fresh one-use email proof is still required;
reset revokes existing sessions and preserves user ID, Google linkage and
ownership. SMTP_FROM must be a syntactically valid provider-verified sender
with the DataRise AI display name; a populated placeholder is not sufficient.
