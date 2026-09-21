from __future__ import annotations

import ssl

from research_mesh.config import TLSMaterial
from research_mesh.tls import build_client_ssl_context


class FakeSSLContext:
    def __init__(self) -> None:
        self.ca_files: list[str] = []
        self.cert_chain: tuple[str, str] | None = None
        self.check_hostname = False
        self.verify_mode = ssl.CERT_NONE
        self.minimum_version: ssl.TLSVersion | None = None

    def load_verify_locations(self, *, cafile: str) -> None:
        self.ca_files.append(cafile)

    def load_cert_chain(self, *, certfile: str, keyfile: str) -> None:
        self.cert_chain = (certfile, keyfile)


def test_client_context_extends_system_trust_with_agent_ca(
    monkeypatch, tmp_path
) -> None:
    cert = tmp_path / "client.pem"
    key = tmp_path / "client.key"
    agent_ca = tmp_path / "agent-ca.pem"
    for path in (cert, key, agent_ca):
        path.write_text("test", encoding="utf-8")

    fake_context = FakeSSLContext()
    create_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_create_default_context(*args, **kwargs):
        create_calls.append((args, kwargs))
        return fake_context

    monkeypatch.setattr(ssl, "create_default_context", fake_create_default_context)

    result = build_client_ssl_context(TLSMaterial(cert, key, agent_ca))

    assert result is fake_context
    assert create_calls == [((ssl.Purpose.SERVER_AUTH,), {})]
    assert fake_context.ca_files == [str(agent_ca)]
    assert fake_context.cert_chain == (str(cert), str(key))
    assert fake_context.check_hostname is True
    assert fake_context.verify_mode == ssl.CERT_REQUIRED
    assert fake_context.minimum_version == ssl.TLSVersion.TLSv1_2
