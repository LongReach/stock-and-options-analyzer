import asyncio

async def do_thing():
    print("thing 1")
    await asyncio.sleep(2.0)
    print("thing 2")

def make_task() -> asyncio.Task:
    task = asyncio.create_task(do_thing())
    return task

async def main():
    task = make_task()
    await task

asyncio.run(main())