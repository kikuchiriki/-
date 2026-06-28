import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import re, json, io
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
FARMS_INDEX = "farms_index.json"

import hashlib as _hashlib

def _farm_key(name):
    """農場名をASCII安全なMD5ハッシュキーに変換"""
    return _hashlib.md5(str(name).encode("utf-8")).hexdigest()[:16]

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
    sb=_get_sb()
    if not sb: return []
    try: return sb.storage.from_(BUCKET).list(prefix) or []
    except Exception as e:
        st.sidebar.warning(f"一覧取得エラー: {e}")
        return []

def _sb_dl(path):
    sb=_get_sb()
    if not sb: return None
    try: return sb.storage.from_(BUCKET).download(path)
    except: return None

def _sb_ul(path, data, mime="text/csv"):
    sb=_get_sb()
    if not sb:
        st.error("Supabase未接続。SecretsにAPIキーが設定されているか確認してください。")
        return False
    try:
        try: sb.storage.from_(BUCKET).remove([path])
        except: pass
        sb.storage.from_(BUCKET).upload(path, data, {"content_type": mime, "upsert": "true"})
        return True
    except Exception as e:
        st.error(f"保存エラー: {e}")
        return False

def _sb_rm(paths):
    sb=_get_sb()
    if not sb or not paths: return
    try: sb.storage.from_(BUCKET).remove(paths)
    except: pass

# ─ 農場インデックス管理（farms_index.json: {hash: 表示名}） ─
@st.cache_data(ttl=30, show_spinner=False)
def _get_farm_index_cached(_ver):
    data=_sb_dl(FARMS_INDEX)
    if data:
        try: return json.loads(data.decode("utf-8"))
        except: pass
    return {}

def _get_farm_index():
    return _get_farm_index_cached(st.session_state.get("upload_ver",0))

def get_farm_list():
    return sorted(_get_farm_index().values())

def _add_farm(farm_name):
    idx=_get_farm_index()
    k=_farm_key(farm_name)
    if k not in idx:
        idx[k]=farm_name
        _sb_ul(FARMS_INDEX, json.dumps(idx,ensure_ascii=False).encode("utf-8"),"application/json")

def delete_farm(farm_name):
    idx=_get_farm_index()
    k=_farm_key(farm_name)
    if k in idx: del idx[k]
    _sb_ul(FARMS_INDEX, json.dumps(idx,ensure_ascii=False).encode("utf-8"),"application/json")
    items=_sb_list(k)
    paths=[f"{k}/{i['name']}" for i in items if i.get("id")]
    _sb_rm(paths)

@st.cache_data(ttl=60, show_spinner=False)
def _load_df_cached(farm_name, keywords_t, _ver):
    k=_farm_key(farm_name)
    items=_sb_list(k)
    for item in items:
        if item.get("id") is None: continue
        stem=item["name"].rsplit(".",1)[0].lower()
        for kw in keywords_t:
            if kw in stem:
                data=_sb_dl(f"{k}/{item['name']}")
                if data is None: return None, None
                bio=io.BytesIO(data)
                try:
                    if item["name"].lower().endswith((".xlsx",".xls")):
                        df=pd.read_excel(bio)
                    else:
                        df=read_csv_auto(bio)
                    ts=None
                    ts_str=item.get("updated_at","")
                    if ts_str:
                        try:
                            dt=datetime.fromisoformat(ts_str.replace("Z","+00:00"))
                            ts=dt.strftime("%m/%d %H:%M")
                        except: pass
                    return df, ts
                except: return None, None
    return None, None

def load_from_sb(farm_name, keywords):
    ver=st.session_state.get("upload_ver",0)
    return _load_df_cached(farm_name, tuple(keywords), ver)

def save_farm_file(uf, farm_name, label):
    uf.seek(0)
    ext=Path(uf.name).suffix.lower()
    k=_farm_key(farm_name)
    path=f"{k}/{label}{ext}"
    mime=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          if ext in [".xlsx",".xls"] else "text/csv")
    return _sb_ul(path, uf.read(), mime)

@st.cache_data(ttl=30, show_spinner=False)
def _load_s_cached(_ver):
    data=_sb_dl("settings.json")
    if data:
        try: return json.loads(data.decode("utf-8"))
        except: pass
    return {}

def load_s():
    ver=st.session_state.get("upload_ver",0)
    return _load_s_cached(ver)

def save_s(d):
    _sb_ul("settings.json", json.dumps(d,ensure_ascii=False).encode("utf-8"), "application/json")

# ═══ 定数 ═══
st.set_page_config(page_title="カウフロー管理アプリ", layout="wide")
st.markdown("""
<style>
.stTabs [data-baseweb="tab-list"] {
    position: sticky; top: 3.2rem; z-index: 100;
    background-color: white; padding-top: 4px;
    box-shadow: 0 2px 4px rgba(0,0,0,.08);
}
</style>
""", unsafe_allow_html=True)

RC_LABELS = {0:"未経産",1:"フレッシュ",2:"フレッシュOK",3:"空胎",4:"妊鑑待ち",5:"受胎",6:"乾乳"}
RC_COLORS  = {0:"#9b59b6",1:"#e74c3c",2:"#e67e22",3:"#f39c12",4:"#3498db",5:"#2ecc71",6:"#95a5a6"}
RC_DISP_MAP    = {0:"未経産",1:"フレッシュ",2:"フレッシュ",3:"空胎",4:"妊鑑待ち",5:"受胎",6:"乾乳"}
RC_DISP_ORDER  = ["未経産","フレッシュ","空胎","妊鑑待ち","受胎","乾乳"]
RC_DISP_COLORS = {"未経産":"#9b59b6","フレッシュ":"#e74c3c","空胎":"#f39c12","妊鑑待ち":"#3498db","受胎":"#2ecc71","乾乳":"#95a5a6"}
DIM_BINS   = [0,30,60,100,150,200,305,9999]
DIM_LABELS = ["〜30日","31〜60日","61〜100日","101〜150日","151〜200日","201〜305日","305日〜"]
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

def find_col(df,key):
    for c in COL_MAP.get(key,[key]):
        if c in df.columns: return c
    return None

def classify_semen(c):
    if not isinstance(c,str): return "その他"
    c=c.strip().upper()
    if not c or c=="-": return "その他"
    if c.startswith("J") or "JE" in c: return "その他"
    if "IVF" in c or c.startswith("ET"): return "F1移植"
    if c.startswith("WA") or c=="WAGYU": return "和牛登録卵"
    if c.startswith("NR"): return "和牛無登録卵"
    if re.match(r'^\d{3,}H\d{3,}X?$',c): return "H判別"
    if re.match(r'^\d+H\d+X$',c): return "H判別"
    if re.match(r'^\d+H\d+$',c): return "H通常"
    if re.match(r'^[A-Z]',c): return "F1授精"
    return "その他"

def map_h_rc(x):
    return {0:"未授精",3:"空胎",4:"妊鑑待ち",5:"受胎",6:"乾乳"}.get(x,"空胎")

def map_preg(val):
    if pd.isna(val) or str(val).strip()=="": return None
    v=str(val).strip().upper()
    if v=="P": return 1
    if v=="O": return 0
    return None

def get_months(n=13):
    today=date.today()
    return [(today-relativedelta(months=i)).strftime("%Y/%m") for i in range(n-1,-1,-1)]

def parse_date_col(series):
    for fmt in ["%m/%d/%y","%m/%d/%Y","%Y/%m/%d","%Y-%m-%d"]:
        try: return pd.to_datetime(series,format=fmt)
        except: pass
    return pd.to_datetime(series,errors="coerce")

