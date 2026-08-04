# Trading Bot Executor

Executor live para estrategias Bollinger Bands duales importadas desde el repositorio de backtesting.

## Entornos soportados

- `papper`: ejecucion simulada con wallet local y datos publicos de mercado.
- `hyperliquid_mainnet`: ejecucion real en Hyperliquid mainnet.
- `hyperliquid_testnet`: ejecucion en Hyperliquid testnet.

Los entornos `binance_spot` y `binance_testnet` fueron retirados del executor. Binance puede seguir apareciendo como proveedor publico de market data para `papper`, pero ya no existe como entorno de ejecucion, wallet ni order book.

## Uso basico

```powershell
python Executor_Dual_Bands.py --environment papper --symbol BTCUSDT
python Executor_Dual_Bands.py --environment hyperliquid_testnet --symbol BTCUSDT
python Executor_Dual_Bands.py --environment hyperliquid_mainnet --symbol BTCUSDT
```

`main.py` queda como launcher secundario/legacy mientras avanza la reestructuracion; el flujo principal auditado es `Executor_Dual_Bands.py`.

## Estrategias

Las estrategias bajo `strategies/` deben mantenerse correlacionadas con el repositorio de backtesting. No se modifican localmente sin registrar la decision en `PLAN_REESTRUCTURACION_EXECUTOR.md`.

El objetivo del executor es calcular ordenes limite al inicio de cada vela usando solo informacion disponible en ese momento.

## Configuracion

Ver `.env.example` para las variables actuales. Las claves Binance y puertos Binance fueron eliminados.

Puertos de dashboard por defecto:

| Entorno | Puerto |
| --- | --- |
| `papper` | `8001` |
| `hyperliquid_mainnet` | `8004` |
| `hyperliquid_testnet` | `8005` |

## Persistencia

La reestructuracion apunta a usar `live_results_{environment}.json` como fuente de verdad para reiniciar o resetear una ejecucion. Borrar ese archivo debe resetear el estado operativo del entorno.

El detalle de decisiones y tareas pendientes esta en `PLAN_REESTRUCTURACION_EXECUTOR.md`.
