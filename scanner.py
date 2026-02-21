"""
Evening Stock Scanner v3.0 - Full Feature Set
Features: Morning confirmation scan, Stop loss/target calculator,
Dollar volume filter, ADX trend strength, Multi-timeframe weekly check,
Gap fill history, Sector rotation, Backtest engine, ML score weighting,
Time-of-day optimization, Unusual options activity
"""

import os, json, time, math, requests
import yfinance as yf
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
RESULTS_FILE = "scan_results.json"
MORNING_FILE = "morning_golist.json"
HISTORY_FILE = "scan_history.json"
BACKTEST_FILE = "backtest_results.json"
GAP_HISTORY_FILE = "gap_history.json"
ML_MODEL_FILE = "ml_weights.json"
TIME_OPT_FILE = "time_optimization.json"

DEFAULT_WATCHLIST = [
    "AAPL","MSFT","NVDA","AMD","TSLA","META","GOOGL","AMZN",
    "NFLX","CRM","PLTR","SOFI","RIVN","LCID","NIO","BABA",
    "COIN","HOOD","MARA","RIOT","UPST","AFRM","SQ","PYPL",
    "SNAP","UBER","LYFT","ABNB","DASH","RBLX","SHOP","SPOT",
    "ZM","ROKU","TWLO","DDOG","NET","CRWD","OKTA","SNOW"
]

SECTOR_ETFS = {
    "XLK": ["AAPL","MSFT","NVDA","AMD","GOOGL","META","CRM","TWLO","DDOG","NET","CRWD","OKTA","SNOW","SHOP"],
    "XLF": ["COIN","HOOD","PYPL","SQ","AFRM","UPST"],
    "XLY": ["AMZN","TSLA","ABNB","DASH","UBER","LYFT","RBLX","SHOP"],
    "XLC": ["GOOGL","META","NFLX","SNAP","SPOT","ROKU","ZM"],
    "XME": ["MARA","RIOT"],
    "XBI": ["SOFI"],
    "XLI": ["RIVN","LCID","NIO","BABA"],
}

def safe_fetch(symbol, period="30d", interval="1d", prepost=False, retries=2):
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval, prepost=prepost)
            return ticker, df
        except Exception as e:
            print(f"  Retry {attempt+1} for {symbol}: {e}")
            time.sleep(1.5)
    return None, None

def load_json(fp, default=None):
    try:
        if os.path.exists(fp):
            with open(fp) as f:
                return json.load(f)
    except: pass
    return default if default is not None else {}

def save_json(fp, data):
    try:
        with open(fp,"w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving {fp}: {e}")

def get_spy_condition():
    try:
        _, df = safe_fetch("SPY", period="60d")
        if df is None or len(df) < 21: return "neutral", 0
        last = df['Close'].iloc[-1]; prev = df['Close'].iloc[-2]
        ma20 = df['Close'].tail(20).mean(); ma50 = df['Close'].tail(50).mean() if len(df)>=50 else ma20
        chg = ((last-prev)/prev)*100
        if chg > 0.5 and last > ma20 and last > ma50: return "bullish", 2
        elif chg > 0.2 and last > ma20: return "bullish", 1
        elif chg < -0.5 or (last < ma20 and last < ma50): return "bearish", -2
        elif chg < -0.2 or last < ma20: return "bearish", -1
        return "neutral", 0
    except: return "neutral", 0

def get_sector_rotation():
    perf = {}
    for etf in SECTOR_ETFS:
        try:
            _, df = safe_fetch(etf, period="30d")
            if df is None or len(df) < 20: continue
            p5 = ((df['Close'].iloc[-1]-df['Close'].iloc[-5])/df['Close'].iloc[-5])*100
            p20 = ((df['Close'].iloc[-1]-df['Close'].iloc[-20])/df['Close'].iloc[-20])*100
            perf[etf] = {"perf_5d":round(p5,2),"perf_20d":round(p20,2),"momentum":"hot" if p5>2 else "cold" if p5<-2 else "neutral"}
            time.sleep(0.3)
        except: continue
    return perf

