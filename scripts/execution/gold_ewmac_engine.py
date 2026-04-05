#!/usr/bin/env python3
"""
Gold EWMAC Engine v2 - 300万口座, Max2, NYMEX, FRED GVZ
"""
import sys,time,json,logging,urllib.request
from datetime import datetime
from pathlib import Path
import numpy as np,pandas as pd
from ib_insync import IB,Future,util

LOG_DIR=Path("logs");LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO,format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_DIR/f"gold_ewmac_{datetime.now().strftime('%Y%m%d')}.log",encoding='utf-8'),
              logging.StreamHandler(sys.stdout)])
log=logging.getLogger('gold_ewmac')

CONFIG={
    'host':'127.0.0.1','port':4002,'client_id':3,
    'symbol':'MGC','exchange':'COMEX','currency':'USD','last_trade_date':'20260626',
    'speeds':[(8,32),(64,256)],'forecast_cap':20.0,'forecast_target':10.0,'fdm':1.3,'vol_span':36,
    'capital':3_000_000,'vol_target':0.20,'max_contracts':2,'idm':1.2,'n_instruments':2,
    'inertia':0.10,'point_value':10,'fx_rate':150.0,'ann_factor':256*7,
    'gvz_zscore_threshold':0.0,'gvz_lookback':20,
    'fred_api_key':'8643a3999d1183cbfaa2bd1b64e9e545',
    'state_file':'gold_ewmac_state.json',
    'history_duration':'365 D','bar_size':'1 hour',
}

def load_state(fp):
    p=Path(fp)
    if p.exists():
        with open(p) as f: return json.load(f)
    return {'position':0,'entry_price':0.0,'last_signal':'flat','last_bar_time':None}

def save_state(state,fp):
    with open(fp,'w') as f: json.dump(state,f,indent=2,default=str)

class GVZFilter:
    def __init__(self,api_key,lookback=20,threshold=0.0):
        self.api_key=api_key;self.lookback=lookback;self.threshold=threshold
        self.gvz_value=None;self.gvz_zscore=None;self.is_active=False;self._last_update=None

    def update(self):
        try:
            url=(f"https://api.stlouisfed.org/fred/series/observations"
                 f"?series_id=GVZCLS&api_key={self.api_key}&file_type=json&sort_order=desc&limit=60")
            req=urllib.request.Request(url);req.add_header('User-Agent','GoldEWMAC/1.0')
            with urllib.request.urlopen(req,timeout=15) as resp: data=json.loads(resp.read())
            observations=data.get('observations',[])
            if not observations: log.warning("FRED: GVZデータなし"); return False
            values=[]
            for obs in reversed(observations):
                if obs['value']!='.': values.append({'date':obs['date'],'close':float(obs['value'])})
            if len(values)<self.lookback+5: log.warning(f"GVZデータ不足:{len(values)}"); return False
            closes=pd.Series([v['close'] for v in values])
            ma=closes.rolling(self.lookback).mean();std=closes.rolling(self.lookback).std()
            zscore=(closes-ma)/std
            self.gvz_value=closes.iloc[-1];self.gvz_zscore=zscore.iloc[-1]
            self.is_active=self.gvz_zscore>self.threshold;self._last_update=datetime.now()
            log.info(f"GVZ(FRED): {self.gvz_value:.2f} zscore:{self.gvz_zscore:+.3f} "
                     f"filter:{'ON' if self.is_active else 'OFF'} date:{values[-1]['date']}")
            return True
        except Exception as e: log.warning(f"FRED GVZ失敗: {e}"); return False

    def needs_update(self,interval_hours=4):
        if self._last_update is None: return True
        return (datetime.now()-self._last_update).total_seconds()>interval_hours*3600

