import os
import sqlite3
import time
from datetime import datetime, timezone
import requests


class BinanceDataDownloader:
    def __init__(self, db_name=None):
        if db_name is None:
            db_dir = os.path.dirname(os.path.abspath(__file__))
            self.db_name = os.path.join(db_dir, "btc_5m.db")
        else:
            self.db_name = db_name

        self.base_url = "https://api.binance.com/api/v3/klines"
        self.symbol = "BTCUSDT"
        self.interval = "5m"
        self.limit = 1000  # Máximo por request

        # Rate limiting optimizado
        self.max_requests_per_minute = 1100
        self.seconds_between_requests = 60.0 / self.max_requests_per_minute  # ~0.055 segundos
        self.last_request_time = 0

    def create_database(self):
        """Crear la base de datos SQLite con la tabla necesaria"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS btc_5m (
                timestamp INTEGER PRIMARY KEY,
                datetime TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                close_time INTEGER,
                quote_volume REAL,
                trades_count INTEGER,
                taker_buy_base_volume REAL,
                taker_buy_quote_volume REAL
            )
        ''')

        # Crear índices para consultas rápidas
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_datetime_5m ON btc_5m(datetime)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp_5m ON btc_5m(timestamp)')

        conn.commit()
        conn.close()
        print(f"Base de datos {self.db_name} creada exitosamente")

    def rate_limit_wait(self):
        """Esperar el tiempo necesario para mantener la tasa de requests"""
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time

        if time_since_last_request < self.seconds_between_requests:
            sleep_time = self.seconds_between_requests - time_since_last_request
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def get_klines(self, start_time=None, end_time=None):
        """Obtener datos de klines desde Binance con rate limiting"""
        params = {
            'symbol': self.symbol,
            'interval': self.interval,
            'limit': self.limit
        }

        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time

        self.rate_limit_wait()

        try:
            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error en la consulta: {e}")
            time.sleep(1)
            return None

    def timestamp_to_datetime(self, timestamp):
        """Convertir timestamp a formato datetime legible"""
        return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    def save_to_database(self, klines_data):
        """Guardar datos en SQLite"""
        if not klines_data:
            return 0

        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        inserted_count = 0
        for kline in klines_data:
            try:
                timestamp = int(kline[0])
                datetime_str = self.timestamp_to_datetime(timestamp)

                cursor.execute('''
                    INSERT OR IGNORE INTO btc_5m 
                    (timestamp, datetime, open, high, low, close, volume, close_time,
                     quote_volume, trades_count, taker_buy_base_volume, taker_buy_quote_volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    timestamp,
                    datetime_str,
                    float(kline[1]),  # open
                    float(kline[2]),  # high
                    float(kline[3]),  # low
                    float(kline[4]),  # close
                    float(kline[5]),  # volume
                    int(kline[6]),    # close_time
                    float(kline[7]),  # quote_volume
                    int(kline[8]),    # trades_count
                    float(kline[9]),  # taker_buy_base_volume
                    float(kline[10])  # taker_buy_quote_volume
                ))
                inserted_count += 1
            except Exception as e:
                print(f"Error insertando registro: {e}")
                continue

        conn.commit()
        conn.close()
        return inserted_count

    def calculate_estimated_time(self, start_timestamp, end_timestamp):
        """Calcular tiempo estimado de descarga"""
        total_minutes = (end_timestamp - start_timestamp) / (1000 * 60)
        # Cada request trae 1000 velas de 5 min = ~83.3 horas de datos
        total_requests = int(total_minutes / (self.limit * 5)) + 1
        estimated_seconds = total_requests * self.seconds_between_requests
        return total_requests, estimated_seconds

    def download_historical_data(self):
        """Descargar todos los datos históricos desde 2017"""
        print("Iniciando descarga optimizada de datos históricos de BTC/USDT (velas 5m) desde 2017...")

        start_timestamp = int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        end_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

        total_requests, estimated_seconds = self.calculate_estimated_time(start_timestamp, end_timestamp)
        estimated_minutes = estimated_seconds / 60

        print(f"Requests estimados: {total_requests:,}")
        print(f"Tiempo estimado: {estimated_minutes:.1f} minutos")
        print(f"Tasa de consulta: {1/self.seconds_between_requests:.1f} requests/minuto")
        print("-" * 50)

        current_timestamp = start_timestamp
        total_inserted = 0
        batch_count = 0
        start_time = time.time()

        while current_timestamp < end_timestamp:
            batch_count += 1

            if batch_count % 10 == 0:
                elapsed_time = time.time() - start_time
                progress = ((current_timestamp - start_timestamp) / (end_timestamp - start_timestamp)) * 100
                current_date = self.timestamp_to_datetime(current_timestamp)

                print(f"Lote {batch_count} | Progreso: {progress:.1f}% | Fecha: {current_date} | "
                      f"Tiempo transcurrido: {elapsed_time/60:.1f}min | Registros: {total_inserted:,}")

            klines = self.get_klines(start_time=current_timestamp)

            if not klines:
                print("Error obteniendo datos. Reintentando...")
                continue

            if len(klines) == 0:
                print("No hay más datos disponibles.")
                break

            inserted = self.save_to_database(klines)
            total_inserted += inserted

            last_timestamp = int(klines[-1][0])
            current_timestamp = last_timestamp + (5 * 60 * 1000)  # +5 minutos en ms

        total_time = time.time() - start_time
        print(f"\n¡Descarga completada en {total_time/60:.1f} minutos!")
        print(f"Total de registros insertados: {total_inserted:,}")
        print(f"Promedio real: {batch_count/(total_time/60):.1f} requests/minuto")
        self.show_database_info()

    def show_database_info(self):
        """Mostrar información de la base de datos"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM btc_5m")
        total_records = cursor.fetchone()[0]

        cursor.execute("SELECT datetime FROM btc_5m ORDER BY timestamp LIMIT 1")
        first_record = cursor.fetchone()

        cursor.execute("SELECT datetime FROM btc_5m ORDER BY timestamp DESC LIMIT 1")
        last_record = cursor.fetchone()

        if os.path.exists(self.db_name):
            file_size = os.path.getsize(self.db_name) / (1024 * 1024)
        else:
            file_size = 0

        print(f"\n=== INFORMACIÓN DE LA BASE DE DATOS ===")
        print(f"Archivo: {self.db_name}")
        print(f"Tamaño: {file_size:.1f} MB")
        print(f"Total de registros: {total_records:,}")
        if first_record and last_record:
            print(f"Primer registro: {first_record[0]}")
            print(f"Último registro: {last_record[0]}")

        conn.close()


def main():
    downloader = BinanceDataDownloader()
    downloader.create_database()
    downloader.download_historical_data()


if __name__ == "__main__":
    main()
