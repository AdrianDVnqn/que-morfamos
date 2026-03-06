import asyncio
import main
from main import lifespan, app, procesar_consulta, df, vectorstore, llm_mini, llm_smart
async def run():
    async with lifespan(app):
        print('Testing growler...')
        ctx = {}
        async for evt in main.procesar_consulta_gen('growler', main.df, main.vectorstore, main.llm_mini, main.llm_smart, ctx):
            print(evt)
if __name__ == '__main__':
    asyncio.run(run())
