"""
Funciones utilitarias para la conciliación de comisiones.

Este módulo provee funciones auxiliares organizadas en secciones:
- Configuración y Constantes
- Normalización de Datos (VIN, Marcas, Montos)
- Validación y Errores
- Operaciones con Archivos
"""

import re
import math
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


# =============================================================================
# SECCIÓN 1: CONFIGURACIÓN Y CONSTANTES
# =============================================================================

MARCAS_ANDES = ['MAXUS', 'JETOUR', 'KARRY', 'KAIYI']

MESES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
    5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
}

MESES_INVERSO = {
    'ENERO': 1, 'FEBRERO': 2, 'MARZO': 3, 'ABRIL': 4,
    'MAYO': 5, 'JUNIO': 6, 'JULIO': 7, 'AGOSTO': 8,
    'SEPTIEMBRE': 9, 'OCTUBRE': 10, 'NOVIEMBRE': 11, 'DICIEMBRE': 12
}

GLOSA_CATEGORIES = {
    'BONO_RETAIL': ['BONO RETAIL'],
    'BONO_MARCA': ['BONO MARCA', 'DIF BONO MARCA'],
    'CM': ['CM '],
    'FLOTA': ['FLOTA'],
    'DIF_FLOTA': ['DIF FLOTA'],
    'INCENTIVO_COMERCIAL': ['INCENTIVO COMERCIAL', 'INC COMERCIAL'],
    'AUT_MARCA': ['AUT MARCA'],
}

COLUMNAS_BONOS_VENTAS = {
    'Acción Comercial Contado (Neto) (+)': 'Bono_Contado_Sistema',
    'Acción Comercial Crédito Pagada (Neto) (+)': 'Bono_Credito_Sistema',
    'Bono Volumen Neto (+)': 'Bono_Volumen_Sistema',
    'Bono Crédito (Neto) (+)': 'Bono_Credito_Extra_Sistema'
}

DEFAULT_FUZZY_THRESHOLD = 85
DEFAULT_TOLERANCE = 1000

COLORES = {
    'header': '2F5496',
    'header_green': '70AD47',
    'header_red': 'C00000',
    'header_orange': 'ED7D31',
    'pagado': 'C6EFCE',
    'pendiente': 'FFC7CE',
    'parcial': 'FFEB9C',
    'fuzzy_high': 'C6EFCE',
    'fuzzy_medium': 'FFEB9C',
    'fuzzy_low': 'FFC7CE',
}

ERROR_CODES = {
    'E001': 'Archivo no encontrado',
    'E002': 'Formato de archivo inválido',
    'E003': 'Hoja de cálculo no encontrada',
    'E004': 'Columna requerida faltante',
    'E005': 'Datos inválidos en columna',
    'E006': 'VIN con formato inválido',
    'E007': 'Marca no reconocida',
    'E008': 'Valor numérico inválido',
}


# =============================================================================
# SECCIÓN 2: NORMALIZACIÓN DE DATOS
# =============================================================================

