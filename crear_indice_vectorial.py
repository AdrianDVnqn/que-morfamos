"""
Fija la dimension de la columna de embeddings y crea el indice HNSW.

Por que: LangChain crea `langchain_pg_embedding.embedding` como `vector` SIN dimension fija.
Con ese tipo, Postgres no puede optimizar el calculo de distancia y ademas pgvector rechaza crear
un indice HNSW ("column does not have dimensions"). El resultado era un Seq Scan que tardaba
568ms de CPU en cada busqueda semantica — en CADA consulta del chat, porque el cache de Redis
tampoco estaba funcionando.

Medido antes/despues sobre 909 embeddings de la coleccion de produccion:
    antes:   Execution Time: 568 ms
    despues: Execution Time:  23 ms   (24x)

La mejora viene sobre todo del ALTER: con dimension fija el calculo es mucho mas rapido. El
indice HNSW queda creado igual, pero el planificador NO lo usa mientras la consulta filtre por
collection_id (pgvector no combina bien HNSW con filtros). Si algun dia la tabla crece mucho,
conviene un indice PARCIAL por coleccion:
    CREATE INDEX ... USING hnsw (embedding vector_cosine_ops) WHERE collection_id = '<uuid>';

Es idempotente: se puede correr las veces que haga falta.

Uso:
    python crear_indice_vectorial.py
"""
import os
import time

if os.path.exists("mis_claves.env"):
    from dotenv import load_dotenv
    load_dotenv("mis_claves.env")

from sqlalchemy import create_engine, text

DIMS = 1536  # text-embedding-3-small


def main():
    # AUTOCOMMIT: CREATE INDEX CONCURRENTLY no puede correr dentro de una transaccion.
    engine = create_engine(os.environ["DATABASE_URL"], isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        dims = conn.execute(text("""
            SELECT DISTINCT vector_dims(embedding) FROM langchain_pg_embedding
        """)).fetchall()
        if len(dims) != 1 or dims[0][0] != DIMS:
            print(f"❌ Abortado: se esperaban todas las filas en {DIMS} dims, se encontro {dims}")
            return

        tipo = conn.execute(text("""
            SELECT format_type(atttypid, atttypmod) FROM pg_attribute
            WHERE attrelid = 'langchain_pg_embedding'::regclass AND attname = 'embedding'
        """)).scalar()

        if tipo == "vector":
            print(f"1) Fijando la dimension de la columna a vector({DIMS})...")
            t0 = time.time()
            conn.execute(text(
                f"ALTER TABLE langchain_pg_embedding ALTER COLUMN embedding TYPE vector({DIMS})"
            ))
            print(f"   ok en {time.time() - t0:.1f}s")
        else:
            print(f"1) La columna ya es {tipo}, no hace falta el ALTER")

        print("2) Creando el indice HNSW (cosine) sin bloquear la tabla...")
        t0 = time.time()
        conn.execute(text("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_lpe_embedding_hnsw
            ON langchain_pg_embedding USING hnsw (embedding vector_cosine_ops)
        """))
        print(f"   ok en {time.time() - t0:.1f}s")

        print("\n✅ Listo. Indices actuales:")
        for r in conn.execute(text("""
            SELECT indexname FROM pg_indexes WHERE tablename = 'langchain_pg_embedding'
        """)).fetchall():
            print(f"   - {r[0]}")


if __name__ == "__main__":
    main()
