"""Shared AI-call reliability layer (Milestone 13).

Every AI call site in the codebase (relevance classification, product-type
classification, entity-resolution arbitration, review extraction) had its
own copy of the same fragile pattern: call the SDK, then blindly index
`response.content[0].text` and `json.loads` a brace-slice with no
exception handling at all. A network error, a rate limit, or a model
response with no valid JSON in it would raise straight out of the
function and crash whatever batch script was running -- one bad AI
response could take down classification for every other listing in the
same run.

`call_ai()` in client.py is the one place that talks to the SDK. It never
raises for anything the AI/network layer itself can fail at -- it always
returns an `AICallResult` describing what happened, so a single failed
request degrades that one call, not the whole pipeline.
"""

from dmie.ai.client import AICallResult, call_ai

__all__ = ["AICallResult", "call_ai"]