def get_sector_score(symbol, rotation):
    for etf, stocks in SECTOR_ETFS.items():
        if symbol in stocks:
            m = rotation.get(etf,{}).get("momentum","neutral")
            return (1,etf) if m=="hot" else (-1,etf) if m=="cold" else (0,etf)
    return 0,"Unknown"

def calculate_adx(df, period=14):
    try:
        if len(df) < period+1: return 0
        h,l,c = df['High'],df['Low'],df['Close']
        pdm = h.diff(); mdm = l.diff().abs()
        pdm[pdm<0]=0; mdm[mdm<0]=0
        tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        pdi = 100*(pdm.rolling(period).mean()/atr)
        mdi = 100*(mdm.rolling(period).mean()/atr)
        dx = 100*((pdi-mdi).abs()/(pdi+mdi))
        adx = dx.rolling(period).mean()
        v = adx.iloc[-1]
        return round(float(v),1) if not pd.isna(v) else 0
    except: return 0

def check_weekly_trend(symbol):
    try:
        _, df = safe_fetch(symbol, period="1y", interval="1wk")
        if df is None or len(df)<10: return 0
        c = df['Close']; ma = c.tail(10).mean(); last = c.iloc[-1]
        wchg = ((last-c.iloc[-2])/c.iloc[-2])*100
        if last>ma and wchg>0: return 1
        elif last<ma and wchg<0: return -1
        return 0
    except: return 0

def calculate_gap_percent(df):
    try:
        if len(df)<2: return 0
        return round(((df['Open'].iloc[-1]-df['Close'].iloc[-2])/df['Close'].iloc[-2])*100,2)
    except: return 0

def calculate_relative_volume(df):
    try:
        if len(df)<6: return 1.0
        avg = df['Volume'].iloc[-6:-1].mean()
        if avg==0: return 1.0
        return round(df['Volume'].iloc[-1]/avg,2)
    except: return 1.0

def calculate_atr(df, period=7):
    try:
        r = df.tail(period); return float((r['High']-r['Low']).mean())
    except: return 0

def calculate_atr_percent(df):
    try:
        atr=calculate_atr(df); last=df['Close'].iloc[-1]
        return round((atr/last)*100,2)
    except: return 0

def calculate_dollar_volume(df):
    try: return float(df['Close'].iloc[-1]*df['Volume'].iloc[-1])
    except: return 0

def check_clean_technical_level(df):
    score=0
    try:
        c=df['Close']; last=c.iloc[-1]
        if last>=df['High'].tail(10).max()*0.98: score+=1
        if len(c)>=5 and last>c.tail(5).mean(): score+=1
        dh,dl=df['High'].iloc[-1],df['Low'].iloc[-1]; dr=dh-dl
        if dr>0 and (last-dl)/dr>=0.75: score+=1
    except: pass
    return score

def get_relative_strength(sdf, spy_df):
    try:
        if len(sdf)<5 or spy_df is None or len(spy_df)<5: return 0
        sc = ((sdf['Close'].iloc[-1]-sdf['Close'].iloc[-5])/sdf['Close'].iloc[-5])*100
        sp = ((spy_df['Close'].iloc[-1]-spy_df['Close'].iloc[-5])/spy_df['Close'].iloc[-5])*100
        rs = sc-sp
        if rs>3: return 2
        elif rs>1: return 1
        elif rs<-3: return -2
        elif rs<-1: return -1
        return 0
    except: return 0

def get_premarket_change(ticker_obj):
    try:
        df = ticker_obj.history(period="2d",prepost=True,interval="1h")
        if df is None or len(df)<2: return 0
        today=datetime.now().date()
        pm = df[(df.index.date==today)&(df.index.hour<9)]
        if pm.empty: return 0
        prev = df[df.index.date<today]['Close']
        if prev.empty: return 0
        return round(((pm['Close'].iloc[-1]-prev.iloc[-1])/prev.iloc[-1])*100,2)
    except: return 0

def get_float_score(ticker_obj):
    try:
        fs = ticker_obj.info.get('floatShares')
        if fs is None: return 0,None
        fm = fs/1_000_000
        if fm<20: return 2,round(fm,1)
        elif fm<50: return 1,round(fm,1)
        elif fm>500: return -1,round(fm,1)
        return 0,round(fm,1)
    except: return 0,None