def read_csv_auto(f):
    for enc in ["utf-8","cp932","utf-8-sig"]:
        try:
            if hasattr(f,"seek"): f.seek(0)
            return pd.read_csv(f,encoding=enc)
        except: pass
    if hasattr(f,"seek"): f.seek(0)
    return pd.read_csv(f,encoding="utf-8",errors="replace")

def validate_herd(df):
    errors=[]; id_c=find_col(df,"id"); rc_c=find_col(df,"rc"); lact_c=find_col(df,"lact"); dim_c=find_col(df,"dim")
    if rc_c:
        bad=df[~df[rc_c].apply(lambda x: str(x).strip() in [str(i) for i in range(7)] if pd.notna(x) else False)]
        for i in bad.index: errors.append({"行":i+1,"牛番号":df.loc[i,id_c] if id_c else i,"項目":"RC","値":df.loc[i,rc_c],"問題":"0〜6以外"})
    if lact_c:
        def ok_l(x):
            try: return 0<=int(float(str(x)))<=20
            except: return False
        bad=df[~df[lact_c].apply(lambda x: ok_l(x) if pd.notna(x) else False)]
        for i in bad.index: errors.append({"行":i+1,"牛番号":df.loc[i,id_c] if id_c else i,"項目":"LACT","値":df.loc[i,lact_c],"問題":"0〜20以外"})
    if dim_c:
        def ok_d(x):
            try: return 0<=int(float(str(x)))<=700
            except: return True
        bad=df[~df[dim_c].apply(lambda x: ok_d(x) if pd.notna(x) else True)]
        for i in bad.index: errors.append({"行":i+1,"牛番号":df.loc[i,id_c] if id_c else i,"項目":"DIM","値":df.loc[i,dim_c],"問題":"0〜700以外"})
    return errors

def process_herd(df,min_age=0):
    if df is None: return None
    df=df.copy(); df.columns=[c.strip() for c in df.columns]
    lact_c=find_col(df,"lact"); rc_c=find_col(df,"rc"); bd_c=find_col(df,"birthdate"); dim_c=find_col(df,"dim")
    if lact_c:
        df["_lact"]=pd.to_numeric(df[lact_c],errors="coerce").fillna(0).astype(int)
        df=df[df["_lact"]<=20].copy()
    if rc_c:   df["_rc"]=pd.to_numeric(df[rc_c],errors="coerce").fillna(0).astype(int)
    if dim_c:  df["_dim"]=pd.to_numeric(df[dim_c],errors="coerce")
    if bd_c:
        df["_bd"]=parse_date_col(df[bd_c])
        df["_age_months"]=((pd.Timestamp.today()-df["_bd"])/pd.Timedelta(days=30.44)).round(1)
    if min_age>0 and "_age_months" in df.columns and "_lact" in df.columns:
        mask=(df["_lact"]==0)&(df["_age_months"]<min_age)
        if mask.sum()>0: df=df[~mask].copy()
    if "_rc" in df.columns:
        df["_rc_disp"]=df["_rc"].map(RC_DISP_MAP)
    return df

def process_events(df):
    if df is None: return None
    df=df.copy(); df.columns=[c.strip() for c in df.columns]
    lact_c=find_col(df,"lact"); dt_c=find_col(df,"date"); et_c=find_col(df,"event_type")
    if lact_c:
        df["_lact"]=pd.to_numeric(df[lact_c],errors="coerce").fillna(0).astype(int)
        df=df[df["_lact"]<=20].copy()
    if dt_c:
        df["_date"]=parse_date_col(df[dt_c])
        df["_month"]=df["_date"].dt.strftime("%Y/%m")
    if et_c: df["_event_type"]=df[et_c].astype(str).str.strip().str.upper()
    return df

def process_bred(df):
    if df is None: return None
    df=df.copy(); df.columns=[c.strip() for c in df.columns]
    sire_c=find_col(df,"sire"); lact_c=find_col(df,"lact"); dt_c=find_col(df,"date")
    preg_c=find_col(df,"preg"); dim_c=find_col(df,"dim")
    if sire_c: df["_category"]=df[sire_c].apply(classify_semen)
    if dt_c:
        df["_date"]=parse_date_col(df[dt_c])
        df["_month"]=df["_date"].dt.strftime("%Y/%m")
    if lact_c:
        df["_lact"]=pd.to_numeric(df[lact_c],errors="coerce").fillna(0).astype(int)
        df=df[df["_lact"]<=20].copy()
        df["_group"]=df["_lact"].apply(lambda x: "未経産" if x==0 else "経産")
    if preg_c:
        df["_preg_raw"]=df[preg_c].astype(str).str.strip().str.upper()
        df["_preg"]=df[preg_c].apply(map_preg)
        df["_is_repeat"]=(df["_preg_raw"]=="R")
    if dim_c: df["_dim"]=pd.to_numeric(df[dim_c],errors="coerce")
    return df

def cr_monthly(bred_df,months,group=None):
    if bred_df is None or "_preg" not in bred_df.columns or "_month" not in bred_df.columns: return {}
    df=bred_df.copy()
    if "_is_repeat" in df.columns: df=df[~df["_is_repeat"]]
    df=df[df["_preg"].notna()]
    if group and "_group" in df.columns: df=df[df["_group"]==group]
    return {m: df[df["_month"]==m]["_preg"].mean()*100 for m in months if len(df[df["_month"]==m])>=3}

def cr_monthly_cat(bred_df,months,cat,group=None):
    if bred_df is None or "_preg" not in bred_df.columns or "_month" not in bred_df.columns: return {}
    if "_category" not in bred_df.columns: return {}
    df=bred_df[bred_df["_category"]==cat].copy()
    if df.empty: return {}
    if "_is_repeat" in df.columns: df=df[~df["_is_repeat"]]
    df=df[df["_preg"].notna()]
    if group and "_group" in df.columns: df=df[df["_group"]==group]
    return {m: df[df["_month"]==m]["_preg"].mean()*100 for m in months if len(df[df["_month"]==m])>=3}

def cr_avg(rd): return np.mean(list(rd.values())) if rd else None
def safe_div(a,b,fb=0): return a/b if b and b!=0 else fb

def calving_forecast(bred_df, n_months=12):
    if bred_df is None or "_preg" not in bred_df.columns or "_date" not in bred_df.columns:
        return [], None, None
    df = bred_df[bred_df["_preg"] == 1].copy()
    df = df[df["_date"].notna()]
    if df.empty: return [], None, None
    id_c = find_col(df, "id")
    if id_c and id_c in df.columns:
        df = df.sort_values("_date").groupby(id_c).last().reset_index()
    df["_exp_calv"] = df["_date"] + pd.Timedelta(days=GESTATION_DAYS)
    df["_calv_month"] = df["_exp_calv"].dt.strftime("%Y/%m")
    fmonths = [(date.today() + relativedelta(months=i)).strftime("%Y/%m") for i in range(n_months)]
    df_f = df[df["_calv_month"].isin(fmonths)]
    total = df_f.groupby("_calv_month").size().reindex(fmonths, fill_value=0)
    if "_lact" in df_f.columns:
        h = df_f[df_f["_lact"] == 0].groupby("_calv_month").size().reindex(fmonths, fill_value=0)
    else:
        h = pd.Series(0, index=fmonths)
    return fmonths, total, h

# ═══ 設定読み込み ═══
_S = load_s()

# ═══ サイドバー ═══
st.sidebar.title("カウフロー管理アプリ")
st.sidebar.markdown("---")
farm_list = get_farm_list()
if farm_list:
    selected_farm = st.sidebar.selectbox("農場選択", farm_list)
else:
    selected_farm = None

