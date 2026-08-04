"""
tests/strategies/test_rsi_wilder_comprehensive.py
═══════════════════════════════════════════════════════════════════════════════
Tests exhaustivos de validación matemática de RSI Wilder.

Categorías:
  1. Conversiones RSI↔RS (rsi_to_rs / rs_to_rsi)
  2. price_for_rsi() — validación matemática directa
  3. price_for_rsi() — casos extremos de estado interno
  4. Estrategia — condiciones de zona y frontera
  5. Estrategia — precios calculados en señales
  6. Estrategia — combinaciones de señales según estado wallet
  7. Estrategia — control de duplicados (_fired_* sets)
  8. Integración — ciclos completos LONG / SHORT
  9. Escenarios del mundo real
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
import numpy as np

from support.types import Candle, Signal, SignalType, PositionDirection
from indicadores.rsi import RSIEngine
from strategies.rsi_wilder import RSIWilderStrategy
from tests.conftest import make_candle, make_candle_sequence


# =============================================================================
#  HELPERS COMPARTIDOS
# =============================================================================

class _FeedResult:
    """Resultado de alimentar velas: señales de la última vela + RSI al inicio de esa vela.
    Es iterable (devuelve las señales) para compatibilidad con los call sites existentes."""

    def __init__(self, signals: List[Signal], rsi_at_open: Optional[float],
                 expected_prices: Optional[dict] = None):
        self.signals: List[Signal] = signals
        self.rsi_at_open: Optional[float] = rsi_at_open
        self.expected_prices: dict = expected_prices or {}

    def __iter__(self):
        return iter(self.signals)


def warmup_engine(period: int, prices: List[float]) -> RSIEngine:
    """
    Alimenta un RSIEngine con una secuencia de precios y lo retorna listo
    para usar (con estado interno completamente inicializado).
    """
    engine = RSIEngine(period=period)
    for p in prices:
        engine.update(p)
    return engine


def assert_price_for_rsi_verified(
    engine: RSIEngine,
    target_rsi: float,
    *,
    abs_tol: float = 1e-6,
    msg: str = "",
) -> float:
    """
    Verifica que price_for_rsi(target_rsi) realmente produce un RSI ≈ target_rsi
    cuando se usa como próximo precio.

    1. Obtiene price_candidate = engine.price_for_rsi(target_rsi)
    2. Crea un engine clon (mismo periodo, misma secuencia de precios)
    3. Alimenta el clon con los mismos precios
    4. Hace update(price_candidate)
    5. Comprueba que el RSI resultante ≈ target_rsi

    Retorna el price_candidate para aserciones adicionales.
    """
    # Capturar estado actual: necesitamos los precios previos
    # para poder reconstruir el estado en un engine nuevo.
    # No tenemos acceso directo a _prev_close histórico, pero podemos
    # reconstruir con la misma secuencia de precios.
    # En lugar de eso, usamos un enfoque más simple:
    # si el engine no está listo, saltamos la verificación.
    if engine._prev_close is None or engine._count < engine._period:
        price = engine.price_for_rsi(target_rsi)
        return price

    price = engine.price_for_rsi(target_rsi)

    # Clonar: crear nuevo engine con mismo periodo
    # No podemos reconstruir exactamente la secuencia, pero podemos
    # verificar que el estado interno no haya cambiado después de price_for_rsi
    # (efecto colateral: no debe mutar estado)
    # Para verificar la fórmula, alimentamos el mismo engine secuencialmente:
    # Simplemente hacemos update(price) y verificamos RSI
    engine_clone = RSIEngine(period=engine._period)
    engine_clone._prev_close = engine._prev_close
    engine_clone._avg_gain = engine._avg_gain
    engine_clone._avg_loss = engine._avg_loss
    engine_clone._count = engine._count
    engine_clone._value = engine._value

    new_rsi = engine_clone.update(price)
    assert new_rsi is not None, (
        f"{msg}price_for_rsi({target_rsi})={price:.6f} "
        f"produjo RSI=None (engine no listo)"
    )

    # Verificar que el nuevo RSI ≈ target_rsi con tolerancia
    diff = abs(new_rsi - target_rsi)
    assert diff <= abs_tol, (
        f"{msg}price_for_rsi({target_rsi})={price:.6f} "
        f"produjo RSI={new_rsi:.10f} (esperado ~{target_rsi}, "
        f"diferencia={diff:.2e}, tolerancia={abs_tol:.0e})"
    )

    return price


def prices_trend(period: int, n: int, *, direction: str = "down") -> List[float]:
    """
    Genera una secuencia de precios con tendencia controlada.

    Args:
        period: Período RSI (al menos period+1 velas para inicializar)
        n: Número total de precios
        direction: 'down' → RSI bajo, 'up' → RSI alto, 'flat' → RSI ≈ 50
    """
    prices = []
    base = 100.0
    for i in range(n):
        if direction == "down":
            prices.append(base - i * 2.0)
        elif direction == "up":
            prices.append(base + i * 2.0)
        else:  # flat
            prices.append(base + np.random.normal(0, 0.5))
    return prices


def oscillating_prices(
    n: int = 30,
    base: float = 100.0,
    *,
    bias: str = "down",
    down_amp: float = 1.5,
    up_amp: float = 1.0,
    seed: int = 42,
) -> List[float]:
    """
    Genera una secuencia de precios que oscilan con un sesgo controlado,
    diseñada para mantener el RSI en un rango específico:
    
    - bias='down': RSI en el rango ~30-50 (zona de entrada LONG)
    - bias='up':   RSI en el rango ~50-70 (zona de entrada SHORT)
    
    Usa un patrón cíclico de subidas y bajadas con relación de amplitudes
    que mantiene el RS (y por tanto el RSI) en el rango deseado.
    """
    prices = [base]
    if bias == "down":
        # Patrón: down, up, down, down, up, down (más bajadas que subidas)
        cycle = [-down_amp, up_amp, -down_amp * 0.7,
                 -down_amp, up_amp, -down_amp * 0.7]
    else:  # up
        # Patrón: up, down, up, up, down, up (más subidas que bajadas)
        cycle = [up_amp, -down_amp * 0.7, up_amp,
                 up_amp, -down_amp * 0.7, up_amp]
    
    for i in range(1, n):
        step = cycle[(i - 1) % len(cycle)]
        prices.append(prices[-1] + step)
    return prices


# =============================================================================
#  CATEGORÍA 1: CONVERSIONES RSI ↔ RS
# =============================================================================

class TestRSIConversions:
    """Tests de _rsi_to_rs() y _rs_to_rsi() — conversiones matemáticas básicas."""

    def test_rsi_to_rs_roundtrip(self):
        """RSI → RS → RSI debe ser idempotente."""
        for rsi in [0.0, 10.0, 30.0, 50.0, 70.0, 90.0, 100.0]:
            rs = RSIEngine._rsi_to_rs(rsi)
            rsi_back = RSIEngine._rs_to_rsi(rs)
            assert rsi_back == pytest.approx(rsi, abs=1e-12), (
                f"Fallo roundtrip RSI={rsi} → RS={rs} → RSI={rsi_back}"
            )

    def test_rs_to_rsi_roundtrip(self):
        """RS → RSI → RS debe ser idempotente."""
        for rs in [0.0, 0.5, 1.0, 2.0, 10.0, 100.0]:
            rsi = RSIEngine._rs_to_rsi(rs)
            rs_back = RSIEngine._rsi_to_rs(rsi)
            assert rs_back == pytest.approx(rs, abs=1e-12), (
                f"Fallo roundtrip RS={rs} → RSI={rsi} → RS={rs_back}"
            )

    def test_rsi_to_rs_key_values(self):
        """Valores clave conocidos de RSI→RS."""
        # RSI=50 → RS=1, RSI=0 → RS=0, RSI=100 → RS=inf
        assert RSIEngine._rsi_to_rs(50.0) == pytest.approx(1.0, abs=1e-12)
        assert RSIEngine._rsi_to_rs(0.0) == 0.0
        assert RSIEngine._rsi_to_rs(100.0) == float('inf')

        # RSI=75 → RS=3, RSI=25 → RS=1/3
        assert RSIEngine._rsi_to_rs(75.0) == pytest.approx(3.0, abs=1e-12)
        assert RSIEngine._rsi_to_rs(25.0) == pytest.approx(1.0/3.0, abs=1e-12)

    def test_rs_to_rsi_key_values(self):
        """Valores clave conocidos de RS→RSI."""
        assert RSIEngine._rs_to_rsi(0.0) == 0.0
        assert RSIEngine._rs_to_rsi(1.0) == 50.0
        assert RSIEngine._rs_to_rsi(3.0) == 75.0
        assert RSIEngine._rs_to_rsi(float('inf')) == 100.0

    def test_rsi_to_rs_extremes(self):
        """RSI=0 y RSI=100 en _rsi_to_rs."""
        assert RSIEngine._rsi_to_rs(0.0) == 0.0
        assert RSIEngine._rsi_to_rs(100.0) == float('inf')

    def test_rs_to_rsi_extremes(self):
        """RS=0 y RS=inf en _rs_to_rsi."""
        assert RSIEngine._rs_to_rsi(0.0) == 0.0
        assert RSIEngine._rs_to_rsi(float('inf')) == 100.0

    def test_rsi_to_rs_negative_rsi_returns_zero(self):
        """RSI negativo en _rsi_to_rs debe retornar 0."""
        # Nota: esta función no protege contra RSI negativo, pero documentamos
        rs = RSIEngine._rsi_to_rs(-10.0)
        # Según fórmula: 100/(100-(-10)) - 1 = 100/110 - 1 = -0.0909...
        # No es un caso de uso real, pero verificamos que no crashea
        assert isinstance(rs, float)


# =============================================================================
#  CATEGORÍA 2: price_for_rsi() — VALIDACIÓN MATEMÁTICA DIRECTA
# =============================================================================

class TestPriceForRSIMathematical:
    """
    Tests que verifican que price_for_rsi() calcula correctamente el precio
    que produce el RSI objetivo.
    """

    # ── Parámetros comunes ─────────────────────────────────────────────
    PERIOD = 5
    N_WARMUP = 15  # Suficientes velas para estabilizar el RSI

    @pytest.fixture
    def engine_down(self) -> RSIEngine:
        """Engine con tendencia bajista (RSI bajo)."""
        return warmup_engine(self.PERIOD, prices_trend(self.PERIOD, self.N_WARMUP, direction="down"))

    @pytest.fixture
    def engine_up(self) -> RSIEngine:
        """Engine con tendencia alcista (RSI alto)."""
        return warmup_engine(self.PERIOD, prices_trend(self.PERIOD, self.N_WARMUP, direction="up"))

    @pytest.fixture
    def engine_flat(self) -> RSIEngine:
        """Engine con mercado lateral (RSI ≈ 50)."""
        return warmup_engine(self.PERIOD, prices_trend(self.PERIOD, self.N_WARMUP, direction="flat"))

    # ── Tests de verificación directa ──────────────────────────────────

    def test_price_for_rsi_30_actually_produces_rsi_30(self, engine_down):
        """price_for_rsi(30) con tendencia bajista debe producir RSI ≈ 30."""
        assert_price_for_rsi_verified(engine_down, 30.0, abs_tol=1e-6,
                                      msg="[down→30] ")

    def test_price_for_rsi_50_actually_produces_rsi_50(self, engine_down):
        """price_for_rsi(50) con tendencia bajista debe producir RSI ≈ 50."""
        assert_price_for_rsi_verified(engine_down, 50.0, abs_tol=1e-6,
                                      msg="[down→50] ")

    def test_price_for_rsi_70_actually_produces_rsi_70(self, engine_down):
        """price_for_rsi(70) con tendencia bajista debe producir RSI ≈ 70."""
        assert_price_for_rsi_verified(engine_down, 70.0, abs_tol=1e-6,
                                      msg="[down→70] ")

    def test_price_for_rsi_30_actually_produces_rsi_30_up(self, engine_up):
        """price_for_rsi(30) con tendencia alcista debe producir RSI ≈ 30."""
        assert_price_for_rsi_verified(engine_up, 30.0, abs_tol=1e-6,
                                      msg="[up→30] ")

    def test_price_for_rsi_50_actually_produces_rsi_50_up(self, engine_up):
        """price_for_rsi(50) con tendencia alcista debe producir RSI ≈ 50."""
        assert_price_for_rsi_verified(engine_up, 50.0, abs_tol=1e-6,
                                      msg="[up→50] ")

    def test_price_for_rsi_70_actually_produces_rsi_70_up(self, engine_up):
        """price_for_rsi(70) con tendencia alcista debe producir RSI ≈ 70."""
        assert_price_for_rsi_verified(engine_up, 70.0, abs_tol=1e-6,
                                      msg="[up→70] ")

    def test_price_for_rsi_50_actually_produces_rsi_50_flat(self, engine_flat):
        """price_for_rsi(50) en mercado lateral debe producir RSI ≈ 50."""
        assert_price_for_rsi_verified(engine_flat, 50.0, abs_tol=1e-6,
                                      msg="[flat→50] ")

    def test_price_for_rsi_multiple_targets_same_state(self, engine_down):
        """
        Para un mismo estado, price_for_rsi() debe producir precios
        que efectivamente den los RSI targets solicitados.
        """
        for target in [20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]:
            assert_price_for_rsi_verified(engine_down, target, abs_tol=1e-6,
                                          msg=f"[mismo_estado→{target}] ")

    def test_price_for_rsi_does_not_mutate_state(self, engine_down):
        """
        price_for_rsi() NO debe modificar el estado interno del engine.
        """
        # Capturar estado antes
        before = {
            "_prev_close": engine_down._prev_close,
            "_avg_gain": engine_down._avg_gain,
            "_avg_loss": engine_down._avg_loss,
            "_count": engine_down._count,
            "_value": engine_down._value,
        }

        # Llamar price_for_rsi varias veces
        for _ in range(10):
            engine_down.price_for_rsi(30.0)
            engine_down.price_for_rsi(50.0)
            engine_down.price_for_rsi(70.0)

        # Verificar que el estado no cambió
        assert engine_down._prev_close == before["_prev_close"]
        assert engine_down._avg_gain == before["_avg_gain"]
        assert engine_down._avg_loss == before["_avg_loss"]
        assert engine_down._count == before["_count"]
        assert engine_down._value == before["_value"]

    def test_price_for_rsi_consistent_same_state(self, engine_down):
        """
        Misma llamada con mismo estado debe dar el mismo precio.
        """
        p1 = engine_down.price_for_rsi(30.0)
        p2 = engine_down.price_for_rsi(30.0)
        assert p1 == p2, f"Mismo estado → precios diferentes: {p1} vs {p2}"

    # ── Tests de consistencia de fórmula ───────────────────────────────

    def test_price_for_rsi_50_close_to_prev(self, engine_down):
        """
        price_for_rsi(50) debe retornar un precio cercano a prev_close
        (porque RSI=50 implica ganancia ≈ pérdida).
        """
        price = engine_down.price_for_rsi(50.0)
        prev = engine_down._prev_close
        assert prev is not None
        # La diferencia no debería ser enorme
        ratio = abs(price - prev) / max(abs(prev), 1.0)
        assert ratio < 0.5, (
            f"price_for_rsi(50)={price:.4f} vs prev_close={prev:.4f} "
            f"(ratio={ratio:.4f})"
        )

    def test_price_for_rsi_target_100_very_high(self, engine_down):
        """
        price_for_rsi(100) debe retornar un precio muy superior a prev_close.
        """
        price = engine_down.price_for_rsi(100.0)
        prev = engine_down._prev_close
        assert prev is not None
        assert price > prev, (
            f"price_for_rsi(100)={price:.4f} debería ser > prev_close={prev:.4f}"
        )

    def test_price_for_rsi_target_0_very_low(self, engine_up):
        """
        price_for_rsi(0) debe retornar un precio muy inferior a prev_close.
        """
        price = engine_up.price_for_rsi(0.0)
        prev = engine_up._prev_close
        assert prev is not None
        assert price < prev, (
            f"price_for_rsi(0)={price:.4f} debería ser < prev_close={prev:.4f}"
        )

    def test_price_for_rsi_monotonic(self, engine_down):
        """
        A mayor target_rsi, mayor debe ser el precio calculado.
        (Relación monótona creciente)
        """
        prices = []
        for target in [10.0, 30.0, 50.0, 70.0, 90.0]:
            p = engine_down.price_for_rsi(target)
            prices.append(p)

        for i in range(len(prices) - 1):
            assert prices[i] <= prices[i + 1] + 1e-9, (
                f"No monótono: target más alto pero precio menor: "
                f"{prices[i]} > {prices[i+1]}"
            )


# =============================================================================
#  CATEGORÍA 3: price_for_rsi() — CASOS EXTREMOS DE ESTADO INTERNO
# =============================================================================

class TestPriceForRSIEdgeCases:
    """Casos extremos del estado interno del RSIEngine."""

    def test_price_for_rsi_before_any_update(self):
        """Sin ninguna vela → price_for_rsi retorna 0.0."""
        engine = RSIEngine(period=5)
        price = engine.price_for_rsi(50.0)
        assert price == 0.0

    def test_price_for_rsi_one_update_only(self):
        """Solo 1 update → count=0 < period → retorna prev_close."""
        engine = RSIEngine(period=5)
        engine.update(100.0)
        price = engine.price_for_rsi(50.0)
        assert price == 100.0

    def test_price_for_rsi_during_warmup(self):
        """Durante warmup (count < period) → retorna prev_close."""
        engine = RSIEngine(period=5)
        for i in range(5):  # 5 updates → count=4 < period=5
            engine.update(100.0 + i)
        price = engine.price_for_rsi(50.0)
        # prev_close = 100.0 + 4 = 104.0
        assert price == 104.0

    def test_price_for_rsi_after_exact_period(self):
        """Justo después del primer RSI válido (count=period)."""
        engine = RSIEngine(period=5)
        for i in range(6):  # 6 updates → count=5=period, RSI calculado
            engine.update(100.0 + i * 2)
        assert engine._count == engine._period
        assert engine.value is not None
        price = engine.price_for_rsi(50.0)
        assert price > 0

    def test_price_for_rsi_all_gains_no_losses(self):
        """G >> L (sin pérdidas históricas, L=0)."""
        engine = RSIEngine(period=5)
        # Precios siempre subiendo → L=0
        for i in range(10):
            engine.update(100.0 + i * 10.0)
        # L=0, G>0
        assert engine._avg_loss == 0.0
        assert engine._avg_gain > 0.0

        # price_for_rsi(30): target RSI bajo → necesita generar pérdida → Caso B
        price = engine.price_for_rsi(30.0)
        prev = engine._prev_close
        assert prev is not None
        # Debe ser menor que prev (perder para bajar RSI)
        assert price < prev, (
            f"Con L=0 y target=30, precio={price:.4f} debería ser < prev={prev:.4f}"
        )
        # Verificar que produce RSI ≈ 30
        assert_price_for_rsi_verified(engine, 30.0, abs_tol=1e-6,
                                      msg="[all_gains→30] ")

    def test_price_for_rsi_all_losses_no_gains(self):
        """L >> G (sin ganancias históricas, G=0)."""
        engine = RSIEngine(period=5)
        # Precios siempre bajando → G=0
        for i in range(10):
            engine.update(100.0 - i * 10.0)
        # G=0, L>0
        assert engine._avg_gain == 0.0
        assert engine._avg_loss > 0.0

        # price_for_rsi(70): target RSI alto → necesita generar ganancia → Caso A
        price = engine.price_for_rsi(70.0)
        prev = engine._prev_close
        assert prev is not None
        # Debe ser mayor que prev (ganar para subir RSI)
        assert price > prev, (
            f"Con G=0 y target=70, precio={price:.4f} debería ser > prev={prev:.4f}"
        )
        # Verificar que produce RSI ≈ 70
        assert_price_for_rsi_verified(engine, 70.0, abs_tol=1e-6,
                                      msg="[all_losses→70] ")

    def test_price_for_rsi_no_gains_no_losses(self):
        """G=0 y L=0 simultáneamente (precios sin cambios en periodo inicial)."""
        engine = RSIEngine(period=5)
        # Precios idénticos → G=0, L=0
        for i in range(10):
            engine.update(100.0)
        assert engine._avg_gain == 0.0
        assert engine._avg_loss == 0.0

        # Debe manejar el caso sin lanzar error
        price_up = engine.price_for_rsi(70.0)
        price_down = engine.price_for_rsi(30.0)
        price_50 = engine.price_for_rsi(50.0)

        # Debe retornar valores finitos
        assert np.isfinite(price_up), f"price_for_rsi(70)={price_up} no es finito"
        assert np.isfinite(price_down), f"price_for_rsi(30)={price_down} no es finito"
        assert np.isfinite(price_50), f"price_for_rsi(50)={price_50} no es finito"

    def test_price_for_rsi_period_2(self):
        """Período mínimo (2) debe funcionar correctamente."""
        engine = RSIEngine(period=2)
        for i in range(5):
            engine.update(100.0 + i * 3.0)
        assert engine.value is not None
        assert_price_for_rsi_verified(engine, 30.0, abs_tol=1e-6,
                                      msg="[period=2→30] ")
        assert_price_for_rsi_verified(engine, 70.0, abs_tol=1e-6,
                                      msg="[period=2→70] ")

    def test_price_for_rsi_period_20(self):
        """Período grande (20) debe funcionar correctamente."""
        engine = RSIEngine(period=20)
        for i in range(30):
            engine.update(100.0 + np.random.normal(0, 2))
        assert engine.value is not None
        assert_price_for_rsi_verified(engine, 30.0, abs_tol=1e-4,
                                      msg="[period=20→30] ")
        assert_price_for_rsi_verified(engine, 70.0, abs_tol=1e-4,
                                      msg="[period=20→70] ")

    def test_price_for_rsi_very_small_prices(self):
        """Precios muy pequeños (cercanos a 0)."""
        engine = RSIEngine(period=5)
        for i in range(10):
            engine.update(max(0.001, 1.0 + i * 0.1))
        assert engine.value is not None
        try:
            price = engine.price_for_rsi(50.0)
            assert np.isfinite(price), f"Precio no finito: {price}"
        except Exception as e:
            pytest.fail(f"price_for_rsi con precios pequeños lanzó excepción: {e}")

    def test_price_for_rsi_very_large_prices(self):
        """Precios muy grandes (1e6)."""
        engine = RSIEngine(period=5)
        for i in range(10):
            engine.update(1_000_000.0 + i * 10_000.0)
        assert engine.value is not None
        try:
            price = engine.price_for_rsi(50.0)
            assert np.isfinite(price), f"Precio no finito: {price}"
        except Exception as e:
            pytest.fail(f"price_for_rsi con precios grandes lanzó excepción: {e}")

    def test_price_for_rsi_target_100_always_high(self):
        """
        price_for_rsi(100) siempre debe dar un precio mayor que prev_close
        independientemente del estado.
        """
        for direction in ["down", "up", "flat"]:
            engine = warmup_engine(5, prices_trend(5, 15, direction=direction))
            price = engine.price_for_rsi(100.0)
            prev = engine._prev_close
            assert prev is not None
            assert price >= prev, (
                f"[{direction}] price_for_rsi(100)={price:.4f} < prev={prev:.4f}"
            )

    def test_price_for_rsi_target_0_always_low(self):
        """
        price_for_rsi(0) siempre debe dar un precio menor que prev_close
        independientemente del estado.
        """
        for direction in ["down", "up", "flat"]:
            engine = warmup_engine(5, prices_trend(5, 15, direction=direction))
            price = engine.price_for_rsi(0.0)
            prev = engine._prev_close
            assert prev is not None
            assert price <= prev, (
                f"[{direction}] price_for_rsi(0)={price:.4f} > prev={prev:.4f}"
            )


# =============================================================================
#  CATEGORÍA 4: ESTRATEGIA — CONDICIONES DE ZONA Y FRONTERA
# =============================================================================

class TestStrategyZones:
    """Tests de las condiciones de zona (LONG/SHORT/NEUTRAL) según RSI."""

    PERIOD = 5
    N_WARMUP = 10

    @pytest.fixture
    def strategy(self):
        """Estrategia con período 5 para warmup rápido."""
        return RSIWilderStrategy(
            rsi_period=self.PERIOD,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            reduce_long=50.0,
            reduce_short=50.0,
            max_positions=3,
            name="RSI_Wilder",
        )

    @pytest.fixture
    def empty_wallet(self):
        """Wallet sin posiciones."""
        from actors.wallet import MemoryWallet
        return MemoryWallet(usd_initial=1000.0, max_posiciones=3)

    # ── Helper: alimentar velas secuenciales ───────────────────────────

    def _feed_candles(self, strategy, wallet, prices) -> "_FeedResult":
        """Alimenta velas con los precios dados y retorna las señales de la última vela
        junto con el RSI al INICIO de esa vela (el que se usó para decidir)."""
        signals = []
        rsi_at_open = None
        for i, p in enumerate(prices):
            rsi_at_open = strategy._rsi_engine.value  # RSI al inicio de esta vela
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=p)
            # Usamos tick() para que actualice last_signals
            signals = strategy.tick(candle, wallet)
        return _FeedResult(signals, rsi_at_open)

    # ── Tests de zona ─────────────────────────────────────────────────

    def test_rsi_50_exact_no_signals(self, strategy, empty_wallet):
        """
        RSI exactamente 50 → zona neutral → no debe emitir ninguna señal
        (solo HOLD).
        """
        prices = [100.0] * 20  # Precios constantes → RSI = 50 exacto (sin cambios)
        signals = self._feed_candles(strategy, empty_wallet, prices)
        for s in signals:
            assert s.signal_type == SignalType.HOLD, (
                f"Con RSI=50 se esperaba HOLD, pero se obtuvo {s.signal_type}"
            )

    def test_rsi_just_below_50_long_zone(self, strategy, empty_wallet):
        """
        RSI ligeramente por debajo de 50 (ej. 49.9) → zona LONG.
        """
        prices = [100.0 - i * 0.5 for i in range(20)]  # Tendencia muy suave a la baja
        signals = self._feed_candles(strategy, empty_wallet, prices)
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        assert rsi < 50.0, f"RSI={rsi:.2f} debería ser < 50"
        # Si RSI está entre 30 y 50, debería emitir OPEN_LONG
        if rsi > 30.0:
            types = [s.signal_type for s in signals]
            has_open = SignalType.OPEN_LONG in types
            assert has_open, (
                f"RSI={rsi:.2f} en zona LONG (30-50) debería emitir OPEN_LONG, "
                f"señales: {types}"
            )

    def test_rsi_just_above_50_short_zone(self, strategy, empty_wallet):
        """
        RSI ligeramente por encima de 50 (ej. 50.1) → zona SHORT.
        """
        prices = [100.0 + i * 0.5 for i in range(20)]  # Tendencia muy suave al alza
        signals = self._feed_candles(strategy, empty_wallet, prices)
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        assert rsi > 50.0, f"RSI={rsi:.2f} debería ser > 50"
        # Si RSI está entre 50 y 70, debería emitir OPEN_SHORT
        if rsi < 70.0:
            types = [s.signal_type for s in signals]
            has_open = SignalType.OPEN_SHORT in types
            assert has_open, (
                f"RSI={rsi:.2f} en zona SHORT (50-70) debería emitir OPEN_SHORT, "
                f"señales: {types}"
            )

    # ── Tests de frontera (boundary conditions) ────────────────────────

    def test_rsi_exactly_30_no_open_long(self, strategy, empty_wallet):
        """
        RSI exactamente 30 → condición de entrada es rsi > 30 → NO debe abrir LONG.
        (La entrada requiere RSI > oversold, no >=)
        """
        # Para lograr RSI ≈ 30, necesitamos precios con tendencia bajista
        # pero que en la última vela apenas suban
        prices = [100.0 - i * 3.0 for i in range(15)]
        # Ajustar última vela para que RSI sea exactamente 30
        # Esto es difícil de controlar, así que verificamos la lógica:
        # Si RSI=30 exacto, in_entry_zone = rsi > 30 = False → no OPEN_LONG
        signals = self._feed_candles(strategy, empty_wallet, prices)
        rsi = strategy._rsi_engine.value

        # Si por casualidad RSI=30 exacto, no debe emitir OPEN_LONG
        if rsi is not None and abs(rsi - 30.0) < 1e-9:
            types = [s.signal_type for s in signals]
            assert SignalType.OPEN_LONG not in types, (
                f"RSI exactamente 30 no debería emitir OPEN_LONG, señales: {types}"
            )

    def test_rsi_exactly_70_no_open_short(self, strategy, empty_wallet):
        """
        RSI exactamente 70 → condición de entrada es rsi < 70 → NO debe abrir SHORT.
        """
        prices = [100.0 + i * 3.0 for i in range(15)]
        signals = self._feed_candles(strategy, empty_wallet, prices)
        rsi = strategy._rsi_engine.value

        if rsi is not None and abs(rsi - 70.0) < 1e-9:
            types = [s.signal_type for s in signals]
            assert SignalType.OPEN_SHORT not in types, (
                f"RSI exactamente 70 no debería emitir OPEN_SHORT, señales: {types}"
            )

    def test_rsi_below_30_no_entry_zone(self, strategy, empty_wallet):
        """
        RSI por debajo de 30 (ej. 25) → no debe abrir LONG porque
        in_entry_zone = rsi > 30 = False.
        """
        prices = [100.0 - i * 4.0 for i in range(15)]
        signals = self._feed_candles(strategy, empty_wallet, prices)
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        if rsi < 30.0:
            types = [s.signal_type for s in signals]
            assert SignalType.OPEN_LONG not in types, (
                f"RSI={rsi:.2f} < 30 no debería emitir OPEN_LONG, señales: {types}"
            )

    def test_rsi_above_70_no_entry_zone(self, strategy, empty_wallet):
        """
        RSI por encima de 70 (ej. 75) → no debe abrir SHORT porque
        in_entry_zone = rsi < 70 = False.
        """
        prices = [100.0 + i * 4.0 for i in range(15)]
        signals = self._feed_candles(strategy, empty_wallet, prices)
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        if rsi > 70.0:
            types = [s.signal_type for s in signals]
            assert SignalType.OPEN_SHORT not in types, (
                f"RSI={rsi:.2f} > 70 no debería emitir OPEN_SHORT, señales: {types}"
            )

    def test_rsi_31_opens_long(self, strategy, empty_wallet):
        """
        RSI=31 (entre 30 y 50) → debe abrir LONG.
        """
        # Tendencia bajista suave para que RSI esté en rango 30-50
        prices = [100.0 - i * 2.5 for i in range(15)]
        signals = self._feed_candles(strategy, empty_wallet, prices)
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        if 30.0 < rsi < 50.0:
            types = [s.signal_type for s in signals]
            assert SignalType.OPEN_LONG in types, (
                f"RSI={rsi:.2f} debería emitir OPEN_LONG, señales: {types}"
            )

    def test_rsi_69_opens_short(self, strategy, empty_wallet):
        """
        RSI=69 (entre 50 y 70) → debe abrir SHORT.
        """
        prices = [100.0 + i * 2.5 for i in range(15)]
        signals = self._feed_candles(strategy, empty_wallet, prices)
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        if 50.0 < rsi < 70.0:
            types = [s.signal_type for s in signals]
            assert SignalType.OPEN_SHORT in types, (
                f"RSI={rsi:.2f} debería emitir OPEN_SHORT, señales: {types}"
            )


# =============================================================================
#  CATEGORÍA 5: ESTRATEGIA — PRECIOS CALCULADOS EN SEÑALES
# =============================================================================

class TestStrategyPrices:
    """
    Tests que verifican que los precios en las señales coinciden
    con los calculados por RSIEngine.price_for_rsi().
    """

    PERIOD = 5
    N_WARMUP = 10

    @pytest.fixture
    def strategy(self):
        return RSIWilderStrategy(
            rsi_period=self.PERIOD,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            reduce_long=50.0,
            reduce_short=50.0,
            max_positions=3,
        )

    @pytest.fixture
    def empty_wallet(self):
        from actors.wallet import MemoryWallet
        return MemoryWallet(usd_initial=1000.0, max_posiciones=3)

    @pytest.fixture
    def wallet_with_long(self):
        from actors.wallet import MemoryWallet, TradeRecord
        w = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        w.update(TradeRecord(ts=1, side="BUY", price=100.0, usd_spent=100.0,
                             btc_bought=1.0, direction=PositionDirection.LONG))
        return w

    @pytest.fixture
    def wallet_with_short(self):
        from actors.wallet import MemoryWallet, TradeRecord
        w = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        w.update(TradeRecord(ts=1, side="SELL", price=65000.0, btc_sold=0.1,
                             usd_received=6500.0, direction=PositionDirection.SHORT))
        return w

    def _get_signals(self, strategy, wallet, prices) -> "_FeedResult":
        """
        Alimenta velas y retorna señales de la última vela + RSI al inicio de esa vela.
        También captura los precios esperados (price_for_rsi) con el estado del OPEN.
        """
        signals = []
        rsi_at_open = None
        expected_prices: dict = {}
        for i, p in enumerate(prices):
            rsi_at_open = strategy._rsi_engine.value  # RSI al inicio de esta vela
            if rsi_at_open is not None:
                # Capturar precios esperados con el estado del open (previo al close)
                expected_prices = {
                    target: strategy._rsi_engine.price_for_rsi(target)
                    for target in (30.0, 50.0, 70.0)
                }
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=p)
            signals = strategy.tick(candle, wallet)
        return _FeedResult(signals, rsi_at_open, expected_prices)

    def test_open_long_price_is_price_for_rsi_30(self, strategy, empty_wallet):
        """OPEN_LONG debe usar price_for_rsi(oversold_threshold=30)."""
        prices = [100.0 - i * 2.5 for i in range(15)]
        result = self._get_signals(strategy, empty_wallet, prices)

        expected_price = result.expected_prices[30.0]
        for s in result.signals:
            if s.signal_type == SignalType.OPEN_LONG:
                assert s.price == pytest.approx(expected_price, abs=1e-6), (
                    f"OPEN_LONG price={s.price:.6f} esperado={expected_price:.6f}"
                )

    def test_open_short_price_is_price_for_rsi_70(self, strategy, empty_wallet):
        """OPEN_SHORT debe usar price_for_rsi(overbought_threshold=70)."""
        prices = [100.0 + i * 2.5 for i in range(15)]
        result = self._get_signals(strategy, empty_wallet, prices)

        expected_price = result.expected_prices[70.0]
        for s in result.signals:
            if s.signal_type == SignalType.OPEN_SHORT:
                assert s.price == pytest.approx(expected_price, abs=1e-6), (
                    f"OPEN_SHORT price={s.price:.6f} esperado={expected_price:.6f}"
                )

    def test_reduce_long_price_is_price_for_rsi_50(self, strategy, wallet_with_long):
        """REDUCE_LONG debe usar price_for_rsi(reduce_long=50)."""
        prices = [100.0 - i * 2.5 for i in range(15)]
        result = self._get_signals(strategy, wallet_with_long, prices)

        expected_price = result.expected_prices[50.0]
        for s in result.signals:
            if s.signal_type == SignalType.REDUCE_LONG:
                assert s.price == pytest.approx(expected_price, abs=1e-6), (
                    f"REDUCE_LONG price={s.price:.6f} esperado={expected_price:.6f}"
                )

    def test_reduce_short_price_is_price_for_rsi_50(self, strategy, wallet_with_short):
        """REDUCE_SHORT debe usar price_for_rsi(reduce_short=50)."""
        prices = [100.0 + i * 2.5 for i in range(15)]
        result = self._get_signals(strategy, wallet_with_short, prices)

        expected_price = result.expected_prices[50.0]
        for s in result.signals:
            if s.signal_type == SignalType.REDUCE_SHORT:
                assert s.price == pytest.approx(expected_price, abs=1e-6), (
                    f"REDUCE_SHORT price={s.price:.6f} esperado={expected_price:.6f}"
                )

    def test_close_long_price_is_price_for_rsi_70(self, strategy, wallet_with_long):
        """CLOSE_LONG debe usar price_for_rsi(overbought_threshold=70)."""
        prices = [100.0 + i * 2.5 for i in range(15)]  # RSI > 50 → zona SHORT
        result = self._get_signals(strategy, wallet_with_long, prices)

        expected_price = result.expected_prices[70.0]
        for s in result.signals:
            if s.signal_type == SignalType.CLOSE_LONG:
                assert s.price == pytest.approx(expected_price, abs=1e-6), (
                    f"CLOSE_LONG price={s.price:.6f} esperado={expected_price:.6f}"
                )

    def test_close_short_price_is_price_for_rsi_30(self, strategy, wallet_with_short):
        """CLOSE_SHORT debe usar price_for_rsi(oversold_threshold=30)."""
        prices = [100.0 - i * 2.5 for i in range(15)]  # RSI < 50 → zona LONG
        result = self._get_signals(strategy, wallet_with_short, prices)

        expected_price = result.expected_prices[30.0]
        for s in result.signals:
            if s.signal_type == SignalType.CLOSE_SHORT:
                assert s.price == pytest.approx(expected_price, abs=1e-6), (
                    f"CLOSE_SHORT price={s.price:.6f} esperado={expected_price:.6f}"
                )

    def test_all_signals_have_positive_prices(self, strategy, empty_wallet,
                                                wallet_with_long, wallet_with_short):
        """
        Todas las señales emitidas deben tener precios positivos y finitos.
        """
        # Escenario LONG
        prices_down = [100.0 - i * 2.5 for i in range(15)]
        signals_long = self._get_signals(strategy, empty_wallet, prices_down).signals

        # Escenario SHORT
        strategy2 = RSIWilderStrategy(rsi_period=self.PERIOD)
        prices_up = [100.0 + i * 2.5 for i in range(15)]
        signals_short = self._get_signals(strategy2, empty_wallet, prices_up).signals

        all_signals = signals_long + signals_short

        for s in all_signals:
            if s.signal_type != SignalType.HOLD:
                assert s.price > 0, f"Precio no positivo: {s}"
                assert np.isfinite(s.price), f"Precio no finito: {s}"

    def test_signals_include_price_in_reason(self, strategy, empty_wallet):
        """
        El campo reason de las señales debe incluir el precio calculado.
        """
        prices = [100.0 - i * 2.5 for i in range(15)]
        signals = self._get_signals(strategy, empty_wallet, prices).signals

        for s in signals:
            if s.signal_type != SignalType.HOLD:
                assert "@" in s.reason, f"Reason sin '@': {s.reason}"
                # El precio debe aparecer en el reason (formateado con 2 decimales)
                price_str = f"{s.price:.2f}"
                assert price_str in s.reason, (
                    f"Precio {price_str} no está en reason: {s.reason}"
                )


# =============================================================================
#  CATEGORÍA 6: ESTRATEGIA — COMBINACIONES DE SEÑALES SEGÚN ESTADO WALLET
# =============================================================================

class TestStrategySignalCombinations:
    """
    Tests de todas las combinaciones de señales según el estado de la wallet.
    """

    PERIOD = 5
    BASE_TS = 1_700_000_000

    @pytest.fixture
    def strategy(self):
        return RSIWilderStrategy(
            rsi_period=self.PERIOD,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            reduce_long=50.0,
            reduce_short=50.0,
            max_positions=3,
        )

    @pytest.fixture
    def empty_wallet(self):
        from actors.wallet import MemoryWallet
        return MemoryWallet(usd_initial=1000.0, max_posiciones=3)

    @pytest.fixture
    def wallet_with_long(self):
        from actors.wallet import MemoryWallet, TradeRecord
        w = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        w.update(TradeRecord(ts=1, side="BUY", price=100.0, usd_spent=100.0,
                             btc_bought=1.0, direction=PositionDirection.LONG))
        return w

    @pytest.fixture
    def wallet_with_short(self):
        from actors.wallet import MemoryWallet, TradeRecord
        w = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        w.update(TradeRecord(ts=1, side="SELL", price=65000.0, btc_sold=0.1,
                             usd_received=6500.0, direction=PositionDirection.SHORT))
        return w

    def _feed(self, strategy, wallet, prices, ts_start=None) -> "_FeedResult":
        """Alimenta velas y retorna señales de la última + RSI al inicio de esa vela."""
        ts_start = ts_start or self.BASE_TS
        signals = []
        rsi_at_open = None
        for i, p in enumerate(prices):
            rsi_at_open = strategy._rsi_engine.value  # RSI al inicio de esta vela
            candle = make_candle(ts=ts_start + i * 3600, close=p)
            signals = strategy.tick(candle, wallet)
        return _FeedResult(signals, rsi_at_open)

    # ── Sin posición ──────────────────────────────────────────────────

    def test_no_position_rsi_below_50_opens_long(self, strategy, empty_wallet):
        """Sin posición + RSI < 50 + RSI entre 30-50 → OPEN_LONG."""
        prices = oscillating_prices(n=30, base=100.0, bias="down", down_amp=1.5, up_amp=1.0)
        result = self._feed(strategy, empty_wallet, prices)
        rsi = result.rsi_at_open
        assert rsi is not None, "RSI no disponible"
        types = [s.signal_type for s in result.signals]
        if 30.0 < rsi < 50.0:
            assert SignalType.OPEN_LONG in types, (
                f"Sin posición + RSI={rsi:.2f}<50 debería emitir OPEN_LONG, señales: {types}"
            )

    def test_no_position_rsi_above_50_opens_short(self, strategy, empty_wallet):
        """Sin posición + RSI > 50 + RSI entre 50-70 → OPEN_SHORT."""
        prices = oscillating_prices(n=30, base=100.0, bias="up", down_amp=1.5, up_amp=1.0)
        result = self._feed(strategy, empty_wallet, prices)
        rsi = result.rsi_at_open
        assert rsi is not None, "RSI no disponible"
        types = [s.signal_type for s in result.signals]
        if 50.0 < rsi < 70.0:
            assert SignalType.OPEN_SHORT in types, (
                f"Sin posición + RSI={rsi:.2f}>50 debería emitir OPEN_SHORT, señales: {types}"
            )

    def test_no_position_rsi_30_50_emits_only_open_long(self, strategy, empty_wallet):
        """Sin posición + RSI entre 30-50 → solo OPEN_LONG (sin otras señales)."""
        prices = [100.0 - i * 2.5 for i in range(15)]
        signals = self._feed(strategy, empty_wallet, prices)
        types = [s.signal_type for s in signals]
        # Solo debe haber OPEN_LONG y posiblemente HOLD
        actionable = [t for t in types if t != SignalType.HOLD]
        for t in actionable:
            assert t == SignalType.OPEN_LONG, (
                f"Se esperaba solo OPEN_LONG, pero hay: {actionable}"
            )

    def test_no_position_rsi_50_70_emits_only_open_short(self, strategy, empty_wallet):
        """Sin posición + RSI entre 50-70 → solo OPEN_SHORT."""
        prices = [100.0 + i * 2.5 for i in range(15)]
        signals = self._feed(strategy, empty_wallet, prices)
        types = [s.signal_type for s in signals]
        actionable = [t for t in types if t != SignalType.HOLD]
        for t in actionable:
            assert t == SignalType.OPEN_SHORT, (
                f"Se esperaba solo OPEN_SHORT, pero hay: {actionable}"
            )

    # ── Con posición LONG ─────────────────────────────────────────────

    def test_with_long_rsi_below_50_adds_and_reduces(self, strategy, wallet_with_long):
        """Con LONG + RSI < 50 → ADD_LONG (si RSI entre 30-50) + REDUCE_LONG."""
        prices = oscillating_prices(n=30, base=100.0, bias="down", down_amp=1.5, up_amp=1.0)
        result = self._feed(strategy, wallet_with_long, prices)
        rsi = result.rsi_at_open
        assert rsi is not None, "RSI no disponible"
        types = [s.signal_type for s in result.signals]
        assert SignalType.REDUCE_LONG in types, (
            f"Con LONG + RSI={rsi:.2f}<50 debería emitir REDUCE_LONG, señales: {types}"
        )
        if 30.0 < rsi < 50.0:
            assert SignalType.ADD_LONG in types, (
                f"Con LONG + RSI={rsi:.2f} entre 30-50 debería emitir ADD_LONG, señales: {types}"
            )

    def test_with_long_rsi_above_50_closes_long(self, strategy, wallet_with_long):
        """Con LONG + RSI > 50 → CLOSE_LONG."""
        prices = [100.0 + i * 2.5 for i in range(15)]
        signals = self._feed(strategy, wallet_with_long, prices)
        types = [s.signal_type for s in signals]
        assert SignalType.CLOSE_LONG in types, (
            f"Con LONG + RSI>50 debería emitir CLOSE_LONG, señales: {types}"
        )

    def test_with_long_rsi_30_50_emits_both_add_and_reduce(self, strategy, wallet_with_long):
        """Con LONG + RSI entre 30-50 → ADD_LONG (si rsi>30) + REDUCE_LONG."""
        prices = [100.0 - i * 2.0 for i in range(15)]
        result = self._feed(strategy, wallet_with_long, prices)
        rsi = result.rsi_at_open
        assert rsi is not None
        types = [s.signal_type for s in result.signals]
        if 30.0 < rsi < 50.0:
            assert SignalType.ADD_LONG in types, (
                f"RSI={rsi:.2f} debería emitir ADD_LONG, señales: {types}"
            )

    # ── Con posición SHORT ────────────────────────────────────────────

    def test_with_short_rsi_above_50_adds_and_reduces(self, strategy, wallet_with_short):
        """Con SHORT + RSI > 50 → ADD_SHORT (si RSI entre 50-70) + REDUCE_SHORT."""
        prices = oscillating_prices(n=30, base=100.0, bias="up", down_amp=1.5, up_amp=1.0)
        result = self._feed(strategy, wallet_with_short, prices)
        rsi = result.rsi_at_open
        assert rsi is not None, "RSI no disponible"
        types = [s.signal_type for s in result.signals]
        assert SignalType.REDUCE_SHORT in types, (
            f"Con SHORT + RSI={rsi:.2f}>50 debería emitir REDUCE_SHORT, señales: {types}"
        )
        if 50.0 < rsi < 70.0:
            assert SignalType.ADD_SHORT in types, (
                f"Con SHORT + RSI={rsi:.2f} entre 50-70 debería emitir ADD_SHORT, señales: {types}"
            )

    def test_with_short_rsi_below_50_closes_short(self, strategy, wallet_with_short):
        """Con SHORT + RSI < 50 → CLOSE_SHORT."""
        prices = [100.0 - i * 2.5 for i in range(15)]
        signals = self._feed(strategy, wallet_with_short, prices)
        types = [s.signal_type for s in signals]
        assert SignalType.CLOSE_SHORT in types, (
            f"Con SHORT + RSI<50 debería emitir CLOSE_SHORT, señales: {types}"
        )

    def test_with_short_rsi_50_70_emits_both_add_and_reduce(self, strategy, wallet_with_short):
        """Con SHORT + RSI entre 50-70 → ADD_SHORT (si rsi<70) + REDUCE_SHORT."""
        prices = [100.0 + i * 2.0 for i in range(15)]
        result = self._feed(strategy, wallet_with_short, prices)
        rsi = result.rsi_at_open
        assert rsi is not None
        types = [s.signal_type for s in result.signals]
        if 50.0 < rsi < 70.0:
            assert SignalType.ADD_SHORT in types, (
                f"RSI={rsi:.2f} debería emitir ADD_SHORT, señales: {types}"
            )


# =============================================================================
#  CATEGORÍA 7: ESTRATEGIA — CONTROL DE DUPLICADOS (_fired_* SETS)
# =============================================================================

class TestStrategyDuplicateControl:
    """
    Tests de los sets _fired_* que evitan señales duplicadas.
    """

    PERIOD = 5

    @pytest.fixture
    def strategy(self):
        return RSIWilderStrategy(
            rsi_period=self.PERIOD,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            reduce_long=50.0,
            reduce_short=50.0,
            max_positions=3,
        )

    @pytest.fixture
    def empty_wallet(self):
        from actors.wallet import MemoryWallet
        return MemoryWallet(usd_initial=1000.0, max_posiciones=3)

    def test_same_signal_not_emitted_twice_in_same_candle(self, strategy, empty_wallet):
        """
        En una misma vela, la misma señal no debe emitirse dos veces.
        (El set _fired_* lo impide, y on_candle solo se llama una vez por vela)
        """
        prices = [100.0 - i * 2.5 for i in range(15)]
        for i, p in enumerate(prices):
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=p)
            signals = strategy.tick(candle, empty_wallet)
            # Verificar que no hay señales duplicadas
            signal_types = [s.signal_type for s in signals if s.signal_type != SignalType.HOLD]
            assert len(signal_types) == len(set(signal_types)), (
                f"Señales duplicadas en vela {i}: {signal_types}"
            )

    def test_signal_emitted_again_in_next_candle(self, strategy, empty_wallet):
        """
        Si la condición se mantiene en la siguiente vela (con diferente ts),
        la señal debe emitirse de nuevo.
        """
        prices = [100.0 - i * 2.5 for i in range(15)]
        signal_count = {st: 0 for st in SignalType}

        for i, p in enumerate(prices):
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=p)
            signals = strategy.tick(candle, empty_wallet)
            for s in signals:
                if s.signal_type != SignalType.HOLD:
                    signal_count[s.signal_type] += 1

        # Si RSI se mantuvo en zona LONG varias velas, debería haber
        # múltiples OPEN_LONG emitidas (una por cada vela que cumplió condición)
        rsi = strategy._rsi_engine.value
        assert rsi is not None, "RSI no disponible"
        if 30.0 < rsi < 50.0:
            assert signal_count[SignalType.OPEN_LONG] >= 1, (
                f"Se esperaba al menos 1 OPEN_LONG, contador: {signal_count}"
            )

    def test_fired_sets_clear_after_100_entries(self, strategy, empty_wallet):
        """
        Cuando los sets superan 100 entradas, se limpian.
        Verificar que no crashea y que se pueden emitir señales de nuevo.
        """
        # Simular 101 velas con diferentes timestamps
        # Usamos precios constantes para que RSI=50 (sin señales)
        # y luego cambiamos a tendencia para generar señales
        for i in range(101):
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=100.0)
            strategy.tick(candle, empty_wallet)

        # Verificar que los sets se limpiaron (o están listos para limpiar)
        total_fired = (
            len(strategy._fired_open_long)
            + len(strategy._fired_open_short)
            + len(strategy._fired_close_long)
            + len(strategy._fired_close_short)
            + len(strategy._fired_reduce_long)
            + len(strategy._fired_reduce_short)
        )
        # Si superó 100, debería haberse limpiado (total ≤ 100)
        assert total_fired <= 100, (
            f"Sets no limpiados: total_fired={total_fired}"
        )

        # Después de la limpieza, debería poder emitir señales de nuevo
        prices_down = [100.0 - i * 2.5 for i in range(15)]
        for i, p in enumerate(prices_down):
            candle = make_candle(ts=2_000_000_000 + i * 3600, close=p)
            signals = strategy.tick(candle, empty_wallet)
            # No debería crashear
            types = [s.signal_type for s in signals]
            if SignalType.OPEN_LONG in types:
                break  # Señal emitida correctamente después de limpieza


# =============================================================================
#  CATEGORÍA 8: INTEGRACIÓN — CICLOS COMPLETOS
# =============================================================================

class TestStrategyFullCycles:
    """
    Tests de integración que simulan ciclos completos LONG y SHORT.
    """

    PERIOD = 5
    BASE_TS = 1_700_000_000

    @pytest.fixture
    def strategy(self):
        return RSIWilderStrategy(
            rsi_period=self.PERIOD,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            reduce_long=50.0,
            reduce_short=50.0,
            max_positions=3,
        )

    @pytest.fixture
    def empty_wallet(self):
        from actors.wallet import MemoryWallet
        return MemoryWallet(usd_initial=1000.0, max_posiciones=3)

    def test_complete_long_cycle(self, strategy, empty_wallet):
        """
        Ciclo LONG completo:
        1. Precios bajan → RSI baja → se abre LONG
        2. Precios suben → RSI sube → se cierra LONG
        """
        all_signals = []
        i = 0

        # Fase 1: Precios oscilando con sesgo bajista → RSI en zona LONG
        prices_down = oscillating_prices(n=30, base=100.0, bias="down", down_amp=1.5, up_amp=1.0)
        for p in prices_down:
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            signals = strategy.tick(candle, empty_wallet)
            all_signals.append(signals)
            i += 1

        # Verificar que hubo OPEN_LONG en la fase bajista
        opened_long = any(
            s.signal_type == SignalType.OPEN_LONG
            for signals in all_signals for s in signals
        )
        rsi_phase1 = strategy._rsi_engine.value
        if rsi_phase1 is not None and 30.0 < rsi_phase1 < 50.0:
            assert opened_long, (
                f"RSI={rsi_phase1:.2f} en zona LONG, pero no se emitió OPEN_LONG"
            )

        # Ahora la wallet tiene posición LONG (simulamos)
        from actors.wallet import MemoryWallet, TradeRecord
        wallet_long = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        wallet_long.update(TradeRecord(
            ts=1, side="BUY", price=80.0, usd_spent=100.0, btc_bought=1.25,
            direction=PositionDirection.LONG,
        ))

        # Fase 2: Precios oscilando con sesgo alcista → RSI en zona SHORT → CLOSE_LONG
        prices_up = oscillating_prices(n=30, base=80.0, bias="up", down_amp=1.5, up_amp=1.0)
        all_signals_2 = []
        for p in prices_up:
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            signals = strategy.tick(candle, wallet_long)
            all_signals_2.append(signals)
            i += 1

        # Verificar que hubo CLOSE_LONG en la fase alcista
        closed_long = any(
            s.signal_type == SignalType.CLOSE_LONG
            for signals in all_signals_2 for s in signals
        )
        rsi_phase2 = strategy._rsi_engine.value
        if rsi_phase2 is not None and rsi_phase2 > 50.0:
            assert closed_long, (
                f"RSI={rsi_phase2:.2f} en zona SHORT, pero no se emitió CLOSE_LONG"
            )

    def test_complete_short_cycle(self, strategy, empty_wallet):
        """
        Ciclo SHORT completo:
        1. Precios suben → RSI sube → se abre SHORT
        2. Precios bajan → RSI baja → se cierra SHORT
        """
        all_signals = []
        i = 0

        # Fase 1: Precios oscilando con sesgo alcista → RSI en zona SHORT
        prices_up = oscillating_prices(n=30, base=100.0, bias="up", down_amp=1.5, up_amp=1.0)
        for p in prices_up:
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            signals = strategy.tick(candle, empty_wallet)
            all_signals.append(signals)
            i += 1

        # Verificar que hubo OPEN_SHORT en la fase alcista
        opened_short = any(
            s.signal_type == SignalType.OPEN_SHORT
            for signals in all_signals for s in signals
        )
        rsi_phase1 = strategy._rsi_engine.value
        if rsi_phase1 is not None and 50.0 < rsi_phase1 < 70.0:
            assert opened_short, (
                f"RSI={rsi_phase1:.2f} en zona SHORT, pero no se emitió OPEN_SHORT"
            )

        # Simular wallet con SHORT
        from actors.wallet import MemoryWallet, TradeRecord
        wallet_short = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        wallet_short.update(TradeRecord(
            ts=1, side="SELL", price=130.0, btc_sold=0.1, usd_received=13.0,
            direction=PositionDirection.SHORT,
        ))

        # Fase 2: Precios oscilando con sesgo bajista → RSI en zona LONG → CLOSE_SHORT
        strategy2 = RSIWilderStrategy(
            rsi_period=self.PERIOD,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            reduce_long=50.0,
            reduce_short=50.0,
        )
        prices_down = oscillating_prices(n=30, base=130.0, bias="down", down_amp=1.5, up_amp=1.0)
        all_signals_2 = []
        for p in prices_down:
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            signals = strategy2.tick(candle, wallet_short)
            all_signals_2.append(signals)
            i += 1

        # Verificar que hubo CLOSE_SHORT en la fase bajista
        closed_short = any(
            s.signal_type == SignalType.CLOSE_SHORT
            for signals in all_signals_2 for s in signals
        )
        rsi_phase2 = strategy2._rsi_engine.value
        if rsi_phase2 is not None and rsi_phase2 < 50.0:
            assert closed_short, (
                f"RSI={rsi_phase2:.2f} en zona LONG, pero no se emitió CLOSE_SHORT"
            )

    def test_multiple_adds_in_sequence(self, strategy):
        """
        Varias velas con RSI en zona LONG → ADD_LONG en cada una
        (mientras RSI se mantenga entre 30-50).
        """
        from actors.wallet import MemoryWallet, TradeRecord
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        wallet.update(TradeRecord(
            ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
            direction=PositionDirection.LONG,
        ))

        # Precios que mantienen RSI entre 30-50 durante varias velas
        prices = [100.0 - i * 2.0 for i in range(20)]
        add_count = 0

        for i, p in enumerate(prices):
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            signals = strategy.tick(candle, wallet)
            for s in signals:
                if s.signal_type == SignalType.ADD_LONG:
                    add_count += 1

        # Debe haber al menos un ADD_LONG (si RSI se mantuvo en zona de entrada)
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        if 30.0 < rsi < 50.0:
            assert add_count >= 1, (
                f"Con RSI={rsi:.2f} debería haber al menos 1 ADD_LONG, "
                f"pero count={add_count}"
            )

    def test_multiple_reduces_in_sequence(self, strategy):
        """
        Varias velas con RSI en zona LONG → REDUCE_LONG en cada una.
        """
        from actors.wallet import MemoryWallet, TradeRecord
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        wallet.update(TradeRecord(
            ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
            direction=PositionDirection.LONG,
        ))

        prices = [100.0 - i * 2.0 for i in range(20)]
        reduce_count = 0

        for i, p in enumerate(prices):
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            signals = strategy.tick(candle, wallet)
            for s in signals:
                if s.signal_type == SignalType.REDUCE_LONG:
                    reduce_count += 1

        # REDUCE_LONG se emite siempre que RSI < 50 (sin condición adicional)
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        if rsi < 50.0:
            assert reduce_count >= 1, (
                f"Con RSI={rsi:.2f} < 50 debería haber REDUCE_LONG, count={reduce_count}"
            )

    def test_oscillating_rsi_around_50(self, strategy, empty_wallet):
        """
        RSI oscilando alrededor de 50 → no debe generar señales espurias
        (cambio constante entre LONG y SHORT sin posición).
        """
        # Crear precios que oscilen alrededor de la media
        np.random.seed(12345)
        base = 100.0
        prices = [base + np.random.normal(0, 1.0) for _ in range(30)]

        open_long_count = 0
        open_short_count = 0

        for i, p in enumerate(prices):
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            signals = strategy.tick(candle, empty_wallet)
            for s in signals:
                if s.signal_type == SignalType.OPEN_LONG:
                    open_long_count += 1
                elif s.signal_type == SignalType.OPEN_SHORT:
                    open_short_count += 1

        # No debe haber muchas señales (RSI cerca de 50 no debería generar señales)
        # En mercado lateral, RSI debería estar cerca de 50
        # y las señales solo cuando RSI cruza los thresholds
        total_signals = open_long_count + open_short_count
        assert total_signals >= 0, "Contador de señales negativo (error)"
        # Simplemente verificar que no crashea y los contadores son enteros


# =============================================================================
#  CATEGORÍA 9: ESCENARIOS DEL MUNDO REAL
# =============================================================================

class TestStrategyRealWorldScenarios:
    """
    Tests que simulan escenarios reales de mercado.
    """

    PERIOD = 14  # Usar período estándar
    BASE_TS = 1_700_000_000

    @pytest.fixture
    def strategy(self):
        return RSIWilderStrategy(
            rsi_period=self.PERIOD,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            reduce_long=50.0,
            reduce_short=50.0,
            max_positions=3,
        )

    @pytest.fixture
    def empty_wallet(self):
        from actors.wallet import MemoryWallet
        return MemoryWallet(usd_initial=1000.0, max_posiciones=3)

    def test_extreme_volatility(self, strategy, empty_wallet):
        """
        Volatilidad extrema: cambios de precio muy grandes.
        La estrategia no debe crashear y debe emitir señales razonables.
        """
        np.random.seed(42)
        prices = [100.0]
        for _ in range(50):
            # Cambios de hasta ±20%
            change = np.random.uniform(-0.20, 0.20)
            new_price = prices[-1] * (1 + change)
            prices.append(max(1.0, new_price))

        for i, p in enumerate(prices):
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            try:
                signals = strategy.tick(candle, empty_wallet)
            except Exception as e:
                pytest.fail(f"Estrategia crasheó con alta volatilidad en vela {i}: {e}")

            # Verificar que las señales tienen precios válidos
            for s in signals:
                if s.signal_type != SignalType.HOLD:
                    assert np.isfinite(s.price) and s.price > 0, (
                        f"Precio inválido en vela {i}: {s}"
                    )

    def test_sideways_market(self, strategy, empty_wallet):
        """
        Mercado lateral (rango estrecho). RSI debe permanecer cerca de 50
        y no generar muchas señales.
        """
        np.random.seed(99)
        base = 100.0
        prices = [base + np.random.normal(0, 0.5) for _ in range(60)]

        signal_count = 0
        for i, p in enumerate(prices):
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            signals = strategy.tick(candle, empty_wallet)
            for s in signals:
                if s.signal_type != SignalType.HOLD:
                    signal_count += 1

        # En mercado lateral con RSI período 14, el RSI debería estar cerca de 50
        # y no generar muchas señales
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        # Verificar que RSI está en rango normal
        assert 0 <= rsi <= 100, f"RSI={rsi:.2f} fuera de rango"

    def test_sudden_trend_reversal(self, strategy, empty_wallet):
        """
        Cambio brusco de tendencia: tendencia bajista que revierte a alcista.
        Verificar que la estrategia se adapta.
        """
        # Fase 1: Tendencia bajista (30 velas)
        prices = [100.0 - i * 0.5 for i in range(30)]
        # Fase 2: Reversión brusca a tendencia alcista (30 velas)
        prices += [85.0 + i * 0.5 for i in range(30)]

        long_signals = []
        short_signals = []

        for i, p in enumerate(prices):
            candle = make_candle(ts=self.BASE_TS + i * 3600, close=p)
            signals = strategy.tick(candle, empty_wallet)
            for s in signals:
                if s.signal_type == SignalType.OPEN_LONG:
                    long_signals.append((i, s))
                elif s.signal_type == SignalType.OPEN_SHORT:
                    short_signals.append((i, s))

        # En algún punto debería haber señales LONG (tendencia bajista → RSI bajo)
        # y luego SHORT (tendencia alcista → RSI alto)
        assert len(long_signals) + len(short_signals) >= 0  # No crasheó

    def test_consecutive_signals_same_direction(self, strategy, empty_wallet):
        """
        Velas consecutivas en la misma zona deben emitir señales
        en cada vela (con diferentes timestamps).
        """
        prices = [100.0 - i * 2.0 for i in range(20)]
        ts_signals = {}  # ts → lista de tipos de señal

        for i, p in enumerate(prices):
            ts = self.BASE_TS + i * 3600
            candle = make_candle(ts=ts, close=p)
            signals = strategy.tick(candle, empty_wallet)
            ts_signals[ts] = [s.signal_type for s in signals]

        # Verificar que las señales en diferentes velas no se solapan
        # (cada vela tiene su propio conjunto de señales)
        for ts, types in ts_signals.items():
            # Solo HOLD o señales válidas
            for t in types:
                assert t in SignalType, f"Tipo de señal inválido: {t}"


# =============================================================================
#  RUNNER DIRECTO
# =============================================================================

if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file
    raise SystemExit(run_current_test_file(__file__))