def get_earnings_risk(ticker_obj):
    try:
        cal = ticker_obj.calendar
        if cal is None: return False,None
        if isinstance(cal,dict):
            ed = cal.get('Earnings Date')
            if ed is None: return False,None
            if hasattr(ed,'__iter__') and not isinstance(ed,str): ed=list(ed)[0]
        elif hasattr(cal,'loc'):
            try: ed=cal.loc['Earnings Date'].iloc[0]
            except: return False,None
        else: return False,None
        if hasattr(ed,'date'): ed=ed.date()
        days=(ed-datetime.now().date()).days
        return (True,days) if 0<=days<=3 else (False,days)
    except: return False,None

def get_risk_reward(df):
    try:
        last=df['Close'].iloc[-1]
        td=df['High'].tail(10).max()-last; sd=last-df['Low'].tail(10).min()
        if sd<=0: return None
        return round(td/sd,2)
    except: return None

def calculate_trade_levels(df):
    try:
        last=float(df['Close'].iloc[-1]); atr=calculate_atr(df)
        entry=round(last*1.002,2)
        stop=round(max(entry-(1.5*atr), float(df['Low'].tail(5).min())*0.99),2)
        risk=entry-stop
        return {
            "entry":entry,"stop":stop,
            "stop_pct":round(((entry-stop)/entry)*100,2),
            "target1":round(entry+risk,2),
            "target2":round(entry+2*risk,2),
            "target3":round(entry+3*risk,2),
            "resistance":round(float(df['High'].tail(10).max()),2),
            "atr":round(atr,2)
        }
    except: return None

def get_gap_fill_risk(symbol, gap_pct):
    h = load_json(GAP_HISTORY_FILE,{}).get(symbol,{})
    total=h.get("total",0)
    if total<5: return None,0
    fr=h["filled"]/total; cr=h.get("continued",0)/total
    if cr>0.7 and gap_pct>0: return fr,1
    elif fr>0.7 and gap_pct>0: return fr,-1
    return fr,0

def check_unusual_options(symbol):
    try:
        t=yf.Ticker(symbol); dates=t.options
        if not dates: return False,None
        chain=t.option_chain(dates[0])
        calls,puts=chain.calls,chain.puts
        if calls.empty or puts.empty: return False,None
        cv=calls['volume'].sum(); pv=puts['volume'].sum()
        coi=calls['openInterest'].sum(); poi=puts['openInterest'].sum()
        if coi==0: return False,None
        voi=(cv+pv)/(coi+poi) if (coi+poi)>0 else 0
        unusual = voi>0.5 and cv>pv*1.5
        return unusual,{"put_call_ratio":round(pv/cv if cv>0 else 0,2),"vol_oi_ratio":round(voi,2),"call_volume":int(cv),"put_volume":int(pv)}
    except: return False,None

def check_news_catalyst(symbol):
    if not NEWS_API_KEY: return False
    try:
        r=requests.get(f"https://newsapi.org/v2/everything?q={symbol}&sortBy=publishedAt&pageSize=3&apiKey={NEWS_API_KEY}",timeout=5)
        today=datetime.now().strftime("%Y-%m-%d")
        return any(a.get("publishedAt","").startswith(today) for a in r.json().get("articles",[]))
    except: return False

def train_ml_model():
    if not ML_AVAILABLE: return None
    history=load_json(HISTORY_FILE,[])
    picks=[p for e in history for p in e.get("picks",[]) if p.get("outcome") and p.get("features")]
    if len(picks)<20:
        print(f"Not enough data for ML ({len(picks)} picks, need 20+)")
        return None
    try:
        keys=["gap_pct","rvol","atr_pct","tech_score","adx","weekly_trend","float_score","rs_score","pm_change","spy_modifier","sector_score","rr_ratio"]
        X=[[p["features"].get(k,0) or 0 for k in keys] for p in picks]
        y=[1 if p["outcome"]=="win" else 0 for p in picks]
        sc=StandardScaler(); Xs=sc.fit_transform(X)
        m=LogisticRegression(max_iter=1000,random_state=42).fit(Xs,y)
        data={"weights":dict(zip(keys,m.coef_[0].tolist())),"trained_on":len(picks),"timestamp":datetime.now().isoformat(),"scaler_mean":sc.mean_.tolist(),"scaler_scale":sc.scale_.tolist(),"feature_keys":keys}
        save_json(ML_MODEL_FILE,data)
        print(f"ML trained on {len(picks)} picks")
        return data
    except Exception as e:
        print(f"ML error: {e}"); return None

