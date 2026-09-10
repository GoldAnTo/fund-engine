from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.api.v1 import tenant_context
from app.errors import PermissionDeniedError, UpstreamUnavailableError
from app.models.identity import ResearchUser
from app.security.oidc import OIDCSettings, OIDCVerifier


ISSUER = "https://identity.example.test/realms/research"
AUDIENCE = "fund-engine"
TENANT_CLAIM = "tenant_id"


class MutableJWKSFetcher:
    def __init__(self, documents: list[object]) -> None:
        self.documents = documents
        self.calls = 0

    def __call__(self, _url: str, _timeout_seconds: float) -> object:
        index = min(self.calls, len(self.documents) - 1)
        self.calls += 1
        return self.documents[index]


class FailingJWKSFetcher:
    def __init__(
        self,
        message: str = "private upstream detail",
        *,
        clock: MutableMonotonicClock | None = None,
        advance_seconds: float = 0.0,
    ) -> None:
        self.message = message
        self.clock = clock
        self.advance_seconds = advance_seconds
        self.calls = 0

    def __call__(self, _url: str, _timeout_seconds: float) -> object:
        self.calls += 1
        if self.clock is not None:
            self.clock.advance(self.advance_seconds)
        raise RuntimeError(self.message)


class SequencedJWKSFetcher:
    def __init__(self, outcomes: list[object | BaseException]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def __call__(self, _url: str, _timeout_seconds: float) -> object:
        index = min(self.calls, len(self.outcomes) - 1)
        self.calls += 1
        outcome = self.outcomes[index]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class MutableMonotonicClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def rsa_keys():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, *, kid: str = "key-1") -> dict[str, object]:
    value = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    value.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return value


def _settings() -> OIDCSettings:
    return OIDCSettings(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=f"{ISSUER}/protocol/openid-connect/certs",
        tenant_claim=TENANT_CLAIM,
    )


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "researcher-123",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
        TENANT_CLAIM: "team-a",
        "name": "  Alice Researcher  ",
        "email": "  Alice@Example.Test ",
        "roles": ["editor", "case_administrator"],
        "realm_access": {"roles": ["reviewer", "editor"]},
    }
    claims.update(overrides)
    return claims


def _token(
    private_key,
    *,
    kid: str = "key-1",
    algorithm: str = "RS256",
    claims: dict[str, object] | None = None,
) -> str:
    key: object = private_key
    if algorithm == "HS256":
        key = "test-symmetric-secret-that-is-long-enough"
    if algorithm == "none":
        key = ""
    return jwt.encode(
        claims or _claims(),
        key,
        algorithm=algorithm,
        headers={"kid": kid, "typ": "JWT"},
    )


def _verifier(fetcher: MutableJWKSFetcher) -> OIDCVerifier:
    return OIDCVerifier(settings=_settings(), fetch_jwks=fetcher)


def test_valid_rs256_token_resolves_stable_server_principal(
    cmd_session, rsa_keys
) -> None:
    fetcher = MutableJWKSFetcher([{"keys": [_jwk(rsa_keys)]}])
    principal = _verifier(fetcher).resolve_principal(
        _token(rsa_keys), session=cmd_session
    )

    user = cmd_session.scalar(select(ResearchUser))
    assert user is not None
    assert principal.user_id == user.id
    assert principal.issuer == ISSUER
    assert principal.subject == "researcher-123"
    assert principal.tenant_id == "team-a"
    assert principal.display_name == "Alice Researcher"
    assert principal.roles == frozenset({"editor", "reviewer", "case_administrator"})
    assert principal.actor == f"user:{user.id}"
    assert principal.server_actor == principal.actor
    assert principal.expires_at.tzinfo is UTC
    assert user.normalized_email == "alice@example.test"
    assert cmd_session.in_transaction()
    assert fetcher.calls == 1


