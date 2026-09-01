-- =============================================================================
-- Endurecer los permisos de PostgREST (anon / authenticated)
-- Escrito el 01-sep-2026, a raíz del hallazgo de RLS en `query_logs`.
-- Correr en: Supabase → SQL Editor.
-- =============================================================================
--
-- QUÉ PROBLEMA RESUELVE
-- ---------------------
-- Supabase otorga por defecto a `anon` y `authenticated` grants COMPLETOS sobre las tablas del
-- esquema `public`: SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER. Hoy lo único
-- que impide que esos permisos se usen es RLS: las tablas tienen políticas que sólo permiten
-- SELECT, así que las escrituras quedan bloqueadas por *ausencia de política*.
--
-- Eso funciona, pero es una sola línea de defensa. Si alguien agrega en el futuro una política
-- permisiva de más (un `FOR ALL` en vez de un `FOR SELECT`, que es un error fácil de cometer en
-- el panel de Supabase), los grants de escritura ya están ahí esperando y pasan a ser usables
-- con la anon key — que es pública por diseño, va embebida en el frontend.
--
-- Este script saca esa segunda vía: revoca los permisos de escritura y deja sólo lectura donde
-- corresponde.
--
-- POR QUÉ NO SE REVOCA TAMBIÉN EL SELECT
-- --------------------------------------
-- Porque el dashboard (`que-morfamos-dashboard`) lee por PostgREST con la anon key desde el
-- cliente. En Postgres, RLS *restringe* lo que un rol ve, pero el rol necesita igual el GRANT de
-- tabla: revocar SELECT dejaría las políticas de "Lectura pública" sin efecto y el dashboard
-- recibiría permiso denegado. Se verificó qué tablas consulta: lugares, scraping_logs, reviews y
-- review_history, y ninguna otra.
--
-- POR QUÉ NO ROMPE NADA MÁS
-- -------------------------
-- Ni el backend ni el scraper pasan por PostgREST: los dos se conectan con `DATABASE_URL`, como
-- rol `postgres`, que es dueño de las tablas y además tiene BYPASSRLS.
-- =============================================================================

-- 1. Tablas que el dashboard SÍ lee. Se les saca la escritura y se conserva la lectura.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON public.lugares, public.reviews, public.review_history,
       public.scraping_logs, public.validation_reports
    FROM anon, authenticated;

-- 2. Tablas que NADIE lee por la API pública. Sin acceso, directamente.
--    - query_logs: consultas reales de usuarios; ya tiene RLS sin políticas desde hoy.
--    - langchain_pg_*: los embeddings; sólo los usa el backend por conexión directa.
REVOKE ALL
    ON public.query_logs, public.langchain_pg_collection, public.langchain_pg_embedding
    FROM anon, authenticated;

-- =============================================================================
-- VERIFICACIÓN — correr después y comparar con lo esperado
-- =============================================================================

-- (a) Qué quedó. Esperado: SELECT en las 5 de lectura, nada en las otras 3.
SELECT table_name,
       string_agg(DISTINCT privilege_type, ', ' ORDER BY privilege_type) AS permisos
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')
GROUP BY table_name
ORDER BY table_name;

-- (b) El dashboard sigue leyendo. Esperado: devuelve filas.
SET LOCAL ROLE anon;
SELECT count(*) AS lugares_visibles_por_anon FROM public.lugares;
RESET ROLE;

-- (c) La escritura por la API está cerrada. Esperado: ERROR "permission denied".
--     Va aparte y comentado a propósito: aborta la transacción si se corre en bloque.
-- SET LOCAL ROLE anon;
-- INSERT INTO public.lugares (nombre) VALUES ('prueba-que-debe-fallar');
-- RESET ROLE;

-- =============================================================================
-- CÓMO REVERTIR, si algo se rompiera
-- =============================================================================
-- GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
--     ON ALL TABLES IN SCHEMA public TO anon, authenticated;
