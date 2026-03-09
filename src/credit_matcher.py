"""
Módulo de matching para ventas a crédito.

Implementa matching en dos pasos:
1. Crédito -> Ventas por RUT (para obtener VIN)
2. VIN -> Pagos (para verificar estado de pago)
"""

import logging
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Any, Tuple
from collections import Counter

try:
    from .utils import (
        normalize_rut_numeric, normalize_rut_string,
        normalize_vin, MARCAS_ANDES, is_empty, to_numeric
    )
except ImportError:
    from utils import (
        normalize_rut_numeric, normalize_rut_string,
        normalize_vin, MARCAS_ANDES, is_empty, to_numeric
    )

logger = logging.getLogger(__name__)


def _fuzzy_ratio(a: str, b: str) -> int:
    """Similitud entre dos strings (0-100)."""
    if not a or not b:
        return 0
    return int(SequenceMatcher(None, a, b).ratio() * 100)


def _fuzzy_partial_ratio(a: str, b: str) -> int:
    """Partial ratio: best ratio of shorter against substrings of longer."""
    if not a or not b:
        return 0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) == 0:
        return 0
    best = 0
    for i in range(len(long_) - len(short) + 1):
        substr = long_[i:i + len(short)]
        score = int(SequenceMatcher(None, short, substr).ratio() * 100)
        if score > best:
            best = score
            if best == 100:
                break
    return best