def get_ml_adjustment(features):
    ml=load_json(ML_MODEL_FILE,None)
    if not ml or not ML_AVAILABLE: return 0
    try:
        keys=ml["feature_keys"]; w=ml["weights"]
        mean=np.array(ml["scaler_mean"]); scale=np.array(ml["scaler_scale"])
        row=np.array([features.get(k,0) or 0 for k in keys])
        scaled=(row-mean)/scale
        logit=sum(scaled[i]*w[keys[i]] for i in range(len(keys)))
        prob=1/(1+math.exp(-logit))
        return round((prob-0.5)*2,2)
    except: return 0

def get_best_trading_window():
    opt=load_json(TIME_OPT_FILE,{})
    if not opt: return "9:30-10:30 AM (collecting data)"
    bh=int(max(opt,key=opt.get).replace("hour_",""))
    eh=bh+1; p="AM" if bh<12 else "PM"; ep="AM" if eh<12 else "PM"
    h12=bh if bh<=12 else bh-12; e12=eh if eh<=12 else eh-12
    return f"{h12}:00-{e12}:00 {p}"

def run_backtest(symbols=None, days=60):
    print("Running backtest engine...")
    if symbols is None: symbols=DEFAULT_WATCHLIST[:10]
    res={"run_date":datetime.now().isoformat(),"total_signals":0,"winning_signals":0,"win_rate":0,"avg_gain":0,"avg_loss":0,"by_grade":{"A":{"wins":0,"total":0},"B":{"wins":0,"total":0},"C":{"wins":0,"total":0}}}
    gains,losses=[],[]
    _,spy_full=safe_fetch("SPY",period="6mo")
    for sym in symbols:
        print(f"  Backtesting {sym}...")
        time.sleep(0.5)
        _,df=safe_fetch(sym,period="6mo")
        if df is None or len(df)<30: continue
        for i in range(20,min(len(df)-1,days)):
            try:
                sl=df.iloc[:i+1]; last=float(sl['Close'].iloc[-1])
                dv=calculate_dollar_volume(sl)
                if dv<10_000_000: continue
                g=calculate_gap_percent(sl); r=calculate_relative_volume(sl)
                t=check_clean_technical_level(sl); adx=calculate_adx(sl)
                score=0
                if 2<=g<=8: score+=3
                elif 0.5<=g<2: score+=1
                if r>=2: score+=2
                elif r>=1.5: score+=1
                score+=t
                if adx>25: score+=1
                if last<5: score-=2
                n=round(min(10,max(0,(score/12)*10)),1)
                grade="A" if n>=8 else "B" if n>=6 else "C"
                if n<3: continue
                nxt=float(df['Close'].iloc[i+1]); pct=((nxt-last)/last)*100; won=pct>1
                res["total_signals"]+=1
                if won: res["winning_signals"]+=1; gains.append(pct)
                else: losses.append(pct)
                res["by_grade"][grade]["total"]+=1
                if won: res["by_grade"][grade]["wins"]+=1
            except: continue
    if res["total_signals"]>0:
        res["win_rate"]=round((res["winning_signals"]/res["total_signals"])*100,1)
    if gains: res["avg_gain"]=round(sum(gains)/len(gains),2)
    if losses: res["avg_loss"]=round(sum(losses)/len(losses),2)
    for g in ["A","B","C"]:
        d=res["by_grade"][g]
        d["win_rate"]=round((d["wins"]/d["total"])*100,1) if d["total"]>0 else 0
    save_json(BACKTEST_FILE,res)
    print(f"Backtest done. Win rate: {res['win_rate']}% on {res['total_signals']} signals")
    return res

