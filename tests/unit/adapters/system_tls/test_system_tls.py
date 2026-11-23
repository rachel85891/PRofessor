"""The TLS context of every HTTPS client: the operating system verifies the certificate."""

import ssl

import truststore

from hw_checker.adapters.system_tls import system_tls_context


def test_the_context_verifies_through_the_system_store_and_stays_strict() -> None:
    context = system_tls_context()

    assert isinstance(context, truststore.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