st.sidebar.markdown("---")
with st.sidebar.expander("農場データを追加・更新", expanded=(not farm_list)):
    new_farm = st.text_input("農場名", value=selected_farm if selected_farm else "")
    up_herd  = st.file_uploader("牛群リスト（LIST）",      type=["csv","xlsx"], key="up_h")
    up_fresh = st.file_uploader("分娩記録（FRESH）",        type=["csv","xlsx"], key="up_f")
    up_died  = st.file_uploader("死亡・除籍（DIED+SOLD）", type=["csv","xlsx"], key="up_d")
    up_bred  = st.file_uploader("授精記録（BRED）",         type=["csv","xlsx"], key="up_b")
    if st.button("保存する", type="primary", disabled=not new_farm):
        saved = []
        if up_herd:  save_farm_file(up_herd, new_farm, "list");  saved.append("牛群リスト")
        if up_fresh: save_farm_file(up_fresh,new_farm, "fresh"); saved.append("分娩記録")
        if up_died:  save_farm_file(up_died, new_farm, "died");  saved.append("死亡・除籍")
        if up_bred:  save_farm_file(up_bred, new_farm, "bred");  saved.append("授精記録")
        if saved:
            _add_farm(new_farm)
            st.session_state["upload_ver"] = st.session_state.get("upload_ver",0)+1
            st.cache_data.clear()
            st.success(f"保存完了: {', '.join(saved)}"); st.rerun()
        else: st.warning("ファイルを選択してください")
if farm_list and selected_farm:
    with st.sidebar.expander("農場を削除"):
        if st.button(f"{selected_farm} を削除", type="secondary"):
            delete_farm(selected_farm)
            st.session_state["upload_ver"] = st.session_state.get("upload_ver",0)+1
            st.cache_data.clear()
            st.rerun()

st.sidebar.markdown("---")
_vt_opts = ["合計","経産のみ","未経産のみ"]
view_toggle    = st.sidebar.radio("表示切替", _vt_opts, index=int(_S.get("view_toggle_idx",0)))
min_heifer_age = st.sidebar.slider("未経産牛 最低月齢", 0, 24, int(_S.get("min_heifer_age",12)), 1)

# ─ データ読み込み（Supabaseから） ─
if selected_farm:
    raw_herd_df,  herd_ts  = load_from_sb(selected_farm, ["list","herd","牛群","リスト"])
    fresh_df_raw, fresh_ts = load_from_sb(selected_farm, ["fresh","分娩"])
    died_df_raw,  died_ts  = load_from_sb(selected_farm, ["died","cull","sold","除籍","死亡"])
    bred_df_raw,  bred_ts  = load_from_sb(selected_farm, ["bred","授精","insem"])
    fresh_df = process_events(fresh_df_raw)
    died_df  = process_events(died_df_raw)
    bred_df  = process_bred(bred_df_raw)
else:
    raw_herd_df=fresh_df=died_df=bred_df=None
    herd_ts=fresh_ts=died_ts=bred_ts=None

st.sidebar.markdown("---")
if selected_farm:
    st.sidebar.markdown("**読み込み状況**")
    for lbl,ts in [("牛群リスト",herd_ts),("分娩記録",fresh_ts),("死亡・除籍",died_ts),("授精記録",bred_ts)]:
        icon = "OK" if ts else "--"
        st.sidebar.markdown(f"{icon} {lbl}" + (f"  `{ts}`" if ts else ""))

if raw_herd_df is not None:
    skey=f"herd_{selected_farm}"
    if skey not in st.session_state or st.session_state.get(f"{skey}_ver")!=st.session_state.get("upload_ver",0):
        st.session_state[skey]=raw_herd_df.copy()
        st.session_state[f"{skey}_ver"]=st.session_state.get("upload_ver",0)
    herd_df     = process_herd(st.session_state[skey], min_heifer_age)
    all_herd_df = process_herd(st.session_state[skey], 0)
else:
    herd_df=all_herd_df=None

months=get_months(13); this_month=date.today().strftime("%Y/%m")
prev_month=months[-2]; same_month_ly=months[0]
n_cows   =int((herd_df["_lact"]>0).sum()) if herd_df is not None and "_lact" in herd_df.columns else 0
n_heifers=int((herd_df["_lact"]==0).sum()) if herd_df is not None and "_lact" in herd_df.columns else 0
cr_all   =cr_monthly(bred_df,months); cr_heifer=cr_monthly(bred_df,months,"未経産"); cr_cow=cr_monthly(bred_df,months,"経産")
cr_h_all =cr_monthly_cat(bred_df,months,"H判別"); cr_h_heifer=cr_monthly_cat(bred_df,months,"H判別","未経産"); cr_h_cow=cr_monthly_cat(bred_df,months,"H判別","経産")
avg_cr_all=cr_avg(cr_all); avg_cr_h_all=cr_avg(cr_h_all); avg_cr_h_h=cr_avg(cr_h_heifer); avg_cr_h_c=cr_avg(cr_h_cow)

# タイトル
ts_parts=[]
for lbl,ts in [("牛群",herd_ts),("分娩",fresh_ts),("除籍",died_ts),("授精",bred_ts)]:
    if ts: ts_parts.append(f"{lbl}:{ts}")
ts_str=" / ".join(ts_parts)
farm_str=f"  [{selected_farm}]" if selected_farm else ""
st.markdown(
    f"<div style='display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;"
    f"position:sticky;top:0;z-index:200;background:white;padding:8px 0 4px 0;"
    f"box-shadow:0 1px 4px rgba(0,0,0,.07)'>"
    f"<span style='font-size:1.5rem;font-weight:700'>🐄 カウフロー管理アプリ{farm_str}</span>"
    f"<span style='font-size:0.78rem;color:#999;margin-left:auto'>更新: {ts_str}</span>"
    f"</div>", unsafe_allow_html=True)
if not farm_list: st.info("サイドバーから農場データを登録してください。")

TABS=st.tabs(["牛群構成","カウフロー","H判別目標","育成牛管理","精液別分析","データ確認・修正"])
_ns=dict(_S)

