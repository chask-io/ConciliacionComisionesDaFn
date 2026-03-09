"""
Motor de conciliación para matching de ventas con pagos de comisiones.

Implementa el algoritmo central de matching para reconciliar ventas de Daniel Achondo
contra los pagos de comisiones de Andes Motors.
"""

import logging
from collections import defaultdict, Counter
from typing import Dict, List, Any, Optional
from datetime import datetime

try:
    from .utils import (
        normalize_vin, normalize_marca, extraer_tipo_glosa,
        normalize_rut_numeric,
        MARCAS_ANDES, COLUMNAS_BONOS_VENTAS, MESES,
        clasificar_estado_conciliacion, DEFAULT_TOLERANCE,
        is_empty, to_numeric, safe_get
    )
    from .fuzzy_matcher import FuzzyVINMatcher
    from .credit_matcher import CreditMatcher
except ImportError:
    from utils import (
        normalize_vin, normalize_marca, extraer_tipo_glosa,
        normalize_rut_numeric,
        MARCAS_ANDES, COLUMNAS_BONOS_VENTAS, MESES,
        clasificar_estado_conciliacion, DEFAULT_TOLERANCE,
        is_empty, to_numeric, safe_get
    )
    from fuzzy_matcher import FuzzyVINMatcher
    from credit_matcher import CreditMatcher

logger = logging.getLogger(__name__)


