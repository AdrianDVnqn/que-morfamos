"""Genera el vocabulario de conceptos a partir del corpus real de resúmenes.

POR QUÉ EXISTE
--------------
`SINONIMOS_CURADOS` en `main.py` tiene 5 conceptos (`sin tacc`, `vegano`, `vegetariano`,
`pelotero`, `celiaco`). Todo lo demás cae al match literal, y eso se paga caro: medido sobre
menciones POSITIVAS en los 929 resúmenes, `cerveza artesanal` pasa de 56 lugares con el término
literal a 439 con sus variantes (7.8×), `terraza` de 16 a 89 (5.6×), y `música en vivo` de **0**
a 43 — el término literal no aparece positivamente en ningún resumen, así que hoy ese concepto
es invisible para el ranking.

Curar más a mano es la misma deuda con otro número: dentro de un mes falta el concepto 26. Este
script deriva las variantes del corpus, así que se regenera con cada scrape y sigue al
vocabulario que la gente y los resúmenes usan de verdad.

CÓMO
----
1. Extrae los n-gramas (1 a 3 palabras) que aparecen en los resúmenes, con frecuencia por
   DOCUMENTO y no por repetición: lo que importa es en cuántos lugares aparece el término.
2. Para cada concepto semilla, propone candidatos por co-ocurrencia: términos que aparecen
   desproporcionadamente en los resúmenes que ya mencionan el concepto. Esto es lo que hace que
   el LLM no tenga que elegir entre 4400 términos sino entre ~40, y que los candidatos estén
   garantizadamente presentes en el corpus.
3. El LLM decide cuáles son EQUIVALENTES al concepto, no meramente correlacionados. Esa
   distinción es la que no se puede sacar de la estadística: "ensalada" co-ocurre con "vegano"
   sin ser un sinónimo, y aceptarlo haría que cualquier lugar con ensaladas cuente como vegano.

USO
---
    python generar_vocabulario_conceptos.py            # genera sinonimos_generados.json
    python generar_vocabulario_conceptos.py --dry-run  # muestra candidatos sin llamar al LLM

El backend consume el resultado desde `variantes_de_concepto()`, que fusiona lo generado con lo
curado a mano. Lo curado siempre gana: son decisiones tomadas con evidencia y no hay que dejar
que una corrida las pise.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

SALIDA = "sinonimos_generados.json"

# Cuánto más común que el concepto puede ser un sinónimo antes de considerarlo un término más
# amplio y descartarlo. 4x es holgado a propósito: los plurales y las formas largas ("cervezas
# artesanales" vs "cerveza artesanal") son legítimamente más o menos frecuentes.
TOLERANCIA_AMPLITUD = 4

# Mínimo de resúmenes que tienen que mencionar el concepto para confiar en la estadística que
# alimenta a los candidatos. Por debajo de esto el lift se calcula sobre un puñado de documentos
# y devuelve cualquier cosa que casualmente aparezca ahí: con 15 se colaba "bar cervecero" como
# sinónimo de "terraza" (los lugares con terraza suelen ser bares, pero no al revés).
MINIMO_RESUMENES = 20

# Conceptos semilla. Salen de tres lados: los que ya estaban curados, los que aparecen en el
# golden dataset, y los que un usuario pediría de verdad en Neuquén. No pretende ser exhaustiva
# —ninguna lista lo es— pero cubre bastante más que los 5 actuales, y ampliarla es agregar una
# línea acá y volver a correr.
CONCEPTOS_SEMILLA = [
    # Restricciones y dietas
    "sin tacc", "vegano", "vegetariano", "sin lactosa", "apto celiaco",
    # Amenities y características del lugar
    "pelotero", "pet friendly", "terraza", "estacionamiento", "wifi",
    "musica en vivo", "vista al rio", "aire libre",
    # "accesible" se saco a proposito: en este corpus significa "precio accesible", no
    # accesibilidad para sillas de ruedas, y mezclar los dos sentidos ensucia el ranking.
    # Momentos y ocasiones
    "desayuno", "merienda", "brunch", "after office", "romantico",
    "para ir con chicos", "para grupos grandes",
    # Productos y tipos
    "cerveza artesanal", "cafe de especialidad", "vinos", "cocteles",
    "milanesa", "sushi", "pizza", "parrilla", "hamburguesa", "pastas",
    "helado", "pasteleria", "empanadas", "comida arabe", "comida peruana",
    # Atributos
    "barato", "porciones abundantes", "atencion rapida",
]

STOP = set(
    "el la los las un una unos unas de del al a y o en con sin por para que se su sus lo es son "
    "este esta muy mas pero como donde cuando ya tambien tiene tienen hay ser fue han he ha les "
    "le nos mi tu si no ni entre desde hasta sobre bajo cada todo toda todos todas otro otra"
    .split()
)


def normalizar(texto):
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    texto = "".join(ch for ch in texto if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9ñ ]+", " ", texto)


def cargar_resumenes():
    load_dotenv("mis_claves.env")
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("Falta DATABASE_URL en mis_claves.env")
    engine = create_engine(url)
    with engine.connect() as conn:
        filas = conn.execute(
            text("SELECT resumen_reviews FROM lugares WHERE resumen_reviews IS NOT NULL")
        )
        return [normalizar(f[0]) for f in filas]


def ngramas_por_documento(docs, maximo=3):
    """{n-grama: en cuántos documentos aparece}. Por documento, no por repetición: un resumen que
    dice "pizza" cinco veces no hace que "pizza" sea más común, hace que ESE lugar sea de pizza."""
    df = Counter()
    for doc in docs:
        palabras = [p for p in doc.split() if p]
        vistos = set()
        for n in range(1, maximo + 1):
            for i in range(len(palabras) - n + 1):
                grupo = palabras[i : i + n]
                if grupo[0] in STOP or grupo[-1] in STOP:
                    continue
                if any(len(p) < 3 for p in grupo):
                    continue
                vistos.add(" ".join(grupo))
        df.update(vistos)
    return df


def candidatos_para(concepto, docs, df_global, tope=40, minimo_docs=4):
    """Términos que aparecen desproporcionadamente donde ya aparece el concepto.

    El score es la razón entre su frecuencia dentro del subcorpus y su frecuencia global: un
    término que aparece en todos lados (ej. "comida") tiene razón ~1 y queda abajo; uno que
    aparece casi sólo junto al concepto sube. Es un lift clásico, y alcanza porque después el LLM
    filtra los correlacionados que no son equivalentes.
    """
    con = normalizar(concepto)
    subcorpus = [d for d in docs if con in d]
    if len(subcorpus) < minimo_docs:
        return [], len(subcorpus)

    df_sub = ngramas_por_documento(subcorpus)
    total_sub, total_glob = len(subcorpus), len(docs)
    puntuados = []
    for termino, freq_sub in df_sub.items():
        if freq_sub < minimo_docs or termino == con:
            continue
        freq_glob = df_global.get(termino, freq_sub)
        lift = (freq_sub / total_sub) / max(freq_glob / total_glob, 1e-9)
        if lift > 1.5:
            puntuados.append((lift, freq_glob, termino))
    puntuados.sort(reverse=True)
    return [t for _, _, t in puntuados[:tope]], len(subcorpus)


PROMPT = """Sos un lexicógrafo trabajando sobre reseñas de restaurantes de Neuquén, Argentina.

