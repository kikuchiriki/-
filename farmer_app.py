import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import re, json, io, hashlib
from pathlib import Path
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

# ═══ Supabase ═══
try:
    from supabase import create_client as _sb_create
    _HAS_SB = True
except ImportError:
    _HAS_SB = False

BUCKET = "farms"

def _farm_key(name):
    return hashlib.md5(str(name).encode("utf-8")).hexdigest()[:16]

@st.cache_resource
def _get_sb():
    if not _HAS_SB: return None
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return _sb_create(url, key)
    except Exception as e:
        st.error(f"Supabase接続エラー: {e}")
        return None

def _sb_list(prefix=""):
    sb = _get_sb()
    if not sb: return []
    try: return sb.storage.from_(BUCKET).list(prefix) or []
    except: return []

def _sb_dl(path):
    sb = _get_sb()
    if not sb: return None
    try: return sb.storage.from_(BUCKET).download(path)
    except: return None

@st.cache_data(ttl=30, show_spinner=False)
def _get_farm_index_cached(_ver):
    data = _sb_dl("farms_index.json")
    if data:
        try:
            parsed = json.loads(data.decode("utf-8"))
            if isinstance(parsed, dict): return parsed
        except: pass
    return {}

def _get_farm_index():
    return _get_farm_index_cached(st.session_state.get("ver", 0))

def verify_farm_key(farm_key):
    """URLのキーが有効な農場に対応しているか確認"""
    idx = _get_farm_index()
    return idx.get(farm_key), idx

@st.cache_data(ttl=60, show_spinner=False)
def _load_df_cached(farm_key, keywords_t, _ver):
    items = _sb_list(farm_key)
    for item in items:
        if item.get("id") is None: continue
        stem = item["name"].rsplit(".", 1)[0].lower()
        for kw in keywords_t:
            if kw in stem:
                data = _sb_dl(f"{farm_key}/{item['name']}")
                if data is None: return None, None
                bio = io.BytesIO(data)
                try:
                    if item["name"].lower().endswith((".xlsx", ".xls")):
                        df = pd.read_excel(bio)
                    else:
                        df = read_csv_auto(bio)
                    ts = None
                    ts_str = item.get("updated_at", "")
                    if ts_str:
                        try:
                            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            ts = dt.strftime("%m/%d %H:%M")
                        except: pass
                    return df, ts
                except: return None, None
    return None, None

def load_from_sb(farm_key, keywords):
    return _load_df_cached(farm_key, tuple(keywords), st.session_state.get("ver", 0))

# ═══ 定数・ユーティリティ ═══
st.set_page_config(page_title="カウフロー管理アプリ", layout="wide")

RC_DISP_MAP    = {0:"未経産",1:"フレッシュ",2:"フレッシュ",3:"空胎",4:"妊鑑待ち",5:"受胎",6:"乾乳"}
RC_DISP_ORDER  = ["未経産","フレッシュ","空胎","妊鑑待ち","受胎","乾乳"]
RC_DISP_COLORS = {"未経産":"#9b59b6","フレッシュ":"#e74c3c","空胎":"#f39c12","妊鑑待ち":"#3498db","受胎":"#2ecc71","乾乳":"#95a5a6"}
DIM_BINS   = [0,30,60,100,150,200,305,9999]
DIM_LABELS = ["~30日","31~60日","61~100日","101~150日","151~200日","201~305日","305日~"]
HEIFER_RC_CATS   = ["未授精","空胎","妊鑑待ち","受胎","乾乳"]
HEIFER_RC_COLORS = {"未授精":"#3498db","空胎":"#e74c3c","妊鑑待ち":"#f39c12","受胎":"#2ecc71","乾乳":"#7f8c8d"}
SEMEN_GROUPS = {
    "全カテゴリ":None,
    "ホルスタイン系（H判別＋H通常）":["H判別","H通常"],
    "F1系（F1授精＋F1移植）":["F1授精","F1移植"],
    "和牛系（登録卵＋無登録卵）":["和牛登録卵","和牛無登録卵"],
    "H判別のみ":["H判別"],"H通常のみ":["H通常"],
    "F1授精のみ":["F1授精"],"F1移植のみ":["F1移植"],
    "和牛登録卵のみ":["和牛登録卵"],
}
GESTATION_DAYS = 280