class CreditMatcher:
    """Realiza matching de ventas a crédito contra ventas y pagos."""

    def __init__(
        self,
        credito_rows: List[Dict],
        ventas: List[Dict],
        pagos_por_vin: Dict[str, Dict],
        fuzzy_name_threshold: int = 85
    ):
        """
        Args:
            credito_rows: Lista de ventas a crédito
            ventas: Lista de ventas mensuales consolidadas (con VIN)
            pagos_por_vin: Dict mapping VIN -> payment aggregation data
            fuzzy_name_threshold: Umbral para matching fuzzy de nombres
        """
        self.credito_raw = [dict(r) for r in credito_rows]
        self.ventas = [dict(r) for r in ventas]
        self.pagos_por_vin = dict(pagos_por_vin)
        self.fuzzy_name_threshold = fuzzy_name_threshold

        self.credito: List[Dict] = []
        self.matched_by_rut: List[Dict] = []
        self.matched_by_name: List[Dict] = []
        self.unmatched: List[Dict] = []
        self.credit_status: Optional[List[Dict]] = None

        self._prepare_data()

    def _prepare_data(self):
        """Prepara y normaliza los datos para matching."""
        logger.info("Preparando datos de ventas a crédito...")

        credito = []
        for row in self.credito_raw:
            r = dict(row)
            r['RUT_NORM'] = normalize_rut_string(r.get('RUT'))
            r['VIN_NORM'] = normalize_vin(r.get('VIN')) if 'VIN' in r else None
            cliente = r.get('CLIENTE', '')
            r['CLIENTE_NORM'] = str(cliente).upper().strip() if not is_empty(cliente) else ''
            modelo = r.get('MODELO', '')
            r['MODELO_NORM'] = str(modelo).upper().strip() if not is_empty(modelo) else ''
            credito.append(r)

        # Normalizar ventas
        for v in self.ventas:
            if 'RUT_NORM' not in v:
                v['RUT_NORM'] = normalize_rut_numeric(v.get('Rut'))
            if 'CLIENTE_NORM' not in v:
                cliente = v.get('Cliente', '')
                v['CLIENTE_NORM'] = str(cliente).upper().strip() if not is_empty(cliente) else ''

        self.credito = credito

        rut_count = sum(1 for r in credito if r['RUT_NORM'] is not None)
        vin_count = sum(1 for r in credito if r['VIN_NORM'] is not None)
        logger.info(f"  Ventas a crédito: {len(credito)}")
        logger.info(f"  Con RUT: {rut_count}")
        logger.info(f"  Con VIN: {vin_count}")

    def match_credit_to_ventas(self) -> List[Dict]:
        """
        Matching de ventas a crédito contra ventas para obtener VIN.

        Returns:
            Lista de dicts con resultados de matching
        """
        logger.info("Realizando matching de créditos a ventas...")

        results = []
        for credit in self.credito:
            result = {
                'mes': credit.get('MES', ''),
                'op': credit.get('OP', ''),
                'cliente': credit.get('CLIENTE', ''),
                'rut': credit.get('RUT', ''),
                'modelo': credit.get('MODELO', ''),
                'marca': credit.get('MARCA', ''),
                'vin_credito': credit.get('VIN_NORM'),
                'vin_from_ventas': None,
                'match_type': 'UNMATCHED',
                'match_score': 0,
                'venta_version': None,
                'venta_cliente': None
            }

            if is_empty(credit['RUT_NORM']):
                result['match_type'] = 'NO_RUT'
                results.append(result)
                self.unmatched.append(result)
                continue

            # Paso 1: Match por RUT
            vin, venta_info = self._match_by_rut(credit)
            if vin:
                result['vin_from_ventas'] = vin
                result['match_type'] = 'RUT_EXACT'
                result['match_score'] = 100
                result['venta_version'] = venta_info.get('version', '')
                result['venta_cliente'] = venta_info.get('cliente', '')
                self.matched_by_rut.append(result)
            else:
                # Paso 2: Match fuzzy por nombre
                vin, match_score, venta_info = self._match_by_name(credit)
                if vin:
                    result['vin_from_ventas'] = vin
                    result['match_type'] = f'FUZZY_NAME_{match_score:.0f}%'
                    result['match_score'] = match_score
                    result['venta_version'] = venta_info.get('version', '')
                    result['venta_cliente'] = venta_info.get('cliente', '')
                    self.matched_by_name.append(result)
                else:
                    result['match_type'] = 'UNMATCHED'
                    self.unmatched.append(result)

            results.append(result)

        logger.info(f"  Matched por RUT: {len(self.matched_by_rut)}")
        logger.info(f"  Matched por nombre: {len(self.matched_by_name)}")
        logger.info(f"  Sin match: {len(self.unmatched)}")
        return results

    def _match_by_rut(self, credit: Dict) -> Tuple[Optional[str], Dict]:
        """Busca venta por RUT y retorna VIN."""
        rut = credit['RUT_NORM']
        modelo_credit = credit['MODELO_NORM']
        marca_credit = str(credit.get('MARCA', '')).upper()

        ventas_rut = [v for v in self.ventas if v.get('RUT_NORM') == rut]

        if not ventas_rut:
            return None, {}

        if len(ventas_rut) == 1:
            venta = ventas_rut[0]
            return venta.get('VIN_NORM'), {
                'version': venta.get('Versión', ''),
                'cliente': venta.get('Cliente', '')
            }

        # Múltiples: encontrar la mejor por modelo/marca
        best_match = None
        best_score = 0

        for venta in ventas_rut:
            version = str(venta.get('Versión', '')).upper()
            marca = str(venta.get('Marca', '')).upper()

            score = _fuzzy_partial_ratio(modelo_credit, version)
            if marca_credit == marca:
                score += 10
            elif marca_credit in ['TANNER', 'GLOBAL', 'AUTOFIN']:
                score += 5

            if score > best_score:
                best_score = score
                best_match = venta

        if best_match is not None:
            return best_match.get('VIN_NORM'), {
                'version': best_match.get('Versión', ''),
                'cliente': best_match.get('Cliente', '')
            }

        venta = ventas_rut[0]
        return venta.get('VIN_NORM'), {
            'version': venta.get('Versión', ''),
            'cliente': venta.get('Cliente', '')
        }

    def _match_by_name(self, credit: Dict) -> Tuple[Optional[str], float, Dict]:
        """Match fuzzy por nombre de cliente y modelo."""
        cliente = credit['CLIENTE_NORM']
        modelo = credit['MODELO_NORM']
        marca = str(credit.get('MARCA', '')).upper()

        if not cliente:
            return None, 0, {}

        if marca in MARCAS_ANDES:
            ventas_filtered = [v for v in self.ventas
                               if str(v.get('Marca', '')).upper() == marca]
        else:
            ventas_filtered = self.ventas

        best_match = None
        best_score = 0

        for venta in ventas_filtered:
            venta_cliente = venta.get('CLIENTE_NORM', '')
            venta_version = str(venta.get('Versión', '')).upper()

            name_score = _fuzzy_ratio(cliente, venta_cliente)
            if name_score >= self.fuzzy_name_threshold:
                modelo_score = _fuzzy_partial_ratio(modelo, venta_version)
                combined_score = (name_score * 0.7) + (modelo_score * 0.3)

                if combined_score > best_score:
                    best_score = combined_score
                    best_match = venta

        if best_match is not None and best_score >= self.fuzzy_name_threshold:
            return best_match.get('VIN_NORM'), best_score, {
                'version': best_match.get('Versión', ''),
                'cliente': best_match.get('Cliente', '')
            }

        return None, 0, {}

    def check_payment_status(self, credit_ventas: List[Dict]) -> List[Dict]:
        """Verifica si los VINs encontrados tienen pagos."""
        logger.info("Verificando estado de pago para ventas a crédito...")

        vins_with_payment = set(self.pagos_por_vin.keys())

        results = []
        for row in credit_ventas:
            r = dict(row)
            effective_vin = r.get('vin_from_ventas') or r.get('vin_credito')
            r['effective_vin'] = effective_vin
            r['has_payment'] = effective_vin in vins_with_payment if effective_vin else False

            # Merge payment data
            if effective_vin and effective_vin in self.pagos_por_vin:
                pago = self.pagos_por_vin[effective_vin]
                r['Total_Pagado_Andes'] = pago.get('Total_Pagado_Andes', 0)
                r['Num_Pagos'] = pago.get('Num_Pagos', 0)
                r['Detalle_Pagos'] = pago.get('Detalle_Pagos', '')
            else:
                r['Total_Pagado_Andes'] = 0
                r['Num_Pagos'] = 0
                r['Detalle_Pagos'] = ''

            r['payment_status'] = self._classify_credit_status(r)
            results.append(r)

        # Log resumen
        status_counter = Counter(r['payment_status'] for r in results)
        logger.info("  Resumen de estados de pago:")
        for status, count in status_counter.items():
            logger.info(f"    {status}: {count}")

        self.credit_status = results
        return results

    def _classify_credit_status(self, row: Dict) -> str:
        """Clasifica el estado de pago de una venta a crédito."""
        if row.get('match_type') == 'NO_RUT':
            return 'SIN_RUT'
        elif row.get('match_type') == 'UNMATCHED':
            return 'SIN_VENTA_REGISTRADA'
        elif not row.get('effective_vin'):
            return 'SIN_VIN'
        elif row.get('has_payment'):
            return 'PAGADO'
        else:
            return 'PENDIENTE'

    def get_statistics(self) -> Dict[str, Any]:
        """Obtiene estadísticas del matching de créditos."""
        if not self.credit_status:
            return {}

        status_counter = Counter(r['payment_status'] for r in self.credit_status)
        total_pagado = sum(r.get('Total_Pagado_Andes', 0) for r in self.credit_status)

        return {
            'total_credit_sales': len(self.credit_status),
            'matched_by_rut': len(self.matched_by_rut),
            'matched_by_name': len(self.matched_by_name),
            'unmatched': len(self.unmatched),
            'with_payment': status_counter.get('PAGADO', 0),
            'pending_payment': status_counter.get('PENDIENTE', 0),
            'no_rut': status_counter.get('SIN_RUT', 0),
            'no_venta': status_counter.get('SIN_VENTA_REGISTRADA', 0),
            'by_status': dict(status_counter),
            'total_pagado': total_pagado
        }