def test_jwks_ignores_non_signing_keys_when_rs256_key_is_available(
    cmd_session, rsa_keys
) -> None:
    encryption_key = _jwk(rsa_keys, kid="encryption-key")
    encryption_key.update({"use": "enc", "alg": "RSA-OAEP"})
    fetcher = MutableJWKSFetcher(
        [{"keys": [_jwk(rsa_keys), encryption_key]}]
    )

    principal = _verifier(fetcher).resolve_principal(
        _token(rsa_keys), session=cmd_session
    )

    assert principal.subject == "researcher-123"
    assert fetcher.calls == 1


def test_production_fastapi_dependency_uses_oidc_and_ignores_x_actor(
    cmd_client,
    cmd_session,
    production_oidc_authentication,
    monkeypatch,
    rsa_keys,
) -> None:
    verifier = _verifier(MutableJWKSFetcher([{"keys": [_jwk(rsa_keys)]}]))
    monkeypatch.setattr(tenant_context, "get_oidc_verifier", lambda: verifier)
    commit_calls = 0
    real_commit = cmd_session.commit

    def tracked_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        real_commit()

    monkeypatch.setattr(cmd_session, "commit", tracked_commit)

    response = cmd_client.get(
        "/api/v1/research-session",
        headers={
            "Authorization": f"Bearer {_token(rsa_keys)}",
            "X-Actor": "human:forged",
        },
    )

    assert response.status_code == 200, response.text
    user = cmd_session.scalar(select(ResearchUser))
    assert user is not None
    user_id = user.id
    assert response.json()["user_id"] == str(user.id)
    assert response.json()["display_name"] == "Alice Researcher"
    assert "human:forged" not in response.text
    assert commit_calls == 1

    cmd_session.rollback()
    cmd_session.expire_all()
    assert cmd_session.get(ResearchUser, user_id) is not None


def test_existing_user_keeps_id_and_updates_only_mutable_profile(
    cmd_session, rsa_keys
) -> None:
    verifier = _verifier(MutableJWKSFetcher([{"keys": [_jwk(rsa_keys)]}]))
    first = verifier.resolve_principal(_token(rsa_keys), session=cmd_session)
    updated_claims = _claims(
        name="Alice Updated",
        email="NEW@EXAMPLE.TEST",
        roles=["viewer"],
        realm_access={"roles": []},
    )
    second = verifier.resolve_principal(
        _token(rsa_keys, claims=updated_claims), session=cmd_session
    )

    user = cmd_session.get(ResearchUser, first.user_id)
    assert user is not None
    assert second.user_id == first.user_id
    assert user.issuer == ISSUER
    assert user.subject == "researcher-123"
    assert user.tenant_id == "team-a"
    assert user.display_name == "Alice Updated"
    assert user.normalized_email == "new@example.test"
    assert second.roles == frozenset({"viewer"})


def test_oidc_verifier_remains_transaction_neutral(
    cmd_session, monkeypatch, rsa_keys
) -> None:
    def forbidden_commit() -> None:
        raise AssertionError("OIDC resolution must not commit the request transaction")

    monkeypatch.setattr(cmd_session, "commit", forbidden_commit)
    principal = _verifier(
        MutableJWKSFetcher([{"keys": [_jwk(rsa_keys)]}])
    ).resolve_principal(_token(rsa_keys), session=cmd_session)

    assert cmd_session.get(ResearchUser, principal.user_id) is not None


def test_user_uniqueness_conflict_rolls_back_only_savepoint(
    cmd_session, rsa_keys
) -> None:
    verifier = _verifier(MutableJWKSFetcher([{"keys": [_jwk(rsa_keys)]}]))
    first = verifier.resolve_principal(_token(rsa_keys), session=cmd_session)

    with pytest.raises(
        PermissionDeniedError,
        match="research user identity conflicts with an existing account",
    ):
        verifier.resolve_principal(
            _token(
                rsa_keys,
                claims=_claims(
                    sub="different-subject",
                    email="alice@example.test",
                ),
            ),
            session=cmd_session,
        )

    assert cmd_session.get(ResearchUser, first.user_id) is not None
    assert (
        cmd_session.scalar(
            select(ResearchUser).where(ResearchUser.subject == "different-subject")
        )
        is None
    )


