import asyncio
import pandas as pd
from main import consultar_estadisticas

class DummyLLM:
    async def ainvoke(self, prompt):
        class Res:
            content = 'heladerias'
        return Res()

async def main():
    df = pd.DataFrame([
        {'restaurante': 'Heladeria del Centro', 'texto': 'La mejor heladería de la ciudad'},
        {'restaurante': 'Pancheria Los Pibes', 'texto': 'Panchos oficiales'},
    ])
    # Fill columns expected by main
    for col in ['restaurante', 'texto', 'direccion', 'barrio', 'zona', 'autor', 'fecha']:
        if col not in df.columns:
            df[col] = ''
    # Call function
    resp, locales = await consultar_estadisticas('cuantas heladerias hay?', df, DummyLLM())
    print('Response:', resp)
    print('Locales:', locales)

if __name__ == '__main__':
    asyncio.run(main())