COL_MAP = {
    "id":         ["ID","id","牛番号","COWID"],
    "lact":       ["LACT","lact","産次","産次数"],
    "rc":         ["RC","rc","繁殖状況","RC_CODE"],
    "dim":        ["DIM","dim","泌乳日数"],
    "birthdate":  ["BIRTHDATE","birthdate","BDAT","生年月日","BIRTH","BirthDate"],
    "date":       ["DATE","Date","date","日付","EVENT_DATE"],
    "sire":       ["SIRE","sire","Remark","REMARK","STRAW","精液コード","BULL","Sire","B"],
    "preg":       ["R","PREG","preg","受胎結果","Preg"],
    "calfsex":    ["CALFSEX","calfsex","仔牛性別"],
    "event_type": ["Event","EVENT","Type","TYPE","WHY","Reason","REASON","Cod","COD","Code","CODE","Evnt"],
}

def find_col(df, key):
    for c in COL_MAP.get(key, [key]):
        if c in df.columns: return c
    return None

def classify_semen(c):
    if not isinstance(c, str): return "その他"
    c = c.strip().upper()
    if not c or c == "-": return "その他"
    if c.startswith("J") or "JE" in c: return "その他"
    if "IVF" in c or c.startswith("ET"): return "F1移植"
    if c.startswith("WA") or c == "WAGYU": return "和牛登録卵"
    if c.startswith("NR"): return "和牛無登録卵"
    if re.match(r'^\d{3,}H\d{3,}X?$', c): return "H判別"
    if re.match(r'^\d+H\d+X$', c): return "H判別"
    if re.match(r'^\d+H\d+$', c): return "H通常"
    if re.match(r'^[A-Z]', c): return "F1授精"
    return "その他"

def map_h_rc(x):
    return {0:"未授精",3:"空胎",4:"妊鑑待ち",5:"受胎",6:"乾乳"}.get(x,"空胎")

def map_preg(val):
    if pd.isna(val) or str(val).strip() == "": return None
    v = str(val).strip().upper()
    if v == "P": return 1
    if v == "O": return 0
    return None

def get_months(n=13):
    today = date.today()
    return [(today - relativedelta(months=i)).strftime("%Y/%m") for i in range(n-1, -1, -1)]

def parse_date_col(series):
    for fmt in ["%m/%d/%y", "%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%d"]:
        try: return pd.to_datetime(series, format=fmt)
        except: pass
    return pd.to_datetime(series, errors="coerce")

def read_csv_auto(f):
    for enc in ["utf-8", "cp932", "utf-8-sig"]:
        try:
            if hasattr(f, "seek"): f.seek(0)
            return pd.read_csv(f, encoding=enc)
        except: pass
    if hasattr(f, "seek"): f.seek(0)
    return pd.read_csv(f, encoding="utf-8", errors="replace")

def process_herd(df, min_age=0):
    if df is None: return None
    df = df.copy(); df.columns = [c.strip() for c in df.columns]
    lact_c=find_col(df,"lact"); rc_c=find_col(df,"rc"); bd_c=find_col(df,"birthdate"); dim_c=find_col(df,"dim")
    if lact_c:
        df["_lact"] = pd.to_numeric(df[lact_c], errors="coerce").fillna(0).astype(int)
        df = df[df["_lact"]<=20].copy()
    if rc_c:   df["_rc"] = pd.to_numeric(df[rc_c], errors="coerce").fillna(0).astype(int)
    if dim_c:  df["_dim"] = pd.to_numeric(df[dim_c], errors="coerce")
    if bd_c:
        df["_bd"] = parse_date_col(df[bd_c])
        df["_age_months"] = ((pd.Timestamp.today()-df["_bd"])/pd.Timedelta(days=30.44)).round(1)
    if min_age > 0 and "_age_months" in df.columns and "_lact" in df.columns:
        mask = (df["_lact"]==0)&(df["_age_months"]<min_age)
        if mask.sum() > 0: df = df[~mask].copy()
    if "_rc" in df.columns:
        df["_rc_disp"] = df["_rc"].map(RC_DISP_MAP)
    return df

def process_events(df):
    if df is None: return None
    df = df.copy(); df.columns = [c.strip() for c in df.columns]
    lact_c=find_col(df,"lact"); dt_c=find_col(df,"date"); et_c=find_col(df,"event_type")
    if lact_c:
        df["_lact"] = pd.to_numeric(df[lact_c], errors="coerce").fillna(0).astype(int)
        df = df[df["_lact"]<=20].copy()
    if dt_c:
        df["_date"] = parse_date_col(df[dt_c])
        df["_month"] = df["_date"].dt.strftime("%Y/%m")
    if et_c: df["_event_type"] = df[et_c].astype(str).str.strip().str.upper()
    return df