@pytest.mark.parametrize(
    ("claims", "algorithm"),
    [
        (_claims(iss="https://wrong.example.test"), "RS256"),
        (_claims(aud="wrong-audience"), "RS256"),
        (
            _claims(exp=int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())),
            "RS256",
        ),
        (
            _claims(iat=int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())),
            "RS256",
        ),
        (_claims(), "none"),
        (_claims(), "HS256"),
    ],
    ids=[
        "wrong-issuer",
        "wrong-audience",
        "expired",
        "future-iat",
        "unsigned",
        "symmetric",
    ],
)
def test_invalid_or_non_rs256_tokens_are_rejected(
    cmd_session, rsa_keys, claims, algorithm
) -> None:
    verifier = _verifier(MutableJWKSFetcher([{"keys": [_jwk(rsa_keys)]}]))
    with pytest.raises(PermissionDeniedError, match="OIDC token is not permitted"):
        verifier.resolve_principal(
            _token(rsa_keys, claims=claims, algorithm=algorithm),
            session=cmd_session,
        )


def test_token_signed_by_a_different_rsa_key_is_rejected(cmd_session, rsa_keys) -> None:
    untrusted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = _verifier(MutableJWKSFetcher([{"keys": [_jwk(rsa_keys)]}]))

    with pytest.raises(PermissionDeniedError, match="OIDC token is not permitted"):
        verifier.resolve_principal(_token(untrusted_key), session=cmd_session)


@pytest.mark.parametrize(
    "claims",
    [
        {key: value for key, value in _claims().items() if key != "sub"},
        {key: value for key, value in _claims().items() if key != TENANT_CLAIM},
        _claims(tenant_id="   "),
        _claims(roles="administrator"),
        _claims(realm_access=["administrator"]),
        _claims(realm_access={"roles": "administrator"}),
    ],
    ids=[
        "missing-subject",
        "missing-tenant",
        "blank-tenant",
        "malformed-top-level-roles",
        "malformed-realm-access",
        "malformed-realm-roles",
    ],
)
def test_missing_identity_claims_and_malformed_roles_are_rejected(
    cmd_session, rsa_keys, claims
) -> None:
    verifier = _verifier(MutableJWKSFetcher([{"keys": [_jwk(rsa_keys)]}]))
    with pytest.raises(PermissionDeniedError, match="OIDC token is not permitted"):
        verifier.resolve_principal(_token(rsa_keys, claims=claims), session=cmd_session)


def test_unknown_kid_forces_exactly_one_refresh_then_rejects(
    cmd_session, rsa_keys
) -> None:
    fetcher = MutableJWKSFetcher([{"keys": [_jwk(rsa_keys, kid="known")]}])
    verifier = _verifier(fetcher)

    with pytest.raises(PermissionDeniedError, match="OIDC token is not permitted"):
        verifier.resolve_principal(_token(rsa_keys, kid="unknown"), session=cmd_session)

    assert fetcher.calls == 2


def test_unknown_kid_refreshes_cached_jwks_after_rotation(
    cmd_session, rsa_keys
) -> None:
    rotated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fetcher = MutableJWKSFetcher(
        [
            {"keys": [_jwk(rsa_keys, kid="old")]},
            {"keys": [_jwk(rotated_key, kid="new")]},
        ]
    )
    verifier = _verifier(fetcher)
    verifier.resolve_principal(_token(rsa_keys, kid="old"), session=cmd_session)
    principal = verifier.resolve_principal(
        _token(rotated_key, kid="new"), session=cmd_session
    )

    assert principal.subject == "researcher-123"
    assert fetcher.calls == 2


