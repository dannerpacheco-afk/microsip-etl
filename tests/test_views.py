"""Tests for views.py — every sql/views file renders and is created in order."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from views import SQL_DIR, create_internal_views

EXPECTED = [
    "v_etl_estado",
    "v_ventas_diarias_articulo",
    "v_ventas_cliente_mes",
    "v_clientes_resumen",
    "v_margen_linea_mes",
    "v_inventario_actual",
    "v_inventario_mensual",
    "v_compras_proveedor_mes",
    "v_ultimo_costo_articulo",
    "v_articulos_sin_movimiento",
    "v_articulo_claves",
    "v_tipos_clientes_formato",
    "v_iscam_ventas_mensual",
    "v_iscam_inventario_mensual",
]


def test_internal_views_created_in_dependency_order():
    loader = MagicMock()
    loader.project_id = "proj"
    created = create_internal_views(loader, "ds")
    assert created == EXPECTED
    sqls = [c.args[0] for c in loader.execute.call_args_list]
    assert all("{project}" not in s and "{dataset}" not in s for s in sqls)
    assert all("`proj.ds." in s for s in sqls)
    # a view may only reference views created before it
    for i, sql in enumerate(sqls):
        later = EXPECTED[i + 1 :]
        for name in later:
            assert f"`proj.ds.{name}`" not in sql, f"{EXPECTED[i]} references later view {name}"


def test_iscam_views_expose_required_columns():
    ventas = (SQL_DIR / "views" / "13_v_iscam_ventas_mensual.sql").read_text(encoding="utf-8")
    for col in ("MES", "FORMATO", "INCLUIR", "ZONA", "ALMACEN", "CLAVE_PRINCIPAL", "CODIGO_BARRAS",
                "DESCRIPCION", "UNIDAD_VENTA", "PROVEEDOR", "PIEZAS", "IMPORTE_SIN_IMPUESTOS",
                "IMPUESTOS", "IMPORTE_CON_IMPUESTOS"):
        assert f" {col}" in ventas or f".{col}" in ventas, col
    inv = (SQL_DIR / "views" / "14_v_iscam_inventario_mensual.sql").read_text(encoding="utf-8")
    for col in ("MES", "ALMACEN", "CLAVE_PRINCIPAL", "CODIGO_BARRAS", "EXISTENCIA_FIN_MES",
                "VALOR_COSTO_FIN_MES"):
        assert col in inv, col
    claves = (SQL_DIR / "views" / "11_v_articulo_claves.sql").read_text(encoding="utf-8")
    assert "^[0-9]{12,14}$" in claves
    formato = (SQL_DIR / "views" / "12_v_tipos_clientes_formato.sql").read_text(encoding="utf-8")
    assert "REGEXP_CONTAINS(UPPER(TRIM(t.NOMBRE)), UPPER(f.PATRON))" in formato
    assert "'Otros'" in formato