def process_bred(df):
    if df is None: return None
    df = df.copy(); df.columns = [c.strip() for c in df.columns]
    sire_c=find_col(df,"sire"); lact_c=find_col(df,"lact"); dt_c=find_col(df,"date")
    preg_c=find_col(df,"preg"); dim_c=find_col(df,"dim")
    if sire_c: df["_category"] = df[sire_c].apply(classify_semen)
    if dt_c:
        df["_date"] = parse_date_col(df[dt_c])
        df["_month"] = df["_date"].dt.strftime("%Y/%m")
    if lact_c:
        df["_lact"] = pd.to_numeric(df[lact_c], errors="coerce").fillna(0).astype(int)
        df = df[df["_lact"]<=20].copy()
        df["_group"] = df["_lact"].apply(lambda x: "未経産" if x==0 else "経産")
    if preg_c:
        df["_preg_raw"] = df[preg_c].astype(str).str.strip().str.upper()
        df["_preg"] = df[preg_c].apply(map_preg)
        df["_is_repeat"] = (df["_preg_raw"]=="R")
    if dim_c: df["_dim"] = pd.to_numeric(df[dim_c], errors="coerce")
    return df

def cr_monthly(bred_df, months, group=None):
    if bred_df is None or "_preg" not in bred_df.columns or "_month" not in bred_df.columns: return {}
    df = bred_df.copy()
    if "_is_repeat" in df.columns: df = df[~df["_is_repeat"]]
    df = df[df["_preg"].notna()]
    if group and "_group" in df.columns: df = df[df["_group"]==group]
    return {m: df[df["_month"]==m]["_preg"].mean()*100 for m in months if len(df[df["_month"]==m])>=3}

def cr_monthly_cat(bred_df, months, cat, group=None):
    if bred_df is None or "_preg" not in bred_df.columns or "_month" not in bred_df.columns: return {}
    if "_category" not in bred_df.columns: return {}
    df = bred_df[bred_df["_category"]==cat].copy()
    if df.empty: return {}
    if "_is_repeat" in df.columns: df = df[~df["_is_repeat"]]
    df = df[df["_preg"].notna()]
    if group and "_group" in df.columns: df = df[df["_group"]==group]
    return {m: df[df["_month"]==m]["_preg"].mean()*100 for m in months if len(df[df["_month"]==m])>=3}

def cr_avg(rd): return np.mean(list(rd.values())) if rd else None
def safe_div(a, b, fb=0): return a/b if b and b!=0 else fb

def calving_forecast(bred_df, n_months=12):
    if bred_df is None or "_preg" not in bred_df.columns or "_date" not in bred_df.columns:
        return [], None, None
    df = bred_df[bred_df["_preg"]==1].copy()
    df = df[df["_date"].notna()]
    if df.empty: return [], None, None
    id_c = find_col(df,"id")
    if id_c and id_c in df.columns:
        df = df.sort_values("_date").groupby(id_c).last().reset_index()
    df["_exp_calv"] = df["_date"] + pd.Timedelta(days=GESTATION_DAYS)
    df["_calv_month"] = df["_exp_calv"].dt.strftime("%Y/%m")
    fmonths = [(date.today()+relativedelta(months=i)).strftime("%Y/%m") for i in range(n_months)]
    df_f = df[df["_calv_month"].isin(fmonths)]
    total = df_f.groupby("_calv_month").size().reindex(fmonths, fill_value=0)
    if "_lact" in df_f.columns:
        h = df_f[df_f["_lact"]==0].groupby("_calv_month").size().reindex(fmonths, fill_value=0)
    else:
        h = pd.Series(0, index=fmonths)
    return fmonths, total, h

def month_cnt(df,m,lact_f=None,cat=None,group=None,preg=None,event_type=None):
    if df is None or "_month" not in df.columns: return 0
    sub = df[df["_month"]==m]
    if lact_f=="first" and "_lact" in sub.columns: sub=sub[sub["_lact"]==1]
    elif lact_f=="multi" and "_lact" in sub.columns: sub=sub[sub["_lact"]>1]
    if cat and "_category" in sub.columns: sub=sub[sub["_category"]==cat]
    if group and "_group" in sub.columns: sub=sub[sub["_group"]==group]
    if preg==1 and "_preg" in sub.columns: sub=sub[sub["_preg"]==1]
    if event_type and "_event_type" in sub.columns: sub=sub[sub["_event_type"]==event_type]
    return len(sub)

