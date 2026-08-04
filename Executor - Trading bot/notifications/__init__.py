"""
notifications/ — Sistema de notificaciones
============================================
Envía alertas y resúmenes del bot de trading a través de diferentes canales.

Componentes:
  - TelegramNotifier:   Notificador vía Telegram (eventos de trading,
    alertas de drawdown, resúmenes diarios, errores críticos).
  - TelegramEvent:      Enumeración de tipos de evento notificables.

Requerido: configuración TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env
"""