def test_unknown_kid_forced_refresh_has_one_global_cooldown(rsa_keys) -> None:
    clock = MutableMonotonicClock()
    fetcher = MutableJWKSFetcher([{"keys": [_jwk(rsa_keys, kid="known")]}])
    verifier = OIDCVerifier(
        settings=_settings(),
        fetch_jwks=fetcher,
        monotonic_clock=clock,
    )
    verifier._signing_key("known")

    for index in range(12):
        with pytest.raises(PermissionDeniedError):
            verifier._signing_key(f"unknown-{index}")

    assert fetcher.calls == 2
    clock.advance(30.1)
    with pytest.raises(PermissionDeniedError):
        verifier._signing_key("unknown-after-cooldown")
    assert fetcher.calls == 3


def test_concurrent_unknown_kids_share_one_forced_refresh(rsa_keys) -> None:
    clock = MutableMonotonicClock()
    fetcher = MutableJWKSFetcher([{"keys": [_jwk(rsa_keys, kid="known")]}])
    verifier = OIDCVerifier(
        settings=_settings(),
        fetch_jwks=fetcher,
        monotonic_clock=clock,
    )
    verifier._signing_key("known")

    def resolve_unknown(index: int) -> str:
        with pytest.raises(PermissionDeniedError):
            verifier._signing_key(f"concurrent-unknown-{index}")
        return "rejected"

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(resolve_unknown, range(16))) == ["rejected"] * 16

    assert fetcher.calls == 2


def test_failed_jwks_fetch_has_one_global_cooldown_and_one_retry() -> None:
    clock = MutableMonotonicClock()
    fetcher = FailingJWKSFetcher("secret provider failure")
    verifier = OIDCVerifier(
        settings=_settings(),
        fetch_jwks=fetcher,
        monotonic_clock=clock,
    )

    errors: list[str] = []
    for index in range(12):
        with pytest.raises(UpstreamUnavailableError) as raised:
            verifier._signing_key(f"unknown-{index}")
        errors.append(str(raised.value))

    assert fetcher.calls == 1
    assert errors == ["OIDC key service unavailable"] * 12
    assert all("secret provider failure" not in message for message in errors)

    clock.advance(5.1)
    with pytest.raises(UpstreamUnavailableError) as retry:
        verifier._signing_key("unknown-after-failure-cooldown")
    assert str(retry.value) == "OIDC key service unavailable"
    assert fetcher.calls == 2


def test_concurrent_failed_jwks_fetch_is_single_flight() -> None:
    clock = MutableMonotonicClock()
    fetcher = FailingJWKSFetcher(
        clock=clock,
        advance_seconds=6.0,
    )
    verifier = OIDCVerifier(
        settings=_settings(),
        fetch_jwks=fetcher,
        monotonic_clock=clock,
    )
    barrier = Barrier(12)

    def resolve(index: int) -> str:
        barrier.wait()
        with pytest.raises(UpstreamUnavailableError) as raised:
            verifier._signing_key(f"concurrent-failure-{index}")
        return str(raised.value)

    with ThreadPoolExecutor(max_workers=12) as pool:
        errors = list(pool.map(resolve, range(12)))

    assert fetcher.calls == 1
    assert errors == ["OIDC key service unavailable"] * 12


def test_failed_refresh_never_extends_stale_keys_but_fresh_keys_still_work(
    rsa_keys,
) -> None:
    clock = MutableMonotonicClock()
    jwks = {"keys": [_jwk(rsa_keys, kid="known")]}
    fetcher = SequencedJWKSFetcher(
        [jwks, RuntimeError("secret"), RuntimeError("secret")]
    )
    verifier = OIDCVerifier(
        settings=_settings(),
        fetch_jwks=fetcher,
        cache_ttl_seconds=20.0,
        monotonic_clock=clock,
    )
    known_key = verifier._signing_key("known")

    with pytest.raises(UpstreamUnavailableError):
        verifier._signing_key("unknown-forced-refresh")
    assert verifier._signing_key("known") is known_key
    assert fetcher.calls == 2

    clock.advance(20.1)
    with pytest.raises(UpstreamUnavailableError) as expired:
        verifier._signing_key("known")
    assert str(expired.value) == "OIDC key service unavailable"
    assert fetcher.calls == 3


