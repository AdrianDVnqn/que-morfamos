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

# Supabase expone TODO el esquema `public` por PostgREST, y `anon`/`authenticated` vienen con
# grants completos (SELECT, INSERT, UPDATE, DELETE, TRUNCATE) sobre las tablas. Lo unico que
# neutraliza esos permisos es RLS: sin politicas, RLS activo deja a esos roles sin ver ni tocar
# nada. Las otras 7 tablas del proyecto ya lo tenian; esta se creo sin el paso y quedo abierta —
# el linter de Supabase la marco el 01-sep-2026 con 1578 consultas reales de usuarios adentro,
# accesibles y BORRABLES con la anon key, que es publica por diseño.
# No rompe el backend: escribe por conexion directa como `postgres`, que es dueño de la tabla y
# ademas tiene BYPASSRLS.
ENABLE_RLS_SQL = "ALTER TABLE query_logs ENABLE ROW LEVEL SECURITY"

COMMENT_SQL = """
COMMENT ON TABLE query_logs IS
'Consultas reales de usuarios. RLS activo SIN politicas a proposito: la escribe solo el backend
por conexion directa (rol postgres, dueño y BYPASSRLS), y no debe ser accesible por PostgREST
con la anon key.'
"""


def main():
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL no configurada en mis_claves.env")
        return

    print("🚀 Creando tabla query_logs (si no existe)...")
    engine = create_engine(get_sqlalchemy_url(DATABASE_URL))

    with engine.connect() as conn:
        conn.execute(text(CREATE_TABLE_SQL))
        conn.execute(text(CREATE_INDEX_SQL))
        # Idempotente: volver a activarlo sobre una tabla que ya lo tiene no falla ni cambia nada.
        conn.execute(text(ENABLE_RLS_SQL))
        conn.execute(text(COMMENT_SQL))
        conn.commit()

    print("✅ Tabla query_logs lista (con RLS activo).")


if __name__ == "__main__":
    main()
