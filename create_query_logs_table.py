"""
Crea la tabla query_logs en PostgreSQL/Supabase para persistir queries reales de usuarios.
Idempotente (CREATE TABLE IF NOT EXISTS): se puede correr más de una vez sin romper nada.

Ejecutar manualmente cuando se decida activar el logging real:
    python create_query_logs_table.py
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv("mis_claves.env")

DATABASE_URL = os.getenv("DATABASE_URL")


def get_sqlalchemy_url(url):
    """Convierte postgres:// a postgresql+psycopg:// para SQLAlchemy (mismo helper que migrate_data.py)."""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url and url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS query_logs (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    query TEXT NOT NULL,
    mode TEXT,
    intencion TEXT,
    zona_detectada TEXT,
    restaurants_returned TEXT[],
    response_time_seconds REAL,
    tone TEXT,
    ai_provider TEXT,
    used_cache BOOLEAN DEFAULT FALSE
)
"""

CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_query_logs_ts ON query_logs (ts)"


def main():
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL no configurada en mis_claves.env")
        return

    print("🚀 Creando tabla query_logs (si no existe)...")
    engine = create_engine(get_sqlalchemy_url(DATABASE_URL))

    with engine.connect() as conn:
        conn.execute(text(CREATE_TABLE_SQL))
        conn.execute(text(CREATE_INDEX_SQL))
        conn.commit()

    print("✅ Tabla query_logs lista.")


if __name__ == "__main__":
    main()
