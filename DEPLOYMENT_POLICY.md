# NaverMailManager Release Feed

Public deployment repository for verified Naver Mail Manager packages.

## Safety rules
- Never publish Naver IDs, app passwords, mail databases, attachments, logs, or personal learning data.
- Publish only generic application/update files.
- Verify package SHA-256 before activation.
- Back up the current installation before updating.
- Switch versions only after validation succeeds.
- Keep the previous working version for rollback.
- Server mail deletion must never be automatic; it requires explicit user review and confirmation.

## Planned feed
`latest.json` will identify the current verified version, download location, SHA-256, entry point, and minimum launcher version.