class ConciliationEngine:
    """Motor para matching de ventas con pagos de comisiones de Andes Motors."""

    def __init__(
        self,
        ventas_rows: List[Dict],
        pagos_rows: List[Dict],
        fuzzy_threshold: int = 85,
        tolerance: float = DEFAULT_TOLERANCE
    ):
        self.ventas_raw = [dict(r) for r in ventas_rows]
        self.pagos_raw = [dict(r) for r in pagos_rows]
        self.fuzzy_threshold = fuzzy_threshold
        self.tolerance = tolerance

        # Prepared data
        self.ventas: List[Dict] = []
        self.pagos: List[Dict] = []
        self.pagos_por_vin: Dict[str, Dict] = {}
        self.conciliacion: List[Dict] = []

        # Results
        self.matched_exact: List[str] = []
        self.matched_fuzzy: List[Dict] = []
        self.unmatched_ventas: List[Dict] = []
        self.unmatched_pagos: List[Dict] = []

        # Fuzzy matcher
        self.fuzzy_matcher = FuzzyVINMatcher(threshold=fuzzy_threshold)

        # Credit data
        self.credito_rows = None
        self.credit_matcher = None
        self.credit_status = None

        # Prepare
        self._prepare_ventas()
        self._prepare_pagos()

    def _prepare_ventas(self):
        """Prepara datos de ventas con campos normalizados."""
        logger.info("Preparando datos de ventas...")

        prepared = []
        for row in self.ventas_raw:
            r = dict(row)
            r['VIN_NORM'] = normalize_vin(r.get('Numero Chasis'))

            # Filtrar solo marcas Andes
            if r.get('Marca') not in MARCAS_ANDES:
                continue

            # Extraer fecha, mes y año
            fecha_venta = r.get('Fecha Venta')
            if fecha_venta is not None:
                if isinstance(fecha_venta, datetime):
                    dt = fecha_venta
                else:
                    dt = self._parse_date(fecha_venta)

                if dt:
                    r['Fecha_Venta_Parsed'] = dt
                    r['Mes_Num'] = dt.month
                    r['Año'] = dt.year
                    r['Mes_Nombre'] = MESES.get(dt.month, 'Mes')
                    r['Mes_Año'] = f"{MESES.get(dt.month, 'Mes')} {dt.year}"
                else:
                    r['Fecha_Venta_Parsed'] = None
                    r['Mes_Num'] = None
                    r['Año'] = None
                    r['Mes_Nombre'] = None
                    r['Mes_Año'] = None
            else:
                r['Fecha_Venta_Parsed'] = None
                r['Mes_Num'] = None
                r['Año'] = None
                r['Mes_Nombre'] = None
                r['Mes_Año'] = None

            # Preparar columnas de bonos
            for col_orig, col_nuevo in COLUMNAS_BONOS_VENTAS.items():
                r[col_nuevo] = to_numeric(r.get(col_orig), 0)

            r['Total_Bonos_Sistema'] = (
                r['Bono_Contado_Sistema'] +
                r['Bono_Credito_Sistema'] +
                r['Bono_Volumen_Sistema'] +
                r['Bono_Credito_Extra_Sistema']
            )

            prepared.append(r)

        self.ventas = prepared
        con_bonos = sum(1 for r in prepared if r['Total_Bonos_Sistema'] > 0)
        logger.info(f"  Ventas marcas Andes: {len(prepared)}")
        logger.info(f"  Con bonos > 0: {con_bonos}")

    def _parse_date(self, value) -> Optional[datetime]:
        """Intenta parsear una fecha en varios formatos."""
        if isinstance(value, datetime):
            return value
        if is_empty(value):
            return None
        s = str(value).strip()
        # Strip time component if present
        if ' ' in s:
            s = s.split(' ')[0]
        for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    def _prepare_pagos(self):
        """Prepara datos de pagos con campos normalizados."""
        logger.info("Preparando datos de pagos...")

        prepared = []
        for row in self.pagos_raw:
            r = dict(row)
            r['VIN_NORM'] = normalize_vin(r.get('vin'))
            r['MONTO_NUM'] = to_numeric(r.get('MONTO'), 0)
            r['TIPO_GLOSA'] = extraer_tipo_glosa(r.get('GLOSA'))
            prepared.append(r)

        logger.info(f"  Total pagos: {len(prepared)}")

        # Log tipos de GLOSA
        tipo_counts = Counter(r['TIPO_GLOSA'] for r in prepared)
        for tipo, count in tipo_counts.items():
            logger.info(f"    - {tipo}: {count}")

        self.pagos = prepared
        self._agregar_pagos_por_vin()

    def _agregar_pagos_por_vin(self):
        """Agrega pagos por VIN y tipo de bono."""
        logger.info("Agregando pagos por VIN y tipo...")

        columnas_pagos = {
            'BONO_RETAIL': 'Pagado_Bono_Retail',
            'BONO_MARCA': 'Pagado_Bono_Marca',
            'CM': 'Pagado_CM',
            'FLOTA': 'Pagado_Flota',
            'DIF_FLOTA': 'Pagado_Dif_Flota',
            'INCENTIVO_COMERCIAL': 'Pagado_Incentivo',
            'AUT_MARCA': 'Pagado_Aut_Marca',
            'OTROS': 'Pagado_Otros'
        }

        # Pivot: aggregate by VIN + TIPO_GLOSA
        vin_tipo_sums = defaultdict(lambda: defaultdict(float))
        vin_glosas = defaultdict(set)
        vin_pago_count = defaultdict(int)

        for p in self.pagos:
            vin = p.get('VIN_NORM')
            if is_empty(vin):
                continue
            tipo = p['TIPO_GLOSA']
            vin_tipo_sums[vin][tipo] += p['MONTO_NUM']
            glosa = p.get('GLOSA')
            if not is_empty(glosa):
                vin_glosas[vin].add(str(glosa))
            vin_pago_count[vin] += 1

        # Build pagos_por_vin dict
        result = {}
        for vin, tipos in vin_tipo_sums.items():
            entry = {'VIN_NORM': vin}
            for tipo_orig, col_nuevo in columnas_pagos.items():
                entry[col_nuevo] = tipos.get(tipo_orig, 0)

            cols_pagado = list(columnas_pagos.values())
            entry['Total_Pagado_Andes'] = sum(entry[c] for c in cols_pagado)
            entry['Num_Pagos'] = vin_pago_count[vin]
            entry['Detalle_Pagos'] = ' | '.join(sorted(vin_glosas[vin]))
            result[vin] = entry

        self.pagos_por_vin = result
        logger.info(f"  VINs únicos con pagos: {len(result)}")

    def match_exact(self):
        """Realiza matching exacto por VIN normalizado."""
        logger.info("Realizando matching exacto por VIN...")

        conciliacion = []
        for venta in self.ventas:
            r = dict(venta)
            vin = r.get('VIN_NORM')

            # Merge payment data
            pago = self.pagos_por_vin.get(vin, {}) if vin else {}

            for col in ['Pagado_Bono_Retail', 'Pagado_Bono_Marca', 'Pagado_CM',
                        'Pagado_Flota', 'Pagado_Dif_Flota', 'Pagado_Incentivo',
                        'Pagado_Aut_Marca', 'Pagado_Otros', 'Total_Pagado_Andes', 'Num_Pagos']:
                r[col] = pago.get(col, 0)

            r['Detalle_Pagos'] = pago.get('Detalle_Pagos', '')
            r['Diferencia'] = r['Total_Bonos_Sistema'] - r['Total_Pagado_Andes']
            r['Estado'] = clasificar_estado_conciliacion(
                r['Total_Bonos_Sistema'], r['Total_Pagado_Andes'], self.tolerance
            )
            r['Match_Type'] = 'EXACTO' if r['Total_Pagado_Andes'] > 0 else ''

            conciliacion.append(r)

        self.conciliacion = conciliacion

        matched = sum(1 for r in conciliacion if r['Total_Pagado_Andes'] > 0)
        unmatched = sum(1 for r in conciliacion if r['Total_Pagado_Andes'] == 0)
        logger.info(f"  Con pago: {matched}")
        logger.info(f"  Sin pago: {unmatched}")

        self.matched_exact = [r['VIN_NORM'] for r in conciliacion if r['Total_Pagado_Andes'] > 0]

    def find_unmatched(self):
        """Identifica VINs sin matching en ambos lados."""
        logger.info("Identificando registros sin matching...")

        self.unmatched_ventas = [
            r for r in self.conciliacion
            if r['Total_Pagado_Andes'] == 0 and r['Total_Bonos_Sistema'] > 0
        ]

        vins_ventas = set(
            r['VIN_NORM'] for r in self.ventas
            if not is_empty(r.get('VIN_NORM'))
        )
        vins_pagos = set(
            p['VIN_NORM'] for p in self.pagos
            if not is_empty(p.get('VIN_NORM'))
        )

        vins_pagos_sin_venta = vins_pagos - vins_ventas
        self.unmatched_pagos = [
            p for p in self.pagos
            if p.get('VIN_NORM') in vins_pagos_sin_venta
        ]

        logger.info(f"  Ventas sin pago (con bonos): {len(self.unmatched_ventas)}")
        logger.info(f"  VINs en pagos sin venta: {len(vins_pagos_sin_venta)}")

    def perform_fuzzy_matching(self):
        """Realiza fuzzy matching en registros no conciliados."""
        logger.info("=" * 80)
        logger.info("Iniciando matching difuso...")
        logger.info("=" * 80)

        ventas_sin_match = [
            r for r in self.conciliacion
            if r['Total_Pagado_Andes'] == 0 and r['Total_Bonos_Sistema'] > 0
        ]

        vins_ventas = set(
            r['VIN_NORM'] for r in self.ventas
            if not is_empty(r.get('VIN_NORM'))
        )
        pagos_sin_match = [
            p for p in self.pagos
            if not is_empty(p.get('VIN_NORM')) and p['VIN_NORM'] not in vins_ventas
        ]

        if not ventas_sin_match or not pagos_sin_match:
            logger.info("No hay registros suficientes para fuzzy matching")
            return

        fuzzy_matches = self.fuzzy_matcher.find_fuzzy_matches(
            ventas_sin_match, pagos_sin_match,
            vin_col_ventas='VIN_NORM', vin_col_pagos='VIN_NORM'
        )

        self.matched_fuzzy = fuzzy_matches

        # Apply fuzzy matches to conciliation data
        # Build a lookup from VIN_NORM -> index in conciliacion
        vin_to_conc_idx = {}
        for i, r in enumerate(self.conciliacion):
            vin = r.get('VIN_NORM')
            if vin and r['Total_Pagado_Andes'] == 0:
                vin_to_conc_idx.setdefault(vin, i)

        fuzzy_applied = 0
        for match in fuzzy_matches:
            vin_venta = match['vin_venta']
            vin_pago = match['vin_pago']

            conc_idx = vin_to_conc_idx.get(vin_venta)
            if conc_idx is None:
                continue

            pago_data = self.pagos_por_vin.get(vin_pago)
            if pago_data is None:
                continue

            conc_row = self.conciliacion[conc_idx]

            for col in ['Pagado_Bono_Retail', 'Pagado_Bono_Marca', 'Pagado_CM',
                        'Pagado_Flota', 'Pagado_Dif_Flota', 'Pagado_Incentivo',
                        'Pagado_Aut_Marca', 'Pagado_Otros', 'Total_Pagado_Andes']:
                conc_row[col] = pago_data.get(col, 0)

            conc_row['Diferencia'] = conc_row['Total_Bonos_Sistema'] - conc_row['Total_Pagado_Andes']
            conc_row['Estado'] = clasificar_estado_conciliacion(
                conc_row['Total_Bonos_Sistema'], conc_row['Total_Pagado_Andes'], self.tolerance
            )
            conc_row['Match_Type'] = match['match_type']
            conc_row['VIN_Pago_Fuzzy'] = vin_pago
            fuzzy_applied += 1

        if fuzzy_applied > 0:
            logger.info(f"Aplicados {fuzzy_applied} fuzzy matches")

        summary = self.fuzzy_matcher.get_summary()
        logger.info(f"  Total difusas: {summary['total']}")
        logger.info(f"  Por marca: {summary['by_marca']}")

    def process_credit_sales(self, credito_rows: List[Dict]):
        """Procesa ventas a crédito y verifica estado de pago."""
        if not credito_rows:
            logger.info("No hay ventas a crédito para procesar")
            return

        logger.info("=" * 80)
        logger.info("PROCESANDO VENTAS A CRÉDITO")
        logger.info("=" * 80)

        self.credito_rows = credito_rows

        # Normalizar RUT en ventas
        for v in self.ventas:
            if 'RUT_NORM' not in v:
                v['RUT_NORM'] = normalize_rut_numeric(v.get('Rut'))

        self.credit_matcher = CreditMatcher(
            credito_rows=credito_rows,
            ventas=self.ventas,
            pagos_por_vin=self.pagos_por_vin,
            fuzzy_name_threshold=self.fuzzy_threshold
        )

        credit_ventas = self.credit_matcher.match_credit_to_ventas()
        self.credit_status = self.credit_matcher.check_payment_status(credit_ventas)

        logger.info("Procesamiento de ventas a crédito completado")

    def run_conciliation(self) -> List[Dict]:
        """Ejecuta el proceso completo de conciliación."""
        logger.info("=" * 80)
        logger.info("INICIANDO PROCESO DE CONCILIACIÓN")
        logger.info("=" * 80)

        self.match_exact()
        self.find_unmatched()
        self.perform_fuzzy_matching()
        self._log_summary()

        return self.conciliacion

    def _log_summary(self):
        """Registra estadísticas resumen."""
        logger.info("=" * 80)
        logger.info("RESUMEN DE CONCILIACIÓN")
        logger.info("=" * 80)

        total = len(self.conciliacion)
        con_bonos = sum(1 for r in self.conciliacion if r['Total_Bonos_Sistema'] > 0)

        logger.info(f"  Total vehículos: {total}")
        logger.info(f"  Con bonos: {con_bonos}")

        estado_counts = Counter(r['Estado'] for r in self.conciliacion)
        for estado, count in estado_counts.items():
            pct = count / total * 100 if total > 0 else 0
            logger.info(f"  {estado}: {count} ({pct:.1f}%)")

        monto_sistema = sum(r['Total_Bonos_Sistema'] for r in self.conciliacion)
        monto_pagado = sum(r['Total_Pagado_Andes'] for r in self.conciliacion)
        logger.info(f"  Monto sistema: ${monto_sistema:,.0f}")
        logger.info(f"  Monto pagado: ${monto_pagado:,.0f}")
        logger.info(f"  Diferencia: ${monto_sistema - monto_pagado:,.0f}")
        logger.info(f"  Matches fuzzy: {len(self.matched_fuzzy)}")

    def get_statistics(self) -> Dict[str, Any]:
        """Obtiene estadísticas detalladas de conciliación."""
        total = len(self.conciliacion)
        con_bonos = sum(1 for r in self.conciliacion if r['Total_Bonos_Sistema'] > 0)
        pagados = sum(1 for r in self.conciliacion if r['Estado'] == 'PAGADO')
        con_pago = sum(1 for r in self.conciliacion
                       if r['Total_Pagado_Andes'] > 0 and r['Total_Bonos_Sistema'] > 0)
        monto_sistema = sum(r['Total_Bonos_Sistema'] for r in self.conciliacion)
        monto_pagado = sum(r['Total_Pagado_Andes'] for r in self.conciliacion)

        pendientes = [r for r in self.conciliacion if r['Estado'] == 'PENDIENTE']
        parciales = [r for r in self.conciliacion if r['Estado'] == 'PAGO PARCIAL']

        pct_cobertura = round(con_pago / con_bonos * 100, 2) if con_bonos > 0 else 0
        pct_exacta = round(pagados / con_bonos * 100, 2) if con_bonos > 0 else 0
        pct_monetaria = round(monto_pagado / monto_sistema * 100, 2) if monto_sistema > 0 else 0

        estado_counts = Counter(r['Estado'] for r in self.conciliacion)

        stats = {
            'total_vehiculos': total,
            'vehiculos_con_bonos': con_bonos,
            'vehiculos_con_pago': con_pago,
            'estados': dict(estado_counts),
            'monto_bonos_sistema': monto_sistema,
            'monto_pagado_andes': monto_pagado,
            'monto_diferencia': sum(r['Diferencia'] for r in self.conciliacion),
            'n_pendiente_total': len(pendientes),
            'n_pendiente_parcial': len(parciales),
            'monto_pendiente_total': sum(r['Diferencia'] for r in pendientes),
            'monto_pendiente_parcial': sum(r['Diferencia'] for r in parciales),
            'pct_cobertura': pct_cobertura,
            'pct_exacta': pct_exacta,
            'pct_monetaria': pct_monetaria,
            'pct_conciliacion': pct_cobertura,
            'matches_exactos': len(self.matched_exact),
            'matches_fuzzy': len(self.matched_fuzzy),
            'vins_sin_venta': len(set(
                p['VIN_NORM'] for p in self.unmatched_pagos if not is_empty(p.get('VIN_NORM'))
            )),
            'fuzzy_summary': self.fuzzy_matcher.get_summary(),
            'by_marca': {},
            'by_mes': {}
        }

        # Stats por marca
        for marca in MARCAS_ANDES:
            rows_marca = [r for r in self.conciliacion if r.get('Marca') == marca]
            if not rows_marca:
                continue
            cb = sum(1 for r in rows_marca if r['Total_Bonos_Sistema'] > 0)
            pg = sum(1 for r in rows_marca if r['Estado'] == 'PAGADO')
            cp = sum(1 for r in rows_marca
                     if r['Total_Pagado_Andes'] > 0 and r['Total_Bonos_Sistema'] > 0)
            ms = sum(r['Total_Bonos_Sistema'] for r in rows_marca)
            mp = sum(r['Total_Pagado_Andes'] for r in rows_marca)

            stats['by_marca'][marca] = {
                'total': len(rows_marca), 'con_bonos': cb,
                'con_pago': cp, 'pagados': pg,
                'pendientes': sum(1 for r in rows_marca if r['Estado'] in ('PENDIENTE', 'PAGO PARCIAL')),
                'monto_sistema': ms, 'monto_pagado': mp,
                'diferencia': sum(r['Diferencia'] for r in rows_marca),
                'pct_cobertura': round(cp / cb * 100, 2) if cb > 0 else 0,
                'pct_exacta': round(pg / cb * 100, 2) if cb > 0 else 0,
                'pct_monetaria': round(mp / ms * 100, 2) if ms > 0 else 0,
                'pct_conciliacion': round(cp / cb * 100, 2) if cb > 0 else 0
            }

        # Stats por mes-año
        mes_año_groups = defaultdict(list)
        for r in self.conciliacion:
            key = r.get('Mes_Año')
            año = r.get('Año')
            mes_num = r.get('Mes_Num')
            if key and año is not None and mes_num is not None:
                mes_año_groups[(int(año), int(mes_num), key)].append(r)

        for (año, mes_num, mes_nombre) in sorted(mes_año_groups.keys(),
                                                    key=lambda x: (x[0], x[1])):
            rows = mes_año_groups[(año, mes_num, mes_nombre)]
            cb = sum(1 for r in rows if r['Total_Bonos_Sistema'] > 0)
            pg = sum(1 for r in rows if r['Estado'] == 'PAGADO')
            cp = sum(1 for r in rows if r['Total_Pagado_Andes'] > 0 and r['Total_Bonos_Sistema'] > 0)
            ms = sum(r['Total_Bonos_Sistema'] for r in rows)
            mp = sum(r['Total_Pagado_Andes'] for r in rows)

            stats['by_mes'][mes_nombre] = {
                'total': len(rows), 'con_bonos': cb, 'con_pago': cp, 'pagados': pg,
                'pendientes': sum(1 for r in rows if r['Estado'] in ('PENDIENTE', 'PAGO PARCIAL')),
                'monto_sistema': ms, 'monto_pagado': mp,
                'diferencia': sum(r['Diferencia'] for r in rows),
                'pct_cobertura': round(cp / cb * 100, 2) if cb > 0 else 0,
                'pct_monetaria': round(mp / ms * 100, 2) if ms > 0 else 0
            }

        # Credit stats
        if self.credit_matcher:
            stats['credit_sales'] = self.credit_matcher.get_statistics()
        else:
            stats['credit_sales'] = {}

        return stats
