import asyncio
import threading
import time
import logging
from pysnmp.hlapi.v3arch.asyncio import *

log = logging.getLogger("router")

class RouterData:
    def __init__(self):
        self.sys_name = "Unbekannt"
        self.uptime_s = 0
        self.cpu_load = None
        self.mem_util = None
        self.traffic_in = 0
        self.traffic_out = 0
        self.traffic_in_mbps = 0.0
        self.traffic_out_mbps = 0.0
        self.erreichbar = False
        self.letzte_abfrage = None
        self.fehler = None

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__dict__}

class RouterMonitor(threading.Thread):
    def __init__(self, ip, community="public", interval=30, callback=None):
        super().__init__(daemon=True, name="Router-Monitor")
        self.ip = ip
        self.community = community
        self.interval = interval
        self.callback = callback
        self._stop = threading.Event()
        self.data = RouterData()
        self._last_poll = None
        self._last_in = None
        self._last_out = None

    def stop(self):
        self._stop.set()

    async def _poll_async(self):
        d = RouterData()
        try:
            snmpEngine = SnmpEngine()
            transport = await UdpTransportTarget.create((self.ip, 161), timeout=2, retries=1)
            
            errInd, errStat, errIdx, varBinds = await get_cmd(
                snmpEngine,
                CommunityData(self.community, mpModel=1),
                transport,
                ContextData(),
                ObjectType(ObjectIdentity('1.3.6.1.2.1.1.5.0')), # sysName
                ObjectType(ObjectIdentity('1.3.6.1.2.1.1.3.0')), # sysUpTime
                ObjectType(ObjectIdentity('1.3.6.1.2.1.2.2.1.10.1037')), # ifInOctets br-lan
                ObjectType(ObjectIdentity('1.3.6.1.2.1.2.2.1.16.1037')), # ifOutOctets br-lan
                ObjectType(ObjectIdentity('1.3.6.1.4.1.2021.11.11.0')) # UCD CPU Idle
            )

            if errInd:
                d.fehler = str(errInd)
            elif errStat:
                d.fehler = errStat.prettyPrint()
            else:
                for oid, val in varBinds:
                    o = str(oid)
                    v_cls = val.__class__.__name__
                    if v_cls in ('NoSuchInstance', 'NoSuchObject'):
                        continue
                        
                    if '1.3.6.1.2.1.1.5.0' in o: d.sys_name = str(val)
                    elif '1.3.6.1.2.1.1.3.0' in o: d.uptime_s = int(val) / 100.0
                    elif '1.3.6.1.2.1.2.2.1.10.1037' in o: d.traffic_in = int(val)
                    elif '1.3.6.1.2.1.2.2.1.16.1037' in o: d.traffic_out = int(val)
                    elif '1.3.6.1.4.1.2021.11.11.0' in o:
                        try: d.cpu_load = max(0, 100 - int(val))
                        except: pass
                d.erreichbar = True

            now = time.time()
            d.letzte_abfrage = now

            # Calculate Mbps
            if self._last_poll and d.erreichbar:
                elapsed = now - self._last_poll
                if elapsed > 0:
                    if self._last_in is not None and d.traffic_in >= self._last_in:
                        d.traffic_in_mbps = ((d.traffic_in - self._last_in) * 8) / (1024 * 1024 * elapsed)
                    if self._last_out is not None and d.traffic_out >= self._last_out:
                        d.traffic_out_mbps = ((d.traffic_out - self._last_out) * 8) / (1024 * 1024 * elapsed)
            
            if d.erreichbar:
                self._last_poll = now
                self._last_in = d.traffic_in
                self._last_out = d.traffic_out

        except Exception as e:
            d.fehler = str(e)
            log.warning(f"[Router] Fehler: {e}")

        self.data = d
        return d

    def poll(self):
        return asyncio.run(self._poll_async())

    def run(self):
        log.info(f"Router-Monitor (SNMP) gestartet: {self.ip}")
        while not self._stop.is_set():
            try:
                d = self.poll()
                if self.callback:
                    self.callback(d)
            except Exception as e:
                log.error(f"Router-Monitor Loop: {e}")
            self._stop.wait(self.interval)
        log.info("Router-Monitor gestoppt")
