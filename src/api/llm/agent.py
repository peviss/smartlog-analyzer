"""The Pydantic AI agent that turns preprocessed logs into an `AnalysisResult`.

The agent is model-agnostic: the model (Anthropic, the rule-based mock or a test double)
is passed at run time. `output_type=AnalysisResult` makes Pydantic AI expose the schema
to the model as a structured-output tool and validate the answer with Pydantic; invalid
answers are sent back to the model with the validation errors (up to `retries` times)
before the run fails with `UnexpectedModelBehavior`.
"""

from dataclasses import dataclass

import pydantic_ai
from pydantic_ai import Agent, RunContext

from api.llm.prompt import SYSTEM_PROMPT
from api.schemas.analysis import AnalysisResult

# The startup banner is meant for interactive use; this is a server.
pydantic_ai.BANNER_ENABLED = False


@dataclass(frozen=True)
class AnalysisDeps:
    allowed_log_ids: frozenset[int]


analysis_agent: Agent[AnalysisDeps, AnalysisResult] = Agent(
    name="log_analyst",
    output_type=AnalysisResult,
    instructions=SYSTEM_PROMPT,
    deps_type=AnalysisDeps,
    defer_model_check=True,
)


@analysis_agent.output_validator
def keep_only_analysed_log_ids(ctx: RunContext[AnalysisDeps], output: AnalysisResult) -> AnalysisResult:
    """Hallucination guard: drop example log ids that were not part of the analysis."""
    allowed = ctx.deps.allowed_log_ids
    for category in output.categories:
        category.example_log_ids = [i for i in category.example_log_ids if i in allowed]
    return output
