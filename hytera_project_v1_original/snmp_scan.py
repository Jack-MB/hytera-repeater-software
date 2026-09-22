import asyncio
from pysnmp.hlapi.v3arch.asyncio import *

async def test_oid(ip, community, oid_str):
    try:
        snmpEngine = SnmpEngine()
        transport = await UdpTransportTarget.create((ip, 161), timeout=2, retries=1)
        errorIndication, errorStatus, errorIndex, varBinds = await get_cmd(
            snmpEngine,
            CommunityData(community, mpModel=1),
            transport,
            ContextData(),
            ObjectType(ObjectIdentity(oid_str))
        )
        if errorIndication or errorStatus:
            print(f"{oid_str}: Error {errorIndication} {errorStatus}")
            return
        for oid, val in varBinds:
            print(f"{oid_str}: {oid} = {val}")
    except Exception as e:
        print(f"{oid_str}: Exception {e}")

async def main():
    ip = "192.168.0.1"
    community = "public"
    await test_oid(ip, community, '1.3.6.1.2.1.1.5.0')  # sysName
    await test_oid(ip, community, '1.3.6.1.4.1.11863.6.4.1.1.1.1.2.1') # TP-Link CPU 5m
    await test_oid(ip, community, '1.3.6.1.4.1.11863.6.4.1.2.1.1.2.1') # TP-Link Memory 
    await test_oid(ip, community, '1.3.6.1.2.1.25.3.3.1.2.1') # hrProcessorLoad
    await test_oid(ip, community, '1.3.6.1.4.1.2021.11.11.0') # UCD-SNMP ssCpuIdle
    await test_oid(ip, community, '1.3.6.1.2.1.2.2.1.10.1') # ifInOctets Port 1
    await test_oid(ip, community, '1.3.6.1.2.1.2.2.1.10.2') # ifInOctets Port 2
    await test_oid(ip, community, '1.3.6.1.2.1.2.2.1.16.1') # ifOutOctets Port 1

asyncio.run(main())
