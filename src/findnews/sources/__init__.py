"""データソースのアダプタとレジストリ(DESIGN.md 第14節)。"""

from .base import Document, ElectionDoc, ElectionSource, IndicatorSource, Observation  # noqa: F401
from .registry import (ELECTION_SOURCES, INDICATOR_SOURCES, INDICATORS, AUX_SOURCES, election_source,  # noqa: F401
                       status_for_pref)
