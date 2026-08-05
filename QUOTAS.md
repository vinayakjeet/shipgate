# LLM Provider Quotas

Human-readable mirror of [`llm/providers/quotas.yaml`](llm/providers/quotas.yaml)
(the machine-readable config `llm/providers/registry.py` actually loads). The
two are **not auto-synced** - update both when a limit, price, or model changes,
and record why in [DECISIONS.md](DECISIONS.md) if it's a meaningful shift.

Measured against a real key on 2026-08-05, not copied from docs. Two things the
documentation does not tell you and which cost real debugging time:

- **Gemini sends no `Retry-After` header.** The delay appears only inside the
  JSON error message ("Please retry in 48.63971551s"). A client reading the
  header alone falls back to a short default and hammers a limit that needs
  most of a minute.
- **`gemini-2.0-flash` and `gemini-2.5-flash` are unavailable to new keys.**
  The first returns a quota error, the second says it is retired. Use
  `gemini-flash-latest`.

Free-tier limits change without notice. "Last verified" is when a human last
checked the provider's own docs/dashboard - if it's more than a month old,
re-verify before relying on it for capacity planning.

| Provider | Free-tier limit | Reset window | Last verified |
|---|---|---|---|
| gemini | 20 requests/window, `gemini-flash-latest` (resolves to gemini-3.6-flash) | rolling, retry delay 20 to 49s | 2026-08-05 |
| groq | 30 requests/min | per minute | 2026-08-04 |
| cerebras | 30 requests/min | per minute | 2026-08-04 |
| openrouter | 20 requests/min (`:free` model suffix) | per minute | 2026-08-04 |
| ollama | none (self-hosted/local) | n/a | 2026-08-04 |
| sarvam | 10 requests/min | per minute | 2026-08-04 |
| mock | none (in-process, no network) | n/a | n/a |

Verify against each provider's own current docs/dashboard before depending on
these numbers for capacity planning - this table is a starting point, not a
guarantee, and exact rate-limit doc URLs move around often enough that they're
deliberately not pasted here. Search "<provider> API rate limits" for each.