# ═══════════════════════════════════════
# TAB 2: カウフロー
# ═══════════════════════════════════════
with TABS[1]:
    st.subheader("カウフロー")

    def month_cnt(df,m,lact_f=None,cat=None,group=None,preg=None,event_type=None):
        if df is None or "_month" not in df.columns: return 0
        sub=df[df["_month"]==m]
        if lact_f=="first" and "_lact" in sub.columns: sub=sub[sub["_lact"]==1]
        elif lact_f=="multi" and "_lact" in sub.columns: sub=sub[sub["_lact"]>1]
        if cat and "_category" in sub.columns: sub=sub[sub["_category"]==cat]
        if group and "_group" in sub.columns: sub=sub[sub["_group"]==group]
        if preg==1 and "_preg" in sub.columns: sub=sub[sub["_preg"]==1]
        if event_type and "_event_type" in sub.columns: sub=sub[sub["_event_type"]==event_type]
        return len(sub)

    yr_first_cnt=sum(month_cnt(fresh_df,m,"first") for m in months)
    yr_calv_cnt =sum(month_cnt(fresh_df,m) for m in months)
    yr_died_cnt =sum(month_cnt(died_df,m) for m in months)
    actual_renewal=safe_div(yr_first_cnt,n_cows,0)*100
    actual_culling=safe_div(yr_died_cnt, n_cows,0)*100
    diff_pct=actual_renewal-actual_culling
    monthly_chg=n_cows*diff_pct/100/12; annual_chg=n_cows*diff_pct/100

    st.markdown("#### 実績参考値（過去13ヶ月）")
    rv1,rv2,rv3,rv4=st.columns(4)
    rv1.metric("経産牛頭数",f"{n_cows} 頭")
    rv2.metric("過去13ヶ月 分娩数",f"{yr_calv_cnt} 頭")
    rv3.metric("実績更新率",f"{actual_renewal:.1f}%")
    rv4.metric("実績淘汰率",f"{actual_culling:.1f}%")

    if diff_pct>=3:   pace_icon="増頭"; pace_col="#27ae60"
    elif diff_pct<=-3: pace_icon="減少"; pace_col="#e74c3c"
    else:             pace_icon="維持"; pace_col="#2980b9"
    st.markdown(
        f"<div style='background:{pace_col}18;border-left:4px solid {pace_col};padding:8px 14px;border-radius:4px'>"
        f"<b style='color:{pace_col}'>{pace_icon}ペース</b>: "
        f"月間 <b>{monthly_chg:+.1f}頭</b> / 年間 <b>{annual_chg:+.0f}頭</b>"
        f"<small style='color:#666'>  (更新率{actual_renewal:.1f}% - 淘汰率{actual_culling:.1f}%)</small></div>",
        unsafe_allow_html=True)

    st.markdown("---")
    with st.expander("目標設定",expanded=True):
        ga1,ga2,ga3=st.columns(3)
        with ga1: goal_annual_calv =st.number_input("年間分娩数目標（頭）",0,1000,int(_S.get("goal_annual_calv",120)),1,key="g_calv")
        with ga2: goal_renewal_rate=st.number_input("目標更新率（%）",0.0,100.0,float(_S.get("goal_renewal_rate",20.0)),0.5,key="g_renew")
        with ga3: goal_culling_rate=st.number_input("目標淘汰率（%）",0.0,100.0,float(_S.get("goal_culling_rate",25.0)),0.5,key="g_cull")
        st.markdown("---")
        hb1,hb2=st.columns(2)
        with hb1:
            preg_base=st.selectbox("H判別受胎目標ベース",
                ["淘汰ベース（経産牛頭数×目標淘汰率）","更新ベース（経産牛頭数×目標更新率）"],
                index=int(_S.get("preg_base_idx",0)),key="preg_base")
        with hb2:
            ai_base=st.selectbox("H判別授精目標ベース",
                ["前年同月受胎率ベース","先月受胎率ベース"],
                index=int(_S.get("ai_base_idx",0)),key="ai_base")

    preg_base_idx=0 if preg_base.startswith("淘汰") else 1
    ai_base_idx  =0 if ai_base.startswith("前年") else 1
    annual_h_preg=(n_cows*goal_culling_rate/100) if preg_base_idx==0 else (n_cows*goal_renewal_rate/100)
    m_h_preg=annual_h_preg/12
    cr_fallback=avg_cr_h_all or avg_cr_all or 50
    cr_ly=cr_h_all.get(same_month_ly); cr_prev=cr_h_all.get(prev_month)
    use_cr_base=(cr_ly if ai_base_idx==0 else cr_prev) or cr_fallback
    m_h_ai=safe_div(m_h_preg,use_cr_base/100,0)
    m_culling=safe_div(n_cows*goal_culling_rate/100,12,goal_culling_rate*goal_annual_calv/100/12)
    act_culling=month_cnt(died_df,this_month); act_first=month_cnt(fresh_df,this_month,"first")
    act_h_ai=month_cnt(bred_df,this_month,cat="H判別"); act_h_preg=month_cnt(bred_df,this_month,cat="H判別",preg=1)
    nxt_culling=max(0,m_culling+max(0,m_culling-act_culling))
    nxt_h_ai   =max(0,m_h_ai   +max(0,m_h_ai   -act_h_ai))
    nxt_h_preg =max(0,m_h_preg +max(0,m_h_preg -act_h_preg))

    def ach_str(act,tgt):
        if tgt<=0: return "-"
        pct=act/tgt*100
        return f"OK {pct:.0f}%" if act>=tgt else f"▲ {pct:.0f}% ({tgt-act:.0f}頭不足)"

    st.markdown("#### 月間目標サマリー")
    rows=[
        ("H判別授精",f"{m_h_ai:.0f}",str(act_h_ai), ach_str(act_h_ai, m_h_ai),  f"{nxt_h_ai:.0f}"),
        ("H判別受胎",f"{m_h_preg:.0f}",str(act_h_preg),ach_str(act_h_preg,m_h_preg),f"{nxt_h_preg:.0f}"),
        ("淘汰",    f"{m_culling:.0f}",str(act_culling),ach_str(act_culling,m_culling),f"{nxt_culling:.0f}"),
    ]
    tbl_html="""
<style>
.summary-tbl{border-collapse:collapse;width:100%;font-size:1.05rem}
.summary-tbl th{background:#2c3e50;color:#fff;padding:10px 14px;text-align:center;font-size:1rem}
.summary-tbl td{padding:10px 14px;text-align:center;border-bottom:1px solid #e0e0e0;font-size:1.05rem}
.summary-tbl tr:nth-child(odd) td{background:#f7f9fc}
.summary-tbl .lbl{font-weight:700;font-size:1.1rem;text-align:left}
.ok{color:#27ae60;font-weight:700}.ng{color:#e74c3c;font-weight:700}
</style>
<table class="summary-tbl">
<tr><th>指標</th><th>月別目標（頭）</th><th>今月実績（頭）</th><th>達成度</th><th>来月目標（頭）</th></tr>
"""
    for lbl,tgt,act,ach,nxt in rows:
        ach_cls="ok" if ach.startswith("OK") else "ng"
        tbl_html+=f"<tr><td class='lbl'>{lbl}</td><td><b>{tgt}</b></td><td><b>{act}</b></td><td class='{ach_cls}'>{ach}</td><td><b>{nxt}</b></td></tr>"
    tbl_html+="</table>"
    st.markdown(tbl_html,unsafe_allow_html=True)

    st.markdown("---")
    if fresh_df is None and died_df is None:
        st.info("分娩記録または死亡・除籍記録を保存すると表示されます。")
    else:
        first_c=[month_cnt(fresh_df,m,"first") for m in months]
        multi_c=[month_cnt(fresh_df,m,"multi") for m in months]
        died_c =[month_cnt(died_df,m) for m in months]
        net    =[f+mu-d for f,mu,d in zip(first_c,multi_c,died_c)]
        fig=go.Figure()
        fig.add_trace(go.Bar(x=months,y=first_c,name="初産分娩",marker_color="#27ae60",text=[str(v) if v>0 else "" for v in first_c],textposition="inside",insidetextanchor="middle"))
        fig.add_trace(go.Bar(x=months,y=multi_c,name="経産分娩",marker_color="#2ecc71",text=[str(v) if v>0 else "" for v in multi_c],textposition="inside",insidetextanchor="middle"))
        fig.add_trace(go.Bar(x=months,y=[-d for d in died_c],name="死亡・除籍",marker_color="#e74c3c",text=[str(v) if v>0 else "" for v in died_c],textposition="inside",insidetextanchor="middle"))
        fig.add_trace(go.Scatter(x=months,y=net,name="純増減",mode="lines+markers",line=dict(color="#2c3e50",width=2),marker=dict(size=7)))
        fig.add_hline(y=goal_annual_calv/12,line_dash="dash",line_color="#27ae60",annotation_text=f"分娩目標 {goal_annual_calv/12:.0f}頭/月",annotation_position="top left",annotation_font_size=11,annotation_font_color="#27ae60",annotation_bgcolor="rgba(255,255,255,0.8)")
        fig.add_hline(y=-m_culling,line_dash="dash",line_color="#e74c3c",annotation_text=f"淘汰目標 {m_culling:.0f}頭/月",annotation_position="bottom left",annotation_font_size=11,annotation_font_color="#e74c3c",annotation_bgcolor="rgba(255,255,255,0.8)")
        fig.update_layout(barmode="relative",height=420,yaxis_title="頭数",margin=dict(t=30,b=10,r=60),legend=dict(orientation="h",y=-0.18,x=0))
        st.plotly_chart(fig,use_container_width=True)

    st.markdown("---")
    st.markdown("#### 今後12ヶ月の分娩予測（授精受胎記録ベース）")
    fmonths,fc_total,fc_heifer=calving_forecast(bred_df,n_months=12)
    if not fmonths:
        st.info("授精記録（受胎結果P付き）があれば、妊娠期間280日を加算して分娩予測を表示します。")
    else:
        fc_cow_vals=[int(fc_total[m])-int(fc_heifer[m]) for m in fmonths]
        fc_h_vals  =[int(fc_heifer[m]) for m in fmonths]
        fig_fc=go.Figure()
        fig_fc.add_trace(go.Bar(x=fmonths,y=fc_h_vals,name="初産分娩予測（未経産→初産）",marker_color="#27ae60",text=[str(v) if v>0 else "" for v in fc_h_vals],textposition="inside",insidetextanchor="middle"))
        fig_fc.add_trace(go.Bar(x=fmonths,y=fc_cow_vals,name="経産分娩予測",marker_color="#2980b9",text=[str(v) if v>0 else "" for v in fc_cow_vals],textposition="inside",insidetextanchor="middle"))
        fig_fc.add_hline(y=goal_annual_calv/12,line_dash="dash",line_color="#e67e22",annotation_text=f"月間分娩目標 {goal_annual_calv/12:.0f}頭",annotation_position="top left",annotation_font_size=11,annotation_font_color="#e67e22",annotation_bgcolor="rgba(255,255,255,.8)")
        fig_fc.update_layout(barmode="stack",height=340,yaxis_title="頭数",legend=dict(orientation="h",y=-0.22,x=0),margin=dict(t=20,b=10,r=40))
        st.plotly_chart(fig_fc,use_container_width=True)
        tbl_fc=pd.DataFrame({"月":fmonths,"経産分娩":fc_cow_vals,"初産分娩":fc_h_vals,"合計":[int(fc_total[m]) for m in fmonths]}).set_index("月")
        st.dataframe(tbl_fc.T,use_container_width=True)
        st.caption(f"※ 受胎確認(P)日から妊娠期間{GESTATION_DAYS}日加算の推計。同一牛は最新受胎を使用。")

    if died_df is not None:
        died_c2=[month_cnt(died_df,m) for m in months]
        st.markdown("---")
        st.markdown("#### 除籍内訳（月別）")
        if "_event_type" in died_df.columns:
            etypes=sorted(died_df["_event_type"].dropna().unique().tolist())
            if len(etypes)>1:
                etype_colors=["#e74c3c","#e67e22","#9b59b6","#3498db","#1abc9c"]
                fig_et=go.Figure()
                for i,et in enumerate(etypes):
                    cnt=[month_cnt(died_df,m,event_type=et) for m in months]
                    fig_et.add_trace(go.Bar(x=months,y=cnt,name=et,marker_color=etype_colors[i%len(etype_colors)],text=[str(v) if v>0 else "" for v in cnt],textposition="inside",insidetextanchor="middle"))
                fig_et.update_layout(barmode="stack",height=300,yaxis_title="頭数",legend=dict(orientation="h",y=-0.25,x=0),margin=dict(t=20,b=10))
                st.plotly_chart(fig_et,use_container_width=True)
                tbl_data={"月":months}
                for et in etypes: tbl_data[et]=[month_cnt(died_df,m,event_type=et) for m in months]
                tbl_data["合計"]=died_c2
                st.dataframe(pd.DataFrame(tbl_data).set_index("月").T,use_container_width=True)
            else:
                st.info("Event列に複数種類が見当たりません。")
        else:
            st.dataframe(pd.DataFrame({"月":months,"除籍数":died_c2}).set_index("月").T,use_container_width=True)

    _ns.update({"goal_annual_calv":goal_annual_calv,"goal_renewal_rate":goal_renewal_rate,
                 "goal_culling_rate":goal_culling_rate,"preg_base_idx":preg_base_idx,"ai_base_idx":ai_base_idx})

