"""Use case: select logs, run the LLM analysis and persist the outcome (success or failure)."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.errors import LogsNotFoundError, NoLogsMatchedError, NotFoundError
from api.database.tables import Analysis, AnalysisLog, AnalysisStatus, LogEntry
from api.llm.analyzer import LLMCallFailed, LogAnalyzer
from api.repositories.logs import LogRepository
from api.schemas.analysis import AnalysisRead, AnalyzeRequest

logger = logging.getLogger("api.analysis")


def to_read_model(analysis: Analysis) -> AnalysisRead:
    error = (
        {"code": analysis.error_code, "message": analysis.error_message or ""}
        if analysis.error_code
        else None
    )
    return AnalysisRead(
        id=analysis.id,
        status=analysis.status,
        created_at=analysis.created_at,
        completed_at=analysis.completed_at,
        request=analysis.request,
        log_count=analysis.log_count,
        pattern_count=analysis.pattern_count,
        stats=analysis.stats,
        provider=analysis.provider,
        model=analysis.model,
        summary=analysis.summary,
        categories=analysis.categories,
        suggestions=analysis.suggestions,
        error=error,
        duration_ms=analysis.duration_ms,
    )


class AnalysisService:
    def __init__(self, session: AsyncSession, analyzer: LogAnalyzer | None = None) -> None:
        """`analyzer` is only needed to run an analysis; reading one does not touch the LLM."""
        self.session = session
        self._analyzer = analyzer
        self.logs = LogRepository(session)

    @property
    def analyzer(self) -> LogAnalyzer:
        if self._analyzer is None:
            raise RuntimeError("AnalysisService needs a LogAnalyzer to run an analysis.")
        return self._analyzer

    async def _select_logs(self, request: AnalyzeRequest) -> list[LogEntry]:
        if request.log_ids is not None:
            logs = await self.logs.get_many(request.log_ids)
            missing = sorted(set(request.log_ids) - {log.id for log in logs})
            if missing:
                raise LogsNotFoundError(
                    f"{len(missing)} of the requested logs do not exist.",
                    details={"missing_ids": missing[:50]},
                )
            return logs

        assert request.filters is not None
        logs = await self.logs.recent_matching(request.filters, request.filters.limit)
        if not logs:
            raise NoLogsMatchedError("No logs match the given filters; nothing to analyse.")
        return logs

    async def analyze(self, request: AnalyzeRequest) -> Analysis:
        logs = await self._select_logs(request)
        prepared = self.analyzer.prepare(logs)

        analysis = Analysis(
            id=uuid.uuid4(),
            status=AnalysisStatus.PENDING,
            request=request.model_dump(mode="json", by_alias=True, exclude_none=True),
            log_count=len(logs),
            pattern_count=len(prepared.patterns),
            stats=prepared.stats,
            provider=self.analyzer.provider_name,
            model=self.analyzer.model_name,
        )
        analysis.logs = [AnalysisLog(log_id=log.id) for log in logs]
        self.session.add(analysis)
        await self.session.commit()  # persisted as pending before the (slow) LLM call

        try:
            outcome = await self.analyzer.run(prepared, allowed_log_ids={log.id for log in logs})
        except LLMCallFailed as failure:
            analysis.status = AnalysisStatus.FAILED
            analysis.error_code = failure.error.code
            analysis.error_message = failure.error.message
            analysis.raw_response = failure.raw_response
            analysis.duration_ms = failure.duration_ms
            analysis.completed_at = datetime.now(UTC)
            await self.session.commit()
            logger.warning(
                "analysis failed",
                extra={"analysis_id": str(analysis.id), "error_code": failure.error.code},
            )
            failure.error.extra["analysis_id"] = str(analysis.id)
            raise failure.error from failure

        result = outcome.result
        analysis.status = AnalysisStatus.COMPLETED
        analysis.summary = result.summary
        analysis.categories = [c.model_dump() for c in result.categories]
        analysis.suggestions = [s.model_dump() for s in result.suggestions]
        analysis.raw_response = outcome.raw_response
        analysis.duration_ms = outcome.duration_ms
        analysis.completed_at = datetime.now(UTC)
        await self.session.commit()
        logger.info(
            "analysis completed",
            extra={
                "analysis_id": str(analysis.id),
                "log_count": analysis.log_count,
                "duration_ms": outcome.duration_ms,
                "llm_requests": outcome.usage.requests,
                "input_tokens": outcome.usage.input_tokens,
                "output_tokens": outcome.usage.output_tokens,
            },
        )
        return analysis

    async def get(self, analysis_id: uuid.UUID) -> Analysis:
        analysis = await self.session.scalar(select(Analysis).where(Analysis.id == analysis_id))
        if analysis is None:
            raise NotFoundError(f"Analysis {analysis_id} not found.")
        return analysis
