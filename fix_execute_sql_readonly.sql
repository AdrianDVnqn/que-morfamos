-- =============================================================================
-- Contener `execute_sql`: que corra como el visitante, no como `postgres`.
-- Escrito el 04-sep-2026, a raíz de la auditoría del dashboard antes de hacerlo público.
-- Correr en: Supabase → SQL Editor.
-- =============================================================================
--
-- QUÉ PROBLEMA RESUELVE
-- ---------------------
-- El "Explorador SQL" del dashboard llama a esta función con la anon key, que es PÚBLICA (va
-- embebida en el frontend). La versión anterior tenía dos defectos que juntos la volvían una
-- fuga de datos:
--
--   1. `SECURITY DEFINER` → la función corría como su dueño, `postgres`. Todo el RLS que
--      protege el resto de la base NO aplicaba por esta vía, porque no consultaba como `anon`
--      sino como el dueño de la base.
--   2. Se defendía sólo con un `LIKE 'SELECT%'` y un regex de keywords. Eso no impide LEER
--      datos sensibles: `SELECT email FROM auth.users` pasa ambos filtros limpio. Idem
--      `vault.secrets` o cualquier tabla de cualquier esquema.
--
-- Resultado: cualquier visitante anónimo podía leer los emails/identidades de `auth.users` y
-- los secretos del vault desde las devtools del navegador.
--
-- POR QUÉ ESTE ARREGLO ALCANZA (Y NO ES UN JUEGO DE FILTRAR SQL)
-- -------------------------------------------------------------
-- El fix de fondo es sacar `SECURITY DEFINER`: la función pasa a `SECURITY INVOKER` y corre
-- como el rol que la invoca (`anon`). A partir de ahí la seguridad la da el motor, no un regex:
-- `anon` NO tiene GRANT de SELECT sobre `auth.users`, `vault.secrets` ni `query_logs`
-- (verificado), así que cualquier intento de leerlas falla con "permission denied" aunque la
-- consulta sea un SELECT perfectamente formado. La función queda limitada exactamente a lo que
-- el dashboard ya puede leer por PostgREST: las 5 tablas públicas de datos de Google.
--
-- Se conserva el chequeo de "sólo SELECT/WITH", pero ya no como muro de seguridad —eso lo hace
-- RLS+GRANTs— sino para dar un error claro y evitar ejecutar basura.
--
-- QUÉ MÁS SE ENDURECE
-- -------------------
--   - `SET statement_timeout = '5s'`: ninguna consulta puede colgar la base (cross joins,
--     pg_sleep). Se revierte solo al terminar la función.
--   - `SET search_path = public`: los nombres sin esquema resuelven a `public` y nada más.
--   - Techo de 1000 filas: se envuelve la consulta antes de agregarla a JSON, para que un
--     `SELECT * FROM reviews` (205k filas) no reviente la memoria ni la red.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.execute_sql(sql_query text)
    RETURNS json
    LANGUAGE plpgsql
    SECURITY INVOKER                    -- corre como el visitante (anon); RLS + GRANTs aplican
    SET statement_timeout = '5s'
    SET search_path = public
AS $function$
DECLARE
    result JSON;
    q TEXT := trim(sql_query);
BEGIN
    -- Sólo lectura: la primera palabra tiene que ser SELECT o WITH (para CTEs de lectura).
    -- No es la capa de seguridad —esa es RLS—, es para devolver un error entendible.
    IF lower(q) !~ '^(select|with)\s' THEN
        RETURN json_build_object(
            'error', 'Sólo se permiten consultas de lectura (SELECT).',
            'detail', 'READ_ONLY'
        );
    END IF;

    -- Se ejecuta la consulta envuelta: se corta a 1000 filas ANTES de agregar a JSON.
    EXECUTE format(
        'SELECT json_agg(row_to_json(t)) FROM (SELECT * FROM (%s) sub LIMIT 1000) t',
        q
    ) INTO result;

    RETURN COALESCE(result, '[]'::json);
EXCEPTION WHEN OTHERS THEN
    -- Un permiso denegado por RLS/GRANT cae acá y vuelve como error legible, no como 500.
    RETURN json_build_object('error', SQLERRM, 'detail', SQLSTATE);
END;
$function$;

-- La función sigue siendo invocable por el dashboard (anon) y por vos (authenticated).
-- No hace falta re-otorgar nada: CREATE OR REPLACE conserva los GRANTs existentes.
