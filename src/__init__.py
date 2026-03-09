"""
Conciliación de Comisiones - Daniel Achondo vs Andes Motors

Módulo para reconciliar ventas de vehículos con pagos de comisiones
de Andes Motors, incluyendo matching exacto y difuso.
"""

from .main import run, ConciliationConfig
from .conciliation_engine import ConciliationEngine
from .fuzzy_matcher import FuzzyVINMatcher
from .excel_generator import generate_conciliation_report
from .utils import (
    normalize_vin,
    normalize_marca,
    extraer_tipo_glosa,
    ValidationError,
    MARCAS_ANDES,
    MESES,
    COLORES
)

__version__ = "1.0.0"
__all__ = [
    "run",
    "ConciliationConfig",
    "ConciliationEngine",
    "FuzzyVINMatcher",
    "generate_conciliation_report",
    "normalize_vin",
    "normalize_marca",
    "extraer_tipo_glosa",
    "ValidationError",
    "MARCAS_ANDES",
    "MESES",
    "COLORES"
]
