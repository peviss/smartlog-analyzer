"""LLM analysis: run one over a set of logs and read it back."""

import uuid

from fastapi import APIRouter, status

from api.routers.deps import AnalyzerDep, SessionDep
from api.schemas.analysis import MAX_ANALYSIS_LOG_IDS, AnalysisRead, AnalyzeRequest
from api.services.analysis import AnalysisService, to_read_model

router = APIRouter(tags=["analysis"])


@router.post(
    "/logs/analyze",
    response_model=AnalysisRead,
    status_code=status.HTTP_201_CREATED,
    summary="Send a set of logs to the LLM for analysis",
    description=(
        f"Select logs by `log_ids` (max {MAX_ANALYSIS_LOG_IDS}) or by `filters` (most recent first, up to `limit`). "
        "The logs are grouped into patterns, sent to the configured LLM and the result is persisted. "
        "On LLM failure the analysis is still persisted with status `failed` and its id is returned in "
        "the error body."
    ),
    responses={
        404: {"description": "Some log ids do not exist"},
        422: {"description": "Validation error or no logs matched"},
        502: {"description": "LLM returned an invalid response or failed"},
        504: {"description": "LLM timed out"},
    },
)
async def analyze_logs(payload: AnalyzeRequest, session: SessionDep, analyzer: AnalyzerDep) -> AnalysisRead:
    analysis = await AnalysisService(session, analyzer).analyze(payload)
    return to_read_model(analysis)


@router.get(
    "/analysis/{analysis_id}",
    response_model=AnalysisRead,
    summary="Get the result of an analysis",
    responses={404: {"description": "Analysis not found"}, 422: {"description": "Invalid id"}},
)
async def get_analysis(analysis_id: uuid.UUID, session: SessionDep) -> AnalysisRead:
    return to_read_model(await AnalysisService(session).get(analysis_id))