class EWMACEngine:
    def __init__(self,cfg):
        self.cfg=cfg;self.bars_2h=pd.DataFrame()
        self.current_forecast=0.0;self.ideal_position=0.0;self.vol=None;self.scalars={}

    def resample_to_2h(self,bars_1h):
        if not bars_1h: return pd.DataFrame()
        df=pd.DataFrame([{'datetime':b.date if isinstance(b.date,datetime) else pd.to_datetime(str(b.date)),
            'open':b.open,'high':b.high,'low':b.low,'close':b.close,'volume':b.volume} for b in bars_1h])
        df['datetime']=pd.to_datetime(df['datetime']);df=df.set_index('datetime').sort_index()
        return df.resample('2h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()

    def initialize(self,bars_1h):
        self.bars_2h=self.resample_to_2h(bars_1h)
        if len(self.bars_2h)<64: return False
        c=self.bars_2h['close'];self._calc_vol(c);self._calc_scalars(c);self._calc_forecast(c)
        log.info(f"EWMAC init: {len(self.bars_2h)} bars(2H) fc={self.current_forecast:+.2f} ideal={self.ideal_position:.2f}")
        return True

    def _calc_vol(self,close):
        vol=close.diff().abs().ewm(span=self.cfg['vol_span'],min_periods=10).mean()
        self.vol=vol.clip(lower=vol.expanding().quantile(0.05))

    def _calc_scalars(self,close):
        for f,s in self.cfg['speeds']:
            ef=close.ewm(span=f,min_periods=f).mean();es=close.ewm(span=s,min_periods=s).mean()
            raw=(ef-es)/self.vol;valid=raw.dropna()
            med=valid.abs().expanding(min_periods=min(s*2,len(valid))).median().iloc[-1] if len(valid)>=s*2 else 0
            self.scalars[(f,s)]=np.clip(10.0/med if med>0 else 5,1,50)
        log.info(f"Scalars: {self.scalars}")

    def _calc_forecast(self,close):
        fcs=[]
        for f,s in self.cfg['speeds']:
            ef=close.ewm(span=f,min_periods=f).mean();es=close.ewm(span=s,min_periods=s).mean()
            fcs.append(((ef-es)/self.vol*self.scalars[(f,s)]).clip(-self.cfg['forecast_cap'],self.cfg['forecast_cap']))
        combined=pd.DataFrame(fcs).T.mean(axis=1)*self.cfg['fdm']
        combined=combined.clip(-self.cfg['forecast_cap'],self.cfg['forecast_cap']);combined[combined<0]=0
        self.current_forecast=combined.iloc[-1]
        av=self.vol.iloc[-1]*np.sqrt(self.cfg['ann_factor']);w=1.0/self.cfg['n_instruments']
        self.ideal_position=np.clip(
            (self.cfg['capital']*self.cfg['vol_target']*self.cfg['idm']*w*self.current_forecast*self.cfg['fdm'])/
            (self.cfg['forecast_target']*av*self.cfg['point_value']*self.cfg['fx_rate']),
            -self.cfg['max_contracts'],self.cfg['max_contracts'])

    def add_bar(self,row):
        nr=pd.DataFrame([row]);nr.index=pd.DatetimeIndex([row['datetime']])
        self.bars_2h=pd.concat([self.bars_2h,nr[['open','high','low','close','volume']]])
        c=self.bars_2h['close'];self._calc_vol(c);self._calc_forecast(c)

    def get_target_position(self,cur,gvz_on):
        if not gvz_on: return 0
        t=round(self.ideal_position)
        if abs(t-cur)<max(abs(cur)*self.cfg['inertia'],0.5): return cur
        return t

class GoldEWMACRealtimeEngine:
    def __init__(self):
        self.ib=IB();self.state=load_state(CONFIG['state_file']);self.contract=None
        self.ewmac=EWMACEngine(CONFIG)
        self.gvz=GVZFilter(CONFIG['fred_api_key'],CONFIG['gvz_lookback'],CONFIG['gvz_zscore_threshold'])
        self._needs_reconnect=False;self._pending=None

    def connect(self):
        log.info("="*50)
        log.info(f"=== Gold EWMAC v2 === {'Paper' if CONFIG['port']==4002 else 'Live'} {CONFIG['capital']/10000:.0f}万 Max{CONFIG['max_contracts']}")
        log.info("="*50)
        self.ib.connect(CONFIG['host'],CONFIG['port'],clientId=CONFIG['client_id'],timeout=20)
        log.info(f"接続OK: {self.ib.managedAccounts()}")
        self.contract=Future(symbol=CONFIG['symbol'],lastTradeDateOrContractMonth=CONFIG['last_trade_date'],
            exchange=CONFIG['exchange'],currency=CONFIG['currency'])
        self.ib.qualifyContracts(self.contract)
        log.info(f"Contract: {self.contract.localSymbol} conId={self.contract.conId}")
        if not self.gvz.update(): log.warning("GVZ取得失敗 filter=OFF")
        log.info("歴史データ取得中...")
        bars=self.ib.reqHistoricalData(self.contract,endDateTime='',durationStr=CONFIG['history_duration'],
            barSizeSetting=CONFIG['bar_size'],whatToShow='TRADES',useRTH=False,formatDate=1,keepUpToDate=False)
        log.info(f"歴史データ: {len(bars)}バー(1H)")
        if not bars: log.error("歴史データなし"); return False
        if not self.ewmac.initialize(bars): log.error("EWMAC init失敗"); return False
        self._sync_pos()
        log.info("バー購読開始...")
        self.live=self.ib.reqHistoricalData(self.contract,endDateTime='',durationStr='2 D',
            barSizeSetting=CONFIG['bar_size'],whatToShow='TRADES',useRTH=False,formatDate=1,keepUpToDate=True)
        self.live.updateEvent+=self._on_bar;self.ib.disconnectedEvent+=self._on_disc
        log.info("="*50);log.info("監視開始")
        log.info(f"  fc={self.ewmac.current_forecast:+.2f} ideal={self.ewmac.ideal_position:.2f} pos={self.state['position']}")
        log.info(f"  GVZ={self.gvz.gvz_value} z={self.gvz.gvz_zscore} {'ON' if self.gvz.is_active else 'OFF'}")
        log.info("="*50);return True

    def _sync_pos(self):
        for p in self.ib.positions():
            if p.contract.symbol==CONFIG['symbol']:
                ip=int(p.position)
                if ip!=self.state['position']:
                    log.warning(f"Pos mismatch: {self.state['position']}->{ip}");self.state['position']=ip;save_state(self.state,CONFIG['state_file'])
                return
        if self.state['position']!=0:
            log.warning("No IBKR pos->0");self.state['position']=0;save_state(self.state,CONFIG['state_file'])

    def _on_bar(self,bars,has_new):
        if not has_new: return
        b=bars[-1];bt=b.date if isinstance(b.date,datetime) else pd.to_datetime(str(b.date))
        if self._pending is None:
            self._pending=b;log.info(f"1H(1/2): {bt} C={b.close:.1f}");return
        p=self._pending
        b2={'datetime':bt,'open':p.open,'high':max(p.high,b.high),'low':min(p.low,b.low),
            'close':b.close,'volume':(p.volume or 0)+(b.volume or 0)}
        self._pending=None
        log.info(f"2H: {bt} O={b2['open']:.1f} H={b2['high']:.1f} L={b2['low']:.1f} C={b2['close']:.1f}")
        self.ewmac.add_bar(b2)
        if self.gvz.needs_update(): self.gvz.update()
        tgt=self.ewmac.get_target_position(self.state['position'],self.gvz.is_active)
        log.info(f"  fc={self.ewmac.current_forecast:+.2f} ideal={self.ewmac.ideal_position:.2f} tgt={tgt} pos={self.state['position']} GVZ={'ON' if self.gvz.is_active else 'OFF'}")
        if tgt!=self.state['position']: self._trade(tgt)

    def _trade(self,tgt):
        cur=self.state['position'];d=tgt-cur
        if d==0: return
        act='BUY' if d>0 else 'SELL';qty=abs(d)
        log.info("="*40);log.info(f"*** {act} {qty}枚 ({cur}->{tgt}) ***");log.info("="*40)
        # Phase 5-6で発注実装
        self.state['position']=tgt;self.state['last_signal']=f"{act}_{qty}"
        if d>0: self.state['entry_price']=self.live[-1].close
        save_state(self.state,CONFIG['state_file'])

    def _on_disc(self):
        log.warning("="*50);log.warning("切断!");log.warning("="*50);self._needs_reconnect=True

    def _reconn(self):
        try:
            try: self.ib.disconnect()
            except: pass
            self.ib=IB();self.ib.connect(CONFIG['host'],CONFIG['port'],clientId=CONFIG['client_id'],timeout=20)
            log.info("再接続OK")
            self.contract=Future(symbol=CONFIG['symbol'],lastTradeDateOrContractMonth=CONFIG['last_trade_date'],
                exchange=CONFIG['exchange'],currency=CONFIG['currency'])
            self.ib.qualifyContracts(self.contract);self._sync_pos();self.gvz.update()
            bars=self.ib.reqHistoricalData(self.contract,endDateTime='',durationStr=CONFIG['history_duration'],
                barSizeSetting=CONFIG['bar_size'],whatToShow='TRADES',useRTH=False,formatDate=1,keepUpToDate=False)
            if bars: self.ewmac=EWMACEngine(CONFIG);self.ewmac.initialize(bars)
            self.live=self.ib.reqHistoricalData(self.contract,endDateTime='',durationStr='2 D',
                barSizeSetting=CONFIG['bar_size'],whatToShow='TRADES',useRTH=False,formatDate=1,keepUpToDate=True)
            self.live.updateEvent+=self._on_bar;self.ib.disconnectedEvent+=self._on_disc
            self._needs_reconnect=False;self._pending=None;log.info("復元完了");return True
        except Exception as e: log.error(f"再接続失敗: {e}"); return False

    def run(self):
        w=10
        while True:
            try:
                if self._needs_reconnect:
                    log.info(f"再接続({w}s後)...");time.sleep(w)
                    if self._reconn(): w=10
                    else: w=min(w*2,300);continue
                self.ib.sleep(1)
            except KeyboardInterrupt: log.info("停止");break
            except Exception as e: log.error(f"Error: {e}");self._needs_reconnect=True

def main():
    eng=GoldEWMACRealtimeEngine()
    for i in range(1,31):
        try:
            if eng.connect(): break
            log.warning(f"init失敗 {i}/30");time.sleep(10)
        except Exception as e:
            log.warning(f"接続失敗({i}/30): {e}");time.sleep(10)
            if i==30: sys.exit(1)
            eng.ib=IB()
    eng.run()

if __name__=='__main__': main()
