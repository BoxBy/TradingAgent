"""LiteLLM CustomLogger callback — TTFT per-request logging.

Every streaming request's first-token time is measured and appended
to ~/.hermes/data/ttft_log.jsonl for historical analysis.

Loaded by litellm_wrapper.py before proxy startup.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from litellm.integrations.custom_logger import CustomLogger

KST = timezone(timedelta(hours=9))
TTFT_LOG = Path("~/.hermes/data/ttft_log.jsonl").expanduser()

# In-process tracking: call_id → first-chunk datetime
_first_chunk: dict = {}


def _append(record: dict):
    TTFT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with TTFT_LOG.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class TTFTCallback(CustomLogger):
    """Measures TTFT for every streaming LLM call via LiteLLM proxy."""

    async def async_log_stream_event(
        self, kwargs, response_obj, start_time, end_time,
    ):
        call_id = kwargs.get("litellm_call_id")
        if call_id and call_id not in _first_chunk:
            _first_chunk[call_id] = end_time

    async def async_log_success_event(
        self, kwargs, response_obj, start_time, end_time,
    ):
        call_id = kwargs.get("litellm_call_id", "")
        first_time = _first_chunk.pop(call_id, None)

        model = kwargs.get("model", "?")
        ll_model = kwargs.get("litellm_params", {}).get("model", model)
        total_s = round((end_time - start_time).total_seconds(), 3)

        record = {
            "ts": datetime.now(KST).isoformat(),
            "model": model,
            "litellm_model": ll_model,
            "total_s": total_s,
        }

        if first_time is not None:
            record["ttft_s"] = round(
                (first_time - start_time).total_seconds(), 3,
            )
            record["streaming"] = True
        else:
            record["ttft_s"] = total_s
            record["streaming"] = False

        usage = getattr(response_obj, "usage", None)
        if usage:
            record["prompt_tokens"] = getattr(usage, "prompt_tokens", 0)
            record["completion_tokens"] = getattr(
                usage, "completion_tokens", 0,
            )

        _append(record)

    async def async_log_failure_event(
        self, kwargs, response_obj, start_time, end_time,
    ):
        call_id = kwargs.get("litellm_call_id", "")
        _first_chunk.pop(call_id, None)

        _append({
            "ts": datetime.now(KST).isoformat(),
            "model": kwargs.get("model", "?"),
            "error": True,
            "error_msg": str(kwargs.get("exception", ""))[:200],
        })

# Module-level instance for litellm config registration.
# litellm's get_instance_fn() returns the class/attribute as-is,
# so we export a pre-built instance instead of the class.
ttft_callback = TTFTCallback()
