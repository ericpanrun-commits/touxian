# Supabase database trust anchor

`supabase-prod-ca-2021.crt` is a **public CA certificate**, not a credential.
The PostgreSQL adapter uses it with `sslmode=verify-full`, validating both the
database certificate chain and the requested hostname. No TLS downgrade or
change to the Supabase project's settings is required.

Source: <https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt>.
The download URL template is published by Supabase Studio in
<https://github.com/supabase/supabase/blob/master/apps/studio/hooks/custom-content/custom-content.json>.
Connection guidance:
<https://supabase.com/docs/guides/platform/ssl-enforcement>.

DER SHA-256:
`807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa`.

Before rotating this file, independently verify the replacement against the
provider's official certificate download and repeat verified TLS connection
and hostname-rejection checks. Do not trust a certificate obtained solely from
an unverified connection to the database server.

2026-10-17: a credential-free libpq connection to the existing project's Session
Pooler passed certificate/hostname verification and reached the password
challenge. No password or SQL was transmitted. This is TLS evidence only;
authenticated database and cloud worker acceptance remain separate checks.
