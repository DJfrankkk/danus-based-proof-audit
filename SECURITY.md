# Security

## Trust model

Danus-Based Proof Audit treats reviewer output as untrusted data. The coordinator validates its
schema, stamps source hashes itself, and is the only component that updates audit state.

The built-in Codex runner uses a read-only sandbox. Custom runner commands are trusted local
configuration and may execute arbitrary programs; review them before use.

The dashboard is read-only and binds to `127.0.0.1` by default.

## Reporting

Report security issues privately to the repository owner before opening a public issue.
