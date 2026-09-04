-- =============================================================================
-- Vistas de agregación para el dashboard público
-- Escritas el 04-sep-2026. Correr en: Supabase → SQL Editor.
-- =============================================================================
--
-- QUÉ PROBLEMA RESUELVEN
-- ----------------------
-- El dashboard calculaba sus agregados en el NAVEGADOR: se traía las filas crudas y las sumaba
-- con JavaScript. Medido, una sola visita a la home bajaba:
--
--   * 800 kB de textos de reseñas (10.000 filas) sólo para contar cuántas tienen texto útil
--   *  75 kB más de textos, para lo mismo pero de la última semana
--   * 929 lugares, cuatro veces (zona, categoría, rating y barrio se piden por separado)
--   * 7.432 filas de scraping_logs, en OCHO round-trips de 1000 (el tope de PostREST)
--
-- Todo eso para dibujar unos pocos números y seis gráficos. Con el panel publicado, ese costo se
-- paga entero en cada visita.
--
-- POR QUÉ VISTAS NORMALES Y NO MATERIALIZADAS
-- -------------------------------------------
-- Se midió antes de decidir. Descontando la latencia de red (~250 ms contra Supabase), el cómputo
-- de estos agregados es prácticamente gratis: los GROUP BY sobre `lugares` (929 filas) no llegan
-- a 1 ms, la serie semanal ronda los 75 ms y hasta el regexp sobre 10.000 textos ronda los 120 ms.
--
-- O sea: lo caro nunca fue calcular, era TRANSFERIR. Una vista normal ya elimina el 100% de esa
-- transferencia, y a cambio no hay que refrescar nada ni programar un cron: los datos están
-- siempre frescos por definición. Materializar acá sería pagar mantenimiento por una ganancia de
-- milisegundos. Si algún día el tráfico lo justifica, convertirlas es cambiar CREATE VIEW por
-- CREATE MATERIALIZED VIEW y agendar el REFRESH.
--
-- SEGURIDAD
-- ---------
-- Todas se crean con `security_invoker = true`, así que se ejecutan con los permisos del visitante
-- y quedan sujetas al mismo RLS que las tablas base. Es la misma decisión que se tomó al contener
-- `execute_sql`: que la primera línea de defensa la ponga el motor, no la aplicación.
-- =============================================================================


-- 1. KPIs de las tarjetas superiores -----------------------------------------------------------
-- Devuelve UNA fila. Reemplaza seis consultas separadas, una de las cuales se traía los 929
-- ratings al cliente sólo para promediarlos.
CREATE OR REPLACE VIEW public.dashboard_kpis
WITH (security_invoker = true) AS
SELECT
    (SELECT count(*) FROM lugares)                                          AS total_lugares,
    (SELECT count(*) FROM reviews)                                          AS total_reviews,
    (SELECT count(*) FROM reviews
      WHERE fecha_scraping::timestamp >= now() - interval '7 days')         AS reviews_semanal,
    (SELECT round(avg(rating_gral), 2) FROM lugares
      WHERE rating_gral IS NOT NULL)                                        AS rating_promedio,
    (SELECT max(fecha) FROM scraping_logs)                                  AS ultimo_scraping,
    (SELECT count(*) FROM scraping_logs
      WHERE estado = 'ERROR' AND fecha >= now() - interval '7 days')        AS errores_semanal;


-- 2. Calidad de las reseñas --------------------------------------------------------------------
-- Dos filas: 'muestra' (las 10.000 más recientes) y 'semana'.
--
-- POR QUÉ NO SE REPLICA EL `normalizeText()` DEL FRONT
-- ---------------------------------------------------
-- El front colapsaba las corridas de 3+ caracteres repetidos antes de medir el largo, para no
-- contar como reseña útil un "buenisimoooooooooo". Se probó traer ese regexp a SQL y se midió:
--
--   * cambia el veredicto en 6 de cada 10.000 reseñas (0,06 %)
--   * y cuesta 8,6 SEGUNDOS, contra 0,3 s sin él
--
-- El patrón necesita un backreference ('(.){2,}'), que obliga al motor de regex a backtrackear:
-- ~1,5 ms por texto. Pagar 30x el tiempo de la vista para corregir 6 casos de 10.000 —sobre un
-- indicador que YA es aproximado, porque mira una muestra de 10.000 de 205.492— no se justifica.
--
-- El número resultante difiere en ~0,1 % del que mostraba el dashboard antes.
CREATE OR REPLACE VIEW public.dashboard_calidad_reviews
WITH (security_invoker = true) AS
SELECT 'muestra'::text AS ambito,
       count(*)                                                    AS total,
       count(*) FILTER (WHERE texto IS NULL OR btrim(texto) = '')  AS sin_texto,
       count(*) FILTER (WHERE length(btrim(coalesce(texto, ''))) >= 30) AS con_texto_util
FROM (
    -- El desempate por review_id hace la muestra determinista: `fecha_scraping` tiene miles de
    -- empates (una corrida entera comparte timestamp), así que sin él las "10.000 más recientes"
    -- cambiaban entre consultas y el número bailaba en pantalla.
    SELECT texto FROM reviews ORDER BY fecha_scraping DESC, review_id DESC LIMIT 10000
) ultimas
UNION ALL
SELECT 'semana'::text,
       count(*),
       count(*) FILTER (WHERE texto IS NULL OR btrim(texto) = ''),
       count(*) FILTER (WHERE length(btrim(coalesce(texto, ''))) >= 30)
