"""
Motor de matching difuso (fuzzy matching) para VINs.

Utiliza difflib.SequenceMatcher para encontrar coincidencias probables
cuando el matching exacto falla.
"""

import logging
from difflib import SequenceMatcher
from typing import List, Dict, Optional

try:
    from .utils import normalize_vin, is_empty
except ImportError:
    from utils import normalize_vin, is_empty

logger = logging.getLogger(__name__)


def fuzzy_ratio(a: str, b: str) -> int:
    """Calcula similitud entre dos strings (0-100), compatible con fuzz.ratio."""
    if not a or not b:
        return 0
    return int(SequenceMatcher(None, a, b).ratio() * 100)


def fuzzy_partial_ratio(a: str, b: str) -> int:
    """Partial ratio: best ratio of shorter string against substrings of longer."""
    if not a or not b:
        return 0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) == 0:
        return 0
    best = 0
    for i in range(len(long) - len(short) + 1):
        substr = long[i:i + len(short)]
        score = int(SequenceMatcher(None, short, substr).ratio() * 100)
        if score > best:
            best = score
            if best == 100:
                break
    return best


class FuzzyVINMatcher:
    """Motor de matching difuso para encontrar coincidencias probables de VINs."""

    def __init__(self, threshold: int = 85):
        self.threshold = threshold
        self.fuzzy_matches = []
        self.stats = {
            'total_comparisons': 0,
            'matches_found': 0,
            'by_score_range': {}
        }

    def find_fuzzy_matches(
        self,
        unmatched_ventas: List[Dict],
        unmatched_pagos: List[Dict],
        vin_col_ventas: str = 'VIN_NORM',
        vin_col_pagos: str = 'VIN_NORM'
    ) -> List[Dict]:
        """
        Encuentra coincidencias difusas entre VINs no conciliados.

        Args:
            unmatched_ventas: Lista de ventas sin matching exacto
            unmatched_pagos: Lista de pagos sin matching exacto

        Returns:
            Lista de diccionarios con coincidencias difusas
        """
        logger.info(f"Iniciando búsqueda difusa (umbral: {self.threshold}%)")
        logger.info(f"  - VINs ventas sin match: {len(unmatched_ventas)}")
        logger.info(f"  - VINs pagos sin match: {len(unmatched_pagos)}")

        fuzzy_matches = []

        if not unmatched_ventas or not unmatched_pagos:
            logger.info("  No hay registros para comparar")
            return fuzzy_matches

        # Agrupar por marca
        marcas = set()
        for v in unmatched_ventas:
            marca = v.get('Marca')
            if not is_empty(marca):
                marcas.add(marca)

        for marca in marcas:
            logger.info(f"  Buscando coincidencias difusas para {marca}...")

            ventas_marca = [v for v in unmatched_ventas if v.get('Marca') == marca]
            pagos_marca = [p for p in unmatched_pagos if p.get('MARCA') == marca]

            if not pagos_marca:
                logger.info(f"    No hay pagos sin match para {marca}")
                continue

            # Crear lista de VINs de pagos con sus índices
            pagos_vins = []
            for i, p in enumerate(pagos_marca):
                vin = p.get(vin_col_pagos)
                if not is_empty(vin) and vin != '':
                    pagos_vins.append((vin, i))

            pagos_vin_strings = [v[0] for v in pagos_vins]

            if not pagos_vin_strings:
                continue

            for venta_idx, venta_row in enumerate(ventas_marca):
                vin_venta = venta_row.get(vin_col_ventas)
                if is_empty(vin_venta) or vin_venta == '':
                    continue

                self.stats['total_comparisons'] += 1

                # Buscar top 3 coincidencias
                scores = []
                for pvin in pagos_vin_strings:
                    score = fuzzy_ratio(str(vin_venta), pvin)
                    scores.append((pvin, score))

                scores.sort(key=lambda x: x[1], reverse=True)
                top_matches = scores[:3]

                for match_vin, score in top_matches:
                    if score >= self.threshold:
                        # Encontrar el pago correspondiente
                        pago_i = next((i for v, i in pagos_vins if v == match_vin), None)
                        if pago_i is not None:
                            pago_row = pagos_marca[pago_i]
                            fuzzy_match = {
                                'venta_row': venta_row,
                                'pago_row': pago_row,
                                'vin_venta': vin_venta,
                                'vin_pago': match_vin,
                                'similarity_score': score,
                                'marca': marca,
                                'modelo_venta': venta_row.get('Versión', ''),
                                'modelo_pago': pago_row.get('MODELO', ''),
                                'monto_pago': pago_row.get('MONTO_NUM', 0),
                                'glosa': pago_row.get('GLOSA', ''),
                                'needs_review': True,
                                'match_type': f'FUZZY {score:.0f}%'
                            }
                            fuzzy_matches.append(fuzzy_match)
                            self.stats['matches_found'] += 1
                            logger.info(f"    {vin_venta} ~ {match_vin} ({score}%)")

        self._calculate_score_ranges(fuzzy_matches)
        logger.info(f"Total coincidencias difusas: {len(fuzzy_matches)}")
        self.fuzzy_matches = fuzzy_matches
        return fuzzy_matches

    def find_similar_vins_in_pagos(
        self, vin: str, pagos: List[Dict],
        vin_col: str = 'VIN_NORM', top_n: int = 5
    ) -> List[Dict]:
        """Busca VINs similares en la lista de pagos."""
        if is_empty(vin):
            return []
        vin_norm = normalize_vin(vin)
        if not vin_norm:
            return []

        pagos_vins = list(set(
            p[vin_col] for p in pagos
            if not is_empty(p.get(vin_col))
        ))
        if not pagos_vins:
            return []

        scores = [(pv, fuzzy_ratio(vin_norm, pv)) for pv in pagos_vins]
        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for match_vin, score in scores[:top_n]:
            pago_row = next((p for p in pagos if p.get(vin_col) == match_vin), None)
            if pago_row:
                results.append({
                    'vin_buscado': vin_norm,
                    'vin_encontrado': match_vin,
                    'similarity_score': score,
                    'marca': pago_row.get('MARCA', ''),
                    'modelo': pago_row.get('MODELO', ''),
                    'monto': pago_row.get('MONTO_NUM', 0),
                    'glosa': pago_row.get('GLOSA', '')
                })
        return results

    def _calculate_score_ranges(self, matches: List[Dict]):
        """Calcula estadísticas por rango de score."""
        self.stats['by_score_range'] = {
            '95-100%': len([m for m in matches if m['similarity_score'] >= 95]),
            '90-94%': len([m for m in matches if 90 <= m['similarity_score'] < 95]),
            '85-89%': len([m for m in matches if 85 <= m['similarity_score'] < 90]),
            '<85%': len([m for m in matches if m['similarity_score'] < 85]),
        }

    def get_summary(self) -> Dict:
        """Genera resumen de las coincidencias difusas."""
        if not self.fuzzy_matches:
            return {'total': 0, 'by_marca': {}, 'by_score_range': {}}

        by_marca = {}
        for match in self.fuzzy_matches:
            marca = match['marca']
            by_marca.setdefault(marca, []).append(match)

        return {
            'total': len(self.fuzzy_matches),
            'by_marca': {k: len(v) for k, v in by_marca.items()},
            'by_score_range': self.stats['by_score_range'],
            'total_comparisons': self.stats['total_comparisons']
        }

    def export_for_review(self) -> List[Dict]:
        """Exporta las coincidencias difusas para revisión."""
        if not self.fuzzy_matches:
            return []

        review_data = []
        for match in self.fuzzy_matches:
            review_data.append({
                'Marca': match['marca'],
                'VIN Venta': match['vin_venta'],
                'VIN Pago': match['vin_pago'],
                'Similitud %': match['similarity_score'],
                'Modelo Venta': match['modelo_venta'],
                'Modelo Pago': match['modelo_pago'],
                'Monto Pago': match['monto_pago'],
                'GLOSA': match['glosa'],
            })

        review_data.sort(key=lambda x: (x['Marca'], -x['Similitud %']))
        return review_data


def find_vin_typos(vin1: str, vin2: str) -> List[Dict]:
    """Identifica las diferencias específicas entre dos VINs."""
    differences = []
    vin1_norm = normalize_vin(vin1) or ''
    vin2_norm = normalize_vin(vin2) or ''

    if len(vin1_norm) != len(vin2_norm):
        differences.append({
            'tipo': 'longitud',
            'vin1_len': len(vin1_norm),
            'vin2_len': len(vin2_norm)
        })

    min_len = min(len(vin1_norm), len(vin2_norm))
    for i in range(min_len):
        if vin1_norm[i] != vin2_norm[i]:
            differences.append({
                'tipo': 'caracter',
                'posicion': i,
                'vin1_char': vin1_norm[i],
                'vin2_char': vin2_norm[i]
            })

    return differences
