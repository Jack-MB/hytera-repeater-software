import asyncio
from pysnmp.hlapi.v3arch.asyncio import *

async def test():
    ip = "192.168.1.1"
    community = "public"
    
    snmpEngine = SnmpEngine()
    transport = await UdpTransportTarget.create((ip, 161), timeout=2, retries=1)
    
    iterator = walk_cmd(
        snmpEngine,
        CommunityData(community, mpModel=1),
        transport,
        ContextData(),
        ObjectType(ObjectIdentity('1.3.6.1.2.1.2.2.1.2')), # ifDescr
        lexicographicMode=False
    )
    
    try:
        while True:
            errInd, errStat, errIdx, varBinds = await iterator.__anext__()
            if errInd or errStat:
                break
            for oid, val in varBinds:
                print(f"{oid} = {val}")
    except StopAsyncIteration:
        pass

asyncio.run(test())
