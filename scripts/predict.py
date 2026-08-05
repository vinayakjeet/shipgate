"""Produce real intent predictions over a dataset, for the judge to then grade.

    uv run python scripts/predict.py

Resumable by design. A hundred calls against a twenty-per-window free tier spans
several windows, so the run will be interrupted, and re-running must not re-pay
for work already done. Predictions are appended one line at a time and flushed.

The classifier prompt here is deliberately ordinary rather than tuned. The point
is a model that makes realistic mistakes on the ambiguous items, because a
predictor that is either perfect or hopeless gives the judge nothing interesting
to be calibrated on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shipgate  # noqa: E402,F401  loads .env
from llm import ChatClient, ChatMessage, ProviderClientError, ProviderError  # noqa: E402
from shipgate.datasets.loader import load_jsonl  # noqa: E402

INTENTS = ("billing", "technical", "account", "other")

SYSTEM = (
    "You classify customer support tickets. Reply with exactly one word from this "
    "list and nothing else: billing, technical, account, other."
)


def normalise(text: str) -> str:
    """Pull an intent out of whatever the model said.

    Returns an empty string when the reply is not one of the four, which is a real
    failure mode worth keeping visible rather than coercing into a guess.
    """
    cleaned = text.strip().strip(".").strip("`").lower()
    for intent in INTENTS:
        if cleaned == intent:
            return intent
    # A model that answered in a sentence still usually names exactly one label.
    named = [i for i in INTENTS if i in cleaned]
    return named[0] if len(named) == 1 else ""


async def main(dataset: str, out_path: Path, provider: str, limit: int) -> None:
    items = load_jsonl(dataset)

    done: dict[str, str] = {}
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                # Only a real prediction counts as done. Failed rows are kept in
                # the file as a record, but must be retried on the next run: a
                # rate limit is a property of the moment, and treating it as
                # settled would permanently lose the item.
                if row.get("prediction"):
                    done[row["item_id"]] = row["prediction"]

    todo = [i for i in items if i.id not in done]
    if limit:
        todo = todo[:limit]

    print(f"{len(done)} already predicted, {len(todo)} to go", flush=True)
    if not todo:
        agree = sum(1 for i in items if done.get(i.id) == i.expected)
        print(f"complete. accuracy against expected: {agree}/{len(items)}", flush=True)
        return

    client = ChatClient(max_retry_attempts=6)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    for n, item in enumerate(todo, start=1):
        prompt = f"Ticket: {item.input['prompt']}\n\nIntent:"
        messages = [
            ChatMessage(role="system", content=SYSTEM),
            ChatMessage(role="user", content=prompt),
        ]
        try:
            response = await client.complete(provider, messages)
            prediction = normalise(response.text)
            model = response.model
            error = "" if prediction else f"unparseable: {response.text.strip()[:60]!r}"
        except (ProviderError, ProviderClientError) as exc:
            prediction, model, error = "", provider, f"{type(exc).__name__}: {exc}"

        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "item_id": item.id,
                        "prediction": prediction,
                        "expected": item.expected,
                        "model": model,
                        "error": error,
                    }
                )
                + "\n"
            )
            fh.flush()

        mark = "ok " if prediction == item.expected else ("XX " if prediction else "ERR")
        print(
            f"[{n}/{len(todo)}] {mark} {item.id} expected={item.expected:<10} "
            f"got={prediction or error[:40]}",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="datasets/support-intent.jsonl")
    parser.add_argument("--out", type=Path, default=Path("datasets/predictions-gemini.jsonl"))
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N, for short windows.")
    args = parser.parse_args()
    asyncio.run(main(args.dataset, args.out, args.provider, args.limit))
