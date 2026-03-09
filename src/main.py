#!/usr/bin/env python3
"""
Conciliación de Comisiones - Daniel Achondo vs Andes Motors

Punto de entrada principal para el proceso de conciliación.
Orquesta la carga de datos, matching exacto, fuzzy matching,
y generación del reporte Excel final.
"""

import os
import sys
import logging
import time
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, List
from datetime import datetime

try:
    from .conciliation_engine import ConciliationEngine
    from .excel_generator import generate_conciliation_report
    from .utils import (
        ValidationError, get_ventas_files, get_configuration_defaults,
        MARCAS_ANDES, read_excel_as_dicts
    )
except ImportError:
    from conciliation_engine import ConciliationEngine
    from excel_generator import generate_conciliation_report
    from utils import (
        ValidationError, get_ventas_files, get_configuration_defaults,
        MARCAS_ANDES, read_excel_as_dicts
    )


@dataclass
class ConciliationConfig:
    """Configuración para el proceso de conciliación."""
    ventas_dir: str
    pagos_file: str
    output_dir: str
    credito_file: Optional[str] = None
    fuzzy_threshold: int = 85
    tolerance: float = 1000

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def configure_logging(log_level: str = "INFO") -> logging.Logger:
    """Configura el sistema de logging."""
    level_map = {
        "DEBUG": logging.DEBUG, "INFO": logging.INFO,
        "WARNING": logging.WARNING, "ERROR": logging.ERROR
    }
    level = level_map.get(log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    return logging.getLogger(__name__)


def load_ventas(ventas_dir: str) -> List[Dict]:
    """Carga y consolida todos los archivos de ventas."""
    logger = logging.getLogger(__name__)
    logger.info(f"Cargando archivos de ventas desde: {ventas_dir}")

    archivos = get_ventas_files(ventas_dir)
    if not archivos:
        raise ValidationError(
            mensaje="No se encontraron archivos de ventas",
            archivo=ventas_dir, error_code='E001'
        )

    all_ventas = []
    for archivo in archivos:
        try:
            rows = read_excel_as_dicts(archivo)
            for row in rows:
                row['Archivo_Origen'] = os.path.basename(archivo)
            all_ventas.extend(rows)
            logger.info(f"  - {os.path.basename(archivo)}: {len(rows)} ventas")
        except Exception as e:
            logger.warning(f"  - Error cargando {archivo}: {e}")

    if not all_ventas:
        raise ValidationError(
            mensaje="No se pudieron cargar archivos de ventas",
            archivo=ventas_dir, error_code='E002'
        )

    logger.info(f"Total ventas cargadas: {len(all_ventas)}")
    return all_ventas


def load_pagos(pagos_file: str) -> List[Dict]:
    """Carga archivo de pagos de Andes Motors."""
    logger = logging.getLogger(__name__)
    logger.info(f"Cargando archivo de pagos: {pagos_file}")

    if not os.path.exists(pagos_file):
        raise ValidationError(
            mensaje="Archivo de pagos no encontrado",
            archivo=pagos_file, error_code='E001'
        )

    rows = read_excel_as_dicts(pagos_file)
    logger.info(f"Total pagos cargados: {len(rows)}")
    return rows


def load_credito(credito_file: str) -> Optional[List[Dict]]:
    """Carga archivo de ventas a crédito."""
    logger = logging.getLogger(__name__)

    if not credito_file:
        logger.info("No se especificó archivo de créditos")
        return None

    if not os.path.exists(credito_file):
        logger.warning(f"Archivo de créditos no encontrado: {credito_file}")
        return None

    logger.info(f"Cargando ventas a crédito: {credito_file}")
    rows = read_excel_as_dicts(credito_file, header_row=1)
    logger.info(f"Total ventas a crédito cargadas: {len(rows)}")
    return rows


def run(config: ConciliationConfig) -> Dict[str, Any]:
    """Ejecuta el proceso completo de conciliación."""
    start_time = time.time()
    logger = configure_logging("INFO")

    try:
        logger.info("=" * 80)
        logger.info("CONCILIACIÓN DE COMISIONES - DANIEL ACHONDO vs ANDES MOTORS")
        logger.info("=" * 80)
        logger.info(f"  Ventas: {config.ventas_dir}")
        logger.info(f"  Pagos: {config.pagos_file}")
        logger.info(f"  Créditos: {config.credito_file or 'No especificado'}")
        logger.info(f"  Umbral fuzzy: {config.fuzzy_threshold}%")
        logger.info(f"  Tolerancia: ${config.tolerance:,.0f}")

        # Paso 1: Cargar datos
        logger.info("\n[25%] PASO 1: Cargando datos")
        ventas_rows = load_ventas(config.ventas_dir)
        pagos_rows = load_pagos(config.pagos_file)
        credito_rows = load_credito(config.credito_file)

        # Paso 2: Ejecutar conciliación
        logger.info("\n[50%] PASO 2: Ejecutando conciliación")
        engine = ConciliationEngine(
            ventas_rows, pagos_rows,
            fuzzy_threshold=config.fuzzy_threshold,
            tolerance=config.tolerance
        )
        conciliacion = engine.run_conciliation()

        # Paso 2b: Procesar ventas a crédito
        if credito_rows is not None:
            logger.info("\n[60%] PASO 2b: Procesando ventas a crédito")
            engine.process_credit_sales(credito_rows)

        statistics = engine.get_statistics()

        # Paso 3: Generar reporte
        logger.info("\n[75%] PASO 3: Generando reporte Excel")
        output_path = generate_conciliation_report(
            conciliacion=conciliacion,
            statistics=statistics,
            pagos=engine.pagos,
            unmatched_pagos=engine.unmatched_pagos,
            fuzzy_matches=engine.matched_fuzzy,
            credit_status=engine.credit_status,
            output_dir=config.output_dir
        )

        elapsed_time = time.time() - start_time

        logger.info("\n[100%] CONCILIACIÓN COMPLETADA")
        logger.info(f"  Total vehículos: {statistics['total_vehiculos']}")
        logger.info(f"  Con bonos: {statistics['vehiculos_con_bonos']}")
        logger.info(f"  % Cobertura: {statistics.get('pct_cobertura', 0):.1f}%")
        logger.info(f"  Monto sistema: ${statistics['monto_bonos_sistema']:,.0f}")
        logger.info(f"  Monto pagado: ${statistics['monto_pagado_andes']:,.0f}")
        logger.info(f"  Diferencia: ${statistics['monto_diferencia']:,.0f}")
        logger.info(f"  Matches fuzzy: {statistics['matches_fuzzy']}")
        logger.info(f"  Archivo: {output_path}")
        logger.info(f"  Tiempo: {elapsed_time:.2f}s")

        return {
            "status": "ok",
            "result": {
                "output_file": output_path,
                "statistics": statistics,
                "summary": {
                    "total_vehiculos": statistics['total_vehiculos'],
                    "vehiculos_con_bonos": statistics['vehiculos_con_bonos'],
                    "vehiculos_con_pago": statistics.get('vehiculos_con_pago', 0),
                    "pct_cobertura": f"{statistics.get('pct_cobertura', 0):.1f}%",
                    "pct_exacta": f"{statistics.get('pct_exacta', 0):.1f}%",
                    "pct_monetaria": f"{statistics.get('pct_monetaria', 0):.1f}%",
                    "monto_diferencia": statistics['monto_diferencia'],
                    "matches_fuzzy": statistics['matches_fuzzy']
                },
                "metadata": {
                    "execution_time_seconds": round(elapsed_time, 2),
                    "timestamp": datetime.now().isoformat(),
                    "configuration": config.to_dict()
                }
            }
        }

    except ValidationError as ve:
        logger.error(str(ve))
        return {"status": "error", "error": f"Error de validación: {ve.mensaje}",
                "error_code": ve.error_code}
    except Exception as e:
        logger.error(f"Error durante la conciliación: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def main():
    """Función principal para ejecución desde línea de comandos."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)

    config = ConciliationConfig(
        ventas_dir=os.path.join(parent_dir, "ventas"),
        pagos_file=os.path.join(parent_dir, "pagos", "COMISIONES 2025 - DANIEL ACHONDO.xlsx"),
        credito_file=os.path.join(parent_dir, "ventas", "VENTAS CON CREDITO BK.xlsx"),
        output_dir=parent_dir,
        fuzzy_threshold=85, tolerance=1000
    )

    result = run(config)
    if result["status"] == "ok":
        print(f"\nArchivo: {result['result']['output_file']}")
        for key, value in result['result']['summary'].items():
            print(f"  - {key}: {value}")
    else:
        print(f"\n{result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