def is_empty(value) -> bool:
    """Check if a value is empty/null (replaces pd.isna)."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == '':
        return True
    return False


def to_numeric(value, default=0) -> float:
    """Convert a value to float, returning default on failure."""
    if is_empty(value):
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def normalize_vin(vin) -> Optional[str]:
    """Normaliza VIN para matching."""
    if is_empty(vin):
        return None
    vin_str = str(vin).strip().upper()
    vin_str = re.sub(r'[\s\-\.\,]', '', vin_str)
    if not vin_str or len(vin_str) < 5:
        return None
    return vin_str


def normalize_rut_numeric(rut) -> Optional[str]:
    """Normaliza RUT desde formato numérico (ej: 17833721.0 → '17833721')."""
    if is_empty(rut):
        return None
    try:
        return str(int(float(rut)))
    except (ValueError, TypeError):
        return None


def normalize_rut_string(rut) -> Optional[str]:
    """Normaliza RUT desde formato string (ej: '10.790.617-7' → '10790617')."""
    if is_empty(rut):
        return None
    rut_str = str(rut).strip()
    rut_clean = re.sub(r'[\.\-\s]', '', rut_str)
    if len(rut_clean) > 1:
        return rut_clean[:-1]
    return None


def normalize_marca(marca) -> Optional[str]:
    """Normaliza nombre de marca para matching."""
    if is_empty(marca):
        return None
    marca_str = str(marca).strip().upper()
    mappings = {
        'MAXUS': 'MAXUS', 'JETOUR': 'JETOUR', 'KARRY': 'KARRY', 'KAIYI': 'KAIYI',
        'VOLKSWAGEN': 'VOLKSWAGEN', 'VW': 'VOLKSWAGEN', 'AUDI': 'AUDI',
        'FOTON': 'FOTON', 'IVECO': 'IVECO',
    }
    return mappings.get(marca_str, marca_str)


def extraer_tipo_glosa(glosa) -> str:
    """Extrae el tipo base de la GLOSA."""
    if is_empty(glosa):
        return 'OTROS'
    glosa_upper = str(glosa).upper()
    if 'BONO RETAIL' in glosa_upper:
        return 'BONO_RETAIL'
    elif 'DIF BONO MARCA' in glosa_upper:
        return 'BONO_MARCA'
    elif 'BONO MARCA' in glosa_upper:
        return 'BONO_MARCA'
    elif glosa_upper.startswith('CM ') or ' CM ' in glosa_upper:
        return 'CM'
    elif 'DIF FLOTA' in glosa_upper:
        return 'DIF_FLOTA'
    elif 'FLOTA' in glosa_upper:
        return 'FLOTA'
    elif 'INCENTIVO COMERCIAL' in glosa_upper or 'INC COMERCIAL' in glosa_upper:
        return 'INCENTIVO_COMERCIAL'
    elif 'AUT MARCA' in glosa_upper:
        return 'AUT_MARCA'
    else:
        return 'OTROS'


def extraer_mes_glosa(glosa) -> Optional[str]:
    """Extrae el mes de la GLOSA."""
    if is_empty(glosa):
        return None
    glosa_upper = str(glosa).upper()
    for mes in ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO',
                'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE']:
        if mes in glosa_upper:
            return mes.capitalize()
    return None


def format_currency(amount) -> str:
    """Formatea monto en CLP."""
    if is_empty(amount):
        return "$0"
    return f"${amount:,.0f}".replace(",", ".")


def clean_numeric_value(value) -> Optional[float]:
    """Limpia y convierte valores numéricos de Excel."""
    if is_empty(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value_str = str(value).strip()
    value_str = re.sub(r'[$\s]', '', value_str)
    if '.' in value_str and ',' not in value_str:
        parts = value_str.split('.')
        if len(parts[-1]) == 3:
            value_str = value_str.replace('.', '')
    value_str = value_str.replace(',', '.')
    try:
        return float(value_str)
    except ValueError:
        logger.warning(f"No se pudo convertir valor numérico: '{value}'")
        return None


def safe_get(row: dict, key: str, default=None):
    """Safely get a value from a dict row, returning default if empty."""
    val = row.get(key, default)
    if is_empty(val):
        return default
    return val


# =============================================================================
# SECCIÓN 3: VALIDACIÓN Y ERRORES
# =============================================================================

class ValidationError(Exception):
    """Excepción personalizada para errores de validación."""
    def __init__(self, mensaje: str, archivo: str = None, detalles: dict = None,
                 error_code: str = None, context: dict = None):
        self.mensaje = mensaje
        self.archivo = archivo
        self.detalles = detalles or {}
        self.error_code = error_code
        self.context = context or {}
        super().__init__(self.mensaje)

    def __str__(self):
        msg = f"\n{'='*80}\nERROR DE VALIDACIÓN"
        if self.error_code:
            msg += f" [{self.error_code}]"
        msg += f"\n{'='*80}\n{self.mensaje}\n"
        if self.archivo:
            msg += f"\nArchivo: {self.archivo}\n"
        if self.context:
            msg += "\nContexto:\n"
            for key, value in self.context.items():
                msg += f"  - {key}: {value}\n"
        if self.detalles:
            msg += "\nDetalles:\n"
            for key, value in self.detalles.items():
                msg += f"  - {key}: {value}\n"
        msg += f"{'='*80}\n"
        return msg


def validate_required_columns(rows: List[Dict], required_columns: List[str],
                              file_path: str = None) -> None:
    """Valida que las filas contengan las columnas requeridas."""
    if not rows:
        return
    available = set(rows[0].keys())
    missing = [col for col in required_columns if col not in available]
    if missing:
        raise ValidationError(
            mensaje="Faltan columnas requeridas en el archivo",
            archivo=file_path,
            error_code='E004',
            detalles={
                'Columnas faltantes': ', '.join(missing),
                'Columnas encontradas': ', '.join(list(available)[:10]) + '...'
            }
        )


# =============================================================================
# SECCIÓN 4: OPERACIONES CON ARCHIVOS
# =============================================================================

def read_excel_as_dicts(filepath: str, header_row: int = 0) -> List[Dict]:
    """
    Lee un archivo Excel y retorna una lista de diccionarios.

    Args:
        filepath: Ruta al archivo Excel
        header_row: Fila del header (0-indexed). Filas antes se ignoran.

    Returns:
        Lista de diccionarios representando las filas
    """
    import openpyxl

    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)

    for _ in range(header_row):
        next(rows_iter, None)

    header = next(rows_iter, None)
    if header is None:
        wb.close()
        return []

    headers = [str(h).strip() if h is not None else f'col_{i}' for i, h in enumerate(header)]

    result = []
    for row_values in rows_iter:
        row_dict = {}
        all_none = True
        for i, val in enumerate(row_values):
            if i < len(headers):
                row_dict[headers[i]] = val
                if val is not None:
                    all_none = False
        if not all_none:
            result.append(row_dict)

    wb.close()
    return result


def get_ventas_files(ventas_dir: str) -> List[str]:
    """Obtiene lista de archivos de ventas en orden cronológico."""
    import os
    archivos = []
    for mes in ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']:
        filename = f'Ventas {mes} 2025.xlsx'
        filepath = os.path.join(ventas_dir, filename)
        if os.path.exists(filepath):
            archivos.append(filepath)
    return archivos


def get_configuration_defaults() -> Dict[str, Any]:
    """Retorna diccionario con valores de configuración por defecto."""
    return {
        'fuzzy_threshold': DEFAULT_FUZZY_THRESHOLD,
        'tolerance': DEFAULT_TOLERANCE,
        'marcas_andes': MARCAS_ANDES,
        'colores': COLORES,
    }


def clasificar_estado_conciliacion(total_sistema: float, total_pagado: float,
                                   tolerance: float = DEFAULT_TOLERANCE) -> str:
    """Clasifica el estado de conciliación de un vehículo."""
    sistema = 0 if is_empty(total_sistema) else float(total_sistema)
    pagado = 0 if is_empty(total_pagado) else float(total_pagado)

    if sistema == 0 and pagado == 0:
        return 'SIN BONO'
    if sistema == 0 and pagado > 0:
        return 'SOBRE-PAGADO'
    if pagado == 0:
        return 'PENDIENTE'

    diferencia = sistema - pagado
    if abs(diferencia) <= tolerance:
        return 'PAGADO'
    elif diferencia > 0:
        return 'PAGO PARCIAL'
    else:
        return 'SOBRE-PAGADO'
