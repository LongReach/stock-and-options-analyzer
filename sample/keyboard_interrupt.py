import asyncio


async def main():
    for i in range(100):
        print(f"Step {i}...")
        await asyncio.sleep(2.0)


asyncio.run(main())