# ═══════════════════════════════════════
# TAB 1: 牛群構成
# ═══════════════════════════════════════
with TABS[0]:
    st.subheader("牛群構成")
    if herd_df is None:
        st.info("サイドバーから牛群リストを保存してください。")
    else:
        if view_toggle=="経産のみ":    disp=herd_df[herd_df["_lact"]>0].copy() if "_lact" in herd_df.columns else herd_df.copy()
        elif view_toggle=="未経産のみ": disp=herd_df[herd_df["_lact"]==0].copy() if "_lact" in herd_df.columns else herd_df.copy()
        else: disp=herd_df.copy()
        total=len(disp); st.metric("表示頭数",f"{total} 頭")
        col1,col2=st.columns(2)
        if "_rc_disp" in disp.columns:
            with col1:
                rc_cnt=disp["_rc_disp"].value_counts()
                xs=[r for r in RC_DISP_ORDER if r in rc_cnt.index]
                ys=[int(rc_cnt[r]) for r in xs]; cs=[RC_DISP_COLORS[r] for r in xs]
                fig_rc=go.Figure(go.Bar(x=xs,y=ys,marker_color=cs,text=ys,textposition="outside"))
                fig_rc.update_layout(title="繁殖状況別頭数",height=300,showlegend=False,yaxis_title="頭数",margin=dict(t=40,b=5))
                st.plotly_chart(fig_rc,use_container_width=True)
                pct=[f"{v/total*100:.1f}%" if total>0 else "-" for v in ys]
                st.dataframe(pd.DataFrame({"区分":xs,"頭数":ys,"割合":pct}).set_index("区分").T,use_container_width=True)
        if "_lact" in disp.columns:
            with col2:
                lc=disp["_lact"].value_counts().sort_index()
                lbl_map={k:("未経産" if k==0 else f"{k}産") for k in lc.index}
                xs2=[lbl_map[k] for k in lc.index]; ys2=list(lc.values)
                fig_lc=go.Figure(go.Bar(x=xs2,y=ys2,marker_color="#3498db",text=ys2,textposition="outside"))
                fig_lc.update_layout(title="産次別頭数",height=300,showlegend=False,yaxis_title="頭数",margin=dict(t=40,b=5))
                st.plotly_chart(fig_lc,use_container_width=True)
                pct2=[f"{v/total*100:.1f}%" if total>0 else "-" for v in ys2]
                st.dataframe(pd.DataFrame({"区分":xs2,"頭数":ys2,"割合":pct2}).set_index("区分").T,use_container_width=True)
        if "_dim" in disp.columns:
            st.markdown("---")
            cows_only=disp[disp["_lact"]>0] if "_lact" in disp.columns else disp
            cd=cows_only[(cows_only["_dim"].notna())&(cows_only["_dim"]>0)]
            if len(cd)>0:
                avg_dim=cd["_dim"].mean()
                avg_concep_dim=None
                if bred_df is not None and "_dim" in bred_df.columns and "_preg" in bred_df.columns and "_lact" in bred_df.columns:
                    bc=bred_df[(bred_df["_lact"]>0)&(bred_df["_preg"]==1)&(bred_df["_dim"].notna())]
                    if len(bc)>0: avg_concep_dim=bc["_dim"].mean()
                dm1,dm2,dm3=st.columns(3)
                dm1.metric("経産牛 平均DIM",f"{avg_dim:.0f} 日")
                dm2.metric("平均受胎DIM",f"{avg_concep_dim:.0f} 日" if avg_concep_dim else "データ不足")
                dm3.metric("経産牛（DIMあり）",f"{len(cd)} 頭")
                cd2=cd.copy()
                cd2["_dim_grp"]=pd.cut(cd2["_dim"],bins=DIM_BINS,labels=DIM_LABELS,right=True)
                if "_rc_disp" in cd2.columns:
                    pivot=cd2.groupby(["_dim_grp","_rc_disp"],observed=True).size().unstack(fill_value=0)
                    fig_dim=go.Figure()
                    for rc_lbl in RC_DISP_ORDER:
                        if rc_lbl not in pivot.columns: continue
                        vals=pivot[rc_lbl].reindex(DIM_LABELS,fill_value=0)
                        fig_dim.add_trace(go.Bar(x=DIM_LABELS,y=vals,name=rc_lbl,marker_color=RC_DISP_COLORS[rc_lbl],text=[str(v) if v>0 else "" for v in vals],textposition="inside",insidetextanchor="middle"))
                    fig_dim.update_layout(barmode="stack",title="分娩後日数（DIM）別 繁殖状況内訳（経産牛のみ）",height=320,yaxis_title="頭数",legend=dict(orientation="h",y=-0.25,x=0),margin=dict(t=40,b=10))
                    st.plotly_chart(fig_dim,use_container_width=True)
                    tbl_pivot=pivot.reindex(columns=[c for c in RC_DISP_ORDER if c in pivot.columns])
                    tbl_pivot.index=tbl_pivot.index.astype(str); tbl_pivot["合計"]=tbl_pivot.sum(axis=1)
                    st.dataframe(tbl_pivot,use_container_width=True)
        if "_rc_disp" in disp.columns and "_lact" in disp.columns:
            st.markdown("---")
            col_l,col_r=st.columns(2)
            h_disp=disp[disp["_lact"]==0]
            with col_l:
                st.subheader("未経産牛 繁殖状況")
                if len(h_disp)>0:
                    h2=h_disp.copy(); h2["_rc_h"]=h2["_rc"].apply(map_h_rc)
                    ht=h2.groupby("_rc_h").size().reindex(HEIFER_RC_CATS,fill_value=0)
                    ht_df=pd.DataFrame({"繁殖状況":HEIFER_RC_CATS,"頭数":[int(ht.get(c,0)) for c in HEIFER_RC_CATS]})
                    ht_df["割合"]=ht_df["頭数"].apply(lambda x: f"{x/len(h_disp)*100:.1f}%" if len(h_disp)>0 else "-")
                    st.dataframe(ht_df.set_index("繁殖状況").T,use_container_width=True)
                else: st.info("未経産牛なし")
            c_disp=disp[(disp["_lact"]>0)&(disp["_rc"]>0)]
            with col_r:
                st.subheader("経産牛 繁殖状況×産次（クロス集計）")
                if len(c_disp)>0:
                    c2=c_disp.copy()
                    c2["_lact_grp"]=c2["_lact"].apply(lambda x: "1産" if x==1 else "2産" if x==2 else "3産以上")
                    c2["_rc_disp2"]=c2["_rc"].map(RC_DISP_MAP)
                    pivot=c2.pivot_table(index="_rc_disp2",columns="_lact_grp",aggfunc="size",fill_value=0)
                    row_order=[r for r in RC_DISP_ORDER if r in pivot.index and r!="未経産"]
                    pivot=pivot.reindex(index=row_order)
                    for col in ["1産","2産","3産以上"]:
                        if col not in pivot.columns: pivot[col]=0
                    pivot=pivot.reindex(columns=["1産","2産","3産以上"]); pivot["合計"]=pivot.sum(axis=1)
                    st.dataframe(pivot,use_container_width=True)
                else: st.info("経産牛なし")