# ═══════════════════════════════════════
# URLクエリパラメータから農場キーを取得
# ═══════════════════════════════════════
params = st.query_params
farm_key = params.get("farm", "")

if not farm_key:
    st.error("URLが無効です。配布されたURLから再度アクセスしてください。")
    st.info("農場管理者に正しいURLを確認してください。")
    st.stop()

farm_name, farm_index = verify_farm_key(farm_key)

if farm_name is None:
    st.error("この農場キーは登録されていません。URLを確認してください。")
    st.info("農場管理者に正しいURLを確認してください。")
    st.stop()

# ─ データ読み込み ─
raw_herd_df,  herd_ts  = load_from_sb(farm_key, ["list","herd","牛群","リスト"])
fresh_df_raw, fresh_ts = load_from_sb(farm_key, ["fresh","分娩"])
died_df_raw,  died_ts  = load_from_sb(farm_key, ["died","cull","sold","除籍","死亡"])
bred_df_raw,  bred_ts  = load_from_sb(farm_key, ["bred","授精","insem"])
fresh_df = process_events(fresh_df_raw)
died_df  = process_events(died_df_raw)
bred_df  = process_bred(bred_df_raw)

min_heifer_age = 12
herd_df     = process_herd(raw_herd_df, min_heifer_age)
all_herd_df = process_herd(raw_herd_df, 0)

months = get_months(13); this_month = date.today().strftime("%Y/%m")
prev_month = months[-2]; same_month_ly = months[0]
n_cows    = int((herd_df["_lact"]>0).sum()) if herd_df is not None and "_lact" in herd_df.columns else 0
n_heifers = int((herd_df["_lact"]==0).sum()) if herd_df is not None and "_lact" in herd_df.columns else 0
cr_all    = cr_monthly(bred_df,months); cr_heifer=cr_monthly(bred_df,months,"未経産"); cr_cow=cr_monthly(bred_df,months,"経産")
cr_h_all  = cr_monthly_cat(bred_df,months,"H判別"); cr_h_heifer=cr_monthly_cat(bred_df,months,"H判別","未経産"); cr_h_cow=cr_monthly_cat(bred_df,months,"H判別","経産")
avg_cr_all=cr_avg(cr_all); avg_cr_h_all=cr_avg(cr_h_all); avg_cr_h_h=cr_avg(cr_h_heifer); avg_cr_h_c=cr_avg(cr_h_cow)
cr_fallback = avg_cr_h_all or avg_cr_all or 50

ts_parts = []
for lbl,ts in [("牛群",herd_ts),("分娩",fresh_ts),("除籍",died_ts),("授精",bred_ts)]:
    if ts: ts_parts.append(f"{lbl}:{ts}")
ts_str = " / ".join(ts_parts)

# ─ ヘッダー（農場名表示・他農場への言及なし） ─
st.markdown(
    f"<div style='display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;"
    f"position:sticky;top:0;z-index:200;background:white;padding:8px 0 4px 0;"
    f"box-shadow:0 1px 4px rgba(0,0,0,.07)'>"
    f"<span style='font-size:1.5rem;font-weight:700'>カウフロー管理　{farm_name}</span>"
    f"<span style='font-size:0.78rem;color:#999;margin-left:auto'>更新: {ts_str if ts_str else '---'}</span>"
    f"</div>", unsafe_allow_html=True)

if raw_herd_df is None and fresh_df is None and died_df is None and bred_df is None:
    st.warning("データがまだ登録されていません。農場管理者にお問い合わせください。")
    st.stop()

# ─ サイドバー（閲覧のみ：農場選択なし） ─
st.sidebar.markdown(f"### {farm_name}")
st.sidebar.markdown("---")
st.sidebar.markdown("**データ更新状況**")
for lbl,ts in [("牛群リスト",herd_ts),("分娩記録",fresh_ts),("死亡・除籍",died_ts),("授精記録",bred_ts)]:
    icon = "✅" if ts else "—"
    st.sidebar.markdown(f"{icon} {lbl}" + (f"  `{ts}`" if ts else ""))
st.sidebar.markdown("---")
st.sidebar.caption("このページはあなたの農場専用です。")

TABS = st.tabs(["牛群構成","カウフロー","H判別目標","育成牛管理","精液別分析"])

