-- =============================================================================
-- Paneles analíticos del dashboard
-- Escritas el 07-sep-2026. Correr en: Supabase → SQL Editor.
-- =============================================================================
--
-- QUÉ SON
-- -------
-- Vistas que alimentan los paneles del dashboard, cada uno con su consulta SQL a la vista para
-- que se pueda leer cómo se calculó. El front NO copia ese SQL: lo lee de `dashboard_definiciones`,
-- que lo saca de la base con pg_get_viewdef(). Así el SQL que se muestra es literalmente el que
-- corrió, y no puede quedar desactualizado respecto de la vista.
--
-- Todas con `security_invoker = true`, igual que el resto: corren con los permisos del visitante.
-- =============================================================================


-- EL QM SCORE ---------------------------------------------------------------------------------
-- Es la valoración propia del motor de recomendación, y vale aclarar qué es y qué no es.
--
--     QM = rating_gral + log10(total_reviews_google + 1) * 2.7
--
-- Premia el rating PERO también el volumen de reseñas, porque el volumen no es sólo popularidad:
-- es señal de que el lugar es relevante y está establecido. Un 5,0 con 20 reseñas es más frágil
-- que un 4,2 con 4.500. Se probó reemplazarlo por un promedio bayesiano y el benchmark lo rechazó
-- (29/33 → 23/33); está documentado en `calc_score` dentro de main.py.
--
-- IMPORTANTE: el QM Score NO es el orden de una búsqueda. El ranking real es una cascada
-- (conceptos cubiertos, evidencia, QM Score) y el QM es el ÚLTIMO desempate: en "pizza sin tacc"
-- ordena cuántos conceptos cubre cada lugar y el QM casi no interviene. El QM manda sólo en
-- búsquedas genéricas tipo "mejores pizzas", y ahí compite con la categoría del local.
--
-- LA NORMALIZACIÓN A 0-5
-- ----------------------
-- El score crudo va de 2,3 a 15,4, que no se lee como nada. Se normaliza dividiendo por un techo
-- TEÓRICO fijo de 16,6 (un 5,0 perfecto con ~20.000 reseñas) y llevándolo a 0-5, para que se pueda
-- poner al lado de las estrellas de Google.
--
-- El techo es fijo a propósito y no el máximo observado: si se usara el máximo real, el QM de un
-- lugar cambiaría porque OTRO lugar creció, que es justo lo que no se quiere en un número que la
-- gente compara. Con 16,6, hoy el mejor local llega a 4,6 y queda techo libre.
--
-- Se evaluó normalizar por percentil y se descartó con datos: dejaba a Cabildo Pizzería (4,2 con
-- 4.545 reseñas, una referencia de pizza en Neuquén) en QM 2,0 y a Amore Pizza Napoletana en 1,0.
-- Un percentil no se lee como una calificación.


-- 1. Top 5 por QM Score --------------------------------------------------------------------
CREATE OR REPLACE VIEW public.dashboard_panel_top_qm
WITH (security_invoker = true) AS
SELECT nombre,
       categoria,
       rating_gral                                             AS rating_google,
       total_reviews_google                                    AS reviews,
       round((rating_gral + log(total_reviews_google + 1) * 2.7)::numeric, 2)          AS qm_bruto,
       round(((rating_gral + log(total_reviews_google + 1) * 2.7) / 16.6 * 5)::numeric, 1) AS qm_score
FROM lugares
WHERE rating_gral IS NOT NULL
ORDER BY rating_gral + log(total_reviews_google + 1) * 2.7 DESC
LIMIT 5;


-- 2. Top 5 por rating de Google ------------------------------------------------------------
-- Deliberadamente SIN mínimo de reseñas: el panel existe para mostrar el contraste con el de
-- arriba. Ordenar por rating puro sube locales de ~150 reseñas con 5,0, y ese es exactamente el
-- problema que el QM Score corrige.
CREATE OR REPLACE VIEW public.dashboard_panel_top_google
WITH (security_invoker = true) AS
SELECT nombre,
       categoria,
       rating_gral                                             AS rating_google,
       total_reviews_google                                    AS reviews,
       round(((rating_gral + log(total_reviews_google + 1) * 2.7) / 16.6 * 5)::numeric, 1) AS qm_score