def test_successful_jwks_retry_clears_the_failure_marker(rsa_keys) -> None:
    clock = MutableMonotonicClock()
    jwks = {"keys": [_jwk(rsa_keys, kid="known")]}
    fetcher = SequencedJWKSFetcher([RuntimeError("secret"), jwks])
    verifier = OIDCVerifier(
        settings=_settings(),
        fetch_jwks=fetcher,
        monotonic_clock=clock,
    )

    with pytest.raises(UpstreamUnavailableError):
        verifier._signing_key("known")
    with pytest.raises(UpstreamUnavailableError):
        verifier._signing_key("known")
    assert fetcher.calls == 1

    clock.advance(5.1)
    assert verifier._signing_key("known") is not None
    assert verifier._signing_key("known") is not None
    assert fetcher.calls == 2


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"keys": "not-a-list"},
        {"keys": [{}]},
        {
            "keys": [
                {
                    "kid": "key-1",
                    "kty": "oct",
                    "use": "sig",
                    "alg": "RS256",
                    "k": "secret",
                }
            ]
        },
        {
            "keys": [
                {
                    "kid": "key-1",
                    "kty": "RSA",
                    "use": "enc",
                    "alg": "RS256",
                    "n": "x",
                    "e": "AQAB",
                }
            ]
        },
    ],
)
def test_malformed_jwks_fails_as_safe_upstream_error(
    cmd_session, rsa_keys, document
) -> None:
    verifier = _verifier(MutableJWKSFetcher([document]))
    with pytest.raises(UpstreamUnavailableError, match="OIDC key service unavailable"):
        verifier.resolve_principal(_token(rsa_keys), session=cmd_session)


def test_missing_oidc_configuration_fails_closed(monkeypatch) -> None:
    for name in (
        "OIDC_ISSUER",
        "OIDC_AUDIENCE",
        "OIDC_JWKS_URL",
        "OIDC_TENANT_CLAIM",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(
        UpstreamUnavailableError, match="OIDC authentication unavailable"
    ):
        OIDCSettings.from_environment()


def _configure_oidc_environment(
    monkeypatch,
    *,
    issuer: str,
    jwks_url: str,
    allow_insecure_http: str | None = None,
    app_env: str | None = None,
) -> None:
    monkeypatch.setenv("OIDC_ISSUER", issuer)
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("OIDC_JWKS_URL", jwks_url)
    monkeypatch.setenv("OIDC_TENANT_CLAIM", TENANT_CLAIM)
    if allow_insecure_http is None:
        monkeypatch.delenv("OIDC_ALLOW_INSECURE_HTTP", raising=False)
    else:
        monkeypatch.setenv("OIDC_ALLOW_INSECURE_HTTP", allow_insecure_http)
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)


def test_direct_oidc_settings_default_to_https_only() -> None:
    assert _settings().allow_insecure_http is False


def test_http_oidc_urls_are_rejected_by_default(monkeypatch) -> None:
    _configure_oidc_environment(
        monkeypatch,
        issuer="http://localhost:8080/realms/research",
        jwks_url="http://keycloak:8080/realms/research/certs",
    )

    with pytest.raises(
        UpstreamUnavailableError, match="OIDC authentication unavailable"
    ):
        OIDCSettings.from_environment()


@pytest.mark.parametrize(
    "issuer",
    [
        "http://localhost:8080/realms/research",
        "http://127.0.0.1:8080/realms/research",
        "http://[::1]:8080/realms/research",
    ],
)
def test_explicit_local_mode_allows_loopback_issuer_and_compose_jwks(
    monkeypatch, issuer
) -> None:
    _configure_oidc_environment(
        monkeypatch,
        issuer=issuer,
        jwks_url="http://keycloak:8080/realms/research/certs",
        allow_insecure_http="true",
        app_env="local",
    )

    settings = OIDCSettings.from_environment()

    assert settings.issuer == issuer
    assert settings.allow_insecure_http is True