def save_scan_to_history(results):
    try:
        h=load_json(HISTORY_FILE,[])
        entry={"date":datetime.now().strftime("%Y-%m-%d"),"picks":[{"symbol":r["symbol"],"score":r["score"],"grade":r["grade"],"last_close":r["last_close"],"features":r.get("features",{}),"outcome":None,"outcome_pct":None} for r in results]}
        h=[x for x in h if x["date"]!=entry["date"]]; h.append(entry); h=h[-90:]
        save_json(HISTORY_FILE,h)
    except Exception as e: print(f"History save error: {e}")

def update_outcomes():
    try:
        h=load_json(HISTORY_FILE,[])
        yest=(datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
        for entry in h:
            if entry["date"]==yest:
                for pick in entry["picks"]:
                    if pick["outcome"] is None:
                        try:
                            _,df=safe_fetch(pick["symbol"],period="5d")
                            if df is not None and len(df)>=1:
                                pct=round(((float(df['Close'].iloc[-1])-pick["last_close"])/pick["last_close"])*100,2)
                                pick["outcome"]="win" if pct>1 else "loss"; pick["outcome_pct"]=pct
                            time.sleep(0.3)
                        except: pass
        save_json(HISTORY_FILE,h)
    except Exception as e: print(f"Outcome update error: {e}")

def calculate_win_rate():
    try:
        h=load_json(HISTORY_FILE,[])
        picks=[p for e in h for p in e.get("picks",[]) if p.get("outcome")]
        if not picks: return None
        total=len(picks); wins=sum(1 for p in picks if p["outcome"]=="win")
        gs={}
        for g in ["A","B","C"]:
            gp=[p for p in picks if p["grade"]==g]
            if gp:
                gw=sum(1 for p in gp if p["outcome"]=="win")
                gs[g]={"win_rate":round((gw/len(gp))*100,1),"total":len(gp),"avg_gain":round(sum(p.get("outcome_pct",0) for p in gp if p["outcome"]=="win")/max(gw,1),2)}
        return {"overall_win_rate":round((wins/total)*100,1),"total_picks":total,"total_wins":wins,"by_grade":gs}
    except: return None

def run_morning_scan():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Morning confirmation scan...")
    last=load_json(RESULTS_FILE,{}); picks=last.get("results",[])
    if not picks: return {"golist":[],"timestamp":datetime.now().isoformat(),"message":"No evening scan results found"}
    golist=[]
    for pick in picks:
        sym=pick["symbol"]
        try:
            time.sleep(0.5)
            ticker,df=safe_fetch(sym,period="5d")
            if ticker is None or df is None: continue
            pm=get_premarket_change(ticker)
            if pm>0.3:
                tl=calculate_trade_levels(df)
                golist.append({"symbol":sym,"evening_score":pick["score"],"grade":pick["grade"],"prev_close":pick["last_close"],"pm_change":pm,"trade_levels":tl,"best_window":get_best_trading_window()})
        except Exception as e: print(f"  Morning error {sym}: {e}")
    golist.sort(key=lambda x:x["pm_change"],reverse=True)
    out={"timestamp":datetime.now().isoformat(),"golist":golist,"total_confirmed":len(golist),"best_window":get_best_trading_window()}
    save_json(MORNING_FILE,out)
    print(f"Morning scan done. {len(golist)} confirmed.")
    return out

def run_scanner():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] DayEdge v3.0 starting...")
    update_outcomes()
    train_ml_model()
    print("Checking SPY..."); spy_cond,spy_mod=get_spy_condition()
    print(f"  Market: {spy_cond} ({spy_mod:+d})")
    print("Sector rotation..."); rotation=get_sector_rotation()
    _,spy_df=safe_fetch("SPY",period="30d")
    results=[]

    for i,sym in enumerate(DEFAULT_WATCHLIST):
        try:
            print(f"[{i+1}/{len(DEFAULT_WATCHLIST)}] {sym}...")
            time.sleep(0.5)
            ticker,df=safe_fetch(sym,period="60d")
            if ticker is None or df is None or len(df)<10: continue
            last=float(df['Close'].iloc[-1]); dv=calculate_dollar_volume(df)
            if dv<5_000_000: continue

            features={
                "gap_pct":calculate_gap_percent(df),
                "rvol":calculate_relative_volume(df),
                "atr_pct":calculate_atr_percent(df),
                "tech_score":check_clean_technical_level(df),
                "adx":calculate_adx(df),
                "has_catalyst":check_news_catalyst(sym),
                "last_close":last,
                "spy_modifier":spy_mod,
                "sector_score":get_sector_score(sym,rotation)[0],
                "float_score":get_float_score(ticker)[0],
                "earnings_risky":get_earnings_risk(ticker)[0],
                "rs_score":get_relative_strength(df,spy_df),
                "pm_change":get_premarket_change(ticker),
                "rr_ratio":get_risk_reward(df),
                "weekly_trend":check_weekly_trend(sym),
                "dollar_vol":dv,
                "gap_fill_modifier":get_gap_fill_risk(sym,calculate_gap_percent(df))[1],
                "unusual_options":check_unusual_options(sym)[0],
            }

            float_score,float_m=get_float_score(ticker)
            earnings_risky,days_earn=get_earnings_risk(ticker)
            sector_score,sector_etf=get_sector_score(sym,rotation)
            unusual_options,options_detail=check_unusual_options(sym)
            gap_fill_prob,_=get_gap_fill_risk(sym,features["gap_pct"])
            trade_levels=calculate_trade_levels(df)
            ml_adj=get_ml_adjustment(features)

            score,reasons=score_stock_v3(features,ml_adj)

            if score>=3:
                results.append({
                    "symbol":sym,"score":score,"last_close":round(last,2),
                    "gap_pct":features["gap_pct"],"rvol":features["rvol"],
                    "atr_pct":features["atr_pct"],"adx":features["adx"],
                    "volume":int(df['Volume'].iloc[-1]),"dollar_vol_m":round(dv/1_000_000,1),
                    "pm_change":features["pm_change"],"float_m":float_m,
                    "sector_etf":sector_etf,"earnings_risky":earnings_risky,
                    "days_to_earnings":days_earn,"rs_score":features["rs_score"],
                    "rr_ratio":features["rr_ratio"],"weekly_trend":features["weekly_trend"],
                    "unusual_options":unusual_options,"options_detail":options_detail,
                    "gap_fill_prob":gap_fill_prob,"has_catalyst":features["has_catalyst"],
                    "tech_score":features["tech_score"],"trade_levels":trade_levels,
                    "ml_adjustment":ml_adj,"reasons":reasons,"features":features,
                    "grade":"A" if score>=8 else "B" if score>=6 else "C"
                })
        except Exception as e:
            print(f"  Error {sym}: {e}"); continue

    results.sort(key=lambda x:x['score'],reverse=True); results=results[:15]
    output={
        "timestamp":datetime.now().isoformat(),
        "market_date":(datetime.now()+timedelta(days=1)).strftime("%A, %B %d %Y"),
        "total_scanned":len(DEFAULT_WATCHLIST),"market_condition":spy_cond,
        "spy_modifier":spy_mod,"sector_rotation":rotation,
        "best_trading_window":get_best_trading_window(),
        "win_rate":calculate_win_rate(),"backtest":load_json(BACKTEST_FILE,None),
        "results":results
    }
    save_json(RESULTS_FILE,output); save_scan_to_history(results)
    print(f"Done. {len(results)} setups found. Market: {spy_cond}")
    return output