FROM lugares
WHERE rating_gral IS NOT NULL
ORDER BY rating_gral DESC, total_reviews_google DESC
LIMIT 5;


-- 3. Top 5 con más movimiento ---------------------------------------------------------------
-- Reseñas SUMADAS en los últimos 30 días, según el histórico que arma el monitor.
CREATE OR REPLACE VIEW public.dashboard_panel_mas_activos
WITH (security_invoker = true) AS
SELECT h.nombre,
       sum(h.delta_since_last)::bigint AS reviews_nuevas,
       -- El rating sale de `lugares` y no del histórico: `review_history.rating` viene NULL en
       -- la mayoría de los registros del monitor.
       max(l.rating_gral)              AS rating_google,
       max(h.recorded_at)              AS ultimo_movimiento
FROM review_history h
LEFT JOIN lugares l ON l.id = h.lugar_id
WHERE h.recorded_at >= now() - interval '30 days'
  AND h.delta_since_last > 0
  AND h.nombre IS NOT NULL
GROUP BY h.nombre
ORDER BY sum(h.delta_since_last) DESC
LIMIT 5;


-- 4. Locales que dejaron de responder -------------------------------------------------------
-- `URL_MUERTA` es lo que registra el scraper cuando la ficha de Google Maps ya no carga: suele
-- significar que el local cerró o que Google la dio de baja. Es una señal dura, distinta de
-- "hace mucho que no suma reseñas", que también puede ser un local tranquilo.
CREATE OR REPLACE VIEW public.dashboard_panel_caidos
WITH (security_invoker = true) AS
SELECT l.nombre,
       l.categoria,
       l.zona,
       count(*)          AS veces_sin_responder,
       max(s.fecha)      AS ultima_deteccion
FROM scraping_logs s
JOIN lugares l ON l.id = s.lugar_id
WHERE s.estado = 'URL_MUERTA'
GROUP BY l.nombre, l.categoria, l.zona
ORDER BY max(s.fecha) DESC, count(*) DESC
LIMIT 10;


-- 5. Cobertura del pipeline -----------------------------------------------------------------
-- Qué proporción del catálogo tiene cada pieza que el RAG necesita para funcionar.
CREATE OR REPLACE VIEW public.dashboard_panel_cobertura
WITH (security_invoker = true) AS
SELECT p.orden, p.etiqueta, p.completos, (SELECT count(*) FROM lugares) AS total,
       round(p.completos * 100.0 / nullif((SELECT count(*) FROM lugares), 0), 1) AS porcentaje
FROM (
    SELECT 1 AS orden, 'Con resumen de reseñas'::text AS etiqueta,
           count(*) FILTER (WHERE resumen_reviews IS NOT NULL) AS completos FROM lugares
    UNION ALL
    SELECT 2, 'Con embedding vectorial',
           count(*) FILTER (WHERE embedding_updated_at IS NOT NULL) FROM lugares
    UNION ALL
    SELECT 3, 'Con coordenadas', count(*) FILTER (WHERE latitud IS NOT NULL) FROM lugares
    UNION ALL
    SELECT 4, 'Con zona asignada', count(*) FILTER (WHERE zona IS NOT NULL) FROM lugares
) p
ORDER BY p.orden;


-- 6. Las definiciones, para mostrarlas en el dashboard --------------------------------------
-- Esta es la vista que hace que el SQL mostrado no pueda mentir: sale de la base, no de una
-- copia escrita a mano en el front.
CREATE OR REPLACE VIEW public.dashboard_definiciones
WITH (security_invoker = true) AS
SELECT c.relname                        AS vista,
       pg_get_viewdef(c.oid, true)      AS sql
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'v'
  AND c.relname LIKE 'dashboard\_%';


-- 7. Permisos --------------------------------------------------------------------------------
GRANT SELECT ON
    public.dashboard_panel_top_qm,
    public.dashboard_panel_top_google,
    public.dashboard_panel_mas_activos,
    public.dashboard_panel_caidos,
    public.dashboard_panel_cobertura,
    public.dashboard_definiciones
TO anon, authenticated;