@pytest.mark.parametrize("malformed", ["1", "yes", "on"])
def test_malformed_insecure_http_boolean_fails_closed(monkeypatch, malformed) -> None:
    _configure_oidc_environment(
        monkeypatch,
        issuer=ISSUER,
        jwks_url=f"{ISSUER}/protocol/openid-connect/certs",
        allow_insecure_http=malformed,
        app_env="local",
    )

    with pytest.raises(
        UpstreamUnavailableError, match="OIDC authentication unavailable"
    ):
        OIDCSettings.from_environment()


def test_insecure_http_flag_is_rejected_in_production_even_for_https(
    monkeypatch,
) -> None:
    _configure_oidc_environment(
        monkeypatch,
        issuer=ISSUER,
        jwks_url=f"{ISSUER}/protocol/openid-connect/certs",
        allow_insecure_http="true",
        app_env="production",
    )

    with pytest.raises(
        UpstreamUnavailableError, match="OIDC authentication unavailable"
    ):
        OIDCSettings.from_environment()


@pytest.mark.parametrize(
    ("issuer", "jwks_url"),
    [
        (
            "http://identity.example.test/realms/research",
            "http://keycloak:8080/realms/research/certs",
        ),
        (
            "http://localhost:8080/realms/research",
            "http://keycloak.internal:8080/realms/research/certs",
        ),
        (
            "http://user:password@localhost:8080/realms/research",
            "http://keycloak:8080/realms/research/certs",
        ),
        (
            "http://localhost:8080/realms/research",
            "http://user:password@keycloak:8080/realms/research/certs",
        ),
    ],
)
def test_local_http_mode_rejects_remote_dotted_hosts_and_credentials(
    monkeypatch, issuer, jwks_url
) -> None:
    _configure_oidc_environment(
        monkeypatch,
        issuer=issuer,
        jwks_url=jwks_url,
        allow_insecure_http="true",
        app_env="local",
    )

    with pytest.raises(
        UpstreamUnavailableError, match="OIDC authentication unavailable"
    ):
        OIDCSettings.from_environment()


def test_jwks_fetch_failure_is_redacted_as_upstream_unavailable(
    cmd_session, rsa_keys
) -> None:
    def unavailable(_url: str, _timeout_seconds: float) -> object:
        raise TimeoutError("private upstream detail")

    verifier = OIDCVerifier(settings=_settings(), fetch_jwks=unavailable)
    with pytest.raises(UpstreamUnavailableError) as raised:
        verifier.resolve_principal(_token(rsa_keys), session=cmd_session)

    assert str(raised.value) == "OIDC key service unavailable"
    assert "private upstream detail" not in str(raised.value)


def test_disabled_user_and_subject_tenant_change_are_rejected(
    cmd_session, rsa_keys
) -> None:
    verifier = _verifier(MutableJWKSFetcher([{"keys": [_jwk(rsa_keys)]}]))
    principal = verifier.resolve_principal(_token(rsa_keys), session=cmd_session)
    user = cmd_session.get(ResearchUser, principal.user_id)
    assert user is not None
    user.active = False
    cmd_session.flush()
    with pytest.raises(PermissionDeniedError, match="research user is disabled"):
        verifier.resolve_principal(_token(rsa_keys), session=cmd_session)

    user.active = True
    cmd_session.flush()
    with pytest.raises(
        PermissionDeniedError, match="OIDC tenant change is not permitted"
    ):
        verifier.resolve_principal(
            _token(rsa_keys, claims=_claims(tenant_id="team-b")),
            session=cmd_session,
        )


def test_production_authentication_source_has_no_opaque_token_fallback() -> None:
    production_files = [
        Path("app/api/v1/tenant_context.py"),
        Path("app/security/oidc.py"),
        Path("app/security/principal.py"),
    ]
    source = "\n".join(path.read_text() for path in production_files)
    assert "RESEARCH_TENANT_TOKENS" not in source
    assert "X-Actor" not in source
