# review/ (owner: B)

The write review function from [interfaces_en.md §5](../interfaces_en.md).

| File | What it is |
| --- | --- |
| `reviewer.py` | `review(title, content, context_blocks) -> {"action", "reason"}`. Never raises; any failure returns `quarantine`. Standard library only. |
| `prompt.md` | The reviewer's system prompt. Treats all content as data. |
| `samples.json` | 3 normal writes (expected `pass`) and 2 injection writes (expected `reject` or `quarantine`). |
| `run_samples.py` | Runs the samples against the real LLM and prints PASS/FAIL. |
| `test_offline.py` | Checks the failure paths (timeout, bad JSON, unknown action, missing key) with no API key. |

## For A: how to call it

```python
from review.reviewer import review   # or add review/ to sys.path
result = review(title, content, context_blocks)  # context_blocks: up to 3 dicts
```

## Settings (environment variables, never in code)

```bash
export LLM_PROVIDER=openai          # or: anthropic
export LLM_API_KEY=...              # your key
export LLM_MODEL=gpt-4o-mini        # or e.g. claude-haiku-4-5
# export LLM_BASE_URL=...           # only for other OpenAI-compatible providers
export REVIEW_TIMEOUT=30
```

## Run the checks

```bash
python3 review/test_offline.py   # no key needed
python3 review/run_samples.py    # needs the settings above
```