# ═══════════════════════════════════════
# TAB 1: 牛群構成
# ═══════════════════════════════════════
with TABS[0]:
    st.subheader("牛群構成")
    if herd_df is None:
        st.info("牛群リストがまだ登録されていません。")
    else:
        total=len(herd_df); st.metric("総頭数",f"{total} 頭")
        col1,col2=st.columns(2)
        if "_rc_disp" in herd_df.columns:
            with col1:
                rc_cnt=herd_df["_rc_disp"].value_counts()
                xs=[r for r in RC_DISP_ORDER if r in rc_cnt.index]
                ys=[int(rc_cnt[r]) for r in xs]; cs=[RC_DISP_COLORS[r] for r in xs]
                fig_rc=go.Figure(go.Bar(x=xs,y=ys,marker_color=cs,text=ys,textposition="outside"))
                fig_rc.update_layout(title="繁殖状況別頭数",height=300,showlegend=False,yaxis_title="頭数",margin=dict(t=40,b=5))
                st.plotly_chart(fig_rc,use_container_width=True)
        if "_lact" in herd_df.columns:
            with col2:
                lc=herd_df["_lact"].value_counts().sort_index()
                lbl_map={k:("未経産" if k==0 else f"{k}産") for k in lc.index}
                xs2=[lbl_map[k] for k in lc.index]; ys2=list(lc.values)
                fig_lc=go.Figure(go.Bar(x=xs2,y=ys2,marker_color="#3498db",text=ys2,textposition="outside"))
                fig_lc.update_layout(title="産次別頭数",height=300,showlegend=False,yaxis_title="頭数",margin=dict(t=40,b=5))
                st.plotly_chart(fig_lc,use_container_width=True)
        if "_dim" in herd_df.columns:
            st.markdown("---")
            cows_only=herd_df[herd_df["_lact"]>0] if "_lact" in herd_df.columns else herd_df
            cd=cows_only[(cows_only["_dim"].notna())&(cows_only["_dim"]>0)]
            if len(cd)>0:
                avg_dim=cd["_dim"].mean()
                dm1,dm2=st.columns(2)
                dm1.metric("経産牛 平均DIM",f"{avg_dim:.0f} 日")
                dm2.metric("経産牛（DIMあり）",f"{len(cd)} 頭")
                cd2=cd.copy()
                cd2["_dim_grp"]=pd.cut(cd2["_dim"],bins=DIM_BINS,labels=DIM_LABELS,right=True)
                if "_rc_disp" in cd2.columns:
                    pivot=cd2.groupby(["_dim_grp","_rc_disp"],observed=True).size().unstack(fill_value=0)
                    fig_dim=go.Figure()
                    for rc_lbl in RC_DISP_ORDER:
                        if rc_lbl not in pivot.columns: continue
                        vals=pivot[rc_lbl].reindex(DIM_LABELS,fill_value=0)
                        fig_dim.add_trace(go.Bar(x=DIM_LABELS,y=vals,name=rc_lbl,marker_color=RC_DISP_COLORS[rc_lbl],
                            text=[str(v) if v>0 else "" for v in vals],textposition="inside",insidetextanchor="middle"))
                    fig_dim.update_layout(barmode="stack",title="分娩後日数（DIM）別 繁殖状況内訳",
                        height=320,yaxis_title="頭数",legend=dict(orientation="h",y=-0.25,x=0),margin=dict(t=40,b=10))
                    st.plotly_chart(fig_dim,use_container_width=True)