FROM reviews
WHERE fecha_scraping::timestamp >= now() - interval '7 days';


-- 3. Distribuciones de `lugares` ---------------------------------------------------------------
CREATE OR REPLACE VIEW public.dashboard_lugares_por_zona
WITH (security_invoker = true) AS
SELECT zona,
       count(*)                                  AS lugares,
       sum(coalesce(total_reviews_google, 0))    AS reviews
FROM lugares
WHERE zona IS NOT NULL
GROUP BY zona
ORDER BY reviews DESC;

CREATE OR REPLACE VIEW public.dashboard_lugares_por_categoria
WITH (security_invoker = true) AS
SELECT categoria, count(*) AS lugares
FROM lugares
WHERE categoria IS NOT NULL
GROUP BY categoria
ORDER BY lugares DESC;

CREATE OR REPLACE VIEW public.dashboard_lugares_por_barrio
WITH (security_invoker = true) AS
SELECT coalesce(barrio, 'Sin barrio') AS barrio, count(*) AS lugares
FROM lugares
GROUP BY 1
ORDER BY lugares DESC;

-- Los rangos y sus etiquetas son los mismos que dibujaba el front; `orden` existe para que el
-- gráfico no dependa de ordenar etiquetas de texto.
CREATE OR REPLACE VIEW public.dashboard_distribucion_rating
WITH (security_invoker = true) AS
SELECT r.orden, r.etiqueta,
       (SELECT count(*) FROM lugares l
         WHERE l.rating_gral IS NOT NULL
           AND l.rating_gral >= r.minimo AND l.rating_gral <= r.maximo) AS lugares
FROM (VALUES
    (1, '1-2 ⭐',      1.0, 1.9),
    (2, '2-3 ⭐',      2.0, 2.9),
    (3, '3.0-3.4 ⭐',  3.0, 3.4),
    (4, '3.5-3.9 ⭐',  3.5, 3.9),
    (5, '4.0-4.2 ⭐',  4.0, 4.2),
    (6, '4.3-4.5 ⭐',  4.3, 4.5),
    (7, '4.6-4.8 ⭐',  4.6, 4.8),
    (8, '4.9-5.0 ⭐',  4.9, 5.0)
) AS r(orden, etiqueta, minimo, maximo)
ORDER BY r.orden;

CREATE OR REPLACE VIEW public.dashboard_distribucion_reviews
WITH (security_invoker = true) AS
SELECT r.orden, r.etiqueta,
       (SELECT count(*) FROM lugares l
         WHERE coalesce(l.total_reviews_google, 0) >= r.minimo
           AND coalesce(l.total_reviews_google, 0) <= r.maximo) AS lugares
FROM (VALUES
    (1, '0-10',     0,      10),
    (2, '11-50',    11,     50),
    (3, '51-100',   51,    100),
    (4, '101-500',  101,   500),
    (5, '500+',     501, 2147483647)
) AS r(orden, etiqueta, minimo, maximo)
ORDER BY r.orden;

CREATE OR REPLACE VIEW public.dashboard_top_lugares
WITH (security_invoker = true) AS
SELECT nombre, coalesce(total_reviews_google, 0) AS reviews, rating_gral, zona
FROM lugares
ORDER BY coalesce(total_reviews_google, 0) DESC
LIMIT 10;


-- 4. Serie semanal de reseñas nuevas -----------------------------------------------------------
-- Reemplaza las OCHO peticiones paginadas que hacía el navegador para después agrupar a mano.
--
-- El corte del 20-ene-2026 vive acá y en ningún otro lado: el 10-ene se cargó la base entera de
-- una vez (80.528 reseñas en un día contra ~1.500 de una semana normal) y ese pico aplasta la
-- escala vertical de cualquier gráfico que lo incluya.
--
-- Las semanas sin actividad aparecen en cero gracias al generate_series: si faltaran, la línea
-- uniría los dos puntos vecinos y aparentaría una continuidad que no hubo.
CREATE OR REPLACE VIEW public.dashboard_reviews_por_semana
WITH (security_invoker = true) AS
WITH limites AS (
    SELECT date_trunc('week', timestamp '2026-01-20') AS desde,
           date_trunc('week', max(fecha))             AS hasta
    FROM scraping_logs
),
semanas AS (
    SELECT generate_series(desde, hasta, interval '7 days') AS semana FROM limites
),
actividad AS (
    SELECT date_trunc('week', fecha) AS semana, sum(coalesce(nuevas_reviews, 0)) AS nuevas
    FROM scraping_logs
    WHERE fecha >= (SELECT desde FROM limites)
    GROUP BY 1
)
SELECT s.semana::date                        AS semana,
       coalesce(a.nuevas, 0)::bigint         AS nuevas
FROM semanas s
LEFT JOIN actividad a USING (semana)
ORDER BY s.semana;


-- 5. Permisos ----------------------------------------------------------------------------------
-- Sólo lectura para los roles de PostgREST. Con `security_invoker`, cada vista queda además
-- sujeta a las políticas RLS de las tablas que consulta.
GRANT SELECT ON
    public.dashboard_kpis,
    public.dashboard_calidad_reviews,
    public.dashboard_lugares_por_zona,
    public.dashboard_lugares_por_categoria,
    public.dashboard_lugares_por_barrio,
    public.dashboard_distribucion_rating,
    public.dashboard_distribucion_reviews,
    public.dashboard_top_lugares,
    public.dashboard_reviews_por_semana
TO anon, authenticated;
