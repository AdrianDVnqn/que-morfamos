import asyncio
import pandas as pd
from main import consultar_estadisticas

class DummyLLM:
    def __init__(self, content='heladerias'):
        self._content = content
    async def ainvoke(self, prompt):
        class Res:
            pass
        r = Res()
        r.content = self._content
        return r

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
    resp, locales = await consultar_estadisticas('cuantas heladerias hay?', df, DummyLLM('heladerias'))
    print('Response:', resp)
    print('Locales:', locales)

    # Test case: user asks empanadas but LLM returns truncated 'empana'
    resp2, locales2 = await consultar_estadisticas('cuantas empanadas hay?', df, DummyLLM('empana'))
    print('\nResponse2 (empana stub):', resp2)
    print('Locales2:', locales2)

    # Test case: LLM returns full 'empanadas'
    resp3, locales3 = await consultar_estadisticas('cuantas empanadas hay?', df, DummyLLM('empanadas'))
    print('\nResponse3 (empanadas):', resp3)
    print('Locales3:', locales3)

if __name__ == '__main__':
    asyncio.run(main())
