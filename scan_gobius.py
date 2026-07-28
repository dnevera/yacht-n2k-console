import asyncio
from bleak import BleakClient

async def scan():
    async with BleakClient("2C:A7:74:21:56:D8", timeout=15) as c:
        for svc in c.services:
            print(f"Service: {svc.uuid}")
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                desc = ch.description or ""
                print(f"  {ch.uuid}  [{props}]  {desc}")
            print()

asyncio.run(scan())
