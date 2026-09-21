from __future__ import annotations

import ssl

from .config import TLSMaterial


def build_client_ssl_context(material: TLSMaterial) -> ssl.SSLContext:
    material.validate("client")
    # Start with the operating system's public CA store, then extend it with
    # the ACPs Agent CA. Passing ``cafile`` to create_default_context replaces
    # the default store entirely, which makes public services such as the
    # Wutong Discovery gateway fail certificate verification.
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.load_verify_locations(cafile=str(material.trust_bundle_file))
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
