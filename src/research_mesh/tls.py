from __future__ import annotations

import ssl

from .config import TLSMaterial


def build_client_ssl_context(material: TLSMaterial) -> ssl.SSLContext:
    material.validate("client")
    context = ssl.create_default_context(
        ssl.Purpose.SERVER_AUTH,
        cafile=str(material.trust_bundle_file),
    )
    context.load_cert_chain(
        certfile=str(material.cert_file),
        keyfile=str(material.key_file),
    )
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def build_server_ssl_context(material: TLSMaterial) -> ssl.SSLContext:
    material.validate("server")
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(
        certfile=str(material.cert_file),
        keyfile=str(material.key_file),
    )
    context.load_verify_locations(cafile=str(material.trust_bundle_file))
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context
