from typing import Annotated

from fastapi import Depends, Request
from pydantic_ai.models import Model
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import Settings, get_settings
from api.database.session import get_session
from api.llm.analyzer import LogAnalyzer

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_llm_model(request: Request) -> Model:
    return request.app.state.llm_model


LLMModelDep = Annotated[Model, Depends(get_llm_model)]


def get_analyzer(settings: SettingsDep, model: LLMModelDep) -> LogAnalyzer:
    return LogAnalyzer(
        model=model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_patterns=settings.analysis_max_patterns,
        output_retries=settings.llm_output_retries,
    )


AnalyzerDep = Annotated[LogAnalyzer, Depends(get_analyzer)]