# ═══════════════════════════════════════
# TAB 2: カウフロー
# ═══════════════════════════════════════
with TABS[1]:
    st.subheader("カウフロー")
    yr_first_cnt = sum(month_cnt(fresh_df,m,"first") for m in months)
    yr_calv_cnt  = sum(month_cnt(fresh_df,m) for m in months)
    yr_died_cnt  = sum(month_cnt(died_df,m) for m in months)
    actual_renewal = safe_div(yr_first_cnt,n_cows,0)*100
    actual_culling = safe_div(yr_died_cnt, n_cows,0)*100
    diff_pct = actual_renewal - actual_culling
    monthly_chg = n_cows*diff_pct/100/12; annual_chg = n_cows*diff_pct/100

    rv1,rv2,rv3,rv4 = st.columns(4)
    rv1.metric("経産牛頭数",f"{n_cows} 頭")
    rv2.metric("過去13ヶ月 分娩数",f"{yr_calv_cnt} 頭")
    rv3.metric("実績更新率",f"{actual_renewal:.1f}%")
    rv4.metric("実績淘汰率",f"{actual_culling:.1f}%")

    if diff_pct>=3:    pace_icon="増頭"; pace_col="#27ae60"
    elif diff_pct<=-3: pace_icon="減少"; pace_col="#e74c3c"
    else:              pace_icon="維持"; pace_col="#2980b9"
    st.markdown(
        f"<div style='background:{pace_col}18;border-left:4px solid {pace_col};padding:8px 14px;border-radius:4px'>"
        f"<b style='color:{pace_col}'>{pace_icon}ペース</b>: "
        f"月間 <b>{monthly_chg:+.1f}頭</b> / 年間 <b>{annual_chg:+.0f}頭</b></div>",
        unsafe_allow_html=True)

    st.markdown("---")
    if fresh_df is None and died_df is None:
        st.info("分娩記録または死亡・除籍記録がまだ登録されていません。")
    else:
        first_c = [month_cnt(fresh_df,m,"first") for m in months]
        died_c  = [month_cnt(died_df,m) for m in months]
        multi_c = [month_cnt(fresh_df,m,"multi") for m in months]
        net     = [f+mu-d for f,mu,d in zip(first_c,multi_c,died_c)]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=months,y=first_c,name="初産分娩",marker_color="#27ae60",
            text=[str(v) if v>0 else "" for v in first_c],textposition="inside",insidetextanchor="middle"))
        fig.add_trace(go.Bar(x=months,y=[-d for d in died_c],name="死亡・除籍",marker_color="#e74c3c",
            text=[str(v) if v>0 else "" for v in died_c],textposition="inside",insidetextanchor="middle"))
        fig.add_trace(go.Scatter(x=months,y=net,name="純増減",mode="lines+markers",
            line=dict(color="#2c3e50",width=2),marker=dict(size=7)))
        fig.update_layout(barmode="relative",height=400,yaxis_title="頭数",
            legend=dict(orientation="h",y=-0.18,x=0),margin=dict(t=20,b=10))
        st.plotly_chart(fig,use_container_width=True)

        if fresh_df is not None and "_lact" in fresh_df.columns:
            st.markdown("---")
            st.markdown("#### 産次別分娩頭数（過去13ヶ月）")
            fr12 = fresh_df[fresh_df["_month"].isin(months)].copy()
            fr12["_lg"] = fr12["_lact"].apply(
                lambda x: "初産（1産）" if x==1 else "2産" if x==2 else "3産以上" if x>=3 else "その他")
            go_l = ["初産（1産）","2産","3産以上","その他"]
            gc_l = {"初産（1産）":"#27ae60","2産":"#2980b9","3産以上":"#8e44ad","その他":"#95a5a6"}
            pvt = fr12.groupby(["_month","_lg"],observed=True).size().unstack(fill_value=0)
            fig_l = go.Figure()
            for g in go_l:
                if g not in pvt.columns: continue
                vs = pvt[g].reindex(months,fill_value=0).tolist()
                fig_l.add_trace(go.Bar(x=months,y=vs,name=g,marker_color=gc_l[g],
                    text=[str(v) if v>0 else "" for v in vs],textposition="inside",insidetextanchor="middle"))
            fig_l.update_layout(barmode="stack",height=300,yaxis_title="頭数",
                legend=dict(orientation="h",y=-0.22,x=0),margin=dict(t=10,b=10))
            st.plotly_chart(fig_l,use_container_width=True)

    st.markdown("---")
    st.markdown("#### 今後12ヶ月の分娩予測")
    fmonths,fc_total,fc_heifer = calving_forecast(bred_df,n_months=12)
    if not fmonths:
        st.info("受胎確認済みの授精記録があれば分娩予測を表示します。")
    else:
        fc_cow_vals = [int(fc_total[m])-int(fc_heifer[m]) for m in fmonths]
        fc_h_vals   = [int(fc_heifer[m]) for m in fmonths]
        fig_fc = go.Figure()
        fig_fc.add_trace(go.Bar(x=fmonths,y=fc_h_vals,name="初産分娩予測",marker_color="#27ae60"))
        fig_fc.add_trace(go.Bar(x=fmonths,y=fc_cow_vals,name="経産分娩予測",marker_color="#2980b9"))
        fig_fc.update_layout(barmode="stack",height=300,yaxis_title="頭数",
            legend=dict(orientation="h",y=-0.22,x=0),margin=dict(t=10,b=10))
        st.plotly_chart(fig_fc,use_container_width=True)
        tbl_fc = pd.DataFrame({"月":fmonths,"経産分娩":fc_cow_vals,"初産分娩":fc_h_vals,
            "合計":[int(fc_total[m]) for m in fmonths]}).set_index("月")
        st.dataframe(tbl_fc.T,use_container_width=True)
        st.caption(f"※ 受胎確認(P)日から妊娠期間{GESTATION_DAYS}日加算の推計。")