def score_stock_v3(features, ml_adj=0):
    score=0; reasons=[]
    g=features.get("gap_pct",0); rvol=features.get("rvol",1)
    atr=features.get("atr_pct",0); tech=features.get("tech_score",0)
    cat=features.get("has_catalyst",False); last=features.get("last_close",0)
    spy=features.get("spy_modifier",0); sec=features.get("sector_score",0)
    flt=features.get("float_score",0); earn=features.get("earnings_risky",False)
    rs=features.get("rs_score",0); pm=features.get("pm_change",0)
    rr=features.get("rr_ratio"); adx=features.get("adx",0)
    wt=features.get("weekly_trend",0); dv=features.get("dollar_vol",0)
    gfm=features.get("gap_fill_modifier",0); uo=features.get("unusual_options",False)

    if dv<10_000_000: return 0,["🔴 Dollar volume under $10M — excluded"]

    if 2<=g<=8: score+=3; reasons.append(f"✅ Ideal gap ({g}%)")
    elif 8<g<=15: score+=2; reasons.append(f"⚠️ Large gap ({g}%) — may be extended")
    elif 0.5<=g<2: score+=1; reasons.append(f"🔹 Small gap ({g}%)")
    elif g<-2: score-=1; reasons.append(f"🔴 Gapping down ({g}%)")

    score+=gfm
    if gfm>0: reasons.append("✅ Historically continues gaps")
    elif gfm<0: reasons.append("⚠️ Historically fills gaps — caution")

    if pm>2: score+=2; reasons.append(f"✅ Strong pre-market ({pm}%)")
    elif pm>0.5: score+=1; reasons.append(f"🔹 Pre-market positive ({pm}%)")
    elif pm<-1: score-=1; reasons.append(f"⚠️ Pre-market weak ({pm}%)")

    if rvol>=3: score+=3; reasons.append(f"✅ Very high RVOL ({rvol}x)")
    elif rvol>=2: score+=2; reasons.append(f"✅ High RVOL ({rvol}x)")
    elif rvol>=1.5: score+=1; reasons.append(f"🔹 Above avg RVOL ({rvol}x)")
    else: reasons.append(f"⚠️ Low RVOL ({rvol}x)")

    score+=tech
    if tech==3: reasons.append("✅ Strong technical setup")
    elif tech==2: reasons.append("🔹 Decent technical setup")
    elif tech==1: reasons.append("⚠️ Weak technical setup")

    if adx>30: score+=2; reasons.append(f"✅ Very strong trend (ADX {adx})")
    elif adx>25: score+=1; reasons.append(f"✅ Strong trend (ADX {adx})")
    elif adx<20: score-=1; reasons.append(f"⚠️ Choppy/weak trend (ADX {adx})")

    if wt==1: score+=2; reasons.append("✅ Weekly uptrend confirmed")
    elif wt==-1: score-=2; reasons.append("🔴 Fighting weekly downtrend")

    if uo: score+=1; reasons.append("✅ Unusual options activity")
    if cat: score+=1; reasons.append("✅ News catalyst detected")

    score+=spy
    if spy>0: reasons.append(f"✅ Market bullish (SPY)")
    elif spy<0: reasons.append(f"🔴 Market bearish (SPY) — caution")

    score+=sec
    if sec>0: reasons.append("✅ Sector strong")
    elif sec<0: reasons.append("⚠️ Sector weak")

    score+=flt
    if flt==2: reasons.append("✅ Low float — big move potential")
    elif flt==1: reasons.append("🔹 Moderate float")
    elif flt<0: reasons.append("⚠️ High float")

    score+=rs
    if rs>0: reasons.append("✅ Outperforming SPY")
    elif rs<0: reasons.append("⚠️ Underperforming SPY")

    if earn: score-=3; reasons.append("🔴 EARNINGS WITHIN 3 DAYS — high risk")
    if atr<1.5: score-=1; reasons.append(f"⚠️ Low volatility ({atr}% ATR)")
    elif atr>=3: reasons.append(f"✅ Good daily range ({atr}% ATR)")
    if last<5: score-=2; reasons.append("🔴 Price under $5")
    elif last>500: reasons.append("ℹ️ High price — size accordingly")
    if rr is not None:
        if rr>=2: score+=1; reasons.append(f"✅ Good R/R ({rr}:1)")
        elif rr<1: score-=1; reasons.append(f"⚠️ Poor R/R ({rr}:1)")

    if ml_adj!=0:
        pts=round(ml_adj*2); score+=pts
        reasons.append(f"🤖 ML {'boost' if pts>0 else 'caution'} ({'+' if pts>0 else ''}{pts}pts)")

    return round(min(10,max(0,(score/20)*10)),1), reasons

if __name__=="__main__":
    import sys
    if len(sys.argv)>1 and sys.argv[1]=="backtest": run_backtest()
    elif len(sys.argv)>1 and sys.argv[1]=="morning": run_morning_scan()
    else: run_scanner()