# ═══════════════════════════════════════
# TAB 3: H判別目標
# ═══════════════════════════════════════
with TABS[2]:
    st.subheader("H判別目標管理")
    with st.expander("未経産目標の設定",expanded=True):
        hp1,hp2=st.columns([1,2])
        with hp1:
            heifer_plan=st.radio("未経産目標算出方法",["I案：未授精牛比率","II案：受胎頭数入力"],index=int(_S.get("heifer_plan_idx",0)),key="heifer_plan")
        with hp2:
            h_cr_base=st.selectbox("未経産 受胎率ベース",["前年同月","先月"],index=int(_S.get("h_cr_base_idx",0)),key="h_cr_base_sel")
            c_cr_base=st.selectbox("経産 受胎率ベース",["前年同月","先月"],index=int(_S.get("c_cr_base_idx",0)),key="c_cr_base_sel")
        st.markdown("---")
        if "II" not in heifer_plan:
            hm1,hm2=st.columns(2)
            with hm1: h_multiplier=st.number_input("未授精牛への月間授精率",0.1,1.0,float(_S.get("h_multiplier",0.8)),0.05,key="h_mult")
            n_unins=0
            if all_herd_df is not None and "_lact" in all_herd_df.columns and "_rc" in all_herd_df.columns:
                mask=(all_herd_df["_lact"]==0)&(all_herd_df["_rc"]==0)
                if "_age_months" in all_herd_df.columns: mask=mask&(all_herd_df["_age_months"]>=min_heifer_age)
                n_unins=int(mask.sum())
            with hm2: st.metric(f"未授精牛（{min_heifer_age}ヶ月齢以上）",f"{n_unins} 頭")
            m_h_preg_h_input=float(_S.get("m_h_preg_h_input",3.0))
        else:
            h_multiplier=float(_S.get("h_multiplier",0.8)); n_unins=0
            hm1,_=st.columns(2)
            with hm1: m_h_preg_h_input=st.number_input("未経産 月間H判別受胎目標（頭）",0.0,200.0,float(_S.get("m_h_preg_h_input",3.0)),0.5,key="h_preg_input")
    heifer_plan_idx=0 if "II" not in heifer_plan else 1
    h_cr_base_idx=0 if h_cr_base=="前年同月" else 1
    c_cr_base_idx=0 if c_cr_base=="前年同月" else 1
    use_cr_h_judai=(cr_h_heifer.get(same_month_ly) if h_cr_base_idx==0 else cr_h_heifer.get(prev_month)) or avg_cr_h_h or cr_fallback
    use_cr_c_judai=(cr_h_cow.get(same_month_ly)    if c_cr_base_idx==0 else cr_h_cow.get(prev_month))    or avg_cr_h_c or cr_fallback
    if heifer_plan_idx==0:
        m_h_ai_h_tgt=n_unins*h_multiplier; m_h_preg_h_tgt=m_h_ai_h_tgt*(use_cr_h_judai/100)
    else:
        m_h_preg_h_tgt=m_h_preg_h_input; m_h_ai_h_tgt=safe_div(m_h_preg_h_tgt,use_cr_h_judai/100,0)
    m_h_preg_c_tgt=max(0,m_h_preg-m_h_preg_h_tgt)
    m_h_ai_c_tgt  =safe_div(m_h_preg_c_tgt,use_cr_c_judai/100,0)
    _ns.update({"heifer_plan_idx":heifer_plan_idx,"h_cr_base_idx":h_cr_base_idx,"c_cr_base_idx":c_cr_base_idx,"h_multiplier":h_multiplier,"m_h_preg_h_input":m_h_preg_h_input})

    st.markdown("#### 月間目標一覧")
    goal_html=f"""
<style>
.goal-card{{display:inline-block;background:#f0f4ff;border:2px solid #3498db;border-radius:10px;padding:14px 24px;margin:6px;text-align:center;min-width:200px}}
.goal-card .title{{font-size:1.0rem;color:#555;margin-bottom:4px}}
.goal-card .val{{font-size:1.6rem;font-weight:700;color:#2c3e50}}
.goal-card .sub{{font-size:0.85rem;color:#888;margin-top:4px}}
</style>
<div style='display:flex;flex-wrap:wrap;gap:4px;margin-bottom:12px'>
  <div class='goal-card'><div class='title'>全体</div><div class='val'>授精 {m_h_ai:.0f}頭</div><div class='sub'>受胎 {m_h_preg:.0f}頭</div></div>
  <div class='goal-card'><div class='title'>未経産</div><div class='val'>授精 {m_h_ai_h_tgt:.0f}頭</div><div class='sub'>受胎 {m_h_preg_h_tgt:.0f}頭</div></div>
  <div class='goal-card'><div class='title'>経産</div><div class='val'>授精 {m_h_ai_c_tgt:.0f}頭</div><div class='sub'>受胎 {m_h_preg_c_tgt:.0f}頭</div></div>
</div>"""
    st.markdown(goal_html,unsafe_allow_html=True)
    st.markdown("---")

    def h_graph(label,group_filter,tgt_ai,tgt_preg,cr_rd):
        if bred_df is None: st.info("授精記録を登録してください。"); return
        if "_category" not in bred_df.columns: st.info("精液コード列が認識されていません。"); return
        df=bred_df.copy()
        if group_filter and "_group" in df.columns: df=df[df["_group"]==group_filter]
        df_h=df[df["_category"]=="H判別"]
        ai_cnt=[len(df_h[df_h["_month"]==m]) for m in months]
        preg_df=df_h[~df_h["_is_repeat"]] if "_is_repeat" in df_h.columns else df_h.copy()
        preg_cnt=[int(preg_df[(preg_df["_month"]==m)&(preg_df["_preg"]==1)].shape[0]) if "_preg" in preg_df.columns else 0 for m in months]
        cr_vals=[cr_rd.get(m) for m in months]
        cr_text=[f"{v:.1f}%" if v is not None else "" for v in cr_vals]
        with st.expander(f"{label} 診断",expanded=(sum(ai_cnt)==0)):
            sire_col=find_col(bred_df,"sire")
            st.write(f"全授精:{len(df)}件 / H判別:{len(df_h)}件 / 精液列:`{sire_col}`")
            if "_category" in df.columns: st.write("精液分類:",dict(df["_category"].value_counts()))
            if sire_col and sire_col in bred_df.columns: st.write("コード例:",list(bred_df[sire_col].dropna().unique()[:12]))
        fig=go.Figure()
        fig.add_trace(go.Bar(x=months,y=ai_cnt,name="H判別授精数",marker_color="#9b59b6",text=[str(v) if v>0 else "" for v in ai_cnt],textposition="outside",cliponaxis=False))
        fig.add_trace(go.Bar(x=months,y=preg_cnt,name="H判別受胎数",marker_color="#2ecc71",text=[str(v) if v>0 else "" for v in preg_cnt],textposition="inside",insidetextanchor="middle"))
        fig.add_trace(go.Scatter(x=months,y=cr_vals,name="H判別受胎率(%)",mode="lines+markers+text",text=cr_text,textposition="top center",textfont=dict(size=10,color="#e67e22"),line=dict(color="#e67e22",width=2),marker=dict(size=7),yaxis="y2",connectgaps=False))
        fig.add_hline(y=tgt_ai,line_dash="dash",line_color="#8e44ad")
        fig.add_hline(y=tgt_preg,line_dash="dot",line_color="#1abc9c")
        fig.update_layout(title=f"{label} H判別授精・受胎成績",barmode="overlay",height=390,yaxis=dict(title="頭数",title_standoff=10),yaxis2=dict(title="H判別受胎率(%)",overlaying="y",side="right",range=[0,100],showgrid=False),legend=dict(orientation="h",y=-0.22,x=0),margin=dict(t=50,b=20,r=70,l=60))
        st.plotly_chart(fig,use_container_width=True)
        gl,gr=st.columns(2)
        gl.markdown(f"<div style='color:#8e44ad;font-size:13px'>- - 授精目標: <b>{tgt_ai:.0f}頭</b></div>",unsafe_allow_html=True)
        gr.markdown(f"<div style='color:#1abc9c;font-size:13px'>... 受胎目標: <b>{tgt_preg:.0f}頭</b></div>",unsafe_allow_html=True)

    st.markdown("### 全体"); h_graph("全体",None,m_h_ai,m_h_preg,cr_h_all)
    c1,c2=st.columns(2)
    with c1: st.markdown("### 未経産"); h_graph("未経産","未経産",m_h_ai_h_tgt,m_h_preg_h_tgt,cr_h_heifer)
    with c2: st.markdown("### 経産");   h_graph("経産","経産",m_h_ai_c_tgt,m_h_preg_c_tgt,cr_h_cow)

