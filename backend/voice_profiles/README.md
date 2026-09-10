# Voice call profiles

The AI voice agent (Nuraveda) auto-discovers `profiles/<id>/profile.json` at
boot and refuses to dispatch to an unknown profile id — `getProfile()` returns
404 `unknown profile`.

Its repo ships **no** `profiles/` directory, so this one is ours and lives
here, versioned, rather than only on one laptop where a re-clone would lose it.

## Install

    cp -r backend/voice_profiles/purity-coffee-b2b \
          <path-to>/ai-voice-agent/profiles/

Restart the service; `GET /profiles` should list it. Then point the adapter at
it in `backend/.env`:

    NURAVEDA_PROFILE=purity-coffee-b2b

## Why the script is NOT in here

The opening line and the questions live in
`app/services/founder_call_pipeline.py` as `OPENING_DISCLOSURE` and
`QUALIFICATION_QUESTIONS`, and travel in the dispatch payload.

That is deliberate. `script_discloses()` validates the opening for the AI
disclosure, and it validates the Python copy. A second copy in the profile
would drift from it, and a profile that carried its own opening line could
quietly drop the disclosure while every test still passed.

## Gotchas in the agent's own .env.example, found the hard way

| documented | what the code actually reads |
|---|---|
| `HTTP_PORT` | `PORT` (server.js:48) |
| `SIP_OUTBOUND_TRUNK_ID` | `LIVEKIT_SIP_TRUNK_ID` (lib/trunks.js:19) |
| *(absent)* | `LIVEKIT_TOOL_SECRET` — required, or every dispatch 401s |

Also: `DISPATCH_MODE` accepts only `live` or `dry_run`. `dry-run` with a hyphen
throws at boot, which is correct — an unreadable kill switch must stop the
process rather than resolve to something that dials.

And the repo has a Prisma schema but no migration files, so `migrate deploy`
reports success while creating nothing. Use `prisma db push`.
