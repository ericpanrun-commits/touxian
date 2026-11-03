# Eric OS cloud deployment

This is a shared Supabase project with LifeLedger and IELTS. This checkout contains only the migrations used by Eric OS and its existing study integration. It is not a complete migration checkout for the shared database.

Migration filenames match the versions returned by the deployed migration history. SQL contents were compared with the remote statements after whitespace normalization; renaming preserved every file byte and did not change the database or its migration history. The original filename mapping and verified backups are retained in the delivery evidence.

Do not run a whole-project reset or blindly replay migrations with `db push --include-all` against this shared project. For a new Eric OS migration, create the migration with the CLI, inspect the existing relevant schema, test it in the isolated database, apply only that new migration, then retain its actual remote version in this checkout. Preserve other applications' tables, grants and auth settings.

Bundle Edge Functions with `node scripts/build-cloud-functions.mjs`. Both functions authenticate inside their own handlers. Deployment must include `index.ts` and `deno.json` and explicitly use the relative `deno.json` import-map path; carrying forward a previous temporary deployment path causes an invalid-import-map failure. Keep all service credentials in the function environment. The frontend uses only the publishable key and authenticated owner/device APIs.