# ═══════════════════════════════════════
# TAB 3: H判別目標
# ═══════════════════════════════════════
with TABS[2]:
    st.subheader("H判別目標管理")
    m_h_preg = 3.0; m_h_ai = 6.0; m_culling = 3.0
    act_culling  = month_cnt(died_df,this_month)
    act_first    = month_cnt(fresh_df,this_month,"first")
    act_h_ai     = month_cnt(bred_df,this_month,cat="H判別")
    act_h_preg   = month_cnt(bred_df,this_month,cat="H判別",preg=1)
    st.info("詳細な目標設定は管理者版アプリで行います。ここでは今月の実績をご確認いただけます。")
    mc1,mc2,mc3,mc4 = st.columns(4)
    mc1.metric("H判別授精（今月）",f"{act_h_ai} 頭")
    mc2.metric("H判別受胎（今月）",f"{act_h_preg} 頭")
    mc3.metric("淘汰（今月）",f"{act_culling} 頭")
    mc4.metric("初産分娩（今月）",f"{act_first} 頭")
    st.markdown("---")
    if bred_df is not None and "_category" in bred_df.columns and "_month" in bred_df.columns:
        st.markdown("#### H判別授精・受胎推移（過去13ヶ月）")
        df_h = bred_df[bred_df["_category"]=="H判別"]
        ai_cnt  = [len(df_h[df_h["_month"]==m]) for m in months]
        preg_df = df_h[~df_h["_is_repeat"]] if "_is_repeat" in df_h.columns else df_h.copy()
        preg_cnt= [int(preg_df[(preg_df["_month"]==m)&(preg_df["_preg"]==1)].shape[0]) if "_preg" in preg_df.columns else 0 for m in months]
        cr_vals = [cr_h_all.get(m) for m in months]
        cr_text = [f"{v:.1f}%" if v is not None else "" for v in cr_vals]
        fig=go.Figure()
        fig.add_trace(go.Bar(x=months,y=ai_cnt,name="H判別授精数",marker_color="#9b59b6",
            text=[str(v) if v>0 else "" for v in ai_cnt],textposition="outside"))
        fig.add_trace(go.Bar(x=months,y=preg_cnt,name="H判別受胎数",marker_color="#2ecc71",
            text=[str(v) if v>0 else "" for v in preg_cnt],textposition="inside",insidetextanchor="middle"))
        fig.add_trace(go.Scatter(x=months,y=cr_vals,name="受胎率(%)",mode="lines+markers+text",
            text=cr_text,textposition="top center",textfont=dict(size=10,color="#e67e22"),
            line=dict(color="#e67e22",width=2),marker=dict(size=7),yaxis="y2",connectgaps=False))
        fig.update_layout(barmode="overlay",height=380,
            yaxis=dict(title="頭数"),yaxis2=dict(title="受胎率(%)",overlaying="y",side="right",range=[0,100],showgrid=False),
            legend=dict(orientation="h",y=-0.22,x=0),margin=dict(t=20,b=10,r=70))
        st.plotly_chart(fig,use_container_width=True)