# ═══════════════════════════════════════
# TAB 4: 育成牛管理
# ═══════════════════════════════════════
with TABS[3]:
    st.subheader("育成牛管理")
    if herd_df is None:
        st.info("牛群リストを保存すると表示されます。")
    else:
        heifers=herd_df[herd_df["_lact"]==0].copy() if "_lact" in herd_df.columns else herd_df.copy()
        n_h=len(heifers); st.metric(f"未経産頭数（{min_heifer_age}ヶ月齢以上）",f"{n_h} 頭")
        if "_age_months" not in heifers.columns:
            st.warning("BDAT（生年月日）列がないため月齢分析ができません。")
        else:
            age_bin_labels=["12〜14月","15〜17月","18〜20月","21〜23月","24月〜"]; age_bins=[0,15,18,21,24,9999]
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
                    fig1.add_trace(go.Bar(x=age_bin_labels,y=vals,name=lab,marker_color=HEIFER_RC_COLORS[lab],text=[str(v) if v>0 else "" for v in vals],textposition="inside",insidetextanchor="middle"))
                fig1.update_layout(barmode="stack",height=350,yaxis_title="頭数",legend=dict(orientation="h",y=-0.2))
                st.plotly_chart(fig1,use_container_width=True)
                pivot_rc.index=pivot_rc.index.astype(str); pivot_rc["合計"]=pivot_rc.sum(axis=1)
                st.dataframe(pivot_rc,use_container_width=True)
            cs_h=heifers[heifers["_age_months"].between(18,24)]
            if len(cs_h)>0:
                st.markdown("---")
                id_c2=find_col(herd_df,"id"); bd_c2=find_col(herd_df,"birthdate")
                show=[c for c in [id_c2,bd_c2,"_age_months","_rc_label"] if c and c in cs_h.columns]
                st.markdown(f"#### 分娩予定牛（18〜24ヶ月）: {len(cs_h)}頭")
                st.dataframe(cs_h[show].rename(columns={"_age_months":"月齢","_rc_label":"繁殖状況"}),use_container_width=True)
        if all_herd_df is not None and "_lact" in all_herd_df.columns and "_age_months" in all_herd_df.columns and "_bd" in all_herd_df.columns:
            young=all_herd_df[(all_herd_df["_lact"]==0)&(all_herd_df["_age_months"]<min_heifer_age)].copy()
            if len(young)>0:
                st.markdown("---")
                st.markdown(f"#### {min_heifer_age}ヶ月齢未満 子牛 {len(young)}頭 育成牛到達スケジュール")
                future_months_sc=[(date.today()+relativedelta(months=i)).strftime("%Y/%m") for i in range(13)]
                young["_turn_age"]=young["_bd"].apply(lambda x: (x+relativedelta(months=int(min_heifer_age))).strftime("%Y/%m") if pd.notna(x) else None)
                sched=young["_turn_age"].value_counts()
                sched_vals=[sched.get(m,0) for m in future_months_sc]
                fig0=go.Figure(go.Bar(x=future_months_sc,y=sched_vals,marker_color="#27ae60",text=sched_vals,textposition="outside"))
                fig0.update_layout(height=280,yaxis_title="頭数",margin=dict(t=10,b=10))
                st.plotly_chart(fig0,use_container_width=True)
        st.markdown("---")
        st.subheader("預託コスト試算")
        cc1,cc2,cc3,cc4=st.columns(4)
        with cc1: sent_age=st.number_input("預託開始月齢（ヶ月）",1,18,4,1)
        with cc2: ret_days=st.number_input("帰牧タイミング（分娩何日前）",0,120,60,10)
        with cc3: calv_age=st.number_input("想定初産月齢（ヶ月）",18,36,24,1)
        with cc4: cost_mo =st.number_input("月間預託費用（円/頭）",0,200000,35000,1000)
        dep_months=calv_age-sent_age-ret_days/30.44
        if dep_months<=0: st.warning("預託期間が0以下です。")
        else:
            cost_per=dep_months*cost_mo
            co1,co2,co3=st.columns(3)
            co1.metric("預託期間",f"{dep_months:.1f} ヶ月"); co2.metric("1頭あたり費用",f"{cost_per:,.0f} 円")
            co3.metric(f"育成牛{n_h}頭 合計（試算）",f"{n_h*cost_per:,.0f} 円")
            if "_age_months" in heifers.columns:
                h2=heifers.copy()
                h2["残り預託月数"]=((calv_age-ret_days/30.44)-h2["_age_months"]).clip(lower=0)
                h2["残り費用（円）"]=h2["残り預託月数"]*cost_mo
                age_cost=h2.groupby("_age_grp",observed=True)["残り費用（円）"].agg(頭数="count",残り費用合計="sum").reindex(age_bin_labels).fillna(0)
                age_cost["1頭平均残り費用"]=age_cost["残り費用合計"]/age_cost["頭数"].replace(0,np.nan)
                age_cost=age_cost.fillna(0)
                st.dataframe(age_cost.style.format({"残り費用合計":"{:,.0f}円","1頭平均残り費用":"{:,.0f}円"}),use_container_width=True)