CONCEPTO: "{concepto}"

Abajo hay términos extraídos del corpus real de reseñas. Elegí SOLO los que un usuario usaría
para pedir LO MISMO que el concepto, o que en una reseña significan que el lugar OFRECE ese
concepto.

REGLA CLAVE — equivalencia, no correlación:
- SÍ: "birra" para "cerveza artesanal" (es la misma cosa dicha de otra forma).
- SÍ: "shows en vivo" para "musica en vivo".
- NO: "ensalada" para "vegano" (co-ocurren, pero una ensalada no hace vegano a un lugar).
- NO: "precio" o "atencion" (aparecen en todos lados).
En la duda, DEJALO AFUERA: un sinónimo de más hace que lugares que no corresponden entren en los
resultados, que es peor que perder uno.

TÉRMINOS DEL CORPUS:
{candidatos}

Respondé SOLO un JSON, sin explicaciones ni markdown:
{{"sinonimos": ["termino1", "termino2"]}}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="muestra candidatos sin llamar al LLM")
    ap.add_argument("--concepto", help="procesa uno solo, para iterar rápido")
    args = ap.parse_args()

    print("Cargando resúmenes...")
    docs = cargar_resumenes()
    print(f"  {len(docs)} resúmenes")
    print("Extrayendo vocabulario del corpus...")
    df_global = ngramas_por_documento(docs)
    print(f"  {len(df_global)} n-gramas")

    conceptos = [args.concepto] if args.concepto else CONCEPTOS_SEMILLA

    llm = None
    cliente = None
    if not args.dry_run:
        # Se importa acá y no arriba para que --dry-run no necesite las claves del LLM.
        from fastapi.testclient import TestClient
        import main as backend

        # Los clientes LLM se construyen en el lifespan de la app, no al importar el módulo: sin
        # levantarlo, `llm_fast` existe pero vale None y todas las llamadas fallan.
        cliente = TestClient(backend.app)
        cliente.__enter__()
        llm = next(
            (c for c in (getattr(backend, n, None) for n in ("llm_fast", "llm_mini", "llm_smart")) if c),
            None,
        )
        if llm is None:
            sys.exit("No hay ningún cliente LLM inicializado; revisá AI_PROVIDER y las claves.")

    resultado = {}
    for concepto in conceptos:
        cands, n_sub = candidatos_para(concepto, docs, df_global)
        if n_sub < MINIMO_RESUMENES:
            print(f"  {concepto:24} muy escaso ({n_sub} resúmenes): no alcanza para inferir nada")
            continue
        if not cands:
            print(f"  {concepto:24} sin candidatos (aparece en {n_sub} resúmenes)")
            continue
        if args.dry_run:
            print(f"  {concepto:24} ({n_sub} resúmenes) -> {cands[:12]}")
            continue

        prompt = PROMPT.format(concepto=concepto, candidatos="\n".join(f"- {c}" for c in cands))
        try:
            crudo = llm.invoke(prompt).content
            limpio = crudo.strip().replace("```json", "").replace("```", "")
            elegidos = json.loads(limpio).get("sinonimos", [])
        except Exception as e:
            print(f"  {concepto:24} ERROR: {e}")
            continue
        # Dos filtros objetivos sobre lo que eligió el modelo:
        # 1. Que exista en el corpus (puede devolver algo que no está).
        # 2. Que tenga especificidad comparable. Un sinónimo no puede ser MUCHO más común que el
        #    concepto: si lo es, es un término más amplio y no un equivalente. Es lo que filtra
        #    "bar" como sinónimo de "terraza" (los lugares con terraza suelen ser bares, pero no
        #    todo bar tiene terraza) y "delivery" como sinónimo de "atencion rapida". El LLM deja
        #    pasar estos correlacionados justo en los conceptos con pocos documentos, donde la
        #    estadística que le da los candidatos es más débil.
        freq_concepto = df_global.get(normalizar(concepto), n_sub) or n_sub
        elegidos = [
            s for s in elegidos
            if normalizar(s) in df_global
            and df_global[normalizar(s)] <= TOLERANCIA_AMPLITUD * max(freq_concepto, 1)
        ]
        resultado[normalizar(concepto)] = elegidos
        print(f"  {concepto:24} ({n_sub} resúmenes) -> {elegidos}")

    if cliente is not None:
        cliente.__exit__(None, None, None)

    if args.dry_run:
        return

    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"\nEscrito {SALIDA} con {len(resultado)} conceptos.")


if __name__ == "__main__":
    main()
