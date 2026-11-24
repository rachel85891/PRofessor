"""System TLS adapter: the TLS context every HTTPS client of the checker uses.

The context verifies a server's certificate through the operating system (the Windows
certificate APIs, the system store on Linux and macOS) with `truststore`, not through
OpenSSL's own chain building. A TLS-inspecting network filter can send two certificate
paths, one ending at a CA no store trusts: the system verifier tries the other and
succeeds, OpenSSL takes the first and fails. Only the contexts made here are affected, never
a global `inject_into_ssl()`.
"""

import ssl

import truststore


def system_tls_context() -> ssl.SSLContext:
    """A client context that checks the hostname and requires a valid certificate chain,
    verified by the operating system's certificate store."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