# ═══════════════════════════════════════
# TAB 5: 精液別分析
# ═══════════════════════════════════════
with TABS[4]:
    st.subheader("精液別受胎率分析")
    if bred_df is None:
        st.info("授精記録を保存すると表示されます。")
    else:
        sire_c2=find_col(bred_df,"sire")
        if sire_c2 is None:
            st.warning("精液コード列（Remark/B/SIRE）が見つかりません。")
        else:
            fa,fb=st.columns(2)
            grp_opts=["全体","未経産","経産","初産（1産）のみ","2産のみ","3産以上"]
            cat_opts=list(SEMEN_GROUPS.keys())
            with fa: sel_grp=st.selectbox("牛群フィルター",grp_opts,index=int(_S.get("sire_grp_idx",0)),key="sire_grp")
            with fb: sel_cat=st.selectbox("精液種別フィルター",cat_opts,index=int(_S.get("sire_cat_idx",0)),key="sire_cat")
            dp=bred_df.copy()
            if "_is_repeat" in dp.columns: dp=dp[~dp["_is_repeat"]]
            if sel_grp=="未経産" and "_group" in dp.columns: dp=dp[dp["_group"]=="未経産"]
            elif sel_grp=="経産" and "_group" in dp.columns: dp=dp[dp["_group"]=="経産"]
            elif sel_grp=="初産（1産）のみ" and "_lact" in dp.columns: dp=dp[dp["_lact"]==1]
            elif sel_grp=="2産のみ" and "_lact" in dp.columns: dp=dp[dp["_lact"]==2]
            elif sel_grp=="3産以上" and "_lact" in dp.columns: dp=dp[dp["_lact"]>=3]
            cats=SEMEN_GROUPS[sel_cat]
            if cats and "_category" in dp.columns: dp=dp[dp["_category"].isin(cats)]
            if "_preg" not in dp.columns or dp["_preg"].notna().sum()==0:
                st.info("受胎結果列（R）がないため授精頭数のみ表示します。")
                if "_category" in dp.columns:
                    cc=dp["_category"].value_counts()
                    fig=go.Figure(go.Bar(x=cc.index.tolist(),y=cc.values.tolist(),marker_color="#9b59b6",text=cc.values.tolist(),textposition="outside"))
                    fig.update_layout(title=f"精液種類別授精頭数（{sel_grp}・{sel_cat}）",height=350)
                    st.plotly_chart(fig,use_container_width=True)
            else:
                dp2=dp[dp["_preg"].notna()]
                grp=dp2.groupby(sire_c2).agg(授精頭数=(sire_c2,"count"),受胎頭数=("_preg","sum")).reset_index()
                grp["受胎率"]=grp["受胎頭数"]/grp["授精頭数"]*100
                grp=grp[grp["授精頭数"]>=5].sort_values("受胎率",ascending=False)
                if len(grp)==0:
                    st.info(f"5頭以上の精液コードがありません（{sel_grp}・{sel_cat}）。")
                else:
                    cs2=[("#e74c3c" if r<30 else "#f39c12" if r<40 else "#2ecc71") for r in grp["受胎率"]]
                    fig=go.Figure(go.Bar(x=grp["受胎率"].tolist(),y=grp[sire_c2].tolist(),orientation="h",marker_color=cs2,text=(grp["受胎率"].round(1).astype(str)+"%").tolist(),textposition="outside"))
                    fig.add_vline(x=30,line_dash="dash",line_color="red",annotation_text="30%",annotation_position="top right")
                    fig.add_vline(x=40,line_dash="dash",line_color="orange",annotation_text="40%",annotation_position="top right")
                    fig.update_layout(title=f"精液別受胎率（5頭以上・{sel_grp}・{sel_cat}）",height=max(300,len(grp)*22+100),xaxis_title="受胎率(%)",yaxis=dict(autorange="reversed"),margin=dict(r=60))
                    st.plotly_chart(fig,use_container_width=True)
                    st.dataframe(grp.rename(columns={sire_c2:"精液コード"}).style.format({"受胎率":"{:.1f}%","授精頭数":"{:.0f}","受胎頭数":"{:.0f}"}),use_container_width=True)
            _ns.update({"sire_grp_idx":grp_opts.index(sel_grp),"sire_cat_idx":cat_opts.index(sel_cat)})

# ═══════════════════════════════════════
# TAB 6: データ確認・修正
# ═══════════════════════════════════════
with TABS[5]:
    st.subheader("データ確認・修正")
    if raw_herd_df is None:
        st.info("牛群リストを保存すると、エラー値の確認・修正ができます。")
    else:
        skey=f"herd_{selected_farm}"
        cur=st.session_state.get(skey,raw_herd_df)
        errs=validate_herd(cur)
        if errs: st.error(f"{len(errs)} 件のエラー値が検出されました"); st.dataframe(pd.DataFrame(errs),use_container_width=True)
        else: st.success("エラー値は検出されませんでした")
        st.markdown("---")
        disp2=cur[[c for c in cur.columns if not c.startswith("_")]].copy()
        rc_cx=find_col(disp2,"rc"); la_cx=find_col(disp2,"lact"); di_cx=find_col(disp2,"dim"); cc2={}
        if rc_cx: cc2[rc_cx]=st.column_config.SelectboxColumn("RC",options=[0,1,2,3,4,5,6],help="0=未経産 1=フレッシュ 2=フレッシュOK 3=空胎 4=妊鑑待ち 5=受胎 6=乾乳")
        if la_cx: cc2[la_cx]=st.column_config.NumberColumn("LACT",min_value=0,max_value=20,step=1)
        if di_cx: cc2[di_cx]=st.column_config.NumberColumn("DIM",min_value=0,max_value=700)
        edited=st.data_editor(disp2,column_config=cc2,use_container_width=True,num_rows="fixed",key=f"editor_{selected_farm}")
        ca,cb=st.columns([1,3])
        with ca:
            if st.button("修正を適用",type="primary"):
                st.session_state[skey]=edited; st.success("修正を適用しました。"); st.rerun()
        with cb:
            if st.button("元のデータに戻す"):
                st.session_state[skey]=raw_herd_df.copy(); st.success("元のデータに戻しました。"); st.rerun