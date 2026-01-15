
# Mapeo de Zonas (Copiado de que-morfamos-scraper/asignar_barrios.py)
# Mantener sincronizado manualmente por ahora.

ZONAS_MAP = {
    # CENTRO
    'ÁREA CENTRO ESTE': 'Centro',
    'ÁREA CENTRO OESTE': 'Centro',
    'ÁREA CENTRO SUR': 'Centro',
    
    # ESTE
    'SANTA GENOVEVA': 'Este',
    'CONFLUENCIA URBANO': 'Este', 
    'MARIANO MORENO': 'Este',
    'VILLA FARRELL': 'Este',
    'SAPERE': 'Este',
    'PROVINCIAS UNIDAS': 'Este',
    'VILLA MARÍA': 'Este',
    'BELGRANO': 'Este',

    # RÍO / PASEO DE LA COSTA
    'RÍO GRANDE': 'Paseo de la Costa',
    'LIMAY': 'Paseo de la Costa',
    'ALTOS DEL LIMAY': 'Paseo de la Costa',
    'CONFLUENCIA RURAL': 'Paseo de la Costa', 
    
    # NORTE / EL ALTO
    'ALTA BARDA': 'Norte / Alto',
    'RINCÓN DE EMILIO': 'Norte / Alto',
    'PARQUE INDUSTRIAL': 'Norte / Alto', # Note: JSON has 'CIUDAD INDUSTRIAL OBISPO DON JAIME DE NEVARES'
    'CIUDAD INDUSTRIAL OBISPO DON JAIME DE NEVARES': 'Norte / Alto',
    '14 DE OCTUBRE y COPOL': 'Norte / Alto',
    'TERRAZAS DEL NEUQUÉN': 'Norte / Alto',
    'BARDAS SOLEADAS': 'Norte / Alto',
    
    # OESTE
    'VILLA FLORENCIA': 'Oeste',
    'VILLA CEFERINO': 'Oeste',
    'SAN LORENZO NORTE': 'Oeste',
    'SAN LORENZO SUR': 'Oeste',
    'GRAN NEUQUÉN NORTE': 'Oeste',
    'GRAN NEUQUÉN SUR': 'Oeste',
    'MELIPAL': 'Oeste',
    'UNIÓN DE MAYO': 'Oeste',
    'GREGORIO ÁLVAREZ': 'Oeste',
    'ISLAS MALVINAS': 'Oeste',
    'BOUQUET ROLDÁN': 'Oeste',
    'VALENTINA SUR RURAL': 'Oeste',
    'VALENTINA SUR URBANO': 'Oeste', 
    'VALENTINA NORTE URBANO': 'Oeste',
    'VALENTINA NORTE RURAL': 'Oeste',
    'ESFUERZO': 'Oeste',
    'HIBEPA': 'Oeste',
    'CUENCA XV': 'Oeste',
    'CANAL V': 'Oeste',
    'MILITAR': 'Oeste',
    'LA SIRENA': 'Oeste',
    'CUMELÉN': 'Oeste', 
    'EL PROGRESO': 'Oeste',
    'HUILICHES': 'Oeste',
    'DON BOSCO II': 'Oeste',
    'DON BOSCO III': 'Oeste',
    'NUEVO': 'Oeste'
}

BARRIOS_RIO = ['RÍO GRANDE', 'LIMAY', 'CONFLUENCIA RURAL', 'RINCÓN DE EMILIO', 'VALENTINA SUR RURAL']
