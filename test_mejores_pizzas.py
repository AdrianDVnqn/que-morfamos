import asyncio
import traceback
import main
from main import lifespan, app, procesar_consulta_gen, df, vectorstore, llm_mini, llm_smart
async def run():
    async with lifespan(app):
        print('Testing mejores pizzas...')
        ctx = {}
        try:
            async for evt in procesar_consulta_gen('mejores pizzas', main.df, main.vectorstore, main.llm_mini, main.llm_smart, ctx):
                if evt.get('type') == 'error':
                    print('ERROR EVENT:', evt)
        except Exception as e:
            traceback.print_exc()
if __name__ == '__main__':
    asyncio.run(run())