# ═══════════════════════════════════════
# TAB 4: 育成牛管理
# ═══════════════════════════════════════
with TABS[3]:
    st.subheader("育成牛管理")
    if herd_df is None:
        st.info("牛群リストがまだ登録されていません。")
    else:
        heifers=herd_df[herd_df["_lact"]==0].copy() if "_lact" in herd_df.columns else herd_df.copy()
        n_h=len(heifers); st.metric(f"未経産頭数（{min_heifer_age}ヶ月齢以上）",f"{n_h} 頭")
        age_bin_labels=["12~14月","15~17月","18~20月","21~23月","24月~"]; age_bins=[0,15,18,21,24,9999]
        if "_age_months" in heifers.columns:
            heifers["_age_grp"]=pd.cut(heifers["_age_months"].astype(float),bins=age_bins,labels=age_bin_labels,right=False)
            if "_rc" in heifers.columns:
                heifers["_rc_label"]=heifers["_rc"].apply(map_h_rc)
                st.markdown("#### 月齢別 × 繁殖状況")
                pivot_rc=heifers.groupby(["_age_grp","_rc_label"],observed=True).size().unstack(fill_value=0)
                for col in HEIFER_RC_CATS:
                    if col not in pivot_rc.columns: pivot_rc[col]=0
                pivot_rc=pivot_rc.reindex(columns=HEIFER_RC_CATS)
                fig1=go.Figure()
                for lab in HEIFER_RC_CATS:
                    vals=pivot_rc[lab].reindex(age_bin_labels,fill_value=0)
                    fig1.add_trace(go.Bar(x=age_bin_labels,y=vals,name=lab,marker_color=HEIFER_RC_COLORS[lab],
                        text=[str(v) if v>0 else "" for v in vals],textposition="inside",insidetextanchor="middle"))
                fig1.update_layout(barmode="stack",height=320,yaxis_title="頭数",legend=dict(orientation="h",y=-0.2))
                st.plotly_chart(fig1,use_container_width=True)

            st.markdown("---")
            id_c2=find_col(heifers,"id"); bd_c2=find_col(heifers,"birthdate")
            if bred_df is not None and "_lact" in bred_df.columns and id_c2:
                bred_id_c=find_col(bred_df,"id")
                if bred_id_c:
                    bh=bred_df[bred_df["_lact"]==0][[bred_id_c]].copy()
                    ai_cnt_h=bh.groupby(bred_id_c).size().reset_index(name="授精回数")
                    merged_h=heifers.merge(ai_cnt_h,left_on=id_c2,right_on=bred_id_c,how="inner")
                    st.markdown(f"#### 繁殖中育成牛（授精済み）: {len(merged_h)}頭")
                    if len(merged_h)>0:
                        show2=[c for c in [id_c2,bd_c2,"_age_months","_rc_label","授精回数"] if c and c in merged_h.columns]
                        disp_h=merged_h[show2].rename(columns={id_c2:"牛番号",bd_c2:"出生年月日","_age_months":"月齢","_rc_label":"繁殖状況"})
                        if "月齢" in disp_h.columns: disp_h=disp_h.sort_values("月齢",ascending=False)
                        st.dataframe(disp_h,use_container_width=True)

# ═══════════════════════════════════════
# TAB 5: 精液別分析
# ═══════════════════════════════════════
with TABS[4]:
    st.subheader("精液別受胎率分析")
    if bred_df is None:
        st.info("授精記録がまだ登録されていません。")
    else:
        sire_c2=find_col(bred_df,"sire")
        if sire_c2 is None:
            st.warning("精液コード列が見つかりません。")
        else:
            cat_opts=list(SEMEN_GROUPS.keys())
            sel_cat=st.selectbox("精液種別フィルター",cat_opts,key="sire_cat")
            dp=bred_df.copy()
            if "_is_repeat" in dp.columns: dp=dp[~dp["_is_repeat"]]
            cats=SEMEN_GROUPS[sel_cat]
            if cats and "_category" in dp.columns: dp=dp[dp["_category"].isin(cats)]
            if "_preg" not in dp.columns or dp["_preg"].notna().sum()==0:
                st.info("受胎結果列（R）がないため授精頭数のみ表示します。")
            else:
                dp2=dp[dp["_preg"].notna()]
                grp=dp2.groupby(sire_c2).agg(授精頭数=(sire_c2,"count"),受胎頭数=("_preg","sum")).reset_index()
                grp["受胎率"]=grp["受胎頭数"]/grp["授精頭数"]*100
                grp=grp[grp["授精頭数"]>=5].sort_values("受胎率",ascending=False)
                if len(grp)==0:
                    st.info("5頭以上の精液コードがありません。")
                else:
                    cs2=[("#e74c3c" if r<30 else "#f39c12" if r<40 else "#2ecc71") for r in grp["受胎率"]]
                    fig=go.Figure(go.Bar(x=grp["受胎率"].tolist(),y=grp[sire_c2].tolist(),orientation="h",
                        marker_color=cs2,text=(grp["受胎率"].round(1).astype(str)+"%").tolist(),textposition="outside"))
                    fig.add_vline(x=30,line_dash="dash",line_color="red")
                    fig.add_vline(x=40,line_dash="dash",line_color="orange")
                    fig.update_layout(title=f"精液別受胎率（5頭以上・{sel_cat}）",
                        height=max(300,len(grp)*22+100),xaxis_title="受胎率(%)",
                        yaxis=dict(autorange="reversed"),margin=dict(r=60))
                    st.plotly_chart(fig,use_container_width=True)
                    st.dataframe(grp.rename(columns={sire_c2:"精液コード"}).style.format({"受胎率":"{:.1f}%","授精頭数":"{:.0f}","受胎頭数":"{:.0f}"}),use_container_width=True)
